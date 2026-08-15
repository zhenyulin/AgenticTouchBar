"""What the sampler does at a transition: which sample it keeps, and the
order the two widgets are updated in.

The pair is a Lyrics widget beside a Now Playing row, and the lyric must
never outlive the row it annotates: closing a player used to leave it rolling
for STATE_MAX_AGE_SECONDS after the row had gone. These tests pin the two
rules that prevent it -- what may be held while nothing reports a track, and
that the lyric is always cleared before the row is settled.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from lyrics import cli, config

from tests.lyrics.support import track  # noqa: F401  (pins config paths first)


def sampled(**overrides):
    """A MediaRemote-sourced sample, as sources/media_remote.py writes one."""
    fields = {"source": "media_remote", "bundle_id": "com.tencent.QQMusicMac"}
    fields.update(overrides)
    return track(**fields)


class HoldPreviousSample(unittest.TestCase):
    """Which previous sample survives a moment when nothing reports a track."""

    def test_a_media_remote_sample_is_held_while_its_player_runs(self):
        with patch("lyrics.sources.media_remote.player_is_running", return_value=True):
            self.assertTrue(cli._hold_previous_sample(sampled(), 1.0))

    def test_a_media_remote_sample_is_dropped_once_its_player_is_gone(self):
        with patch("lyrics.sources.media_remote.player_is_running", return_value=False):
            self.assertFalse(cli._hold_previous_sample(sampled(), 1.0))

    def test_an_unnameable_player_is_held(self):
        # No bundle id means no answer, and an unknown state must not tear
        # the pair down mid-song. Deliberately not mocked: the fallback is
        # player_is_running's own, and it is the one that has to be safe.
        self.assertTrue(cli._hold_previous_sample(sampled(bundle_id=""), 1.0))

    def test_an_apple_music_sample_is_never_held(self):
        # Music is read directly -- pgrep, then AppleScript -- so not_running
        # is an answer, not silence.
        self.assertFalse(cli._hold_previous_sample(track(), 1.0))

    def test_a_sample_past_its_usable_age_is_not_held(self):
        with patch("lyrics.sources.media_remote.player_is_running", return_value=True):
            self.assertFalse(
                cli._hold_previous_sample(
                    sampled(), config.STATE_MAX_AGE_SECONDS + 0.1
                )
            )

    def test_nothing_is_held_when_the_previous_sample_showed_no_track(self):
        self.assertFalse(cli._hold_previous_sample(None, 0.0))
        self.assertFalse(cli._hold_previous_sample(sampled(state="stopped"), 0.0))


class AbandonedSession(unittest.TestCase):
    """Telling a MediaRemote pause from a player that has quit.

    MediaRemote keeps publishing the last track with isPlaying false for a
    second or two after its app goes, which reads as a pause -- the one state
    that makes the pair come apart, because a paused lyric goes and a paused
    row stays.
    """

    def test_a_paused_session_whose_player_is_gone_is_a_quit(self):
        with patch("lyrics.sources.media_remote.player_is_running", return_value=False):
            self.assertTrue(
                cli._abandoned_session(sampled(state="paused"), sampled())
            )

    def test_a_real_pause_keeps_its_session(self):
        with patch("lyrics.sources.media_remote.player_is_running", return_value=True):
            self.assertFalse(
                cli._abandoned_session(sampled(state="paused"), sampled())
            )

    def test_only_the_moment_playback_turns_paused_is_questioned(self):
        # Already paused, or still playing: no transition to hang the
        # question on, and no subprocess spent asking it.
        with patch("lyrics.sources.media_remote.player_is_running") as running:
            self.assertFalse(
                cli._abandoned_session(sampled(state="paused"), sampled(state="paused"))
            )
            self.assertFalse(cli._abandoned_session(sampled(), sampled()))
            self.assertFalse(cli._abandoned_session(None, sampled()))
        running.assert_not_called()


class TransitionSequence(unittest.TestCase):
    """The BetterTouchTool calls a transition makes, and their order."""

    def setUp(self):
        self.calls = []
        patcher = patch.object(cli, "_btt_call", lambda *statements: self.calls.append(statements))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cli.clear_marker)
        cli.clear_marker()

    def statements(self):
        self.assertEqual(len(self.calls), 1, f"expected one call, got {self.calls}")
        return list(self.calls[0])

    def assert_lyric_cleared_first(self, statements):
        """The lyric leaves the screen before the row is touched, in one call."""
        self.assertEqual(len(statements), 2)
        self.assertIn(config.LYRICS_WIDGET_UUID, statements[0])
        self.assertIn('text ""', statements[0])
        self.assertIn(config.NOW_PLAYING_WIDGET_UUID, statements[1])

    def marker(self):
        try:
            return json.loads(config.CLEARED_MARKER_PATH.read_text(encoding="utf-8"))
        except OSError:
            return None

    def test_a_quit_player_clears_the_lyric_then_hides_the_row(self):
        cli.maybe_transition_sequence(track(), {"state": "not_running"})

        statements = self.statements()
        self.assert_lyric_cleared_first(statements)
        self.assertIn('text ""', statements[1])
        self.assertEqual(self.marker()["title"], "Yellow")

    def test_a_stopped_player_clears_the_lyric_and_keeps_the_row(self):
        # A plain stop keeps the row showing its track (HideWhenPaused: 0).
        cli.maybe_transition_sequence(track(), {"state": "stopped"})

        statements = self.statements()
        self.assert_lyric_cleared_first(statements)
        self.assertIn("refresh_widget", statements[1])

    def test_a_track_change_clears_the_lyric_then_repaints_the_row(self):
        cli.maybe_transition_sequence(track(), track(title="Trouble"))

        statements = self.statements()
        self.assert_lyric_cleared_first(statements)
        self.assertIn("refresh_widget", statements[1])
        self.assertEqual(self.marker()["title"], "Yellow")

    def test_a_pause_clears_the_lyric_then_repaints_the_row(self):
        cli.maybe_transition_sequence(track(), track(state="paused"))

        statements = self.statements()
        self.assert_lyric_cleared_first(statements)
        self.assertIn("refresh_widget", statements[1])

    def test_a_pause_leaves_no_marker_behind(self):
        # A paused track keeps its identity, so a marker naming it would
        # still be holding the widget empty when playback resumes.
        cli.maybe_transition_sequence(track(), track(state="paused"))

        self.assertIsNone(self.marker())

    def test_continuing_playback_touches_neither_widget(self):
        cli.maybe_transition_sequence(track(), track(position=45.0))

        self.assertEqual(self.calls, [])

    def test_a_sample_past_the_cleared_track_drops_the_marker(self):
        cli.closing_sequence(track(), hide_row=False)
        self.calls.clear()

        cli.maybe_transition_sequence(track(title="Trouble"), track(title="Trouble"))

        self.assertIsNone(self.marker())


if __name__ == "__main__":
    unittest.main()
