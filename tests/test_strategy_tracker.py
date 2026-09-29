"""Unit tests for paper strategy tracker (no network)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.strategy_tracker import (
    _levels_from_row,
    force_close_all_open,
    ingest_candidates,
    mark_to_market,
    summarize,
)


class StrategyTrackerTests(unittest.TestCase):
    def test_levels_long(self):
        lv = _levels_from_row({"entry": 10.0, "stop": 9.0, "tp1": 11.5, "tp2": 13.0})
        self.assertEqual(lv, (10.0, 9.0, 11.5, 13.0))

    def test_ingest_and_win_tp1(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"), mock.patch(
                "bot.strategy_tracker._riyadh_eod_flat", return_value=False
            ):
                n = ingest_candidates(
                    sniper=[
                        {
                            "symbol": "TEST",
                            "last": 1.0,
                            "entry": 1.0,
                            "stop": 0.9,
                            "tp1": 1.1,
                            "tp2": 1.2,
                            "tier_key": "cents",
                        }
                    ]
                )
                self.assertEqual(n, 1)
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["trades"][0]["status"], "open")
                data["trades"][0]["opened_ts"] = data["trades"][0]["opened_ts"] - 120
                path.write_text(json.dumps(data), encoding="utf-8")
                mark_to_market({"TEST": 1.12})
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["status"], "win_tp1")
                self.assertGreater(t["pnl_pct"], 0)
                s = summarize([t])
                self.assertEqual(s["wins"], 1)
                self.assertEqual(s["win_rate"], 100.0)

    def test_stop_loss(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"):
                ingest_candidates(
                    opportunities=[
                        {
                            "symbol": "AAA",
                            "side": "long",
                            "allowed": True,
                            "last": 50.0,
                            "entry": 50.0,
                            "stop": 48.0,
                            "target": 55.0,
                            "strategies": ["ارتداد VWAP"],
                        }
                    ]
                )
                data = json.loads(path.read_text(encoding="utf-8"))
                data["trades"][0]["opened_ts"] -= 200
                path.write_text(json.dumps(data), encoding="utf-8")
                mark_to_market({"AAA": 47.5})
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["status"], "loss_sl")
                self.assertLess(t["pnl_pct"], 0)

    def test_no_ingest_when_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=False
            ):
                n = ingest_candidates(
                    qannas=[
                        {
                            "symbol": "ZZZ",
                            "last": 2.0,
                            "entry": 2.0,
                            "stop": 1.8,
                            "tp1": 2.2,
                            "tp2": 2.4,
                        }
                    ]
                )
                self.assertEqual(n, 0)
                self.assertFalse(path.exists())

    def test_session_end_flattens_open(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ):
                ingest_candidates(
                    qannas=[
                        {
                            "symbol": "BBB",
                            "last": 10.0,
                            "entry": 10.0,
                            "stop": 9.0,
                            "tp1": 11.0,
                            "tp2": 12.0,
                        }
                    ]
                )
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=False
            ):
                mark_to_market({"BBB": 10.4})
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["status"], "session_end")
                self.assertIn("إغلاق الجلسة", t["result_ar"])
                self.assertGreater(t["pnl_pct"], 0)

    def test_force_close_all(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ):
                ingest_candidates(
                    qannas=[
                        {
                            "symbol": "CCC",
                            "last": 5.0,
                            "entry": 5.0,
                            "stop": 4.5,
                            "tp1": 5.5,
                            "tp2": 6.0,
                        }
                    ]
                )
                n = force_close_all_open()
                self.assertEqual(n, 1)
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["status"], "session_end")


    def test_win_rate_excludes_flat_session_end(self):
        trades = [
            {
                "symbol": "W1",
                "status": "win_tp1",
                "pnl_pct": 10.0,
                "r_multiple": 1.0,
                "source_ar": "القناص",
                "strategies": ["القناص"],
                "opened_local": "2026-09-29 10:00",
                "exit_ts": 1,
                "exit_local": "2026-09-29 10:10",
                "entry": 1,
                "exit": 1.1,
                "stop": 0.9,
            },
            {
                "symbol": "L1",
                "status": "loss_sl",
                "pnl_pct": -10.0,
                "r_multiple": -1.0,
                "source_ar": "القناص",
                "strategies": ["القناص"],
                "opened_local": "2026-09-29 11:00",
                "exit_ts": 2,
                "exit_local": "2026-09-29 11:10",
                "entry": 1,
                "exit": 0.9,
                "stop": 0.9,
            },
            {
                "symbol": "F1",
                "status": "session_end",
                "pnl_pct": 0.0,
                "r_multiple": 0.0,
                "source_ar": "القناص",
                "strategies": ["القناص"],
                "result_ar": "إغلاق الجلسة",
                "opened_local": "2026-09-29 12:00",
                "exit_ts": 3,
                "exit_local": "2026-09-29 23:00",
                "entry": 1,
                "exit": 1.0,
                "stop": 0.9,
            },
        ]
        s = summarize(trades)
        self.assertEqual(s["wins"], 1)
        self.assertEqual(s["losses"], 1)
        self.assertEqual(s["flat"], 1)
        self.assertEqual(s["session_end"], 1)
        # 1 win / (1 win + 1 loss) — flat excluded
        self.assertEqual(s["win_rate"], 50.0)
        self.assertEqual(s["avg_pnl_pct"], 0.0)  # (10 + -10) / 2

    def test_trades_for_day_filters(self):
        from bot.strategy_tracker import _trades_for_day

        trades = [
            {"id": "qannas:A:2026-09-28", "opened_local": "2026-09-28 10:00", "status": "open"},
            {"id": "qannas:B:2026-09-29", "opened_local": "2026-09-29 10:00", "status": "open"},
            {"id": "qannas:C:2026-09-29", "opened_ts": 1727610000, "opened_local": "", "status": "win_tp1"},
        ]
        # Force day via opened_local for B
        today = _trades_for_day(trades, "2026-09-29")
        syms = {t["id"].split(":")[1] for t in today}
        self.assertIn("B", syms)
        self.assertNotIn("A", syms)


if __name__ == "__main__":
    unittest.main()
