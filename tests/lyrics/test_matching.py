"""Scoring provider candidates and picking the one to cache."""

from __future__ import annotations

import unittest

from lyrics.text.matching import candidate_score, choose_candidate

from tests.lyrics.support import candidate, track  # noqa: F401  (pins config paths)

SYNCED = "[00:01.00]line"

# candidate_score returns this for anything it refuses outright, so the
# thresholds below read as "rejected" rather than "scored badly".
REJECTED = -1.0


class CandidateScore(unittest.TestCase):
    def test_an_exact_match_scores_one(self):
        # Every weighted component is 1.0, and the weights sum to 1.
        self.assertAlmostEqual(candidate_score(track(), candidate()), 1.0)

    def test_a_different_title_is_rejected(self):
        self.assertEqual(
            candidate_score(track(), candidate(trackName="Something Else Entirely")),
            REJECTED,
        )

    def test_an_unrelated_artist_is_rejected(self):
        self.assertEqual(
            candidate_score(
                track(duration=269.0),
                candidate(artistName="王菲", duration=180.0),
            ),
            REJECTED,
        )

    def test_a_different_latin_artist_is_rejected(self):
        # Strict artist match: a same-titled song by another band is another
        # song's lyrics, no matter how strong the title looks.
        self.assertEqual(
            candidate_score(
                track(duration=269.0),
                candidate(artistName="Radiohead", duration=180.0),
            ),
            REJECTED,
        )

    def test_an_unrecognised_artist_is_rejected_despite_title_and_duration(self):
        # Romanisations and credited-as names the alias file has never heard
        # of no longer get a free pass from the title and length agreeing.
        self.assertEqual(
            candidate_score(
                track(duration=269.0),
                candidate(artistName="Nobody At All", duration=269.0),
            ),
            REJECTED,
        )

    def test_an_instrumental_annotation_on_either_side_does_not_block_a_match(self):
        # The reported case: a pure-instrumental edition of a song must still
        # match the song's own lyrics, with the annotation trimmed on either
        # side of the comparison.
        self.assertAlmostEqual(
            candidate_score(track(title="Yellow (Instrumental)"), candidate()),
            1.0,
        )
        self.assertAlmostEqual(
            candidate_score(track(), candidate(trackName="Yellow (Instrumental)")),
            1.0,
        )

    def test_a_candidate_subtitle_is_trimmed_for_matching(self):
        # Community catalogs annotate subtitles ("Yellow (电影主题曲)"); the
        # trimmed candidate must still match the plain sampled title.
        scored = candidate_score(track(), candidate(trackName="Yellow (电影主题曲)"))
        self.assertGreater(scored, 0.6)

    def test_an_extended_title_is_rejected(self):
        # "Yellow Submarine" is not "Yellow", even by the same artist.
        self.assertEqual(
            candidate_score(track(), candidate(trackName="Yellow Submarine")),
            REJECTED,
        )

    def test_a_leading_article_on_the_artist_is_forgiven(self):
        # The artist keeps containment: "The Beatles" is the same act as
        # "Beatles", and the title matches exactly.
        scored = candidate_score(
            track(artist="The Beatles"), candidate(artistName="Beatles")
        )
        self.assertGreater(scored, 0.6)

    def test_an_alias_artist_still_matches(self):
        # The alias file makes these the same act, so the candidate must not
        # be thrown out for using the other spelling.
        scored = candidate_score(
            track(title="寶貝", artist="張懸", album="My Life Will…", duration=200.0),
            candidate(
                trackName="寶貝",
                artistName="Deserts Chang",
                albumName="My Life Will…",
                duration=200.0,
            ),
        )
        self.assertAlmostEqual(scored, 1.0)

    def test_a_close_duration_scores_above_a_distant_one(self):
        near = candidate_score(track(duration=269.0), candidate(duration=272.0))
        far = candidate_score(track(duration=269.0), candidate(duration=299.0))
        self.assertGreater(near, far)

    def test_a_duration_gap_beyond_thirty_seconds_stops_costing_more(self):
        # The duration term floors at zero, so a wildly wrong length is not
        # allowed to drag the total negative and mimic a rejection.
        far = candidate_score(track(duration=269.0), candidate(duration=400.0))
        further = candidate_score(track(duration=269.0), candidate(duration=900.0))
        self.assertEqual(far, further)
        self.assertGreater(far, 0.0)

    def test_a_missing_duration_neither_helps_nor_rejects(self):
        # Several providers omit the length; the candidate is still usable.
        scored = candidate_score(track(duration=0), candidate(duration=0))
        self.assertGreater(scored, 0.6)
        self.assertLess(scored, 1.0)

    def test_edition_noise_in_the_candidate_title_is_forgiven(self):
        self.assertAlmostEqual(
            candidate_score(track(), candidate(trackName="Yellow (Remastered 2011)")),
            1.0,
        )

    def test_a_wrong_album_costs_only_a_little(self):
        # The album is the weakest signal: compilations and reissues rename it
        # constantly while the recording is the same.
        scored = candidate_score(track(), candidate(albumName="Greatest Hits"))
        self.assertGreater(scored, 0.9)
        self.assertLess(scored, 1.0)


