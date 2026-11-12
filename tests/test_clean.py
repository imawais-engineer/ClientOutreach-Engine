import unittest
from datetime import datetime, timezone

from pipeline.clean import (
    clean_leads,
    dedupe,
    names_match,
    normalize_business,
    score_lead,
    why_suits,
)
from pipeline.validate import validate_lead

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

PROFILE = {
    "services": ["n8n Workflow Automation", "AI Lead Qualification & CRM Automation"],
    "outreach_angles": [
        {
            "service": "WhatsApp & Meta messaging automation",
            "hook": "A 24/7 WhatsApp assistant cuts repeat tickets.",
            "case_study_to_link": "https://imawais.engineer/projects/whatsapp-ai-assistant-google-docs",
        },
        {
            "service": "AI Customer Support Triage",
            "hook": "Instant acknowledgement and 90% faster replies.",
            "case_study_to_link": "https://imawais.engineer/projects/ai-customer-support-triage",
        },
        {
            "service": "AI Lead Qualification & CRM Automation",
            "hook": "Every inquiry scored and routed before it goes cold.",
            "case_study_to_link": "https://imawais.engineer/projects/ai-lead-qualification-real-estate",
        },
        {
            "service": "AI Social Media Automation",
            "hook": "Monitor 50+ Instagram accounts and surface real outliers.",
            "case_study_to_link": "https://imawais.engineer/projects/instagram-reels-outlier-telegram-n8n",
        },
        {
            "service": "Virtual Assistant Automation",
            "hook": "Hand off inbox triage and admin to an AI-assisted VA.",
            "case_study_to_link": "https://imawais.engineer/projects/ai-email-labeling-agent-gmail",
        },
        {
            "service": "n8n Workflow Automation",
            "hook": "Replace 10+ hours of manual reporting.",
            "case_study_to_link": "https://imawais.engineer/projects/slack-reporting-pipeline-apify-n8n",
        },
    ],
}


def make_lead(**overrides):
    lead = {
        "source": "remoteok",
        "platform": "RemoteOK",
        "business": "Acme Automation",
        "title": "AI Automation Engineer",
        "need": "Build chatbot and n8n automation",
        "description": "Build n8n workflow automation and a chatbot. Remote worldwide contractor.",
        "location": "Worldwide",
        "published_at": "2026-10-22T09:00:00+00:00",
        "url": "https://example.com/job",
        "application_url": "https://example.com/apply",
    }
    lead.update(overrides)
    return lead


class NormalizationTests(unittest.TestCase):
    def test_strips_legal_suffix_and_punctuation(self):
        self.assertEqual(normalize_business("Acme Automation, LLC"), "acme automation")
        self.assertEqual(normalize_business("AURA Skin & Laser Clinic (Pvt Ltd)"), "aura skin and laser clinic")

    def test_matches_branch_variant(self):
        self.assertTrue(
            names_match("Digital Marketing Agency", "Digital Marketing Agency Garden 59-T")
        )

    def test_does_not_match_short_unrelated_names(self):
        self.assertFalse(names_match("AI Labs", "AI Media"))


class DedupeTests(unittest.TestCase):
    def test_merges_same_business_and_keeps_freshest(self):
        older = make_lead(
            business="AURA Skin Clinic",
            source="google_maps",
            platform="Google Maps",
            published_at=None,
            last_review_at="2026-10-18T09:00:00+00:00",
            application_url=None,
            phone="+92 300 1234567",
            title="",
            need="Skin clinic",
            description="Skin clinic in Sialkot",
        )
        newer = make_lead(
            business="AURA Skin Clinic Sialkot",
            source="instagram",
            platform="Instagram",
            published_at=None,
            created_at="2026-10-22T09:00:00+00:00",
            application_url=None,
            profile_url="https://instagram.com/aura",
            dm_link="https://instagram.com/aura/direct",
            title="",
            need="Need WhatsApp chatbot",
            description="We need a WhatsApp chatbot for bookings",
        )
        merged = dedupe([older, newer])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["business"], "AURA Skin Clinic Sialkot")
        self.assertEqual(merged[0]["phone"], "+92 300 1234567")
        self.assertEqual(merged[0]["dm_link"], "https://instagram.com/aura/direct")
        self.assertEqual(merged[0]["sources"], ["google_maps", "instagram"])


class PitchTests(unittest.TestCase):
    def test_chatbot_maps_to_whatsapp_angle(self):
        pitch, service = why_suits(make_lead(description="need a chatbot for bookings"), PROFILE)
        self.assertIn("WhatsApp", service)
        self.assertIn("imawais.engineer/projects/", pitch)

    def test_support_maps_to_triage(self):
        _, service = why_suits(
            make_lead(
                title="Support automation",
                need="Support automation",
                description="ticket triage for helpdesk",
            ),
            PROFILE,
        )
        self.assertIn("Support Triage", service)

    def test_fallback_returns_an_angle(self):
        pitch, service = why_suits(make_lead(description="we need automation"), PROFILE)
        self.assertTrue(pitch)
        self.assertTrue(service)


class ScoreTests(unittest.TestCase):
    def test_fresh_contactable_lead_scores_high(self):
        lead = validate_lead(
            make_lead(email="owner@example.com", phone="+92 300 1234567"), PROFILE, NOW
        )
        self.assertGreaterEqual(score_lead(lead, NOW), 8)

    def test_old_lead_scores_lower(self):
        fresh = validate_lead(make_lead(), PROFILE, NOW)
        stale = validate_lead(
            make_lead(published_at="2026-10-18T09:00:00+00:00"), PROFILE, NOW
        )
        self.assertGreater(score_lead(fresh, NOW), score_lead(stale, NOW))

    def test_score_is_capped_at_ten(self):
        lead = validate_lead(
            make_lead(email="owner@example.com", phone="+92 300 1234567"), PROFILE, NOW
        )
        self.assertLessEqual(score_lead(lead, NOW), 10)


class CleanLeadsTests(unittest.TestCase):
    def test_filters_unvalidated_and_shapes_output(self):
        good = validate_lead(make_lead(email="owner@example.com"), PROFILE, NOW)
        bad = validate_lead(
            make_lead(description="US Citizen Only. Accounting role."), PROFILE, NOW
        )
        output = clean_leads([good, bad], PROFILE, NOW)
        self.assertEqual(len(output), 1)
        record = output[0]
        expected = {
            "business",
            "need",
            "why_suits",
            "service_to_pitch",
            "contact",
            "recency",
            "platform",
            "remote_eligible_pak",
            "validation_passed",
            "score",
            "source_url",
        }
        self.assertEqual(set(record), expected)
        self.assertTrue(record["remote_eligible_pak"])
        self.assertEqual(record["recency"], "2026-10-22 - yesterday")


if __name__ == "__main__":
    unittest.main()
