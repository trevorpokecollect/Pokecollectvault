"""End-to-end test of the core loop: intake -> collection -> ship-out -> shipped.

Run: python3 -m unittest discover tests
"""
import io
import os
import shutil
import tempfile
import unittest

import cv2

TMP = tempfile.mkdtemp(prefix="vault-test-")
os.environ["VAULT_DB"] = os.path.join(TMP, "vault.db")
os.environ["VAULT_UPLOADS"] = os.path.join(TMP, "uploads")
os.environ["STAFF_PASSCODE"] = "test-pass"

import app as vault_app  # noqa: E402
from scripts import daily, seed  # noqa: E402
from tests.test_imaging import make_card, scene  # noqa: E402
from vault import db  # noqa: E402


def card_scan():
    return scene(make_card(), (25, 25, 30), [[380, 260], [1040, 330], [960, 1250], [300, 1180]])


class CoreLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed.main()
        vault_app.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        self.client = vault_app.app.test_client()

    def staff_login(self):
        r = self.client.post("/staff/login", data={"passcode": "test-pass"})
        self.assertEqual(r.status_code, 302)

    def test_staff_pages_need_passcode(self):
        self.assertEqual(self.client.get("/staff/intake").status_code, 302)
        r = self.client.post("/staff/login", data={"passcode": "wrong"}, follow_redirects=True)
        self.assertIn(b"Wrong passcode", r.data)

    def test_catalog_search(self):
        self.staff_login()
        rows = self.client.get("/api/catalog/search?q=rayquaza 218").get_json()
        self.assertEqual(rows[0]["label"], "Rayquaza VMAX (Alternate Art Secret)")
        self.assertEqual(rows[0]["nm"], 1318.25)

    def test_preview_crop(self):
        self.staff_login()
        r = self.client.post("/api/preview-crop", data={"image": (io.BytesIO(card_scan()), "f.jpg"), "kind": "raw"},
                             content_type="multipart/form-data")
        body = r.get_json()
        self.assertTrue(body["image"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(body["warnings"], [])

    def test_full_loop(self):
        self.staff_login()
        card = self.client.get("/api/catalog/search?q=mew ex 347").get_json()[0]

        # 1. intake for a new customer
        r = self.client.post("/staff/intake", data={
            "kind": "raw", "card_id": card["id"], "condition": "NM", "customer_id": "new",
            "new_name": "Test Collector", "new_email": "test@example.com",
            "front": (io.BytesIO(card_scan()), "front.jpg"), "back": (io.BytesIO(card_scan()), "back.jpg"),
        }, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        vault_id = r.headers["Location"].rsplit("/", 1)[-1]
        self.assertRegex(vault_id, r"^VLT-\d{6}$")
        page = self.client.get(f"/staff/item/{vault_id}")
        self.assertIn(b"Added " + vault_id.encode(), page.data)
        self.assertNotIn(b"Check crop", page.data)

        with db.session() as conn:
            item = conn.execute("SELECT * FROM vault_items WHERE vault_id = ?", (vault_id,)).fetchone()
            customer_id = item["customer_id"]
            for key in ("front_image", "back_image", "front_thumb"):
                path = os.path.join(os.environ["VAULT_UPLOADS"], item[key])
                img = cv2.imread(path)
                self.assertIsNotNone(img, key)
            # backfill a snapshot so the history chart has two points
            conn.execute("INSERT INTO value_snapshots (vault_item_id, day, value) VALUES (?, date('now', '-10 day'), 580.0)", (item["id"],))
            daily.snapshot(conn)

        # 2. customer sees it with its value and chart
        self.client.post(f"/demo-sign-in/{customer_id}")
        col = self.client.get("/app")
        self.assertEqual(col.status_code, 200)
        self.assertIn(b"$616.00", col.data)
        self.assertIn(b"<polyline", col.data)
        self.assertIn("+$36.00 (+6.2%)".encode(), col.data.replace("−".encode(), b"-"))
        detail = self.client.get(f"/app/card/{vault_id}")
        self.assertIn(b"data-card3d", detail.data)
        self.assertIn(b"Ship home", detail.data)

        # 3. ship-out request
        self.assertIn(b"Enter the full", self.client.post(f"/app/card/{vault_id}/ship", data={"address": "short"}, follow_redirects=True).data)
        r = self.client.post(f"/app/card/{vault_id}/ship", data={"address": "Test Collector\n123 Main St\nMadison, AL 35758"})
        self.assertIn(b"Ship-out requested", r.data)
        self.assertIn(b"Ship-out requested", self.client.get("/app").data)
        self.assertIn(b"already has a ship-out", self.client.get(f"/app/card/{vault_id}/ship", follow_redirects=True).data)

        # 4. staff marks it shipped; it leaves the collection
        with db.session() as conn:
            sid = conn.execute("SELECT id FROM shipments WHERE vault_item_id = ?", (item["id"],)).fetchone()["id"]
        r = self.client.post("/staff/shipments", data={"shipment_id": sid, "tracking": "1Z999"}, follow_redirects=True)
        self.assertIn(f"Marked {vault_id} as shipped".encode(), r.data)
        col = self.client.get("/app")
        self.assertNotIn(vault_id.encode(), col.data)
        act = self.client.get("/app/activity")
        for text in (b"Added to vault", b"Ship-out", b"Shipped", b"1Z999"):
            self.assertIn(text, act.data)

    def test_customer_cannot_open_someone_elses_card(self):
        with db.session() as conn:
            other = conn.execute("INSERT INTO customers (name, email, created_at) VALUES ('Other', 'o@example.com', ?)", (db.now_iso(),)).lastrowid
            card_id = conn.execute("SELECT id FROM catalog_cards LIMIT 1").fetchone()["id"]
            conn.execute("INSERT INTO vault_items (vault_id, customer_id, card_id, condition, intake_at) VALUES ('VLT-999999', ?, ?, 'NM', ?)",
                         (other, card_id, db.now_iso()))
            demo = conn.execute("SELECT id FROM customers WHERE email = 'demo@example.com'").fetchone()["id"]
        self.client.post(f"/demo-sign-in/{demo}")
        self.assertEqual(self.client.get("/app/card/VLT-999999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
