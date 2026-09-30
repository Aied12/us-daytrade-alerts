"""Arabic news translation helpers — cache must never store English fallbacks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import news_ar


class NewsArTranslateTests(unittest.TestCase):
    def test_is_good_ar(self):
        self.assertTrue(news_ar._is_good_ar("أسهم أبل ترتفع", "Apple shares rise"))
        self.assertFalse(news_ar._is_good_ar("Apple shares rise", "Apple shares rise"))
        self.assertFalse(news_ar._is_good_ar("MYMEMORY WARNING: quota", "x"))
        self.assertFalse(news_ar._is_good_ar("", "x"))

    def test_translate_does_not_cache_english_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            text = "Apple shares surge after strong iPhone sales"
            with mock.patch.object(news_ar, "CACHE_DIR", cache), mock.patch.object(
                news_ar, "_via_mymemory", return_value=""
            ), mock.patch.object(news_ar, "_via_google", return_value=""), mock.patch.object(
                news_ar, "_via_argos", return_value=""
            ), mock.patch.dict(news_ar._TRANSLATE_BUDGET, {"left": 5}):
                out = news_ar._translate_ar(text)
                self.assertEqual(out, text)
                self.assertEqual(list(cache.glob("*.json")), [])

    def test_translate_caches_good_arabic(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            text = "Apple shares surge after strong iPhone sales"
            ar = "أسهم أبل ترتفع بعد مبيعات آيفون القوية"
            with mock.patch.object(news_ar, "CACHE_DIR", cache), mock.patch.object(
                news_ar, "_via_mymemory", return_value=ar
            ), mock.patch.dict(news_ar._TRANSLATE_BUDGET, {"left": 5}):
                out = news_ar._translate_ar(text)
                self.assertEqual(out, ar)
                files = list(cache.glob("*.json"))
                self.assertEqual(len(files), 1)
                payload = json.loads(files[0].read_text(encoding="utf-8"))
                self.assertEqual(payload["ar"], ar)

    def test_purge_bad_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            good = cache / "good.json"
            bad = cache / "bad.json"
            good.write_text(
                json.dumps({"en": "Hello", "ar": "مرحبا"}, ensure_ascii=False),
                encoding="utf-8",
            )
            bad.write_text(
                json.dumps({"en": "Hello world", "ar": "Hello world"}, ensure_ascii=False),
                encoding="utf-8",
            )
            with mock.patch.object(news_ar, "CACHE_DIR", cache):
                n = news_ar.purge_bad_news_ar_cache()
            self.assertEqual(n, 1)
            self.assertTrue(good.exists())
            self.assertFalse(bad.exists())

    def test_ensure_news_arabic_budget(self):
        rows = [
            {"title": "Stock A rises on deal", "title_ar": "Stock A rises on deal"},
            {"title": "Stock B jumps after beat", "title_ar": ""},
        ]
        with mock.patch.object(
            news_ar, "_translate_ar", side_effect=lambda t, force=False: f"عربي:{t}"
        ):
            n = news_ar.ensure_news_arabic(rows, max_new=1)
        self.assertEqual(n, 1)
        self.assertTrue(str(rows[0]["title_ar"]).startswith("عربي:"))
        self.assertEqual(rows[1]["title_ar"], "")


if __name__ == "__main__":
    unittest.main()
