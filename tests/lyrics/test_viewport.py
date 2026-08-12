"""Dividing the Touch Bar row between the Now Playing and Lyrics widgets.

Only the pure half is exercised here. `measure_px`, `measure_track`,
`ensure_viewport` and `store_measured_viewport` shell out to osascript for
Cocoa text metrics, which would make these tests slow and font-dependent, so
anything below them takes its widths as arguments instead.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.lyrics.support import track  # noqa: F401  (pins config paths first)

from lyrics import config
from lyrics.display import viewport

STOCK_FORMATS = ("{album} ▸ {title}", "{artist}")


class StripParens(unittest.TestCase):
    def test_removes_a_parenthetical(self):
        self.assertEqual(viewport.strip_parens("Song (feat. X)"), "Song")

    def test_collapses_the_gap_it_leaves(self):
        self.assertEqual(viewport.strip_parens("A (x) B"), "A B")

    def test_leaves_plain_text_alone(self):
        self.assertEqual(viewport.strip_parens("Yellow"), "Yellow")

    def test_matches_what_the_now_playing_widget_draws(self):
        # widgets/now-playing.sh strips identically; if the two drift the
        # measured width stops being the drawn width and the row overflows.
        self.assertEqual(viewport.strip_parens("  a   (b)   c  "), "a c")


class DisplayAlbum(unittest.TestCase):
    def test_keeps_only_the_first_work_of_a_multi_work_album(self):
        # The Touch Bar is too short for both halves of a classical album.
        self.assertEqual(
            viewport.display_album("Mozart: Concerto K. 488; Sonata K. 333"),
            "Mozart: Concerto K. 488",
        )

    def test_also_strips_parentheticals(self):
        self.assertEqual(viewport.display_album("Parachutes (Deluxe)"), "Parachutes")

    def test_leaves_a_single_work_alone(self):
        self.assertEqual(viewport.display_album("Parachutes"), "Parachutes")


class NowPlayingFormats(unittest.TestCase):
    """The row-choice rule, mirrored from widgets/now-playing.sh.

    Of the two layouts the widget draws, it keeps whichever has the narrower
    widest row -- that row is what the widget's width comes to, and what is
    left of the pair's budget goes to the lyric.
    """

    def setUp(self):
        patcher = patch.object(config, "NOW_PLAYING_LINE_FORMATS", STOCK_FORMATS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_long_title_and_short_album_swap_to_the_balanced_pair(self):
        # Stock would give "X ▸ <long title>" over a two-character artist,
        # a wide first row; the title alone is narrower than that.
        rows = viewport.now_playing_lines(
            track(title="A Very Long Song Title Here", artist="Ye", album="X")
        )
        self.assertEqual(rows, ["A Very Long Song Title Here", "Ye ▸ X"])

    def test_a_dominant_album_stays_on_the_stock_layout(self):
        # Swapping cannot help when the album is the long part: it lands on
        # whichever row it is joined to and sets the width there, and beside
        # the artist it is a character longer still.
        rows = viewport.now_playing_lines(
            track(
                title="I",
                artist="Ye",
                album="A Very Long Album Name Indeed That Runs On",
            )
        )
        self.assertEqual(rows[0], "A Very Long Album Name Indeed That Runs On ▸ I")

    def test_a_dominant_artist_stays_on_the_stock_layout(self):
        # The artist is on row 2 either way, so joining the album to it only
        # lengthens the row that already sets the width. Stock keeps it alone.
        rows = viewport.now_playing_lines(
            track(title="I", artist="A Very Long Artist Name Collective", album="X")
        )
        self.assertEqual(rows, ["X ▸ I", "A Very Long Artist Name Collective"])

    def test_a_stock_pair_with_no_longer_row_to_gain_is_left_alone(self):
        # "Parachutes ▸ Yellow" is 19 characters against the balanced
        # layout's 21 ("Coldplay ▸ Parachutes"), so the stock order stands.
        rows = viewport.now_playing_lines(
            track(title="Yellow", artist="Coldplay", album="Parachutes")
        )
        self.assertEqual(rows, ["Parachutes ▸ Yellow", "Coldplay"])

    def test_a_track_without_an_artist_never_swaps(self):
        # The balanced layout puts the artist on row 2; with no artist there
        # is nothing to balance against.
        rows = viewport.now_playing_lines(track(artist="", album="X", title="Y"))
        self.assertEqual(rows[0], "X ▸ Y")

    def test_a_custom_format_is_used_verbatim(self):
        # The user's own layout is never second-guessed.
        with patch.object(config, "NOW_PLAYING_LINE_FORMATS", ("{title}", "{album}")):
            rows = viewport.now_playing_lines(track())
            self.assertEqual(rows, ["Yellow", "Parachutes"])

    def test_an_unknown_placeholder_costs_nothing_instead_of_raising(self):
        # BTT accepts more placeholders than the sampler fills in; one of them
        # appearing in a mirrored format must not blow up the measurement.
        with patch.object(
            config, "NOW_PLAYING_LINE_FORMATS", ("{composer}", "{artist}")
        ):
            self.assertEqual(viewport.now_playing_lines(track())[0], "")

    def test_the_rows_are_stripped_and_album_shortened(self):
        rows = viewport.now_playing_lines(
            track(title="Yellow (Live)", album="Parachutes; Extras", artist="Coldplay")
        )
        self.assertEqual(rows, ["Parachutes ▸ Yellow", "Coldplay"])


class LyricBudget(unittest.TestCase):
    def test_apple_music_gets_the_plain_budget(self):
        self.assertEqual(
            viewport.lyric_budget_px(track(source="apple_music")),
            config.LYRIC_WIDTH_BUDGET_PX,
        )

    def test_another_player_gains_the_hidden_star_widget_s_slot(self):
        # The Star widget only draws for Apple Music; with QQ Music it renders
        # empty, BTT hides it, and the pair may use the slot it frees.
        self.assertEqual(
            viewport.lyric_budget_px(track(source="media_remote")),
            config.LYRIC_WIDTH_BUDGET_PX + config.LYRIC_EXTRA_WORD_PX,
        )

    def test_an_unknown_source_is_treated_as_apple_music(self):
        # Claiming the Star widget's slot when it is in fact drawn would push
        # the lyric off the end of the row.
        sample = track()
        del sample["source"]
        self.assertEqual(
            viewport.lyric_budget_px(sample), config.LYRIC_WIDTH_BUDGET_PX
        )


class LyricWidth(unittest.TestCase):
    def test_the_lyric_takes_whatever_now_playing_leaves(self):
        self.assertEqual(viewport.lyric_width_px(200.0, 570.0), 370.0)

    def test_a_very_long_title_cannot_squeeze_the_lyric_below_the_floor(self):
        # Past the floor the row overflows instead, which is at least readable.
        self.assertEqual(
            viewport.lyric_width_px(560.0, 570.0), config.MIN_LYRIC_WIDTH_PX
        )

    def test_the_share_is_capped_at_the_widget_s_own_width(self):
        self.assertEqual(viewport.lyric_width_px(0.0, 9999.0), config.MAX_LYRIC_WIDTH_PX)


class EffectiveBudget(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.hide_flag = self.directory / "opencode-hide"
        patcher = patch.object(config, "OPENCODE_HIDE_PATH", self.hide_flag)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_without_the_hide_flag_the_budget_is_just_the_pair_s(self):
        self.assertEqual(
            viewport.effective_budget_px(track()), viewport.lyric_budget_px(track())
        )

    def test_the_hidden_opencode_slot_is_added_back_when_enabled(self):
        # While the pair is cramped the lyrics widget hides OpenCode, and the
        # slot it frees becomes budget the pair may spend.
        self.hide_flag.touch()
        with patch.object(config, "OPENCODE_HIDE_ENABLED", True):
            self.assertGreater(
                viewport.effective_budget_px(track()),
                viewport.lyric_budget_px(track()),
            )

    def test_the_flag_is_ignored_while_the_hide_is_disabled(self):
        self.hide_flag.touch()
        with patch.object(config, "OPENCODE_HIDE_ENABLED", False):
            self.assertEqual(
                viewport.effective_budget_px(track()),
                viewport.lyric_budget_px(track()),
            )


class StoredMeasurements(unittest.TestCase):
    def test_a_stored_width_is_read_back(self):
        self.assertEqual(viewport.stored_lyric_px({"lyric_px": 240.0}), 240.0)

    def test_a_missing_or_unusable_width_reads_as_none(self):
        # The caller falls back to the conservative default rather than
        # trusting a half-written measurement.
        self.assertIsNone(viewport.stored_lyric_px({}))
        self.assertIsNone(viewport.stored_lyric_px({"lyric_px": "wide"}))

    def test_encode_track_is_stable_and_distinguishes_tracks(self):
        self.assertEqual(viewport.encode_track(track()), viewport.encode_track(track()))
        self.assertNotEqual(
            viewport.encode_track(track()), viewport.encode_track(track(title="Clocks"))
        )


if __name__ == "__main__":
    unittest.main()
