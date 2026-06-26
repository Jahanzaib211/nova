"""Tests for iGIN0 privacy audit trail."""

import unittest
from unittest.mock import patch


class TestAuditTrail(unittest.TestCase):

    def test_record_search(self):
        from deerflow.community.searxng.audit import AuditTrail, AuditRecord
        trail = AuditTrail(enabled=True)
        rec = trail.search(
            thread_id="t1",
            query="test query",
            privacy_mode=True,
            tor_used=True,
            sources_searched=["google"],
            results_returned=5,
        )
        self.assertIsNotNone(rec)
        self.assertEqual(rec.query, "test query")
        self.assertTrue(rec.privacy_mode)
        self.assertTrue(rec.tor_used)
        self.assertEqual(rec.results_returned, 5)
        self.assertIn("search", rec.compliance_tags)

    def test_record_fetch(self):
        from deerflow.community.searxng.audit import AuditTrail
        trail = AuditTrail(enabled=True)
        rec = trail.fetch(
            thread_id="t1",
            url="http://example.com",
            source="example",
            success=True,
            duration_ms=150.0,
        )
        self.assertIsNotNone(rec)
        self.assertIn("fetch", rec.compliance_tags)
        self.assertEqual(rec.results_succeeded, 1)

    def test_get_records(self):
        from deerflow.community.searxng.audit import AuditTrail
        trail = AuditTrail(enabled=True)
        trail.search(thread_id="t1", query="q1", sources_searched=[], results_returned=0)
        trail.search(thread_id="t1", query="q2", sources_searched=[], results_returned=0)
        records = trail.get_records(limit=10)
        self.assertEqual(len(records), 2)

    def test_get_stats(self):
        from deerflow.community.searxng.audit import AuditTrail
        trail = AuditTrail(enabled=True)
        trail.search(thread_id="t1", query="q1", sources_searched=[], results_returned=0, tor_used=True)
        trail.search(thread_id="t1", query="q2", sources_searched=[], results_returned=0, error="timeout")
        stats = trail.get_stats()
        self.assertEqual(stats["total_records"], 2)
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(stats["tor_usage"], 1)
        self.assertTrue(stats["enabled"])

    def test_disabled_is_noop(self):
        from deerflow.community.searxng.audit import AuditTrail
        trail = AuditTrail(enabled=False)
        trail.search(thread_id="t1", query="q", sources_searched=[], results_returned=0)
        records = trail.get_records()
        self.assertEqual(len(records), 0)

    def test_clear(self):
        from deerflow.community.searxng.audit import AuditTrail
        trail = AuditTrail(enabled=True)
        trail.search(thread_id="t1", query="q", sources_searched=[], results_returned=0)
        trail.clear()
        self.assertEqual(len(trail.get_records()), 0)

    def test_max_records_rotation(self):
        from deerflow.community.searxng.audit import AuditTrail
        trail = AuditTrail(enabled=True)
        trail._max_records = 5
        for i in range(10):
            trail.search(thread_id="t1", query=f"q{i}", sources_searched=[], results_returned=0)
        records = trail.get_records(limit=100)
        self.assertLessEqual(len(records), 5)


class TestAuditRecord(unittest.TestCase):

    def test_audit_record_id(self):
        from deerflow.community.searxng.audit import AuditRecord
        rec = AuditRecord()
        self.assertIsNotNone(rec.audit_id)
        self.assertEqual(len(rec.audit_id), 16)

    def test_audit_record_timestamp(self):
        from deerflow.community.searxng.audit import AuditRecord
        rec = AuditRecord()
        self.assertIn("T", rec.timestamp)

    def test_to_dict(self):
        from deerflow.community.searxng.audit import AuditRecord
        rec = AuditRecord(query="test", thread_id="t1")
        d = rec.to_dict()
        self.assertEqual(d["query"], "test")
        self.assertEqual(d["thread_id"], "t1")

    def test_to_json(self):
        from deerflow.community.searxng.audit import AuditRecord
        rec = AuditRecord(query="test")
        j = rec.to_json()
        self.assertIn("test", j)


class TestAuditSingleton(unittest.TestCase):

    def test_get_audit_trail(self):
        from deerflow.community.searxng.audit import get_audit_trail
        t1 = get_audit_trail()
        t2 = get_audit_trail()
        self.assertIs(t1, t2)


if __name__ == "__main__":
    unittest.main()
