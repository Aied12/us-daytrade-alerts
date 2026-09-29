"""Tests for 23:00 Riyadh EOD flatten + archive (cumulative kept)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from bot.performance_eod import (
    archive_day,
    block_new_opens,
    past_flat_time,
    run_eod_if_needed,
    today_kpis_cleared,
)
from bot.strategy_tracker import ingest_candidates, mark_to_market, summarize

RIYADH = ZoneInfo("Asia/Riyadh")


class PerformanceEodTests(unittest.TestCase):
    def test_past_flat_time_window(self):
        before = datetime(2026, 9, 29, 22, 59, tzinfo=RIYADH)
        at = datetime(2026, 9, 29, 23, 0, tzinfo=RIYADH)
        after = datetime(2026, 9, 29, 23, 30, tzinfo=RIYADH)
        morning = datetime(2026, 9, 30, 1, 0, tzinfo=RIYADH)
        self.assertFalse(past_flat_time(before))
        self.assertTrue(past_flat_time(at))
        self.assertTrue(past_flat_time(after))
        self.assertFalse(past_flat_time(morning))
        self.assertTrue(block_new_opens(at))
        self.assertFalse(block_new_opens(before))

    def test_eod_closes_and_archives_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = root / "strategy_ledger.json"
            state = root / "performance_state.json"
            day_dir = root / "day_performance"
            arch = day_dir / "archive"
            pages = root / "pages"
            pages.mkdir()
            # seed one open trade
            ledger.write_text(
                json.dumps(
                    {
                        "trades": [
                            {
                                "id": "qannas:AAA:2026-09-29",
                                "symbol": "AAA",
                                "source": "qannas",
                                "source_ar": "القناص",
                                "strategies": ["القناص"],
                                "opened_ts": 1790680000,
                                "opened_local": "2026-09-29 16:00",
                                "entry": 10.0,
                                "stop": 9.0,
                                "tp1": 11.0,
                                "tp2": 12.0,
                                "last": 10.5,
                                "status": "open",
                                "mfe_pct": 5,
                                "mae_pct": 0,
                                "result_ar": "مفتوح",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            flat_now = datetime(2026, 9, 29, 23, 5, tzinfo=RIYADH)
            with mock.patch("bot.performance_eod.ROOT", root), mock.patch(
                "bot.performance_eod.STATE_PATH", state
            ), mock.patch("bot.performance_eod.ARCHIVE_DIR", arch), mock.patch(
                "bot.performance_eod._now_riyadh", return_value=flat_now
            ), mock.patch(
                "bot.strategy_tracker.LEDGER_PATH", ledger
            ), mock.patch(
                "bot.day_performance.LEDGER_PATH", ledger
            ), mock.patch(
                "bot.day_performance.DAY_DIR", day_dir
            ), mock.patch(
                "bot.day_performance.QANNAS_BOARD", root / "missing.json"
            ), mock.patch(
                "bot.day_performance.QANNAS_SEEN", root / "missing_seen.json"
            ), mock.patch(
                "bot.day_performance.SNIPER_BOARD", root / "missing_s.json"
            ), mock.patch(
                "bot.day_performance.SNIPER_SEEN", root / "missing_ss.json"
            ), mock.patch(
                "bot.day_performance._day_str", return_value="2026-09-29"
            ):
                out = run_eod_if_needed(price_by_symbol={"AAA": 10.5})
                self.assertTrue(out.get("archived") or out.get("already_done"))
                data = json.loads(ledger.read_text(encoding="utf-8"))
                self.assertEqual(data["trades"][0]["status"], "session_end")
                self.assertIn("11 مساءً", data["trades"][0]["result_ar"])
                self.assertTrue((arch / "2026-09-29.json").exists())
                st = json.loads(state.read_text(encoding="utf-8"))
                self.assertEqual(st.get("archived_day"), "2026-09-29")
                self.assertTrue(today_kpis_cleared(flat_now))
                # second call idempotent
                out2 = run_eod_if_needed(price_by_symbol={"AAA": 10.5})
                self.assertTrue(out2.get("already_done"))
                # cumulative still present
                s = summarize(data["trades"])
                self.assertEqual(s["closed"], 1)


if __name__ == "__main__":
    unittest.main()
