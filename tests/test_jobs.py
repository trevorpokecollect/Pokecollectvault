"""Tests for startup setup and the built-in daily job."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from vault import db, jobs


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vault-jobs-")
        self.env = mock.patch.dict(os.environ, {"VAULT_DB": os.path.join(self.tmp, "vault.db")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_start_loads_catalog_and_demo_customer(self):
        jobs.ensure_ready()
        jobs.ensure_ready()  # safe to run on every start
        with db.session() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM catalog_cards").fetchone()[0], 78)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)

    def test_daily_job_runs_once_per_day(self):
        jobs.ensure_ready()
        with db.session() as conn:
            card = conn.execute("SELECT id FROM catalog_cards WHERE name = 'Mew ex' AND language = 'Japanese'").fetchone()["id"]
            conn.execute("INSERT INTO vault_items (vault_id, customer_id, card_id, condition, intake_at) VALUES ('VLT-000001', 1, ?, 'NM', ?)",
                         (card, db.now_iso()))
        with mock.patch.object(jobs, "DAILY_HOUR_UTC", 0):
            self.assertTrue(jobs._due())
            self.assertEqual(jobs.run_daily(), 1)
            self.assertFalse(jobs._due())
        with db.session() as conn:
            row = conn.execute("SELECT value FROM value_snapshots WHERE day = ?", (db.today(),)).fetchone()
        self.assertEqual(row["value"], 616.0)

    def test_not_due_before_the_daily_hour(self):
        jobs.ensure_ready()
        with mock.patch.object(jobs, "DAILY_HOUR_UTC", 24):
            self.assertFalse(jobs._due())


if __name__ == "__main__":
    unittest.main()
