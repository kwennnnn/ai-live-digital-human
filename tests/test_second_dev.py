"""Second-dev baseline: safe visual edits + interrupt-and-resume.

Runs against LiveRoom directly, in dry-run mode: no Vidu call, no cost.
The paid Vidu path (reference-image switch acks, AUDIO_INTERRUPTED on a
real session) is deliberately NOT claimed here; see SECOND_DEV.md.
"""
from __future__ import annotations

import asyncio
import copy
import json
import shutil
import sys
import tempfile
import time
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


class SecondDev(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="live-avatar-second-dev-"))
        assets = self.tmp / "assets"
        (assets / "library").mkdir(parents=True)
        for name in ("beach.jpg", "water.jpg"):
            shutil.copy(ROOT / "assets/avatars/xiaoshen.jpg", assets / "library" / name)
        (assets / "library.json").write_text(json.dumps({"items": [
            {"kind": "background", "name": "海边日落", "aliases": ["海边"], "file": "library/beach.jpg"},
            {"kind": "object", "name": "水", "aliases": ["一杯水"], "file": "library/water.jpg"},
        ]}, ensure_ascii=False))
        wardrobe = self.tmp / "outfits.json"
        wardrobe.write_text(json.dumps({"items": [
            {"name": "白色T恤", "aliases": ["白T"], "file": str(ROOT / "assets/avatars/xiaoshen.jpg"), "base": True},
            {"name": "银色飞行夹克", "aliases": ["银色夹克"], "file": str(ROOT / "assets/avatars/xiaoshen.jpg")},
        ]}, ensure_ascii=False))
        cfg = copy.deepcopy(server.DEFAULTS)
        cfg["safety"]["live_visual_edits"] = True
        cfg["safety"]["visual_edit_cooldown_seconds"] = 20
        cfg["safety"]["visual_edit_max_consecutive"] = 2
        cfg["safety"]["visual_edit_single_change_only"] = True
        cfg["safety"]["visual_edit_bundle_outfit"] = True
        cfg["schedule"]["interrupt_policy"] = "gift"
        cfg["generator"]["enabled"] = False
        cfg["avatars"]["xiaoshen"]["outfits"] = str(wardrobe)
        self.http = aiohttp.ClientSession()
        self.room = LiveRoom(cfg, ROOT, self.http, None,
                             AssetResolver(AssetLibrary(assets), None),
                             Moderator.from_file(ROOT / "moderation.json"), self.tmp)
        self.room.mode = "dry_run"
        self.room.status = "live"
        self.room.outfit = "白色T恤"

    async def asyncTearDown(self):
        await self.http.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def speaking(self):
        self.room.speaking_until = time.time() + 100
        self.room.current_line = "先帝创业未半而中道崩殂"
        self.room.subtitle = self.room.current_line

    async def test_single_visual_change_per_danmaku(self):
        result = await self.room.ingest("阿明", "海边背景，喝口水")
        cmd = result["command"]
        self.assertEqual(cmd["kind"], "background")
        self.assertEqual(len(cmd["parts"]), 1)
        self.assertIn("暂缓", cmd["note"])

    async def test_visual_cooldown_and_consecutive_cap(self):
        self.room.last_visual_edit_at = time.time()
        self.room.consecutive_visual_edits = 1
        result = await self.room.ingest("阿明", "去海边吧")
        self.assertEqual(result["command"]["status"], "failed")
        self.assertIn("冷却", result["command"]["note"])
        self.assertEqual(self.room.queue.waiting(), [])
        self.room.last_visual_edit_at = 0.0
        self.room.consecutive_visual_edits = 2
        result = await self.room.ingest("老王", "去海边吧")
        self.assertEqual(result["command"]["status"], "failed")
        self.assertIn("已达上限", result["command"]["note"])

    async def test_switch_bundles_outfit_and_revert_restores_snapshot(self):
        result = await self.room.ingest("阿明", "去海边吧")
        cmd = self.room.queue.waiting()[0]
        self.assertEqual(result["command"]["kind"], "background")
        await self.room._switch_visual(cmd)
        self.assertIn("background", self.room.visual)
        self.assertIn("garment", self.room.visual)  # current outfit rides along
        self.assertEqual(self.room.visual["garment"]["name"], "白色T恤")
        self.assertEqual(self.room.consecutive_visual_edits, 1)
        self.assertIsNotNone(self.room.visual_snapshot)
        self.assertTrue(await self.room.revert_visual())
        self.assertEqual(self.room.visual, {})
        self.assertEqual(self.room.consecutive_visual_edits, 0)

    async def test_gift_danmaku_interrupts_and_resumes(self):
        self.speaking()
        result = await self.room.ingest("土豪", "讲个笑话", gift=10)
        self.assertTrue(result["accepted"])
        self.assertEqual(len(self.room._priority), 2)
        self.assertIn("笑话", self.room._priority[0].text)
        self.assertEqual(self.room._priority[1].kind, "resume")
        self.assertIn("先帝创业", self.room._priority[1].text)
        self.assertEqual(self.room.speaking_until, 0.0)

    async def test_non_gift_does_not_interrupt_under_gift_policy(self):
        self.speaking()
        await self.room.ingest("阿明", "讲个笑话")
        self.assertEqual(self.room._priority, [])
        self.assertEqual(len(self.room.queue.waiting()), 1)
        self.assertGreater(self.room.speaking_until, time.time())

    async def test_off_policy_preserves_no_cut(self):
        self.room.cfg["schedule"]["interrupt_policy"] = "off"
        self.speaking()
        await self.room.ingest("土豪", "讲个笑话", gift=10)
        self.assertEqual(self.room._priority, [])
        self.assertEqual(len(self.room.queue.waiting()), 1)

    async def test_operator_interrupt_and_queue(self):
        self.speaking()
        result = await self.room.interrupt_and_queue("先说这个")
        self.assertEqual(result["priority"], 2)
        self.assertEqual(self.room._priority[0].text, "先说这个")
        self.assertEqual(self.room._priority[1].kind, "resume")


if __name__ == "__main__":
    unittest.main()
