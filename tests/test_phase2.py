"""Second-dev phase 2: votes, KPI, song/big dance, product spotlight, audience memory, Douyin bridge mapping.

Runs against LiveRoom directly, in dry-run mode: no Vidu call, no cost.
Singing and big-dance quality are model behaviour and are NOT claimed here:
these tests only prove the prompt is routed, not that the model sings or
stands up. See SECOND_DEV_PHASE2.md.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixtures import ensure_default_avatars  # noqa: E402

ensure_default_avatars()
import server  # noqa: E402
from live.assets import AssetLibrary, AssetResolver  # noqa: E402
from live.moderation import Moderator  # noqa: E402
from live.session import LiveRoom  # noqa: E402


def load_douyin_bridge():
    spec = importlib.util.spec_from_file_location("douyin_bridge", ROOT / "tools" / "douyin_chat_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Phase2(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="live-avatar-phase2-"))
        assets = self.tmp / "assets"
        (assets / "library").mkdir(parents=True)
        (assets / "library.json").write_text(json.dumps({"items": []}, ensure_ascii=False))
        cfg = copy.deepcopy(server.DEFAULTS)
        cfg["show"]["kpi_target"] = 100
        cfg["show"]["kpi_name"] = "本场营业目标"
        cfg["products"] = [{"name": "养生茶", "price": "39元"}]
        cfg["generator"]["enabled"] = False
        self.http = aiohttp.ClientSession()
        self.room = LiveRoom(cfg, ROOT, self.http, None,
                             AssetResolver(AssetLibrary(assets), None),
                             Moderator.from_file(ROOT / "moderation.json"), self.tmp)
        self.room.mode = "dry_run"
        self.room.status = "live"

    async def asyncTearDown(self):
        await self.http.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_vote_lifecycle(self):
        self.room.start_vote("选造型", ["红队", "蓝队"], duration_seconds=60)
        result = await self.room.ingest("阿明", "投票红队")
        self.assertEqual(result["command"]["kind"], "vote")
        self.assertEqual(result["command"]["status"], "done")
        await self.room.ingest("阿明", "投票蓝队")  # later vote overwrites
        await self.room.ingest("小红", "投票红队")
        closed = self.room.close_vote()
        self.assertEqual(closed["tally"], {"红队": 1, "蓝队": 1})
        self.assertEqual(closed["total"], 2)
        # A fresh user, so this is not the upstream 60-second duplicate filter:
        # it must fail because there is no active round.
        after = await self.room.ingest("新人", "投票红队")
        self.assertEqual(after["command"]["status"], "failed")

    async def test_vote_invalid_option_rejected(self):
        self.room.start_vote("选造型", ["红队", "蓝队"])
        result = await self.room.ingest("阿明", "投票绿队")
        self.assertEqual(result["command"]["status"], "failed")
        self.assertEqual(self.room.vote_session.snapshot()["total"], 0)

    async def test_kpi_reaches_and_next_round_resets(self):
        await self.room.ingest("送礼观众", "", gift=60)
        self.assertFalse(self.room.kpi.snapshot()["reached"])
        await self.room.ingest("送礼观众", "", gift=50)
        snap = self.room.kpi.snapshot()
        self.assertTrue(snap["reached"])
        self.assertEqual(snap["current"], 110)
        self.room.next_kpi_round()
        snap = self.room.kpi.snapshot()
        self.assertEqual((snap["round"], snap["current"], snap["reached"]), (2, 0, False))

    async def test_song_and_big_dance_routing(self):
        song = await self.room.ingest("阿明", "唱首歌")
        self.assertEqual(song["command"]["kind"], "song")
        dance = await self.room.ingest("阿明", "来段热舞")
        self.assertEqual(dance["command"]["kind"], "action")
        queued = self.room.queue.waiting()
        big = next(c for c in queued if c.kind == "action")
        self.assertIn("大幅度", big.action_text)
        # Executing the song in dry-run must not raise and must mark done.
        song_cmd = next(c for c in queued if c.kind == "song")
        await self.room._execute(song_cmd)
        self.assertEqual(song_cmd.status, "done")

    async def test_product_spotlight_no_order(self):
        result = await self.room.ingest("阿明", "介绍一下养生茶")
        self.assertEqual(result["command"]["kind"], "product")
        cmd = next(c for c in self.room.queue.waiting() if c.kind == "product")
        await self.room._execute(cmd)
        self.assertEqual(self.room.snapshot()["product_spotlight"]["name"], "养生茶")
        self.assertEqual(cmd.status, "done")

    async def test_audience_memory_persists_to_file(self):
        await self.room.ingest("老观众", "你好")
        await self.room.ingest("老观众", "又来了")
        self.assertEqual(self.room.snapshot()["audience"]["count"], 1)
        self.assertEqual(self.room.audience.get("老观众")["messages"], 2)
        self.assertTrue((self.tmp / ".state" / "audience.json").exists())

    async def test_vote_close_announces_winner(self):
        self.room.start_vote("选造型", ["红队", "蓝队"])
        await self.room.ingest("阿明", "投票红队")
        self.room.close_vote()
        announced = [c for c in self.room.queue.waiting() if "投票结果" in c.text]
        self.assertEqual(len(announced), 1)
        self.assertIn("红队", announced[0].text)

    async def test_kpi_reached_announces(self):
        await self.room.ingest("送礼观众", "", gift=100)
        announced = [c for c in self.room.queue.waiting() if "KPI 达成" in c.text]
        self.assertEqual(len(announced), 1)

    def test_workbench_show_controls_exist(self):
        # Static guard only: it proves the controls are wired to the right
        # endpoints, not that a browser renders them.
        html = (ROOT / "static" / "workbench.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "workbench.js").read_text(encoding="utf-8")
        for element_id in ("voteForm", "voteCloseBtn", "kpiForm", "kpiNextBtn", "productForm"):
            self.assertIn(f'id="{element_id}"', html)
        for endpoint in ("/api/vote/start", "/api/vote/close", "/api/kpi/next_round", "/api/product/spotlight"):
            self.assertIn(endpoint, js)

    async def test_song_keeps_title(self):
        result = await self.room.ingest("阿明", "点歌月亮代表我的心")
        self.assertEqual(result["command"]["kind"], "song")
        self.assertEqual(result["command"]["item"], "月亮代表我的心")
        cmd = next(c for c in self.room.queue.waiting() if c.kind == "song")
        await self.room._execute(cmd)
        self.assertTrue(any("月亮代表我的心" in e["message"] for e in self.room.logs))

    async def test_vote_winner_changes_outfit(self):
        wardrobe_file = self.tmp / "outfits.json"
        wardrobe_file.write_text(json.dumps({"items": [
            {"name": "白色T恤", "aliases": ["白T"], "file": str(ROOT / "assets/avatars/xiaoshen.jpg"), "base": True},
            {"name": "银色飞行夹克", "aliases": ["银色夹克"], "file": str(ROOT / "assets/avatars/xiaoshen.jpg")},
        ]}, ensure_ascii=False))
        self.room.cfg["avatars"]["xiaoshen"]["outfits"] = str(wardrobe_file)
        self.room._wardrobes.clear()
        self.room.outfit = "白色T恤"
        self.room.start_vote("选造型", ["银色飞行夹克", "白色T恤"])
        await self.room.ingest("阿明", "投票银色飞行夹克")
        self.room.close_vote()
        outfits = [c for c in self.room.queue.waiting() if c.kind == "outfit"]
        self.assertEqual(len(outfits), 1)
        self.assertEqual(outfits[0].item, "银色飞行夹克")

    async def test_returning_audience_is_greeted(self):
        await self.room.ingest("老观众", "你好")
        first = next(c for c in self.room.queue.waiting() if c.kind == "chat")
        await self.room._execute(first)
        await self.room.ingest("老观众", "又来了")
        second = next(c for c in self.room.queue.waiting() if c.kind == "chat")
        await self.room._execute(second)
        self.assertTrue(any("老观众" in e["message"] and "第2次" in e["message"] for e in self.room.logs))

    async def test_big_dance_stands_up_first(self):
        await self.room.ingest("阿明", "来段热舞")
        cmd = next(c for c in self.room.queue.waiting() if c.kind == "action")
        await self.room._execute(cmd)
        messages = " ".join(e["message"] for e in self.room.logs)
        self.assertIn("站起", messages)
        self.assertIn("大幅度", messages)

    def test_stage_overlay_show_card_exists(self):
        html = (ROOT / "static" / "stage.html").read_text(encoding="utf-8")
        self.assertIn('id="showCard"', html)
        self.assertIn("s.vote", html)
        self.assertIn("s.kpi", html)
        self.assertIn("s.product_spotlight", html)

    def test_douyin_bridge_mapping(self):
        bridge = load_douyin_bridge()
        self.assertEqual(bridge.map_event({"nickname": "阿明", "content": "你好"}),
                         {"user": "阿明", "text": "你好", "gift": 0.0, "source": "douyin"})
        self.assertEqual(bridge.map_event({"user": "送礼", "msg_type": "gift"})["gift"], 1.0)
        self.assertIsNone(bridge.map_event({"user": "阿明", "text": ""}))
        self.assertIsNone(bridge.map_event("not-a-dict"))
