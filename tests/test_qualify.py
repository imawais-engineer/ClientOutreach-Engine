import unittest
from datetime import datetime, timezone

from pipeline import spec
from pipeline.qualify import (
    admit,
    build_record,
    business_key,
    dedupe,
    normalize_level,
    qualification_tier,
)
from pipeline.score import classify, heuristic_scores, weighted_total


def candidate(**overrides):
    base = {
        "business": "Links Digital",
        "need": "Needs CRM lead routing and a WhatsApp assistant",
        "description": "We run lead generation campaigns and use HubSpot CRM. Manual follow-up is repetitive.",
        "url": "https://example.com/job/1",
        "website": "https://linksdigital.example",
        "city": "Lahore",
        "country": "PK",
        "platform": "Job Boards",
        "evidence_tier": "B",
    }
    base.update(overrides)
    return base


class BusinessKeyTests(unittest.TestCase):
    def test_normalizes_legal_suffixes_and_punctuation(self):
        self.assertEqual(business_key("Links Digital (Pvt) Ltd."), "links-digital")
        self.assertEqual(business_key("ACME & Co"), "acme")

    def test_branch_and_legal_variants_match(self):
        self.assertEqual(business_key("Links Digital Pvt Ltd"), business_key("links digital"))


class LevelTests(unittest.TestCase):
    def test_tiers_map_to_the_shared_scale(self):
        self.assertEqual(normalize_level("A"), "direct")
        self.assertEqual(normalize_level("1"), "direct")
        self.assertEqual(normalize_level("B"), "plausible")
        self.assertEqual(normalize_level("3"), "weak")
        self.assertIsNone(normalize_level("D"))