class ChooseCandidate(unittest.TestCase):
    def test_returns_none_for_an_empty_list(self):
        self.assertIsNone(choose_candidate(track(), []))

    def test_ignores_candidates_with_no_lyrics_at_all(self):
        # A search hit carrying neither timed nor plain text is not an answer.
        self.assertIsNone(choose_candidate(track(), [candidate()]))

    def test_ignores_non_dict_entries(self):
        self.assertIsNone(choose_candidate(track(), ["nonsense", None, 7]))

    def test_picks_the_only_usable_candidate(self):
        wanted = candidate(syncedLyrics=SYNCED)
        self.assertIs(choose_candidate(track(), [wanted]), wanted)

    def test_timed_lyrics_beat_plain_text(self):
        # Plain text has to be spread across the duration by guesswork, so a
        # genuinely timed record wins even from a slightly worse match.
        plain = candidate(id="plain", plainLyrics="words")
        synced = candidate(id="synced", trackName="Yellow", syncedLyrics=SYNCED)
        self.assertEqual(choose_candidate(track(), [plain, synced])["id"], "synced")

    def test_plain_text_beats_an_instrumental_marker(self):
        instrumental = candidate(id="instrumental", instrumental=True)
        plain = candidate(id="plain", plainLyrics="words")
        chosen = choose_candidate(track(), [instrumental, plain])
        self.assertEqual(chosen["id"], "plain")

    def test_an_instrumental_marker_wins_when_nothing_else_exists(self):
        instrumental = candidate(id="instrumental", instrumental=True)
        self.assertIs(choose_candidate(track(), [instrumental]), instrumental)

    def test_a_same_title_by_another_band_is_rejected(self):
        # This is the wrong song's lyrics; the exact title no longer carries
        # it past the strict artist gate.
        wrong_band = candidate(
            artistName="Radiohead", duration=180.0, syncedLyrics=SYNCED
        )
        self.assertIsNone(choose_candidate(track(duration=269.0), [wrong_band]))

    def test_a_poor_match_is_refused_even_with_timed_lyrics(self):
        # Showing the wrong song's lyrics in time is worse than showing none.
        wrong = candidate(
            trackName="Entirely Different Song",
            artistName="Another Band",
            syncedLyrics=SYNCED,
        )
        self.assertIsNone(choose_candidate(track(), [wrong]))

    def test_the_better_of_two_timed_candidates_wins(self):
        exact = candidate(id="exact", syncedLyrics=SYNCED)
        loose = candidate(
            id="loose", albumName="Greatest Hits", duration=299.0, syncedLyrics=SYNCED
        )
        self.assertEqual(choose_candidate(track(), [exact, loose])["id"], "exact")


if __name__ == "__main__":
    unittest.main()
