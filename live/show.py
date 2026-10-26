"""Phase 2 show mechanics: votes, gift KPI, product spotlight, audience memory.

These are the parts of the video effect that *code* can own. What code cannot
own is written down in SECOND_DEV_PHASE2.md, not papered over here:

- Singing quality, dance amplitude and full-body framing are Vidu model
  behaviour. The router only sends a stronger prompt; a seated base photo
  may still stay seated, and a model that cannot sing will talk instead.
- A gift amount arriving here is whatever the danmaku source reported. The
  bridge, not this module, must map a platform gift to the KPI unit, and
  must never invent a conversion rate.
- A product spotlight is not an order. No payment, cart or platform product
  link is created here; the operator shows the real link on the platform.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class VoteSession:
    """One vote round. One user, one vote; a later vote overwrites the earlier
    one, so a mistyped vote can be corrected without operator help."""

    def __init__(self) -> None:
        self.topic: str = ""
        self.options: list[str] = []
        self.votes: dict[str, str] = {}
        self.started_at: float = 0.0
        self.duration_seconds: int = 60
        self.last_result: dict | None = None

    def start(self, topic: str, options: list[str], duration_seconds: int = 60) -> dict:
        options = [o.strip() for o in options if o.strip()]
        if len(options) < 2:
            raise ValueError("投票至少需要两个选项")
        self.topic, self.options = topic.strip() or "投票", options
        self.votes = {}
        self.started_at = time.time()
        self.duration_seconds = max(10, min(3600, int(duration_seconds)))
        self.last_result = None
        return self.snapshot()

    def is_active(self, now: float | None = None) -> bool:
        if not self.topic or not self.started_at:
            return False
        now = time.time() if now is None else now
        return now - self.started_at < self.duration_seconds

    def vote(self, user: str, option: str) -> dict:
        if not self.is_active():
            return {"ok": False, "reason": "当前没有进行中的投票"}
        option = option.strip()
        if option not in self.options:
            return {"ok": False, "reason": f"选项不在本轮投票里：{'、'.join(self.options)}"}
        self.votes[user] = option
        return {"ok": True, "user": user, "option": option, "tally": self.tally()}

    def tally(self) -> dict[str, int]:
        return {o: sum(1 for v in self.votes.values() if v == o) for o in self.options}

    def close(self) -> dict:
        if not self.topic:
            return {"ok": False, "reason": "没有进行中的投票"}
        tally = self.tally()
        winner = max(self.options, key=lambda o: (tally[o], -self.options.index(o))) if self.votes else None
        result = {"ok": True, "topic": self.topic, "options": list(self.options),
                  "tally": tally, "total": len(self.votes), "winner": winner,
                  "reached_target": False}
        self.last_result = result
        self.topic, self.options, self.votes, self.started_at = "", [], {}, 0.0
        return result

    def snapshot(self) -> dict:
        return {"active": self.is_active(), "topic": self.topic, "options": list(self.options),
                "tally": self.tally(), "total": len(self.votes),
                "seconds_left": max(0, int(self.duration_seconds - (time.time() - self.started_at))) if self.is_active() else 0,
                "last_result": self.last_result}


class KpiTracker:
    """Gift KPI for one round. Target 0 means disabled, never reached.

    Reaching the target does *not* auto-reset: the operator decides the
    next round, so a show never silently restarts its KPI mid-stream."""

    def __init__(self, target: float = 0.0, name: str = "本场营业目标") -> None:
        self.target = float(target or 0)
        self.name = name
        self.current = 0.0
        self.round = 1
        self.reached = False

    def add(self, amount: float) -> bool:
        """Return True only on the transition into reached."""
        if amount <= 0 or self.target <= 0:
            return False
        self.current += float(amount)
        if not self.reached and self.current >= self.target:
            self.reached = True
            return True
        return False

    def next_round(self) -> dict:
        self.round += 1
        self.current = 0.0
        self.reached = False
        return self.snapshot()

    def snapshot(self) -> dict:
        return {"name": self.name, "target": self.target, "current": round(self.current, 2),
                "round": self.round, "reached": self.reached,
                "progress": round(self.current / self.target, 3) if self.target > 0 else 0.0}


class ProductCatalog:
    """Products the avatar can introduce. Spotlight only, no checkout."""

    def __init__(self, products: list[dict] | None = None) -> None:
        self.products = [p for p in (products or []) if p.get("name")]
        self.spotlight: dict | None = None

    def find(self, text: str) -> dict | None:
        for product in self.products:
            if product["name"] and product["name"] in text:
                return product
        return None

    def set_spotlight(self, product: dict) -> None:
        self.spotlight = product

    def snapshot(self) -> dict | None:
        return dict(self.spotlight) if self.spotlight else None


class AudienceLedger:
    """Cross-session audience memory: who came, how often, how much they
    gifted. Stored as plain JSON next to the room's .state, loaded at
    startup, saved after each accepted danmaku. It remembers behaviour,
    never platform private data beyond the danmaku name already shown."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.users: dict[str, dict] = {}
        if path and path.exists():
            try:
                self.users = json.loads(path.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError):
                self.users = {}

    def record(self, user: str, gift: float = 0.0) -> dict:
        entry = self.users.setdefault(user, {"messages": 0, "gifts": 0.0, "first_seen": time.time(), "last_seen": time.time()})
        entry["messages"] += 1
        entry["gifts"] = round(entry.get("gifts", 0.0) + float(gift or 0), 2)
        entry["last_seen"] = time.time()
        self._save()
        return entry

    def get(self, user: str) -> dict | None:
        return self.users.get(user)

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.users, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def snapshot(self) -> dict:
        return {"count": len(self.users),
                "top_gifters": sorted(((u, v.get("gifts", 0)) for u, v in self.users.items()),
                                      key=lambda kv: -kv[1])[:5]}
