import unittest
from datetime import datetime, timezone

from pipeline.maps import build_queries, item_to_lead, pitch_for
from pipeline.validate import validate_lead

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

PLACE = {
    "title": "Aura Skin & Laser Clinic",
    "categories": ["Skin care clinic", "Medical clinic"],
    "address": "Sialkot",
    "city": "Sialkot",
    "countryCode": "PK",
    "phone": "+92 327 1221111",
    "website": "https://auraslclinic.com/",
    "url": "https://www.google.com/maps/place/aura",
    "totalScore": 4.7,
    "reviewsCount": 461,
}


class PitchTests(unittest.TestCase):
    def test_clinic_pitch(self):
        self.assertIn("WhatsApp", pitch_for("Skin care clinic"))

    def test_real_estate_pitch(self):
        self.assertIn("lead qualification", pitch_for("Real estate agency"))

    def test_unknown_category_uses_default(self):
        self.assertIn("chatbot", pitch_for("Pet grooming").lower())


class MappingTests(unittest.TestCase):
    def test_item_to_lead_shape(self):
        lead = item_to_lead(PLACE, "skin clinic Sialkot", NOW)
        self.assertEqual(lead["source"], "maps")
        self.assertEqual(lead["platform"], "Google Maps")
        self.assertEqual(lead["business"], "Aura Skin & Laser Clinic")
        self.assertEqual(lead["recency_source"], "observed_live")
        self.assertEqual(lead["phone"], "+92 327 1221111")
        self.assertEqual(lead["website"], "https://auraslclinic.com/")

    def test_listing_without_website_gets_website_pitch(self):
        place = {k: v for k, v in PLACE.items() if k != "website"}
        lead = item_to_lead(place, "skin clinic Sialkot", NOW)
        self.assertIsNone(lead["website"])
        self.assertIn("Website", lead["need"])
        self.assertNotIn("chatbot", lead["need"].lower())
        self.assertIsNone(lead["published_at"])

    def test_build_queries_combines_keywords_and_cities(self):
        profile = {"hunting_keywords": {"google_maps": ["skin clinic", "real estate agency"]}}
        queries = build_queries(profile, ["Sialkot", "Lahore"])
        self.assertEqual(len(queries), 4)
        self.assertIn("skin clinic Sialkot", queries)


class MapsValidationTests(unittest.TestCase):
    def test_live_listing_passes_all_checks(self):
        lead = item_to_lead(PLACE, "skin clinic Sialkot", NOW)
        result = validate_lead(lead, {}, now=NOW)
        self.assertTrue(result["passed"], result["validation_reasons"])
        self.assertEqual(
            result["validation_passed"],
            ["remote", "recency", "relevance", "fit", "contact"],
        )
        self.assertIn("phone", result["contact_paths"])
        self.assertIn("website", result["contact_paths"])

    def test_listing_without_contact_is_rejected(self):
        place = {k: v for k, v in PLACE.items() if k not in {"phone", "website"}}
        lead = item_to_lead(place, "skin clinic Sialkot", NOW)
        result = validate_lead(lead, {}, now=NOW)
        self.assertIn("contact", result["validation_failed"])

    def test_unknown_date_without_live_flag_is_rejected(self):
        lead = item_to_lead(PLACE, "skin clinic Sialkot", NOW)
        lead.pop("recency_source")
        result = validate_lead(lead, {}, now=NOW)
        self.assertIn("recency", result["validation_failed"])


if __name__ == "__main__":
    unittest.main()
