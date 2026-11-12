import unittest
from datetime import datetime, timezone

from pipeline.qualify import build_record
from pipeline.push import to_payload


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.record = build_record(
            {
                "business": "Links Digital (Pvt) Ltd",
                "need": "CRM lead routing + WhatsApp intake",
                "description": "Manual follow-up across HubSpot and WhatsApp.",
                "url": "https://example.com/job/1",
                "website": "https://linksdigital.example",
                "city": "Lahore",
                "country": "PK",
                "platform": "Job Boards",
                "evidence_tier": "B",
            },
            "job_boards",
            datetime(2026, 10, 25, tzinfo=timezone.utc),
        )

    def test_payload_carries_identity_and_details(self):
        payload = to_payload(self.record)
        self.assertEqual(payload["business_key"], "links-digital")
        self.assertEqual(payload["source"], "job_boards")
        self.assertIn("evidence", payload["details"])
        self.assertTrue(payload["details"]["evidence"][0]["url"])
        self.assertIsInstance(payload["weighted_score"], int)
        self.assertIn(payload["classification"], {"priority", "potential", "deprioritize"})

    def test_payload_has_no_none_keys(self):
        payload = to_payload(self.record)
        for key in ("business", "need", "platform", "source", "details"):
            self.assertIsNotNone(payload[key], key)

    def test_evidence_level_is_on_the_shared_scale(self):
        payload = to_payload(self.record)
        self.assertIn(payload["evidence_level"], {"direct", "plausible", "weak", None})


if __name__ == "__main__":
    unittest.main()
