"""Tests for the auto-crop pipeline. Run: python3 -m unittest discover tests"""
import os
import unittest

import cv2
import numpy as np

from vault.imaging import SIZES, process_card

HERE = os.path.dirname(__file__)
SAMPLE = os.path.join(HERE, "fixtures", "card-photo.jpg")


def make_card(w=630, h=880):
    """A synthetic card face: light border, darker art box, text bars (no real artwork)."""
    card = np.full((h, w, 3), (200, 205, 210), np.uint8)
    cv2.rectangle(card, (30, 90), (w - 30, h // 2), (90, 140, 60), -1)
    for i in range(6):
        y = h // 2 + 40 + i * 50
        cv2.rectangle(card, (40, y), (w - 40 - (i * 37) % 120, y + 18), (60, 60, 60), -1)
    cv2.putText(card, "TEST", (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (30, 30, 30), 3)
    return card


def scene(card, bg, quad, size=(1400, 1800)):
    W, H = size
    canvas = np.full((H, W, 3), bg, np.uint8)
    noise = np.random.default_rng(1).integers(-10, 10, (H, W, 3))
    canvas = np.clip(canvas.astype(int) + noise, 0, 255).astype(np.uint8)
    h, w = card.shape[:2]
    M = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), np.float32(quad))
    warped = cv2.warpPerspective(card, M, (W, H))
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (W, H))
    canvas[mask > 0] = warped[mask > 0]
    return cv2.imencode(".jpg", canvas)[1].tobytes()


def corner_error(found, truth):
    found = np.float32(found)
    truth = np.float32(truth)
    # compare as sets: each true corner to its nearest found corner
    return max(np.min(np.linalg.norm(found - t, axis=1)) for t in truth)


class AutoCropTests(unittest.TestCase):
    def setUp(self):
        self.card = make_card()

    def check(self, bg, quad, max_err=10):
        r = process_card(scene(self.card, bg, quad))
        self.assertEqual(r["method"], "outline")
        self.assertEqual(r["warnings"], [])
        self.assertLess(corner_error(r["corners"], quad), max_err)
        img = cv2.imdecode(np.frombuffer(r["image"], np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual((img.shape[1], img.shape[0]), SIZES["raw"])
        return r

    def test_dark_background_rotated(self):
        self.check((25, 25, 30), [[380, 260], [1040, 330], [960, 1250], [300, 1180]])

    def test_white_scanner_bed(self):
        self.check((235, 235, 235), [[200, 300], [830, 300], [830, 1180], [200, 1180]])

    def test_angled_photo(self):
        self.check((60, 50, 40), [[420, 200], [980, 260], [1100, 1500], [300, 1450]], max_err=14)

    def test_sideways_card_comes_out_portrait(self):
        r = self.check((20, 20, 20), [[1200, 300], [1250, 950], [350, 1000], [300, 350]])
        img = cv2.imdecode(np.frombuffer(r["image"], np.uint8), cv2.IMREAD_COLOR)
        self.assertGreater(img.shape[0], img.shape[1])

    def test_graded_slab_size(self):
        slab = make_card(620, 1024)
        r = process_card(scene(slab, (30, 30, 30), [[300, 200], [920, 200], [920, 1224], [300, 1224]]), kind="graded")
        img = cv2.imdecode(np.frombuffer(r["image"], np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual((img.shape[1], img.shape[0]), SIZES["graded"])

    def test_bad_upload_raises(self):
        with self.assertRaises(ValueError):
            process_card(b"not an image")

    @unittest.skipUnless(os.path.exists(SAMPLE), "no real photo fixture")
    def test_tight_phone_photo(self):
        r = process_card(open(SAMPLE, "rb").read())
        self.assertEqual(r["method"], "edges")


if __name__ == "__main__":
    unittest.main()
