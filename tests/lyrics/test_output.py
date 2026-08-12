"""Printing a widget frame, remembering it, and tracing the run."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.lyrics.support import SANDBOX  # noqa: F401  (pins config paths first)

from lyrics import config
from lyrics.runtime.output import emit, emit_last_output, log_error, remember_output, trace


def printed(function, *args, **kwargs) -> str:
    """Whatever the call wrote to stdout, without the trailing newline."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        function(*args, **kwargs)
    return buffer.getvalue().rstrip("\n")


class OutputTestCase(unittest.TestCase):
    """Redirect every file the output module touches into a fresh directory."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        for attribute, value in (
            ("CACHE_DIR", self.directory),
            ("LAST_TEXT_PATH", self.directory / "last.txt"),
            ("VALUE_PATH", self.directory / "lyrics.value"),
            ("TRACE_PATH", self.directory / "trace.tsv"),
        ):
            patcher = patch.object(config, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class Emit(OutputTestCase):
    def test_prints_a_single_row(self):
        self.assertEqual(printed(emit, "hello"), "hello")

    def test_collapses_interior_whitespace(self):
        self.assertEqual(printed(emit, "a    b"), "a b")

    def test_keeps_a_row_s_leading_indent(self):
        # The wrapped second row of a lyric is indented on purpose
        # (CONTINUATION_INDENT); collapsing that would lose the alignment.
        self.assertEqual(printed(emit, "first\n   second"), "first\n   second")

    def test_drops_rows_that_are_only_whitespace(self):
        self.assertEqual(printed(emit, "a\n   \nb"), "a\nb")

    def test_normalises_carriage_returns(self):
        self.assertEqual(printed(emit, "a\r\nb\rc"), "a\nb\nc")

    def test_empty_text_stays_empty(self):
        # BetterTouchTool removes a script widget whose text is empty, which is
        # how Lyrics hides itself. An empty frame must not become a placeholder.
        self.assertEqual(printed(emit, ""), "")

    def test_a_font_colour_switches_to_the_widget_json(self):
        payload = json.loads(printed(emit, "hello", "255,255,255,255"))
        self.assertEqual(payload, {"text": "hello", "font_color": "255,255,255,255"})

    def test_the_json_frame_keeps_non_ascii_readable(self):
        payload = json.loads(printed(emit, "寶貝", "1,2,3,255"))
        self.assertEqual(payload["text"], "寶貝")

    def test_the_json_frame_is_not_whitespace_collapsed(self):
        # The coloured path is used for markers and pre-built rows, which are
        # already laid out; re-flowing them would move the indent.
        payload = json.loads(printed(emit, "a    b", "1,2,3,255"))
        self.assertEqual(payload["text"], "a    b")


class RememberOutput(OutputTestCase):
    def test_emit_records_what_it_printed(self):
        printed(emit, "hello")
        self.assertEqual(config.LAST_TEXT_PATH.read_text(encoding="utf-8"), "hello")
        self.assertEqual(config.VALUE_PATH.read_text(encoding="utf-8"), "hello")

    def test_reprints_the_remembered_value(self):
        # A tick that could not take the widget lock has nothing of its own to
        # show; reprinting beats blanking the widget for a frame.
        remember_output("previous")
        self.assertEqual(printed(emit_last_output), "previous")

    def test_falls_back_to_a_note_when_nothing_is_remembered(self):
        self.assertEqual(printed(emit_last_output), "♪")

    def test_reprinting_refreshes_the_file_mtime(self):
        # The file's age is what tells "BTT stopped running the widget" from
        # "the widget keeps drawing the same thing".
        remember_output("previous")
        old = 1_000_000.0
        import os

        os.utime(config.LAST_TEXT_PATH, (old, old))
        printed(emit_last_output)
        self.assertGreater(config.LAST_TEXT_PATH.stat().st_mtime, old)


class Trace(OutputTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(config, "TRACE_ENABLED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_writes_one_tab_separated_row_per_call(self):
        trace("widget", 0.0, "ok")
        trace("widget", 0.0, "stale")
        lines = config.TRACE_PATH.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        # Same six columns as lib/btt-widget.sh writes, so one --report covers
        # the shell widgets and this one together.
        self.assertEqual(len(lines[0].split("\t")), 6)

    def test_records_the_widget_name_mode_and_outcome(self):
        trace("widget", 0.0, "ok")
        columns = config.TRACE_PATH.read_text(encoding="utf-8").split("\t")
        self.assertEqual(columns[1], "lyrics")
        self.assertEqual(columns[2], "widget")
        self.assertEqual(columns[4], "ok")

    def test_extra_fields_are_appended_as_key_value_pairs(self):
        trace("fetch", 0.0, "ok", source="lrclib")
        self.assertIn("source=lrclib", config.TRACE_PATH.read_text(encoding="utf-8"))

    def test_disabling_the_trace_writes_nothing(self):
        with patch.object(config, "TRACE_ENABLED", False):
            trace("widget", 0.0, "ok")
        self.assertFalse(config.TRACE_PATH.exists())

    def test_an_oversized_trace_is_rotated_to_one_generation(self):
        with patch.object(config, "TRACE_MAX_BYTES", 1):
            trace("widget", 0.0, "ok")
        rotated = config.TRACE_PATH.with_name(config.TRACE_PATH.name + ".1")
        self.assertTrue(rotated.exists())
        self.assertFalse(config.TRACE_PATH.exists())


class LogError(OutputTestCase):
    def test_appends_a_timestamped_line(self):
        log_error("something broke")
        contents = (self.directory / "error.log").read_text(encoding="utf-8")
        self.assertIn("something broke", contents)

    def test_appends_rather_than_replaces(self):
        log_error("first")
        log_error("second")
        contents = (self.directory / "error.log").read_text(encoding="utf-8")
        self.assertIn("first", contents)
        self.assertIn("second", contents)


if __name__ == "__main__":
    unittest.main()
