import unittest
from datetime import datetime, timedelta, timezone

from pipeline.followups import eligibility, review

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def lead(**overrides):
    base = {
        "id": "1",
        "business": "Acme",
        "status": "contacted",
        "last_contact_at": (NOW - timedelta(days=4)).isoformat(),
        "next_follow_up_at": (NOW - timedelta(days=1)).isoformat(),
        "opt_out": False,
        "hidden": False,
        "details": {},
    }
    base.update(overrides)
    return base


class EligibilityTests(unittest.TestCase):
    def test_due_contacted_lead_is_eligible(self):
        ok, reason = eligibility(lead(), NOW)
        self.assertTrue(ok, reason)

    def test_opt_out_blocks_everything(self):
        ok, reason = eligibility(lead(opt_out=True), NOW)
        self.assertFalse(ok)
        self.assertEqual(reason, "opt_out")

    def test_missing_contact_history_is_unverifiable(self):
        ok, reason = eligibility(lead(last_contact_at=None), NOW)
        self.assertFalse(ok)
        self.assertEqual(reason, "contact_history_unverified")

    def test_future_follow_up_waits(self):
        ok, reason = eligibility(lead(next_follow_up_at=(NOW + timedelta(days=2)).isoformat()), NOW)
        self.assertFalse(ok)
        self.assertEqual(reason, "not_due_yet")

    def test_won_or_lost_is_closed(self):
        for status in ("won", "lost", "do_not_contact", "disqualified"):
            ok, _ = eligibility(lead(status=status), NOW)
            self.assertFalse(ok, status)


class ReviewTests(unittest.TestCase):
    def test_opt_out_is_suppressed_not_contacted(self):
        rows = review([lead(opt_out=True)], NOW)
        self.assertEqual(rows[0]["recommended_action"], "suppress")
        self.assertEqual(rows[0]["recommended_status"], "do_not_contact")

    def test_not_interested_closes_the_lead(self):
        rows = review([lead(status="replied", details={"reply_classification": "not_interested"})], NOW)
        self.assertEqual(rows[0]["recommended_action"], "close")

    def test_ambiguous_records_need_human_review(self):
        rows = review([lead(last_contact_at=None)], NOW)
        self.assertEqual(rows[0]["recommended_action"], "review_record")
        self.assertTrue(rows[0]["human_review_required"])

    def test_no_suggested_message_is_ever_generated_here(self):
        rows = review([lead()], NOW)
        self.assertIsNone(rows[0]["suggested_message"])


if __name__ == "__main__":
    unittest.main()
