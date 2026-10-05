"""Unit tests for paper strategy tracker (no network)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.strategy_tracker import (
    BREAKOUT_CONFIRM_SEC,
    _levels_from_row,
    force_close_all_open,
    ingest_candidates,
    mark_to_market,
    summarize,
)


def _promote_open(path: Path, symbol: str, last: float) -> None:
    """Simulate breakout hold completed → status open."""
    data = json.loads(path.read_text(encoding="utf-8"))
    t = data["trades"][0]
    t["status"] = "open"
    t["result_ar"] = "مفتوح — اختراق مؤكد"
    t["breakout_confirmed_ts"] = int(t.get("opened_ts") or 0)
    t["opened_ts"] = int(t.get("opened_ts") or 0) - 200
    t["last"] = last
    path.write_text(json.dumps(data), encoding="utf-8")


class StrategyTrackerTests(unittest.TestCase):
    def test_levels_long(self):
        lv = _levels_from_row({"entry": 10.0, "stop": 9.0, "tp1": 11.5, "tp2": 13.0})
        self.assertEqual(lv, (10.0, 9.0, 11.5, 13.0))

    def test_ingest_pending_then_trail_to_breakeven(self):
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
                self.assertEqual(data["trades"][0]["status"], "pending_breakout")
                _promote_open(path, "TEST", 1.0)
                # Hit TP1 → raise stop to entry, stay open
                mark_to_market({"TEST": 1.12})
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["status"], "open")
                self.assertEqual(t["stop_trail_stage"], 1)
                self.assertEqual(float(t["stop"]), 1.0)
                self.assertIn("هدف1", t["result_ar"])
                # Break raised stop at entry → win_be
                data = json.loads(path.read_text(encoding="utf-8"))
                data["trades"][0]["opened_ts"] -= 200
                path.write_text(json.dumps(data), encoding="utf-8")
                mark_to_market({"TEST": 0.99})
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["status"], "win_be")
                s = summarize([t])
                # tiny negative around BE still counts by pnl sign; 0.99 is -1%
                self.assertEqual(s["losses"], 1)

    def test_breakout_confirm_after_hold(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            base = 1_700_000_000.0
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"), mock.patch(
                "bot.strategy_tracker._riyadh_eod_flat", return_value=False
            ), mock.patch("bot.strategy_tracker._now", return_value=base):
                ingest_candidates(
                    sniper=[
                        {
                            "symbol": "BRK",
                            "last": 2.0,
                            "entry": 2.0,
                            "stop": 1.8,
                            "tp1": 2.2,
                            "tp2": 2.4,
                        }
                    ]
                )
            data = json.loads(path.read_text(encoding="utf-8"))
            t = data["trades"][0]
            self.assertEqual(t["status"], "pending_breakout")
            t["breakout_above_since"] = int(base - BREAKOUT_CONFIRM_SEC - 5)
            t["appeared_signal_ts"] = int(base - BREAKOUT_CONFIRM_SEC - 5)
            path.write_text(json.dumps(data), encoding="utf-8")
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._riyadh_eod_flat", return_value=False), mock.patch(
                "bot.strategy_tracker._now", return_value=base
            ):
                mark_to_market({"BRK": 2.05})
            t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
            self.assertEqual(t["status"], "open")
            self.assertIn("اختراق مؤكد", t["result_ar"])

    def test_tp1_then_tp2_trails_stop_to_tp1(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"), mock.patch(
                "bot.strategy_tracker._riyadh_eod_flat", return_value=False
            ):
                ingest_candidates(
                    sniper=[
                        {
                            "symbol": "RUN",
                            "last": 10.0,
                            "entry": 10.0,
                            "stop": 9.0,
                            "tp1": 11.0,
                            "tp2": 12.0,
                        }
                    ]
                )
                _promote_open(path, "RUN", 10.0)
                mark_to_market({"RUN": 11.5})
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["stop_trail_stage"], 1)
                self.assertEqual(float(t["stop"]), 10.0)
                data = json.loads(path.read_text(encoding="utf-8"))
                data["trades"][0]["opened_ts"] -= 200
                path.write_text(json.dumps(data), encoding="utf-8")
                mark_to_market({"RUN": 12.5})
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["status"], "open")
                self.assertEqual(t["stop_trail_stage"], 2)
                self.assertEqual(float(t["stop"]), 11.0)
                data = json.loads(path.read_text(encoding="utf-8"))
                data["trades"][0]["opened_ts"] -= 200
                path.write_text(json.dumps(data), encoding="utf-8")
                mark_to_market({"RUN": 10.9})
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["status"], "win_trail_tp1")
                self.assertGreater(t["pnl_pct"], 0)

    def test_stop_loss(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"), mock.patch(
                "bot.strategy_tracker._riyadh_eod_flat", return_value=False
            ):
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
                # Pending: stop cracked before confirm
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
            ), mock.patch("bot.strategy_tracker._riyadh_eod_flat", return_value=False), mock.patch(
                "bot.strategy_tracker.QANNAS_PAPER_LATE_HOUR", 24
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
                            "dollar_volume": 5_000_000,
                            "rvol": 4.0,
                            "change_pct": 25.0,
                            "complete": True,
                            "has_news": True,
                        }
                    ]
                )
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=False
            ), mock.patch("bot.strategy_tracker._riyadh_eod_flat", return_value=False):
                mark_to_market({"BBB": 10.4})
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["status"], "session_end")
                self.assertGreater(t["pnl_pct"], 0)

    def test_force_close_all(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._riyadh_eod_flat", return_value=False), mock.patch(
                "bot.strategy_tracker.QANNAS_PAPER_LATE_HOUR", 24
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
                            "dollar_volume": 5_000_000,
                            "rvol": 4.0,
                            "change_pct": 25.0,
                            "complete": True,
                            "has_news": True,
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
                "status": "win_be",
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
        self.assertEqual(s["win_rate"], 50.0)
        self.assertEqual(s["avg_pnl_pct"], 0.0)

    def test_trades_for_day_filters(self):
        from bot.strategy_tracker import _trades_for_day

        trades = [
            {"id": "qannas:A:2026-09-28", "opened_local": "2026-09-28 10:00", "status": "open"},
            {"id": "qannas:B:2026-09-29", "opened_local": "2026-09-29 10:00", "status": "open"},
            {"id": "qannas:C:2026-09-29", "opened_ts": 1727610000, "opened_local": "", "status": "win_be"},
        ]
        today = _trades_for_day(trades, "2026-09-29")
        syms = {t["id"].split(":")[1] for t in today}
        self.assertIn("B", syms)
        self.assertNotIn("A", syms)

    def _qannas_row(self, **over):
        row = {
            "symbol": "QOK",
            "last": 2.0,
            "entry": 2.0,
            "stop": 1.8,
            "tp1": 2.12,
            "tp2": 2.28,
            "dollar_volume": 5_000_000,
            "rvol": 4.0,
            "change_pct": 25.0,
            "has_news": True,
            "complete": True,
            "checks_ok": 4,
            "tier": "ready",
        }
        row.update(over)
        return row

    def test_qannas_no_green_exit_after_5min(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"), mock.patch(
                "bot.strategy_tracker._riyadh_eod_flat", return_value=False
            ), mock.patch("bot.strategy_tracker.QANNAS_PAPER_LATE_HOUR", 24):
                ingest_candidates(qannas=[self._qannas_row(symbol="FADE")])
                _promote_open(path, "FADE", 2.0)
                data = json.loads(path.read_text(encoding="utf-8"))
                data["trades"][0]["opened_ts"] -= 320
                data["trades"][0]["mfe_pct"] = 0.0
                path.write_text(json.dumps(data), encoding="utf-8")
                mark_to_market({"FADE": 1.95})
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["status"], "loss_fade")
                self.assertIn("5د", t["result_ar"])
                self.assertLess(t["pnl_pct"], 0)

    def test_qannas_skips_thin_dollar_volume(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"), mock.patch(
                "bot.strategy_tracker._riyadh_eod_flat", return_value=False
            ), mock.patch("bot.strategy_tracker.QANNAS_PAPER_LATE_HOUR", 24):
                n = ingest_candidates(
                    qannas=[self._qannas_row(symbol="THIN", dollar_volume=200_000)]
                )
                self.assertEqual(n, 0)

    def test_qannas_skips_late_hour(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"), mock.patch(
                "bot.strategy_tracker._riyadh_eod_flat", return_value=False
            ), mock.patch("bot.strategy_tracker.QANNAS_PAPER_LATE_HOUR", 0):
                n = ingest_candidates(qannas=[self._qannas_row(symbol="LATE")])
                self.assertEqual(n, 0)

    def test_qannas_skips_parabolic_extension(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"), mock.patch(
                "bot.strategy_tracker._riyadh_eod_flat", return_value=False
            ), mock.patch("bot.strategy_tracker.QANNAS_PAPER_LATE_HOUR", 24):
                n = ingest_candidates(
                    qannas=[self._qannas_row(symbol="PARA", change_pct=150.0)]
                )
                self.assertEqual(n, 0)

    def test_qannas_skips_sub_min_price(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"), mock.patch(
                "bot.strategy_tracker._riyadh_eod_flat", return_value=False
            ), mock.patch("bot.strategy_tracker.QANNAS_PAPER_LATE_HOUR", 24):
                n = ingest_candidates(
                    qannas=[self._qannas_row(symbol="PENNY", last=0.2, entry=0.2)]
                )
                self.assertEqual(n, 0)

    def test_qannas_accepts_liquid_in_window(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"), mock.patch(
                "bot.strategy_tracker._riyadh_eod_flat", return_value=False
            ), mock.patch("bot.strategy_tracker.QANNAS_PAPER_LATE_HOUR", 24):
                n = ingest_candidates(qannas=[self._qannas_row(symbol="GOOD")])
                self.assertEqual(n, 1)
                t = json.loads(path.read_text(encoding="utf-8"))["trades"][0]
                self.assertEqual(t["status"], "pending_breakout")

    def test_qannas_skips_incomplete_watch(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategy_ledger.json"
            with mock.patch("bot.strategy_tracker.LEDGER_PATH", path), mock.patch(
                "bot.strategy_tracker._paper_session_open", return_value=True
            ), mock.patch("bot.strategy_tracker._session_phase", return_value="regular"), mock.patch(
                "bot.strategy_tracker._riyadh_eod_flat", return_value=False
            ), mock.patch("bot.strategy_tracker.QANNAS_PAPER_LATE_HOUR", 24):
                n = ingest_candidates(
                    qannas=[
                        self._qannas_row(
                            symbol="WATCH",
                            complete=False,
                            has_news=False,
                            checks_ok=2,
                            tier="watch",
                        )
                    ]
                )
                self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