class ScoringTests(unittest.TestCase):
    def test_weights_sum_to_one_for_every_source(self):
        for source_id in spec.source_ids():
            dims = spec.dimensions(source_id)
            self.assertAlmostEqual(sum(d["weight"] for d in dims.values()), 1.0, places=6, msg=source_id)

    def test_weighted_total_uses_the_source_weights(self):
        dims = spec.dimensions("google_maps")
        perfect = {name: {"score": 5} for name in dims}
        self.assertEqual(weighted_total(perfect, dims), 100)
        empty = {name: {"score": 0} for name in dims}
        self.assertEqual(weighted_total(empty, dims), 0)

    def test_classification_bands(self):
        # v2 bands: priority >= 60, potential >= 40. Buying intent earns nothing
        # without evidence, so a strong no-intent prospect lands near 60-70, not 90.
        self.assertEqual(classify(100), "priority")
        self.assertEqual(classify(60), "priority")
        self.assertEqual(classify(59), "potential")
        self.assertEqual(classify(40), "potential")
        self.assertEqual(classify(39), "deprioritize")
        self.assertEqual(classify(0), "deprioritize")

    def test_band_edges_match_the_configured_bands(self):
        bands = spec.classification_bands()
        self.assertEqual(bands["priority_prospect"]["min"], 60)
        self.assertEqual(bands["potential_prospect"]["min"], 40)

    def test_google_maps_uses_the_six_v2_dimensions(self):
        dims = spec.dimensions("google_maps")
        self.assertEqual(
            set(dims),
            {
                "service_fit",
                "need_evidence",
                "buying_intent",
                "verification",
                "contactability",
                "geographic_relevance",
            },
        )
        self.assertEqual(dims["buying_intent"]["weight"], 0.2)
        self.assertEqual(dims["need_evidence"]["weight"], 0.25)

    def test_each_v2_dimension_is_classified_correctly(self):
        from pipeline.score import _dimension_kind

        self.assertEqual(_dimension_kind("service_fit"), "fit")
        self.assertEqual(_dimension_kind("need_evidence"), "evidence")
        self.assertEqual(_dimension_kind("buying_intent"), "intent")
        self.assertEqual(_dimension_kind("verification"), "verify")
        self.assertEqual(_dimension_kind("contactability"), "contact")
        self.assertEqual(_dimension_kind("geographic_relevance"), "geo")

    def test_buying_intent_scores_zero_without_evidence(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        record = build_record(candidate(), "google_maps", now)
        scores = heuristic_scores(record, "google_maps")
        self.assertEqual(scores["buying_intent"]["score"], 0)

    def test_buying_intent_scores_when_requirement_is_confirmed(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        record = build_record(candidate(), "google_maps", now)
        record["details"]["confirmed_requirement"] = "yes"
        scores = heuristic_scores(record, "google_maps")
        self.assertEqual(scores["buying_intent"]["score"], 5)
        self.assertEqual(scores["need_evidence"]["score"], 5)

    def test_geographic_relevance_rewards_only_active_markets(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        # PK is not one of the active pilot markets, so it earns nothing.
        local = build_record(candidate(), "google_maps", now)
        self.assertEqual(heuristic_scores(local, "google_maps")["geographic_relevance"]["score"], 0)

        in_market = build_record(
            candidate(city="Austin", country="US"), "google_maps", now
        )
        self.assertGreaterEqual(
            heuristic_scores(in_market, "google_maps")["geographic_relevance"]["score"], 3
        )

    def test_missing_location_never_raises_the_score(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        record = build_record(candidate(city=None, country=None), "google_maps", now)
        self.assertEqual(heuristic_scores(record, "google_maps")["geographic_relevance"]["score"], 0)

    def test_heuristic_scores_cover_every_dimension(self):
        record = build_record(candidate(), "job_boards", datetime(2026, 10, 25, tzinfo=timezone.utc))
        scores = heuristic_scores(record, "job_boards")
        self.assertEqual(set(scores), set(spec.dimensions("job_boards")))
        for entry in scores.values():
            self.assertGreaterEqual(entry["score"], 0)
            self.assertLessEqual(entry["score"], 5)


class AdmissionTests(unittest.TestCase):
    def test_evidence_backed_candidate_is_admitted(self):
        record = build_record(candidate(), "job_boards", datetime(2026, 10, 25, tzinfo=timezone.utc))
        ok, reason = admit(record, "job_boards")
        self.assertTrue(ok, reason)

    def test_no_service_match_is_rejected(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        record = build_record(candidate(), "google_maps", now)
        record["details"]["scores"] = {
            name: {"score": 1, "weight": dim["weight"]}
            for name, dim in spec.dimensions("google_maps").items()
        }
        record["details"]["evidence_classification"] = "observed_opportunity_signal"
        ok, reason = admit(record, "google_maps")
        self.assertFalse(ok)
        self.assertEqual(reason, "no_service_match")

    def test_no_relevant_opportunity_is_rejected(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        record = build_record(candidate(), "google_maps", now)
        record["details"]["evidence_classification"] = "no_relevant_opportunity"
        ok, reason = admit(record, "google_maps")
        self.assertFalse(ok)
        self.assertEqual(reason, "no_opportunity")

    def test_do_not_contact_is_never_admitted(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        record = build_record(candidate(), "job_boards", now)
        record["opt_out"] = True
        ok, reason = admit(record, "job_boards")
        self.assertFalse(ok)
        self.assertEqual(reason, "do_not_contact")


class QualificationTierTests(unittest.TestCase):
    def test_confirmed_requirement_is_qualified(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        record = build_record(candidate(), "google_maps", now)
        record["details"]["evidence_classification"] = "confirmed_requirement"
        self.assertEqual(qualification_tier(record), "qualified")

    def test_observed_opportunity_signal_is_qualified(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        record = build_record(candidate(), "google_maps", now)
        record["details"]["evidence_classification"] = "observed_opportunity_signal"
        self.assertEqual(qualification_tier(record), "qualified")

    def test_hypothesis_only_is_research_only(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        record = build_record(candidate(), "google_maps", now)
        record["details"]["evidence_classification"] = "hypothesis_only"
        self.assertEqual(qualification_tier(record), "research_only")

    def test_a_high_score_cannot_override_hypothesis_only(self):
        """No blended total may promote a hypothesis to a confirmed need."""
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        record = build_record(candidate(), "google_maps", now)
        record["details"]["evidence_classification"] = "hypothesis_only"
        record["weighted_score"] = 99
        record["classification"] = "priority"
        self.assertEqual(qualification_tier(record), "research_only")

    def test_unclassified_record_below_the_need_gate_is_research_only(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        record = build_record(candidate(), "google_maps", now)
        record["details"]["scores"] = {
            "service_fit": {"score": 5, "weight": 0.25},
            "need_evidence": {"score": 1, "weight": 0.25},
            "buying_intent": {"score": 0, "weight": 0.2},
            "verification": {"score": 5, "weight": 0.15},
            "contactability": {"score": 3, "weight": 0.1},
            "geographic_relevance": {"score": 5, "weight": 0.05},
        }
        self.assertEqual(qualification_tier(record), "research_only")

    def test_contextual_only_is_rejected(self):
        record = build_record(
            candidate(evidence_tier="C", description="We are a marketing agency."),
            "job_boards",
            datetime(2026, 10, 25, tzinfo=timezone.utc),
        )
        ok, reason = admit(record, "job_boards")
        self.assertFalse(ok)
        self.assertIn(reason, {"contextual_only", "below_band"})

    def test_no_citable_url_is_rejected(self):
        record = build_record(candidate(url=None), "job_boards", datetime(2026, 10, 25, tzinfo=timezone.utc))
        ok, reason = admit(record, "job_boards")
        self.assertFalse(ok)
        self.assertEqual(reason, "no_citable_evidence")

    def test_poor_skill_fit_is_rejected(self):
        record = build_record(candidate(), "freelance", datetime(2026, 10, 25, tzinfo=timezone.utc))
        record["details"]["skill_fit"] = "poor"
        ok, reason = admit(record, "freelance")
        self.assertFalse(ok)
        self.assertEqual(reason, "capability")


class DedupeTests(unittest.TestCase):
    def test_same_business_from_two_sources_merges_evidence(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        first = build_record(candidate(), "job_boards", now)
        second = build_record(
            candidate(url="https://linkedin.com/company/links", platform="LinkedIn"),
            "directories",
            now,
        )
        merged = dedupe([first, second])
        self.assertEqual(len(merged), 1)
        urls = {e["url"] for e in merged[0]["details"]["evidence"]}
        self.assertEqual(urls, {"https://example.com/job/1", "https://linkedin.com/company/links"})

    def test_different_businesses_stay_separate(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        merged = dedupe(
            [
                build_record(candidate(), "job_boards", now),
                build_record(
                    candidate(
                        business="Other Studio",
                        url="https://example.com/2",
                        website="https://otherstudio.example",
                    ),
                    "job_boards",
                    now,
                ),
            ]
        )
        self.assertEqual(len(merged), 2)

    def test_same_domain_different_branch_name_merges(self):
        """Branch listings that normalise differently still collapse to one lead."""
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        merged = dedupe(
            [
                build_record(
                    candidate(business="M.R. Consultants Gulberg", website="https://mr.example"),
                    "google_maps",
                    now,
                ),
                build_record(
                    candidate(business="M.R. Consultants DHA", website="https://mr.example"),
                    "google_maps",
                    now,
                ),
            ]
        )
        self.assertEqual(len(merged), 1)

    def test_www_and_subdomain_variants_of_one_domain_merge(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        merged = dedupe(
            [
                build_record(candidate(website="https://www.example.com"), "google_maps", now),
                build_record(
                    candidate(business="Example Incorporated", website="https://example.com/contact"),
                    "google_maps",
                    now,
                ),
            ]
        )
        self.assertEqual(len(merged), 1)


if __name__ == "__main__":
    unittest.main()
