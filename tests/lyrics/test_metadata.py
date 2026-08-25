"""Normalising track metadata and building provider search variants."""

from __future__ import annotations

import unittest

from lyrics import config
from lyrics.text.metadata import (
    aliases_equivalent,
    basic_metadata_variants,
    best_similarity,
    comparison_key,
    load_alias_groups,
    local_title_variants,
    metadata_variants,
    normalized,
    similarity,
    strict_identity_match,
    strip_version_annotations,
    track_artist_variants,
    track_cache_key,
    track_title_variants,
)

from tests.lyrics.support import track  # noqa: F401  (pins config paths first)


class StripVersionAnnotations(unittest.TestCase):
    def test_removes_a_parenthesised_version_marker(self):
        self.assertEqual(
            strip_version_annotations("Yellow (Remastered 2011)"), "Yellow"
        )

    def test_removes_a_bracketed_version_marker(self):
        self.assertEqual(strip_version_annotations("Yellow [Live]"), "Yellow")

    def test_keeps_a_parenthetical_that_is_not_a_version(self):
        # A subtitle carries meaning and is part of how catalogs name the
        # track; only edition/version noise is dropped here.
        self.assertEqual(
            strip_version_annotations("因为爱情 (电影主题曲)"), "因为爱情 (电影主题曲)"
        )

    def test_removes_a_trailing_feat_clause(self):
        self.assertEqual(strip_version_annotations("Song feat. X"), "Song")

    def test_removes_instrumental_and_performance_markers(self):
        self.assertEqual(strip_version_annotations("Yellow (Instrumental)"), "Yellow")
        self.assertEqual(strip_version_annotations("Yellow (Inst.)"), "Yellow")
        self.assertEqual(strip_version_annotations("Yellow [KTV]"), "Yellow")
        self.assertEqual(strip_version_annotations("Yellow (Cover)"), "Yellow")
        self.assertEqual(strip_version_annotations("Yellow - Instrumental"), "Yellow")
        self.assertEqual(strip_version_annotations("向阳花 (伴奏)"), "向阳花")
        self.assertEqual(strip_version_annotations("海阔天空 (纯音乐)"), "海阔天空")

    def test_word_boundary_markers_do_not_fire_inside_words(self):
        # "cover" must not match "Discover", and the boundary check keeps
        # "inst" out of "Instructor".
        self.assertEqual(
            strip_version_annotations("Song (Discover)"), "Song (Discover)"
        )
        self.assertEqual(
            strip_version_annotations("Song (Instructor)"), "Song (Instructor)"
        )

    def test_normalises_full_width_characters(self):
        # NFKC folding, so a full-width catalog title matches an ASCII one.
        self.assertEqual(strip_version_annotations("ＡＢＣ"), "ABC")

    def test_collapses_whitespace(self):
        self.assertEqual(strip_version_annotations("  a   b  "), "a b")

    def test_dash_suffixed_version_marker_is_removed(self):
        # Apple Music and LRCLIB both commonly use the dash form, so the
        # edition must not reach providers or the comparison key with its
        # marker attached.
        self.assertEqual(
            strip_version_annotations("Yellow - Remastered 2011"),
            "Yellow",
        )
        self.assertEqual(strip_version_annotations("Berlin - Live"), "Berlin")

    def test_feat_inside_parentheses_is_removed_cleanly(self):
        # The feat clause swallows its own opening bracket, so no dangling
        # "Song (" is left behind.
        self.assertEqual(strip_version_annotations("Song (feat. X)"), "Song")


class Normalized(unittest.TestCase):
    def test_casefolds_and_drops_punctuation(self):
        self.assertEqual(normalized("Yellow!"), "yellow")

    def test_keeps_cjk_characters(self):
        self.assertEqual(normalized("因为爱情"), "因为爱情")

    def test_absorbs_the_dangling_bracket_case(self):
        # Whatever strip_version_annotations leaves behind, the normalised
        # form used for comparison is clean.
        self.assertEqual(normalized("Song (feat. X)"), "song")

    def test_empty_string(self):
        self.assertEqual(normalized(""), "")


