"""Fitting a lyric into the Touch Bar row: measuring, cropping, wrapping and
scrolling.

Everything here is pure. The one outside input is the stored measurement that
`character_width` consults, which the tests hand over by patching
`viewport.read_viewport` -- never by measuring, which would cost an osascript
launch and make the numbers depend on the machine's fonts.
"""

from tests.lyrics.support import track  # noqa: F401  (pins config paths)

import unittest
from unittest.mock import patch

from lyrics import config
from lyrics.display import layout, viewport


def measured(widths_px: dict[str, float]):
    """Pretend the last measurement stored these glyph advances, in points.

    `character_width` reaches the metrics through `viewport.read_viewport`, so
    replacing that one function covers every caller below it.
    """
    return patch.object(
        viewport, "read_viewport", lambda: {"character_widths": dict(widths_px)}
    )


def pixels_per_cell(value: float = 7.0):
    """Pin the px-per-cell divisor, which is environment-tunable."""
    return patch.object(config, "PIXELS_PER_CELL", value)


class CharacterWidthTests(unittest.TestCase):
    def test_ascii_character_is_one_cell(self):
        self.assertEqual(layout.character_width("a"), 1)
        self.assertEqual(layout.character_width(" "), 1)

    def test_wide_east_asian_character_is_two_cells(self):
        # W (ideograph), F (full-width form) and A (ambiguous) all render at
        # roughly double an ASCII glyph in the Touch Bar font.
        self.assertEqual(layout.character_width("中"), 2)
        self.assertEqual(layout.character_width("，"), 2)
        self.assertEqual(layout.character_width("α"), 2)

    def test_combining_mark_has_no_width(self):
        # It draws on top of the previous glyph, so it advances nothing.
        self.assertEqual(layout.character_width("\u0301"), 0)
        # A decomposed "e" + combining acute costs one cell in total.
        self.assertEqual(layout.display_width("e\u0301"), 1)

    def test_measured_width_overrides_the_class_default(self):
        with pixels_per_cell(7.0), measured({"a": 3.5}):
            self.assertEqual(layout.character_width("a"), 0.5)

    def test_measured_width_overrides_the_wide_default_too(self):
        # A measurement beats the east-asian-width guess, not just the ASCII
        # one: the guess is only there for glyphs the helper never measured.
        with pixels_per_cell(7.0), measured({"中": 7.0}):
            self.assertEqual(layout.character_width("中"), 1.0)

    def test_unmeasured_character_falls_back_to_the_class_default(self):
        with pixels_per_cell(7.0), measured({"a": 3.5}):
            self.assertEqual(layout.character_width("b"), 1)
            self.assertEqual(layout.character_width("中"), 2)

    def test_unusable_stored_metrics_fall_back_to_the_class_default(self):
        with pixels_per_cell(7.0), measured({"a": "wide"}):
            self.assertEqual(layout.character_width("a"), 1)
        with patch.object(viewport, "read_viewport", lambda: {}):
            self.assertEqual(layout.character_width("a"), 1)


class DisplayWidthTests(unittest.TestCase):
    def test_display_width_of_empty_text_is_zero(self):
        self.assertEqual(layout.display_width(""), 0)

    def test_display_width_sums_mixed_scripts(self):
        self.assertEqual(layout.display_width("ab中"), 4)

    def test_display_width_sums_fractional_cells(self):
        with pixels_per_cell(7.0), measured({"a": 3.5, "b": 10.5}):
            self.assertEqual(layout.display_width("aab"), 2.5)


class CropCellsTests(unittest.TestCase):
    def test_crop_cells_takes_the_leading_cells(self):
        self.assertEqual(layout.crop_cells("abcdef", 0, 3), "abc")

    def test_crop_cells_starts_at_the_offset(self):
        self.assertEqual(layout.crop_cells("abcdef", 2, 3), "cde")

    def test_crop_cells_drops_a_character_straddling_the_start(self):
        # "中" spans cells 0-2, so a start of 1 lands inside it; half a glyph
        # cannot be drawn, so the whole character is skipped.
        self.assertEqual(layout.crop_cells("中文", 1, 4), "文")

    def test_crop_cells_stops_before_a_character_that_would_overflow(self):
        # Two cells fit, the second "中" would take the width to 4.
        self.assertEqual(layout.crop_cells("中文", 0, 3), "中")

    def test_crop_cells_past_the_end_is_empty(self):
        self.assertEqual(layout.crop_cells("abc", 10, 5), "")

    def test_crop_cells_trims_the_surrounding_whitespace(self):
        # A crop that lands mid-gap would otherwise start the row with a
        # space, which reads as a ragged left edge while scrolling.
        self.assertEqual(layout.crop_cells("a bcd e", 1, 4), "bcd")

    def test_crop_cells_respects_fractional_widths(self):
        with pixels_per_cell(7.0), measured({"a": 3.5}):
            self.assertEqual(layout.crop_cells("aaaa", 0, 1), "aa")

    def test_crop_cells_zero_width_is_empty(self):
        self.assertEqual(layout.crop_cells("abc", 0, 0), "")


