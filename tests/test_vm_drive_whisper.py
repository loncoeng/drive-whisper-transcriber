"""Tests for the pure functions in vm_drive_whisper.py.

Three things carry the weight.
  - escaping values put into a Drive query (safety)
  - matching an existing document (idempotency — break this and audio gets
    transcribed twice)
  - formatting a transcript and correcting what was misheard (behaviour)

The Japanese below is data, not prose: it is what Drive holds, what the
documents are named, and what Whisper produces. The correction table only
exists because Whisper mishears Japanese, so an ASCII fixture would exercise
nothing.
"""

from __future__ import annotations

import unittest

import _stub_deps

_stub_deps.install()

import vm_drive_whisper as vm  # noqa: E402


class Segment:
    """Stands in for a faster-whisper segment. Only start and text are used."""

    def __init__(self, start: float, text: str) -> None:
        self.start = start
        self.text = text


class Info:
    """Stands in for faster-whisper's detected-language information."""

    def __init__(self, language: str = "ja", probability: float = 0.98) -> None:
        self.language = language
        self.language_probability = probability


MEDIA = {"id": "media-1", "name": "定例会議_2026-01-15.mp4"}
PREFIX = "【文字起こし】"   # argparse's default for --doc-prefix


class QuoteEscapingTest(unittest.TestCase):
    def test_a_single_quote_is_escaped(self):
        # A Drive query wraps values in '. Unescaped, a folder with a ' in its
        # name breaks the syntax and quietly changes what the query matches.
        self.assertEqual(vm.q("O'Brien"), "O\\'Brien")

    def test_a_string_without_quotes_comes_back_unchanged(self):
        self.assertEqual(vm.q("定例会議"), "定例会議")

    def test_every_quote_is_escaped(self):
        self.assertEqual(vm.q("a'b'c"), "a\\'b\\'c")


class MatchingDocumentTest(unittest.TestCase):
    """Deciding whether this was already transcribed. Idempotency rests here."""

    def test_a_matching_source_id_is_found_even_after_a_rename(self):
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

    def test_without_an_id_the_default_name_is_enough(self):
        children = [
            {
                "id": "doc-2",
                "name": "【文字起こし】定例会議_2026-01-15",
                "mimeType": vm.DOC_MIME,
            }
        ]
        self.assertEqual(vm.matching_document(MEDIA, children, PREFIX)["id"], "doc-2")

    def test_the_right_name_on_something_that_is_not_a_document_does_not_count(self):
        children = [
            {
                "id": "not-a-doc",
                "name": "【文字起こし】定例会議_2026-01-15",
                "mimeType": "application/pdf",
            }
        ]
        self.assertIsNone(vm.matching_document(MEDIA, children, PREFIX))

    def test_a_document_from_a_different_source_does_not_count(self):
        children = [
            {
                "id": "doc-3",
                "name": "【文字起こし】別の会議",
                "mimeType": vm.DOC_MIME,
                "appProperties": {"sourceMediaFileId": "media-999"},
            }
        ]
        self.assertIsNone(vm.matching_document(MEDIA, children, PREFIX))

    def test_no_candidates_is_None(self):
        self.assertIsNone(vm.matching_document(MEDIA, [], PREFIX))

    def test_a_changed_prefix_still_finds_the_existing_document(self):
        # A regression test. matching_document used to have the prefix written
        # into it, so changing --doc-prefix from the default stopped the name
        # matching and the same audio was transcribed twice.
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

    def test_a_different_prefix_is_not_matched_by_name(self):
        children = [
            {
                "id": "doc-5",
                "name": "【文字起こし】定例会議_2026-01-15",
                "mimeType": vm.DOC_MIME,
            }
        ]
        self.assertIsNone(vm.matching_document(MEDIA, children, "【議事録】"))

    def test_a_different_prefix_still_matches_on_the_source_id(self):
        # The name may have changed, and appProperties survives it. This did
        # not hold for a single-file rerun (--source-file-id), and duplicate
        # documents were created.
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
    def test_the_heading_carries_the_file_name_language_and_source_id(self):
        # The heading is Japanese because it is read in the finished document.
        text = vm.format_transcript(MEDIA, [], Info())
        self.assertIn("定例会議_2026-01-15.mp4", text)
        self.assertIn("検出言語: ja（確率 0.98）", text)
        self.assertIn("元ファイルID: media-1", text)

    def test_a_start_time_is_formatted_as_hh_mm_ss(self):
        segments = [Segment(0, "冒頭"), Segment(61, "1分1秒"), Segment(3725, "1時間2分5秒")]
        text = vm.format_transcript(MEDIA, segments, Info())
        self.assertIn("[00:00:00] 冒頭", text)
        self.assertIn("[00:01:01] 1分1秒", text)
        self.assertIn("[01:02:05] 1時間2分5秒", text)

    def test_misheard_names_are_corrected(self):
        segments = [Segment(0, "トレロとスラップの件はシャローシに確認")]
        text = vm.format_transcript(MEDIA, segments, Info())
        self.assertIn("Trello と Slack".replace(" と ", "と"), text)
        self.assertIn("社労士", text)
        self.assertNotIn("トレロ", text)
        self.assertNotIn("スラップ", text)
        self.assertNotIn("シャローシ", text)

    def test_the_longer_term_is_corrected_first(self):
        # トレロー contains トレロ. Take the shorter one first and the
        # replacement leaves the trailing long-vowel mark behind: Trelloー.
        # The table is ordered longest-first for exactly this.
        segments = [Segment(0, "トレローの件")]
        self.assertIn("[00:00:00] Trelloの件", vm.format_transcript(MEDIA, segments, Info()))

    def test_a_multi_character_correction_survives_a_shorter_overlap(self):
        # ローム部 and ローム are both in the table. This one comes out right
        # either way, and it is here because it is the pair that gets edited.
        segments = [Segment(0, "ローム部へ連絡")]
        self.assertIn("労務部へ連絡", vm.format_transcript(MEDIA, segments, Info()))

    def test_a_segment_of_only_whitespace_produces_no_line(self):
        segments = [Segment(0, "   "), Segment(5, "本文")]
        lines = [l for l in vm.format_transcript(MEDIA, segments, Info()).splitlines() if l.startswith("[")]
        self.assertEqual(lines, ["[00:00:05] 本文"])

    def test_a_repeated_phrase_is_collapsed(self):
        # Whisper repeats itself over a silent stretch.
        segments = [Segment(0, "会社の会社の会社の会社の方針")]
        self.assertIn("[00:00:00] 会社の方針", vm.format_transcript(MEDIA, segments, Info()))

    def test_it_ends_with_a_newline_even_with_no_body(self):
        self.assertTrue(vm.format_transcript(MEDIA, [], Info()).endswith("\n"))


if __name__ == "__main__":
    unittest.main()