class Variants(unittest.TestCase):
    def test_basic_variants_lead_with_the_original(self):
        variants = basic_metadata_variants("Yellow (Remastered 2011)")
        self.assertEqual(variants[0], "Yellow (Remastered 2011)")
        self.assertIn("Yellow", variants)

    def test_variants_are_deduplicated(self):
        # A title with nothing to strip yields exactly one variant.
        self.assertEqual(basic_metadata_variants("Yellow"), ["Yellow"])

    def test_metadata_variants_pull_in_the_alias_group(self):
        variants = metadata_variants("張懸")
        self.assertEqual(variants[0], "張懸")
        for alias in ("张悬", "Deserts Chang", "安溥", "Anpu"):
            self.assertIn(alias, variants)

    def test_metadata_variants_of_an_unknown_name_stay_alone(self):
        self.assertEqual(metadata_variants("Coldplay"), ["Coldplay"])

    def test_title_variants_lead_with_the_subtitle_free_title(self):
        # Community catalogs usually index the plain title, so it is the
        # primary search key even though the sampled title has a subtitle.
        variants = track_title_variants(track(title="人生马拉松 (二十周年主题曲)"))
        self.assertEqual(variants[0], "人生马拉松")

    def test_title_variants_honour_the_search_title_override(self):
        # catalog.catalog_chinese_identity sets search_title when the iTunes
        # catalog knows a better name to search with.
        variants = track_title_variants(
            track(title="Unconditional", search_title="無條件")
        )
        self.assertIn("無條件", variants)
        self.assertIn("Unconditional", variants)

    def test_artist_variants_honour_the_search_artist_override(self):
        variants = track_artist_variants(
            track(artist="Eason Chan", search_artist="陳奕迅")
        )
        self.assertIn("陳奕迅", variants)
        self.assertIn("Eason Chan", variants)

    def test_artist_variants_lead_with_the_sampled_artist(self):
        self.assertEqual(track_artist_variants(track(artist="Coldplay"))[0], "Coldplay")

    def test_local_variants_also_offer_the_subtitle_free_form(self):
        variants = local_title_variants("人生马拉松 (二十周年主题曲)")
        self.assertIn("人生马拉松", variants)
        self.assertIn("人生马拉松 (二十周年主题曲)", variants)

    def test_local_title_variants_are_used_when_local_is_set(self):
        # The local provider matches file names, so it must not use the
        # catalog-oriented ordering that leads with the stripped title.
        local = track_title_variants(track(title="Yellow"), local=True)
        self.assertEqual(local, ["Yellow"])


class Aliases(unittest.TestCase):
    def test_alias_file_is_loaded(self):
        groups = load_alias_groups()
        self.assertTrue(groups, "the shipped lyrics_aliases.json should not be empty")

    def test_members_of_one_group_are_equivalent(self):
        self.assertTrue(aliases_equivalent("張懸", "Deserts Chang"))
        self.assertTrue(aliases_equivalent("安溥", "张悬"))

    def test_members_of_different_groups_are_not(self):
        self.assertFalse(aliases_equivalent("張懸", "Galaxy Express"))

    def test_unrelated_names_are_not_equivalent(self):
        self.assertFalse(aliases_equivalent("Coldplay", "Radiohead"))

    def test_the_same_name_is_equivalent_to_itself(self):
        self.assertTrue(aliases_equivalent("Coldplay", "coldplay"))


class Similarity(unittest.TestCase):
    def test_identical_strings_score_one(self):
        self.assertEqual(similarity("Yellow", "Yellow"), 1.0)

    def test_disjoint_strings_score_zero(self):
        self.assertEqual(similarity("aaa", "bbb"), 0.0)

    def test_near_miss_scores_between(self):
        score = similarity("Yellow", "Yello")
        self.assertGreater(score, 0.8)
        self.assertLess(score, 1.0)

    def test_best_similarity_treats_an_alias_as_an_exact_match(self):
        self.assertEqual(best_similarity("張懸", "Deserts Chang"), 1.0)

    def test_best_similarity_ignores_edition_noise(self):
        # The variant that had its version marker stripped is the one that
        # matches, so an edition suffix does not cost the candidate its score.
        self.assertEqual(best_similarity("Yellow (Remastered 2011)", "Yellow"), 1.0)

    def test_best_similarity_of_an_empty_side_is_zero(self):
        self.assertEqual(best_similarity("", "Yellow"), 0.0)
        self.assertEqual(best_similarity("Yellow", "   "), 0.0)