class BreakPositionTests(unittest.TestCase):
    def test_break_positions_point_just_past_each_delimiter(self):
        # Offsets, not indexes: a row ends *after* the mark it breaks on.
        self.assertEqual(layout.break_positions("a,b、c", "，,、"), [2, 4])

    def test_break_positions_without_a_delimiter_is_empty(self):
        self.assertEqual(layout.break_positions("abc", "，,、"), [])


class RowWidthTests(unittest.TestCase):
    def test_row_width_picks_the_row_s_own_budget(self):
        self.assertEqual(layout.row_width([20.0, 13.0], 0), 20.0)
        self.assertEqual(layout.row_width([20.0, 13.0], 1), 13.0)

    def test_row_width_past_the_end_reuses_the_last_budget(self):
        # Callers pass one width per row they planned for; any further row
        # keeps the narrowest known budget rather than raising.
        self.assertEqual(layout.row_width([20.0, 13.0], 5), 13.0)


class RowsOverflowTests(unittest.TestCase):
    def test_rows_overflow_is_not_positive_when_every_row_fits(self):
        self.assertLessEqual(layout.rows_overflow(["abc", "de"], [5.0, 5.0]), 0)

    def test_rows_overflow_reports_the_worst_row(self):
        self.assertEqual(layout.rows_overflow(["abc", "de"], [5.0, 1.0]), 1.0)


