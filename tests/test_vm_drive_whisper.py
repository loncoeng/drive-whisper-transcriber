"""vm_drive_whisper.py の純粋関数に対する検査。

重点は3つ。
  - Drive クエリへ値を埋め込むときのエスケープ (安全性)
  - 既存ドキュメントの照合 (冪等性。ここが崩れると二重に文字起こしされる)
  - 文字起こしの整形と誤認識の補正 (挙動)
"""

from __future__ import annotations

import unittest

import _stub_deps

_stub_deps.install()

import vm_drive_whisper as vm  # noqa: E402


class Segment:
    """faster-whisper のセグメントの代役。使うのは start と text だけ。"""

    def __init__(self, start: float, text: str) -> None:
        self.start = start
        self.text = text


class Info:
    """faster-whisper の検出言語情報の代役。"""

    def __init__(self, language: str = "ja", probability: float = 0.98) -> None:
        self.language = language
        self.language_probability = probability


MEDIA = {"id": "media-1", "name": "定例会議_2026-01-15.mp4"}
PREFIX = "【文字起こし】"   # argparse の --doc-prefix 既定値


class QuoteEscapingTest(unittest.TestCase):
    def test_単一引用符をエスケープする(self):
        # Drive のクエリは値を ' で囲む。エスケープしないと
        # 名前に ' を含むフォルダで構文が壊れ、条件が意図せず変わる。
        self.assertEqual(vm.q("O'Brien"), "O\\'Brien")

    def test_引用符が無ければそのまま返す(self):
        self.assertEqual(vm.q("定例会議"), "定例会議")

    def test_複数の引用符をすべてエスケープする(self):
        self.assertEqual(vm.q("a'b'c"), "a\\'b\\'c")


class MatchingDocumentTest(unittest.TestCase):
    """既に文字起こし済みかどうかの判定。ここが冪等性の要。"""

    def test_元ファイルIDが一致すれば名前が変わっていても見つける(self):
        children = [
            {
                "id": "doc-1",
                "name": "議事録 (整形後)",
                "mimeType": vm.DOC_MIME,
                "appProperties": {"sourceMediaFileId": "media-1"},
            }
        ]
        found = vm.matching_document(MEDIA, children, PREFIX)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "doc-1")

    def test_IDが無くても既定の名前が一致すれば見つける(self):
        children = [
            {
                "id": "doc-2",
                "name": "【文字起こし】定例会議_2026-01-15",
                "mimeType": vm.DOC_MIME,
            }
        ]
        self.assertEqual(vm.matching_document(MEDIA, children, PREFIX)["id"], "doc-2")

    def test_ドキュメント以外は名前が一致しても対象にしない(self):
        children = [
            {
                "id": "not-a-doc",
                "name": "【文字起こし】定例会議_2026-01-15",
                "mimeType": "application/pdf",
            }
        ]
        self.assertIsNone(vm.matching_document(MEDIA, children, PREFIX))

    def test_別の元ファイルのドキュメントは対象にしない(self):
        children = [
            {
                "id": "doc-3",
                "name": "【文字起こし】別の会議",
                "mimeType": vm.DOC_MIME,
                "appProperties": {"sourceMediaFileId": "media-999"},
            }
        ]
        self.assertIsNone(vm.matching_document(MEDIA, children, PREFIX))

    def test_候補が無ければNoneを返す(self):
        self.assertIsNone(vm.matching_document(MEDIA, [], PREFIX))

    def test_接頭辞を変えても既存ドキュメントを検出できる(self):
        # 回帰テスト。以前は matching_document が接頭辞を直書きしていたため、
        # --doc-prefix を既定から変えると名前照合が働かず、同じ音声を
        # 二重に文字起こししていた。
        children = [
            {
                "id": "doc-4",
                "name": "【議事録】定例会議_2026-01-15",
                "mimeType": vm.DOC_MIME,
            }
        ]
        found = vm.matching_document(MEDIA, children, "【議事録】")
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "doc-4")

    def test_接頭辞が違えば名前照合の対象にしない(self):
        children = [
            {
                "id": "doc-5",
                "name": "【文字起こし】定例会議_2026-01-15",
                "mimeType": vm.DOC_MIME,
            }
        ]
        self.assertIsNone(vm.matching_document(MEDIA, children, "【議事録】"))

    def test_接頭辞が違っても元ファイルIDが一致すれば検出する(self):
        # 名前が変わっていても appProperties が残っていれば拾える。
        # 単一ファイルのやり直し (--source-file-id) でこれが効かず、
        # 重複ドキュメントが作られていた。
        children = [
            {
                "id": "doc-6",
                "name": "2026-01-15 議事録",
                "mimeType": vm.DOC_MIME,
                "appProperties": {"sourceMediaFileId": "media-1"},
            }
        ]
        self.assertEqual(vm.matching_document(MEDIA, children, "【議事録】")["id"], "doc-6")


class FormatTranscriptTest(unittest.TestCase):
    def test_見出しに元ファイル名と検出言語とファイルIDを含む(self):
        text = vm.format_transcript(MEDIA, [], Info())
        self.assertIn("定例会議_2026-01-15.mp4", text)
        self.assertIn("検出言語: ja（確率 0.98）", text)
        self.assertIn("元ファイルID: media-1", text)

    def test_開始秒をhh_mm_ss形式に整形する(self):
        segments = [Segment(0, "冒頭"), Segment(61, "1分1秒"), Segment(3725, "1時間2分5秒")]
        text = vm.format_transcript(MEDIA, segments, Info())
        self.assertIn("[00:00:00] 冒頭", text)
        self.assertIn("[00:01:01] 1分1秒", text)
        self.assertIn("[01:02:05] 1時間2分5秒", text)

    def test_誤認識された固有名詞を補正する(self):
        segments = [Segment(0, "トレロとスラップの件はシャローシに確認")]
        text = vm.format_transcript(MEDIA, segments, Info())
        self.assertIn("Trello と Slack".replace(" と ", "と"), text)
        self.assertIn("社労士", text)
        self.assertNotIn("トレロ", text)
        self.assertNotIn("スラップ", text)
        self.assertNotIn("シャローシ", text)

    def test_長い語を先に補正して部分一致で壊さない(self):
        # 「ローム部」は「ローム」を含む。短い方を先に適用すると
        # 「労務部」ではなく「労務部」にならず壊れる。
        segments = [Segment(0, "ローム部へ連絡")]
        self.assertIn("労務部へ連絡", vm.format_transcript(MEDIA, segments, Info()))

    def test_空白だけのセグメントは行にしない(self):
        segments = [Segment(0, "   "), Segment(5, "本文")]
        lines = [l for l in vm.format_transcript(MEDIA, segments, Info()).splitlines() if l.startswith("[")]
        self.assertEqual(lines, ["[00:00:05] 本文"])

    def test_同じ語の連続を畳む(self):
        # 無音区間で Whisper が同じ語を繰り返すことがある。
        segments = [Segment(0, "会社の会社の会社の会社の方針")]
        self.assertIn("[00:00:00] 会社の方針", vm.format_transcript(MEDIA, segments, Info()))

    def test_本文が無くても改行で終わる(self):
        self.assertTrue(vm.format_transcript(MEDIA, [], Info()).endswith("\n"))


if __name__ == "__main__":
    unittest.main()