class ComparisonKey(unittest.TestCase):
    def test_trims_annotations_and_subtitles(self):
        self.assertEqual(comparison_key("Yellow (Remastered 2011)"), "yellow")
        self.assertEqual(comparison_key("人生马拉松 (二十周年主题曲)"), "人生马拉松")

    def test_trims_dash_suffixed_editions(self):
        self.assertEqual(comparison_key("Yellow - Remastered 2011"), "yellow")

    def test_trims_credit_tails_kept_by_search_variants(self):
        # "Title - Artist" is a common catalog form; the search variant keeps
        # it but the comparison key must not let it block a strict match.
        self.assertEqual(
            strip_version_annotations("Hotline Bling - Drake"),
            "Hotline Bling - Drake",
        )
        self.assertEqual(comparison_key("Hotline Bling - Drake"), "hotline bling")

    def test_keeps_dashes_without_spaces(self):
        self.assertEqual(comparison_key("L-O-V-E"), "l o v e")


class StrictIdentity(unittest.TestCase):
    def test_identical_names_match(self):
        self.assertTrue(strict_identity_match("Yellow", "Yellow"))

    def test_annotations_are_trimmed_on_both_sides(self):
        self.assertTrue(strict_identity_match("Yellow (Instrumental)", "Yellow"))
        self.assertTrue(strict_identity_match("Yellow", "Yellow (Instrumental)"))
        self.assertTrue(strict_identity_match("Yellow", "Yellow - Remastered 2011"))
        self.assertTrue(
            strict_identity_match("人生马拉松 (二十周年主题曲)", "人生马拉松")
        )
        self.assertTrue(strict_identity_match("Hotline Bling", "Hotline Bling - Drake"))

    def test_alias_groups_match(self):
        self.assertTrue(strict_identity_match("張懸", "Deserts Chang"))

    def test_an_extended_title_never_matches(self):
        # The title is exact-only at its call site (containment is never
        # enabled for titles): "Yellow Submarine" is a different song.
        self.assertFalse(strict_identity_match("Yellow", "Yellow Submarine"))

    def test_artist_containment_is_opt_in(self):
        self.assertFalse(strict_identity_match("The Beatles", "Beatles"))
        self.assertTrue(
            strict_identity_match("The Beatles", "Beatles", allow_containment=True)
        )
        self.assertTrue(strict_identity_match("xx", "The xx", allow_containment=True))

    def test_artist_containment_needs_a_whole_token(self):
        # "on" must not match inside "one direction".
        self.assertFalse(
            strict_identity_match("on", "One Direction", allow_containment=True)
        )

    def test_empty_sides_never_match(self):
        self.assertFalse(strict_identity_match("", "Yellow"))
        self.assertFalse(strict_identity_match("Yellow", "   "))


class TrackCacheKey(unittest.TestCase):
    def test_is_stable_for_the_same_track(self):
        self.assertEqual(track_cache_key(track()), track_cache_key(track()))

    def test_is_a_short_hex_digest(self):
        key = track_cache_key(track())
        self.assertEqual(len(key), 24)
        self.assertTrue(all(character in "0123456789abcdef" for character in key))

    def test_changes_with_title_artist_and_album(self):
        base = track_cache_key(track())
        self.assertNotEqual(base, track_cache_key(track(title="Clocks")))
        self.assertNotEqual(base, track_cache_key(track(artist="Radiohead")))
        self.assertNotEqual(base, track_cache_key(track(album="X&Y")))

    def test_ignores_sub_second_duration_jitter(self):
        # The sampled position drifts between ticks; a key that moved with it
        # would miss the cache entry written moments earlier.
        self.assertEqual(
            track_cache_key(track(duration=269.0)),
            track_cache_key(track(duration=269.4)),
        )

    def test_changes_with_the_cache_version(self):
        # Bumping CACHE_KEY_VERSION is how a provider change invalidates every
        # stored record, so it must reach the key.
        base = track_cache_key(track())
        original = config.CACHE_KEY_VERSION
        try:
            config.CACHE_KEY_VERSION = original + 1
            self.assertNotEqual(base, track_cache_key(track()))
        finally:
            config.CACHE_KEY_VERSION = original

    def test_edition_noise_does_not_change_the_key(self):
        self.assertEqual(
            track_cache_key(track(title="Yellow")),
            track_cache_key(track(title="Yellow (Remastered 2011)")),
        )


if __name__ == "__main__":
    unittest.main()