class WrapLyricTests(unittest.TestCase):
    def setUp(self):
        # Both knobs are environment-tunable; pin them to the shipped values.
        for attribute, value in (
            ("LINE_BREAK_PUNCTUATION", "，,、"),
            ("BREAK_ON_SPACE", True),
        ):
            patcher = patch.object(config, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_wrap_lyric_keeps_a_fitting_line_on_one_row(self):
        self.assertEqual(layout.wrap_lyric("short line", [20.0, 13.0], 2), ["short line"])

    def test_wrap_lyric_breaks_at_punctuation_keeping_the_mark_on_its_row(self):
        self.assertEqual(
            layout.wrap_lyric("one two, three four", [12.0, 12.0], 2),
            ["one two,", "three four"],
        )

    def test_wrap_lyric_breaks_at_a_full_width_comma(self):
        # The CJK marks are the reason the break list is configurable at all.
        self.assertEqual(
            layout.wrap_lyric("早安，世界", [6.0, 6.0], 2), ["早安，", "世界"]
        )

    def test_wrap_lyric_prefers_the_latest_punctuation_that_still_fits(self):
        # Breaking at the first comma would waste most of row 1.
        self.assertEqual(
            layout.wrap_lyric("a, b, c, ddddddddddddd", [12.0, 20.0], 2),
            ["a, b, c,", "ddddddddddddd"],
        )

    def test_wrap_lyric_breaks_at_punctuation_before_spaces(self):
        # Both tiers make the rows fit here, so the punctuation tier wins and
        # row 1 ends at the comma rather than at the later space.
        self.assertEqual(
            layout.wrap_lyric("one two, three four", [16.0, 16.0], 2),
            ["one two,", "three four"],
        )

    def test_wrap_lyric_falls_back_to_spaces_when_punctuation_cannot_fit(self):
        # The only comma sits past the row's width, so breaking there leaves
        # row 1 overflowing; a space break gets both rows inside the budget.
        self.assertEqual(
            layout.wrap_lyric("alpha beta gamma, delta", [12.0, 12.0], 2),
            ["alpha beta", "gamma, delta"],
        )

    def test_wrap_lyric_without_any_delimiter_stays_on_one_row(self):
        # Nothing to break on, so the line keeps the scrolling behaviour.
        self.assertEqual(
            layout.wrap_lyric("abcdefghijklmnopqrst", [8.0, 8.0], 2),
            ["abcdefghijklmnopqrst"],
        )

    def test_wrap_lyric_with_one_row_never_wraps(self):
        self.assertEqual(
            layout.wrap_lyric("one two, three four", [12.0], 1), ["one two, three four"]
        )

    def test_wrap_lyric_never_exceeds_max_rows(self):
        text = "one, two, three, four, five, six"
        rows = layout.wrap_lyric(text, [10.0, 10.0], config.MAX_LYRIC_ROWS)
        self.assertLessEqual(len(rows), config.MAX_LYRIC_ROWS)
        # Nothing is dropped: the last row carries the whole remainder and
        # scrolls if it has to.
        self.assertEqual("".join(rows).replace(" ", ""), text.replace(" ", ""))

    def test_wrap_at_breaks_measures_each_row_against_its_own_width(self):
        # render.py gives row 2 a different budget from row 1 (the viewport
        # minus CONTINUATION_INDENT), so the second cut must use row 2's own
        # width. Same text, same row 1, but a roomier row 2 swallows one more
        # phrase -- which only happens if the width is read per row.
        text = "a, bb, ccc, dddd"
        self.assertEqual(
            layout.wrap_at_breaks(text, [2.0, 6.0], 3, "，,、"),
            ["a,", "bb,", "ccc, dddd"],
        )
        self.assertEqual(
            layout.wrap_at_breaks(text, [2.0, 9.0], 3, "，,、"),
            ["a,", "bb, ccc,", "dddd"],
        )

    def test_wrap_at_breaks_takes_the_latest_delimiter_that_still_fits(self):
        # Packing each row full keeps the lyric on as few rows as possible;
        # breaking at the first comma instead would waste most of row 1.
        self.assertEqual(
            layout.wrap_at_breaks(
                "one two, three four, five six", [20.0, 6.0], 3, "，,、"
            ),
            ["one two, three four,", "five six"],
        )


class MarqueeTests(unittest.TestCase):
    def setUp(self):
        for attribute, value in (
            ("SCROLL_LONG_LINES", True),
            ("SCROLL_DELAY_SECONDS", 0.8),
            ("SCROLL_CELLS_PER_SECOND", 5.0),
        ):
            patcher = patch.object(config, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.text = "abcdefghijklmnopqrstuvwxyz"  # 26 cells

    def test_marquee_never_scrolls_text_that_already_fits(self):
        self.assertEqual(layout.marquee("abc", 99.0, 10.0), "abc")

    def test_marquee_holds_the_head_until_the_scroll_delay(self):
        # The reader needs the start of the line still for a moment.
        self.assertEqual(layout.marquee(self.text, 0.0, 10.0), "abcdefghij")
        self.assertEqual(layout.marquee(self.text, 0.79, 10.0), "abcdefghij")
        self.assertEqual(layout.marquee(self.text, 0.8, 10.0), "abcdefghij")

    def test_marquee_scrolls_at_the_configured_rate(self):
        # One second of movement at 5 cells/s starts the crop five cells in.
        self.assertEqual(layout.marquee(self.text, 1.8, 10.0), "fghijklmno")

    def test_marquee_stops_at_the_end_of_the_line(self):
        # Past the tail it would show blank space instead of the last words.
        self.assertEqual(layout.marquee(self.text, 60.0, 10.0), "qrstuvwxyz")

    def test_marquee_truncates_with_an_ellipsis_when_scrolling_is_off(self):
        with patch.object(config, "SCROLL_LONG_LINES", False):
            self.assertEqual(layout.marquee(self.text, 60.0, 10.0), "abcdefghi…")

    def test_marquee_without_a_width_returns_the_whole_line(self):
        # A zero viewport means "unknown"; cropping to nothing would blank the
        # widget, so the line is handed back untouched.
        self.assertEqual(layout.marquee(self.text, 60.0, 0.0), self.text)


class CurrentLyricLineTests(unittest.TestCase):
    LINES = [(5.0, "first"), (10.0, "second"), (20.0, "third")]

    def test_current_lyric_line_picks_the_line_that_started_last(self):
        self.assertEqual(layout.current_lyric_line(self.LINES, 12.5), ("second", 2.5, 1))

    def test_current_lyric_line_includes_its_own_timestamp(self):
        # At exactly the timestamp the new line is already on screen, with no
        # elapsed time yet -- which is what stops the scroll from jumping.
        self.assertEqual(layout.current_lyric_line(self.LINES, 10.0), ("second", 0.0, 1))

    def test_current_lyric_line_before_the_first_timestamp_is_none(self):
        # The intro has no line yet; index -1 tells the caller so.
        self.assertEqual(layout.current_lyric_line(self.LINES, 1.0), (None, 0.0, -1))

    def test_current_lyric_line_after_the_last_timestamp_holds_it(self):
        self.assertEqual(layout.current_lyric_line(self.LINES, 100.0), ("third", 80.0, 2))

    def test_current_lyric_line_of_an_empty_lyric_is_none(self):
        self.assertEqual(layout.current_lyric_line([], 12.5), (None, 0.0, -1))


if __name__ == "__main__":
    unittest.main()
