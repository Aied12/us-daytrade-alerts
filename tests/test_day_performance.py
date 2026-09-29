"""Tests for daily appear→open→close performance log."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.day_performance import build_day_report, snapshot_from_row, write_day_report
from bot.strategy_tracker import ingest_candidates, mark_to_market


class DayPerformanceTests(unittest.TestCase):
    def test_snapshot_fields(self):
        snap = snapshot_from_row(
            {
                "symbol": "AAA",
                "name": "Aaa Inc",
                "first_ts": 1700000000,
                "last": 2.5,
                "change_pct": 25.0,
                "rvol": 4.2,
                "has_news": True,
                "news_title": "FDA ok",
                "score": 99,
                "checks_ok": 3,
                "checks_total": 4,
                "session_ar": "Premarket",
            },
            source="qannas",
        )
        self.assertEqual(snap["appeared_ts"], 1700000000)
        self.assertEqual(snap["appear_price"], 2.5)
        self.assertEqual(snap["appear_change_pct"], 25.0)
        self.assertTrue(snap["appear_has_news"])

    def test_write_day_report_with_enriched_trade(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = root / "strategy_ledger.json"
            pages = root / "pages"
            pages.mkdir()
            board = root / "qannas_board.json"
            seen = root / "qannas_seen.json"
            board.write_text(
                json.dumps(
                    {
                        "updated_ts": 1700000100,
                        "items": [
                            {
                                "symbol": "ZZZ",
                                "name": "Zed",
                                "first_ts": 1700000000,
                                "last": 1.0,
                                "entry": 1.0,
                                "stop": 0.9,
                                "tp1": 1.1,
                                "tp2": 1.2,
                                "change_pct": 30,
                                "rvol": 5,
                                "has_news": False,
                                "score": 50,
                                "session_ar": "Open",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            seen.write_text(json.dumps({"ZZZ": 1700000000}), encoding="utf-8")
            ledger.write_text(
                json.dumps(
                    {
                        "trades": [
                            {
                                "id": "qannas:ZZZ:2099-01-01",
                                "symbol": "ZZZ",
                                "source": "qannas",
                                "source_ar": "القناص",
                                "strategies": ["القناص"],
                                "opened_ts": 1700000060,
                                "opened_local": "2099-01-01 10:01",
                                "entry": 1.0,
                                "stop": 0.9,
                                "tp1": 1.1,
                                "tp2": 1.2,
                                "last": 1.05,
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
            with mock.patch("bot.day_performance.LEDGER_PATH", ledger), mock.patch(
                "bot.day_performance.QANNAS_BOARD", board
            ), mock.patch("bot.day_performance.QANNAS_SEEN", seen), mock.patch(
                "bot.day_performance.SNIPER_BOARD", root / "missing_sniper.json"
            ), mock.patch(
                "bot.day_performance.SNIPER_SEEN", root / "missing_sniper_seen.json"
            ), mock.patch(
                "bot.day_performance.DAY_DIR", root / "day_performance"
            ), mock.patch(
                "bot.day_performance._day_str", return_value="2099-01-01"
            ):
                report = write_day_report(day="2099-01-01", out_dirs=[pages])
                self.assertEqual(report["day"], "2099-01-01")
                self.assertEqual(len(report["trades"]), 1)
                t = report["trades"][0]
                self.assertEqual(t["appeared_ts"], 1700000000)
                self.assertEqual(t["time_to_open_sec"], 60)
                self.assertTrue((pages / "day-performance.json").exists())
                self.assertTrue((pages / "day-performance.csv").exists())
                csv_text = (pages / "day-performance.csv").read_text(encoding="utf-8")
                self.assertIn("ZZZ", csv_text)

    def test_ingest_attaches_appearance(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            # Same Riyadh calendar day as "now" so sticky-day clamp does not fire
            from datetime import datetime
            from zoneinfo import ZoneInfo

            now = datetime.now(ZoneInfo("Asia/Riyadh"))
            first = int(now.replace(hour=9, minute=0, second=0).timestamp())
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ):
                n = ingest_candidates(
                    qannas=[
                        {
                            "symbol": "Q1",
                            "name": "Q One",
                            "first_ts": first,
                            "last": 3.0,
                            "entry": 3.0,
                            "stop": 2.8,
                            "tp1": 3.2,
                            "tp2": 3.5,
                            "change_pct": 22,
                            "rvol": 3.1,
                            "has_news": True,
                            "news_title": "Deal",
                            "score": 80,
                            "session_ar": "RTH",
                            "strategies": ["القناص"],
                        }
                    ]
                )
                self.assertEqual(n, 1)
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["appeared_ts"], first)
                self.assertEqual(t["appear_change_pct"], 22)
                self.assertTrue(t["appear_has_news"])
                self.assertIsInstance(t.get("price_trail"), list)
                self.assertGreaterEqual(len(t["price_trail"]), 1)

    def test_win_rate_skips_flat_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = root / "strategy_ledger.json"
            ledger.write_text(
                json.dumps(
                    {
                        "trades": [
                            {
                                "id": "qannas:W:2099-01-01",
                                "symbol": "W",
                                "opened_local": "2099-01-01 10:00",
                                "opened_ts": 4102444800,
                                "status": "win_tp1",
                                "pnl_pct": 8.0,
                                "r_multiple": 0.8,
                                "entry": 1,
                                "stop": 0.9,
                                "exit": 1.08,
                            },
                            {
                                "id": "qannas:F:2099-01-01",
                                "symbol": "F",
                                "opened_local": "2099-01-01 11:00",
                                "opened_ts": 4102448400,
                                "status": "session_end",
                                "pnl_pct": 0.0,
                                "r_multiple": 0.0,
                                "entry": 1,
                                "stop": 0.9,
                                "exit": 1.0,
                                "result_ar": "إغلاق الجلسة",
                            },
                            {
                                "id": "qannas:L:2099-01-01",
                                "symbol": "L",
                                "opened_local": "2099-01-01 12:00",
                                "opened_ts": 4102452000,
                                "status": "loss_sl",
                                "pnl_pct": -5.0,
                                "r_multiple": -0.5,
                                "entry": 1,
                                "stop": 0.9,
                                "exit": 0.95,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch("bot.day_performance.LEDGER_PATH", ledger), mock.patch(
                "bot.day_performance.QANNAS_BOARD", root / "missing.json"
            ), mock.patch("bot.day_performance.QANNAS_SEEN", root / "missing_seen.json"), mock.patch(
                "bot.day_performance.SNIPER_BOARD", root / "missing_s.json"
            ), mock.patch(
                "bot.day_performance.SNIPER_SEEN", root / "missing_ss.json"
            ), mock.patch(
                "bot.day_performance._day_str", return_value="2099-01-01"
            ):
                report = build_day_report(day="2099-01-01")
                s = report["summary"]
                self.assertEqual(s["wins"], 1)
                self.assertEqual(s["losses"], 1)
                self.assertEqual(s["flat"], 1)
                self.assertEqual(s["win_rate"], 50.0)
                self.assertEqual(s["avg_pnl_pct"], 1.5)  # (8 + -5) / 2


if __name__ == "__main__":
    unittest.main()
