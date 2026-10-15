"""Tests for the counting in drive_folder_inventory.py.

The script that answers "how many, and how many hours" before a run. Wrong
here and the estimate for the bill and the running time is wrong with it.
"""

from __future__ import annotations

import unittest

import _stub_deps

_stub_deps.install()

import drive_folder_inventory as inv  # noqa: E402


class QuoteEscapingTest(unittest.TestCase):
    def test_a_single_quote_is_escaped(self):
        self.assertEqual(inv.quote("O'Brien"), "O\\'Brien")

    def test_a_string_without_quotes_comes_back_unchanged(self):
        # Non-ASCII, because the names in Drive are, and because it is the
        # case where nothing should happen at all.
        self.assertEqual(inv.quote("録画"), "録画")


class DurationTest(unittest.TestCase):
    def test_a_duration_comes_back_as_whole_milliseconds(self):
        item = {"videoMediaMetadata": {"durationMillis": "3600000"}}
        self.assertEqual(inv.duration_ms(item), 3_600_000)

    def test_no_metadata_is_None(self):
        self.assertIsNone(inv.duration_ms({}))

    def test_metadata_without_a_duration_is_None(self):
        self.assertIsNone(inv.duration_ms({"videoMediaMetadata": {}}))


class SummarizeTest(unittest.TestCase):
    def test_known_and_unknown_durations_are_counted_separately(self):
        items = [
            {"videoMediaMetadata": {"durationMillis": "1800000"}},   # 0.5h
            {"videoMediaMetadata": {"durationMillis": "5400000"}},   # 1.5h
            {},                                                       # unknown
        ]
        result = inv.summarize(items)
        self.assertEqual(result["files"], 3)
        self.assertEqual(result["duration_known"], 2)
        self.assertEqual(result["duration_unknown"], 1)
        self.assertEqual(result["known_total_hours"], 2.0)

    def test_the_total_is_rounded_to_two_places(self):
        items = [{"videoMediaMetadata": {"durationMillis": "1000000"}}]
        self.assertEqual(inv.summarize(items)["known_total_hours"], 0.28)

    def test_empty_input_comes_back_as_zeroes(self):
        result = inv.summarize([])
        self.assertEqual(result["files"], 0)
        self.assertEqual(result["duration_known"], 0)
        self.assertEqual(result["duration_unknown"], 0)
        self.assertEqual(result["known_total_hours"], 0)

    def test_all_unknown_still_totals_zero_hours(self):
        result = inv.summarize([{}, {}])
        self.assertEqual(result["duration_unknown"], 2)
        self.assertEqual(result["known_total_hours"], 0)


if __name__ == "__main__":
    unittest.main()
