#!/usr/bin/env python3
"""Douyin live chat -> live room danmaku, via the Douyin Open Platform.

This is a *receiver*, not a Douyin API client and not a scraper. The
upstream project was explicit: do not scrape a live room's web page. The
operator must therefore:

1. Create an app on the Douyin Open Platform and get live-room message
   push permission for their own account (the platform decides this;
   no code here can grant it).
2. Point the platform's callback at this bridge (``--listen``).
3. Map the platform's gift to the KPI unit in ``map_event`` below, or
   leave ``gift`` at 0 for chat. There is no universal Douyin gift ->
   RMB rate baked in here, and inventing one would corrupt the KPI.

The callback payload shape is deliberately loose: Douyin's push format
varies by product version, so the operator adapts ``map_event`` to the
exact fields their subscription sends, then runs the mock mode to prove
the forwarding path before going live:

    python3 tools/douyin_chat_bridge.py --mock --server http://127.0.0.1:28900
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer


def map_event(event: dict) -> dict | None:
    """Map one platform callback event to a /api/danmaku body.

    Accepts the common shapes: {"user": ..., "text": ..., "gift": ...},
    {"nickname": ..., "content": ...}, and {"msg_type": "gift", ...}.
    Returns None for events with no user-visible text or gift.
    """
    if not isinstance(event, dict):
        return None
    user = str(event.get("user") or event.get("nickname") or event.get("username") or "").strip() or "观众"
    text = str(event.get("text") or event.get("content") or event.get("message") or "").strip()
    gift = 0.0
    for key in ("gift", "gift_amount", "amount", "gift_value"):
        if event.get(key) is not None:
            try:
                gift = float(event[key])
            except (TypeError, ValueError):
                gift = 0.0
            break
    if event.get("msg_type") == "gift" and gift == 0:
        gift = 1.0  # a gift event with no amount: count it, do not price it
    if not text and gift <= 0:
        return None
    return {"user": user, "text": text, "gift": gift, "source": "douyin"}


def forward(server: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{server.rstrip('/')}/api/danmaku",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def make_handler(server: str, secret: str | None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # keep the bridge quiet on stream
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            if secret and self.headers.get("X-Bridge-Secret") != secret:
                self._reply(401, {"ok": False, "error": "bad secret"})
                return
            try:
                event = json.loads(raw.decode() or "{}")
            except ValueError:
                self._reply(400, {"ok": False, "error": "bad json"})
                return
            body = map_event(event)
            if body is None:
                self._reply(200, {"ok": True, "skipped": True})
                return
            try:
                result = forward(server, body)
            except Exception as error:  # noqa: BLE001 - a dead room must not kill the bridge
                self._reply(502, {"ok": False, "error": str(error)[:200]})
                return
            self._reply(200, {"ok": True, "forwarded": result})

        def _reply(self, status: int, payload: dict):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Douyin Open Platform callback receiver -> /api/danmaku")
    parser.add_argument("--server", default="http://127.0.0.1:28900")
    parser.add_argument("--listen", default="127.0.0.1:28901", help="host:port to receive callbacks on")
    parser.add_argument("--secret", default="", help="shared secret checked in X-Bridge-Secret, if set")
    parser.add_argument("--mock", action="store_true", help="send a few sample messages, then exit")
    args = parser.parse_args()
    if args.mock:
        samples = [
            {"user": "测试观众", "text": "你好"},
            {"user": "测试观众", "text": "投票A"},
            {"user": "送礼观众", "text": "", "gift": 10},
        ]
        for event in samples:
            body = map_event(event)
            if body:
                print(forward(args.server, body))
        return 0
    host, port = args.listen.rsplit(":", 1)
    server = HTTPServer((host, int(port)), make_handler(args.server, args.secret or None))
    print(f"Douyin bridge listening on {args.listen}, forwarding to {args.server}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
