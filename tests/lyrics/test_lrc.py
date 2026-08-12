"""Parsing and synthesising LRC text."""

from __future__ import annotations

import unittest

from tests.lyrics.support import track  # noqa: F401  (pins config paths first)

from lyrics.text.lrc import parse_lrc, plain_to_lrc, strip_credit_lines


class ParseLrc(unittest.TestCase):
    def test_parses_minutes_seconds_and_hundredths(self):
        self.assertEqual(parse_lrc("[01:02.34]hello"), [(62.34, "hello")])

    def test_parses_three_decimal_places(self):
        # QQ Music emits milliseconds; two- and three-digit fractions must both
        # land on the same timeline.
        self.assertEqual(parse_lrc("[01:02.345]x"), [(62.345, "x")])

    def test_parses_whole_second_timestamps(self):
        self.assertEqual(parse_lrc("[00:07]x"), [(7.0, "x")])

    def test_one_line_may_carry_several_timestamps(self):
        # A repeated chorus is stored once with every time it recurs.
        self.assertEqual(
            parse_lrc("[00:01.00][00:09.00]same"),
            [(1.0, "same"), (9.0, "same")],
        )

    def test_lines_are_sorted_by_time(self):
        self.assertEqual(
            parse_lrc("[00:09.00]late\n[00:01.00]early"),
            [(1.0, "early"), (9.0, "late")],
        )

    def test_identical_timestamps_keep_the_last_text(self):
        # Two lines at one instant cannot both show; the later one wins so a
        # translation appended below the original is what gets drawn.
        self.assertEqual(
            parse_lrc("[00:05.00]first\n[00:05.00]second"), [(5.0, "second")]
        )

    def test_enhanced_word_timings_are_stripped_from_the_text(self):
        # Karaoke-style LRCs carry per-word <mm:ss.xx> marks inline; the widget
        # draws whole lines, so only the line timestamp survives.
        self.assertEqual(
            parse_lrc("[00:01.00]<00:01.00>Hey <00:02.00>you"),
            [(1.0, "Hey you")],
        )

    def test_offset_tag_shifts_every_timestamp(self):
        self.assertEqual(parse_lrc("[offset:+500]\n[00:10.00]a"), [(10.5, "a")])
        self.assertEqual(parse_lrc("[offset:-500]\n[00:10.00]a"), [(9.5, "a")])

    def test_offset_never_pushes_a_line_before_zero(self):
        # A negative offset larger than the first timestamp would otherwise
        # produce a line the playback position can never reach.
        self.assertEqual(parse_lrc("[offset:-9000]\n[00:01.00]a"), [(0.0, "a")])

    def test_untimed_and_empty_lines_are_dropped(self):
        self.assertEqual(
            parse_lrc("[ti:Title]\nplain text\n\n[00:01.00]kept"),
            [(1.0, "kept")],
        )

    def test_timestamped_line_with_no_text_is_dropped(self):
        self.assertEqual(parse_lrc("[00:01.00]   \n[00:02.00]x"), [(2.0, "x")])

    def test_empty_input(self):
        self.assertEqual(parse_lrc(""), [])


class PlainToLrc(unittest.TestCase):
    def test_spreads_lines_evenly_across_the_duration(self):
        self.assertEqual(plain_to_lrc("a\nb", 120), "[00:00.000]a\n[01:00.000]b")

    def test_blank_lines_are_ignored_when_spacing(self):
        self.assertEqual(plain_to_lrc("a\n\n  \nb", 120), "[00:00.000]a\n[01:00.000]b")

    def test_no_duration_yields_nothing(self):
        # Without a duration there is no timeline to spread across, and a
        # fabricated one would scroll at random.
        self.assertEqual(plain_to_lrc("a\nb", 0), "")
        self.assertEqual(plain_to_lrc("a\nb", -5), "")

    def test_no_text_yields_nothing(self):
        self.assertEqual(plain_to_lrc("", 120), "")

    def test_output_round_trips_through_parse_lrc(self):
        parsed = parse_lrc(plain_to_lrc("a\nb\nc", 90))
        self.assertEqual([text for _, text in parsed], ["a", "b", "c"])
        self.assertEqual([round(at) for at, _ in parsed], [0, 30, 60])


class StripCreditLines(unittest.TestCase):
    def test_removes_credit_tags_but_keeps_lyrics(self):
        self.assertEqual(
            strip_credit_lines(
                "[00:00.00]作词：某人\n[00:01.00]Real lyric\n[00:02.00]词：X"
            ),
            "[00:01.00]Real lyric",
        )

    def test_keeps_a_lyric_that_merely_starts_with_a_credit_word(self):
        # The tag must be followed by a colon or space to count as a credit;
        # a lyric beginning with the same characters is still a lyric.
        kept = "[00:01.00]作词人的故事"
        self.assertEqual(strip_credit_lines(kept), kept)

    def test_untimed_credit_lines_are_removed_too(self):
        self.assertEqual(strip_credit_lines("编曲: Someone\n[00:01.00]x"), "[00:01.00]x")

    def test_empty_input(self):
        self.assertEqual(strip_credit_lines(""), "")


if __name__ == "__main__":
    unittest.main()
