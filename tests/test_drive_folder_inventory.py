"""drive_folder_inventory.py の集計ロジックに対する検査。

実行前に「何件・何時間ぶんを処理することになるのか」を数えるための
スクリプト。ここが狂うと課金と実行時間の見積もりを誤る。
"""

from __future__ import annotations

import unittest

import _stub_deps

_stub_deps.install()

import drive_folder_inventory as inv  # noqa: E402


class QuoteEscapingTest(unittest.TestCase):
    def test_単一引用符をエスケープする(self):
        self.assertEqual(inv.quote("O'Brien"), "O\\'Brien")

    def test_引用符が無ければそのまま返す(self):
        self.assertEqual(inv.quote("録画"), "録画")


class DurationTest(unittest.TestCase):
    def test_再生時間を整数ミリ秒で返す(self):
        item = {"videoMediaMetadata": {"durationMillis": "3600000"}}
        self.assertEqual(inv.duration_ms(item), 3_600_000)

    def test_メタデータが無ければNoneを返す(self):
        self.assertIsNone(inv.duration_ms({}))

    def test_再生時間の項目が無ければNoneを返す(self):
        self.assertIsNone(inv.duration_ms({"videoMediaMetadata": {}}))


class SummarizeTest(unittest.TestCase):
    def test_再生時間が分かるものと分からないものを数え分ける(self):
        items = [
            {"videoMediaMetadata": {"durationMillis": "1800000"}},   # 0.5h
            {"videoMediaMetadata": {"durationMillis": "5400000"}},   # 1.5h
            {},                                                       # 不明
        ]
        result = inv.summarize(items)
        self.assertEqual(result["files"], 3)
        self.assertEqual(result["duration_known"], 2)
        self.assertEqual(result["duration_unknown"], 1)
        self.assertEqual(result["known_total_hours"], 2.0)

    def test_合計時間を小数第2位で丸める(self):
        items = [{"videoMediaMetadata": {"durationMillis": "1000000"}}]
        self.assertEqual(inv.summarize(items)["known_total_hours"], 0.28)

    def test_空の入力でもゼロを返す(self):
        result = inv.summarize([])
        self.assertEqual(result["files"], 0)
        self.assertEqual(result["duration_known"], 0)
        self.assertEqual(result["duration_unknown"], 0)
        self.assertEqual(result["known_total_hours"], 0)

    def test_全件が不明でも合計は0時間になる(self):
        result = inv.summarize([{}, {}])
        self.assertEqual(result["duration_unknown"], 2)
        self.assertEqual(result["known_total_hours"], 0)


if __name__ == "__main__":
    unittest.main()
