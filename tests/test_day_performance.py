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
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path):
                n = ingest_candidates(
                    qannas=[
                        {
                            "symbol": "Q1",
                            "name": "Q One",
                            "first_ts": 1700000000,
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
                self.assertEqual(t["appeared_ts"], 1700000000)
                self.assertEqual(t["appear_change_pct"], 22)
                self.assertTrue(t["appear_has_news"])
                self.assertIsInstance(t.get("price_trail"), list)
                self.assertGreaterEqual(len(t["price_trail"]), 1)


if __name__ == "__main__":
    unittest.main()
