import unittest
from datetime import datetime, timezone

from pipeline.validate import (
    check_contact,
    check_fit,
    check_recency,
    check_relevance,
    check_remote,
    validate_lead,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def make_lead(**overrides):
    lead = {
        "source": "remoteok",
        "platform": "RemoteOK",
        "business": "Acme Automation",
        "title": "AI Automation Engineer",
        "need": "Build chatbot and n8n automation",
        "description": (
            "Build n8n workflow automation and a chatbot for clients. "
            "Remote worldwide contractor role."
        ),
        "location": "Worldwide",
        "published_at": "2026-10-21T09:00:00+00:00",
        "url": "https://example.com/job",
        "application_url": "https://example.com/apply",
    }
    lead.update(overrides)
    return lead


class RemoteCheckTests(unittest.TestCase):
    def test_accepts_worldwide_remote(self):
        ok, reason = check_remote(make_lead())
        self.assertTrue(ok, reason)

    def test_rejects_us_citizen_only(self):
        ok, reason = check_remote(make_lead(description="US Citizen Only. Remote role."))
        self.assertFalse(ok)
        self.assertIn("blocked", reason)

    def test_rejects_w2_only(self):
        ok, _ = check_remote(make_lead(description="W2 only, must be in the US."))
        self.assertFalse(ok)

    def test_maps_pakistan_accepted(self):
        ok, reason = check_remote(
            make_lead(source="google_maps", location="Lahore, Pakistan", description="")
        )
        self.assertTrue(ok)
        self.assertIn("Pakistan", reason)

    def test_maps_outside_pakistan_needs_remote_signal(self):
        ok, _ = check_remote(
            make_lead(source="google_maps", location="Dubai, UAE", description="", need="")
        )
        self.assertFalse(ok)

    def test_direct_social_client_accepted(self):
        ok, _ = check_remote(
            make_lead(source="instagram", description="need a chatbot for my clinic", location="")
        )
        self.assertTrue(ok)

    def test_unknown_job_board_rejected(self):
        ok, _ = check_remote(make_lead(description="Build chatbots.", location=""))
        self.assertFalse(ok)


class RecencyCheckTests(unittest.TestCase):
    def test_accepts_three_days_old(self):
        ok, _, ts = check_recency(make_lead(published_at="2026-10-20T09:00:00+00:00"), NOW)
        self.assertTrue(ok)
        self.assertIsNotNone(ts)

    def test_rejects_eight_days_old(self):
        ok, reason, _ = check_recency(make_lead(published_at="2026-10-15T09:00:00+00:00"), NOW)
        self.assertFalse(ok)
        self.assertIn("older than", reason)

    def test_rejects_missing_date(self):
        ok, _, _ = check_recency(make_lead(published_at=None), NOW)
        self.assertFalse(ok)

    def test_uses_last_post_proxy_for_maps(self):
        lead = make_lead(
            source="google_maps",
            published_at=None,
            last_post_at="2026-10-22T10:00:00+00:00",
        )
        ok, _, _ = check_recency(lead, NOW)
        self.assertTrue(ok)


class RelevanceCheckTests(unittest.TestCase):
    def test_matches_chatbot_and_n8n(self):
        ok, reason, score = check_relevance(make_lead())
        self.assertTrue(ok, reason)
        self.assertGreaterEqual(score, 2)

    def test_rejects_unrelated_job(self):
        ok, _, score = check_relevance(
            make_lead(
                title="Accounting Manager",
                need="Payroll and bookkeeping",
                description="Manage accounts payable and monthly close.",
            )
        )
        self.assertFalse(ok)
        self.assertLess(score, 2)


class FitCheckTests(unittest.TestCase):
    def test_rejects_senior_title(self):
        ok, reason = check_fit(make_lead(title="Senior AI Automation Engineer"))
        self.assertFalse(ok)
        self.assertIn("senior", reason)

    def test_rejects_years_of_experience(self):
        ok, reason = check_fit(
            make_lead(description="Build chatbots. 5+ years of experience required.")
        )
        self.assertFalse(ok)
        self.assertIn("years", reason)

    def test_rejects_three_to_five_years(self):
        ok, _ = check_fit(
            make_lead(description="3-5 years experience building n8n workflows.")
        )
        self.assertFalse(ok)

    def test_allows_one_year(self):
        ok, _ = check_fit(
            make_lead(description="1 year experience with chatbots is enough.")
        )
        self.assertTrue(ok)

    def test_rejects_degree_requirement(self):
        ok, reason = check_fit(
            make_lead(description="Bachelor's degree in computer science required.")
        )
        self.assertFalse(ok)
        self.assertIn("education", reason)

    def test_degree_with_equivalent_experience_allowed(self):
        ok, _ = check_fit(
            make_lead(description="Bachelor's degree or equivalent experience.")
        )
        self.assertTrue(ok)

    def test_allows_entry_level_part_time(self):
        ok, reason = check_fit(
            make_lead(
                title="Junior Chatbot Assistant",
                description="Part-time, entry-level, training provided.",
            )
        )
        self.assertTrue(ok)
        self.assertIn("beginner", reason)

    def test_direct_clients_skip_seniority_rules(self):
        ok, reason = check_fit(
            make_lead(
                source="google_maps",
                business="Senior Living Care Clinic",
                description="Elderly care clinic in Sialkot.",
            )
        )
        self.assertTrue(ok)
        self.assertIn("direct client", reason)


class ContactCheckTests(unittest.TestCase):
    def test_application_url_passes(self):
        ok, _, paths = check_contact(make_lead())
        self.assertTrue(ok)
        self.assertIn("application_url", paths)

    def test_email_and_phone_pass(self):
        ok, _, paths = check_contact(
            make_lead(application_url=None, email="owner@example.com", phone="+92 300 1234567")
        )
        self.assertTrue(ok)
        self.assertIn("email", paths)
        self.assertIn("phone", paths)

    def test_no_contact_fails(self):
        ok, _, _ = check_contact(
            make_lead(application_url=None, url=None, email=None, phone=None)
        )
        self.assertFalse(ok)


class ValidateLeadTests(unittest.TestCase):
    def test_full_pass(self):
        result = validate_lead(make_lead(), now=NOW)
        self.assertTrue(result["passed"])
        self.assertEqual(
            result["validation_passed"],
            ["remote", "recency", "relevance", "fit", "contact"],
        )
        self.assertTrue(result["remote_eligible_pak"])

    def test_us_only_job_fails_remote(self):
        result = validate_lead(
            make_lead(description="US Citizen Only. Build n8n chatbot automation."), now=NOW
        )
        self.assertFalse(result["passed"])
        self.assertIn("remote", result["validation_failed"])

    def test_stale_job_fails_recency(self):
        result = validate_lead(make_lead(published_at="2026-09-01T09:00:00+00:00"), now=NOW)
        self.assertFalse(result["passed"])
        self.assertIn("recency", result["validation_failed"])


if __name__ == "__main__":
    unittest.main()
