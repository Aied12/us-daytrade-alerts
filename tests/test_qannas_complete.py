"""قناص: مكتمل 4/4 vs مراقبة."""

from __future__ import annotations

import unittest

from bot.qannas_scan import _checklist, _is_complete


class QannasCompleteTests(unittest.TestCase):
    def test_complete_requires_all_four(self):
        cl = _checklist(
            has_news=True,
            rvol=4.0,
            mcap=500_000_000,
            float_shares=8_000_000,
            change_pct=25.0,
            day_open=1.0,
            last=1.3,
            dollar_volume=6_000_000,
            phase="regular",
        )
        self.assertEqual(sum(1 for c in cl if c["ok"]), 4)
        self.assertTrue(_is_complete(cl))

    def test_rocket_without_news_is_incomplete(self):
        cl = _checklist(
            has_news=False,
            rvol=8.0,
            mcap=500_000_000,
            float_shares=8_000_000,
            change_pct=55.0,
            day_open=1.0,
            last=1.6,
            dollar_volume=8_000_000,
            phase="regular",
        )
        self.assertFalse(next(c for c in cl if c["key"] == "news")["ok"])
        self.assertFalse(_is_complete(cl))
        self.assertEqual(sum(1 for c in cl if c["ok"]), 3)

    def test_liq_needs_dollar_volume(self):
        cl = _checklist(
            has_news=True,
            rvol=5.0,
            mcap=500_000_000,
            float_shares=8_000_000,
            change_pct=25.0,
            day_open=1.0,
            last=1.3,
            dollar_volume=100_000,  # below 5M regular floor
            phase="regular",
        )
        self.assertFalse(next(c for c in cl if c["key"] == "liq")["ok"])
        self.assertFalse(_is_complete(cl))


if __name__ == "__main__":
    unittest.main()
