"""Fictional screenshots exercise OCR, review, isolation, and deduplication."""

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

from drbudget import screenshots, store
from drbudget.web import create_app


BODY = ("Credit Card XX1234 was used for AED42.35 on 02/03/2026 10:11:12 at "
        "FICTIONAL Example Market, Test City-AE. Available limit AED98765.43")
SECOND = BODY.replace("42.35", "15.50").replace("10:11:12", "10:21:12")
PARTIAL = "Credit Card XX1234 was used for AED18.00 on"


def fixture_image(marker=0, notifications=(BODY, SECOND, PARTIAL)):
    image = Image.new("RGB", (1000, 1250), (12 + marker, 12, 16))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("DejaVuSans.ttf", 28)
    draw.text((50, 30), "FICTIONAL TEST ONLY", font=font, fill="white")
    for index, body in enumerate(notifications):
        top = 110 + index * 350
        draw.rounded_rectangle((30, top, 970, top + 320), radius=25, fill=(35, 35, 40))
        draw.text((65, top + 20), "ADCBAlert", font=font, fill="white")
        draw.multiline_text((65, top + 70), textwrap.fill(body, width=54), font=font, fill="white", spacing=10)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ScreenshotParsingTests(unittest.TestCase):
    def test_actual_ocr_reads_fictional_dark_notification_stack(self):
        text = screenshots.local_ocr(screenshots.normalize_image(fixture_image()))
        candidates = screenshots.extract_candidates(text)
        parsed = [screenshots.restored_purchase(p) for _, p, _ in candidates if p]
        self.assertEqual([p.amount_minor for p in parsed], [4235, 1550], text)
        self.assertTrue(any(error for _, _, error in candidates), text)

    def test_partial_prefix_and_tail_are_not_silently_dropped(self):
        text = "FICTIONAL clipped merchant. Available limit AED100.00\n" + BODY + "\n" + PARTIAL
        rows = screenshots.extract_candidates(text)
        self.assertEqual(len(rows), 3)
        self.assertIsNone(rows[0][1])
        self.assertIsNotNone(rows[1][1])
        self.assertIsNone(rows[2][1])

    def test_empty_ocr_produces_an_unresolved_item(self):
        rows = screenshots.extract_candidates("")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][1])
        self.assertTrue(rows[0][2])

    def test_image_validation_rejects_non_images_oversized_and_unsupported_files(self):
        for raw in (b"<svg><script>fictional()</script></svg>", b"", b"x" * (screenshots.MAX_UPLOAD_BYTES + 1)):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                screenshots.normalize_image(raw)
        with patch.object(screenshots, "MAX_PIXELS", 10), self.assertRaises(ValueError):
            screenshots.normalize_image(fixture_image())

    def test_normalized_image_does_not_preserve_embedded_metadata(self):
        metadata = PngInfo()
        metadata.add_text("Comment", "FICTIONAL private metadata")
        encoded = io.BytesIO()
        with Image.open(io.BytesIO(fixture_image())) as image:
            image.save(encoded, format="PNG", pnginfo=metadata)
        normalized = screenshots.normalize_image(encoded.getvalue())
        self.assertNotIn(b"FICTIONAL private metadata", normalized)
        with Image.open(io.BytesIO(normalized)) as image:
            self.assertNotIn("Comment", image.info)


class ScreenshotWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "private"
        self.app = create_app(self.root, {"TESTING": True, "NOW": lambda: datetime(2026, 3, 3, 8, tzinfo=timezone.utc)})
        self.client = self.app.test_client()
        self.client.get("/register")
        self.post("/register", dict(name="Fictional Screenshot Test", email="screenshot@example.test", password="fictional-screenshot-password"))
        with self.client.session_transaction() as session:
            self.uid = session["user_id"]
        with self.db() as connection:
            self.hid = connection.execute("SELECT household_id FROM users WHERE id=?", (self.uid,)).fetchone()[0]
            self.cat = store.add_category(connection, self.hid, "Fictional groceries", "spending", True, 10000, "2026-03-01")

    def db(self):
        return store.database(self.app.config["DATABASE"])

    def post(self, path, data=None, client=None, **kwargs):
        client = client or self.client
        with client.session_transaction() as session:
            csrf = session.get("csrf", "")
        return client.post(path, data={**(data or {}), "csrf": csrf}, **kwargs)

    def upload(self, text=BODY, marker=0):
        with patch("drbudget.screenshots.local_ocr", return_value=text):
            response = self.post("/screenshots", {"screenshot": (io.BytesIO(fixture_image(marker)), "../../private.png")})
        self.assertEqual(response.status_code, 302)
        return response.location.rsplit("/", 1)[-1]

    def item(self, bid):
        with self.db() as connection:
            return dict(connection.execute("SELECT * FROM screenshot_items WHERE batch_id=? ORDER BY position", (bid,)).fetchone())

    def decide(self, item, action="confirm", **extra):
        return self.post("/screenshot-items/" + item["id"], dict(version=str(item["version"]), action=action, category_id=self.cat, **extra))

    def test_private_upload_creates_review_items_but_no_transactions(self):
        bid = self.upload(BODY + "\n" + SECOND + "\n" + PARTIAL)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM screenshot_items").fetchone()[0], 3)
        path = self.root / "screenshots" / (bid + ".png")
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        response = self.client.get(f"/screenshots/{bid}/image")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        response.close()

    def test_identical_upload_reuses_batch_without_reprocessing(self):
        first = self.upload()
        with patch("drbudget.screenshots.local_ocr") as ocr:
            response = self.post("/screenshots", {"screenshot": (io.BytesIO(fixture_image()), "copy.png")})
        self.assertTrue(response.location.endswith(first))
        ocr.assert_not_called()
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM screenshot_batches").fetchone()[0], 1)

    def test_confirmation_is_idempotent_and_updates_budget_once(self):
        bid = self.upload()
        item = self.item(bid)
        self.decide(item)
        self.decide(item)
        with self.db() as connection:
            rows = connection.execute("SELECT amount, status FROM transactions").fetchall()
            self.assertEqual([tuple(r) for r in rows], [(4235, "confirmed")])
            self.assertEqual(connection.execute("SELECT state FROM screenshot_batches").fetchone()[0], "complete")
        self.assertIn(b"42.35", self.client.get("/").data)

    def test_overlap_requires_resolution_and_can_link_existing_transaction(self):
        first = self.item(self.upload())
        self.decide(first)
        second_batch = self.upload(marker=1)
        second = self.item(second_batch)
        self.assertIn(b"Possible duplicate", self.client.get(f"/screenshots/{second_batch}").data)
        self.decide(second)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 1)
            tid = connection.execute("SELECT id FROM transactions").fetchone()[0]
        self.decide(second, action="link", existing_transaction=tid)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT state FROM screenshot_items WHERE id=?", (second["id"],)).fetchone()[0], "linked")

    def test_different_times_are_separate_purchases_even_for_equal_amounts(self):
        first = self.item(self.upload())
        self.decide(first)
        second_batch = self.upload(BODY.replace("10:11:12", "10:21:12"), marker=1)
        self.assertNotIn(b"Possible duplicate", self.client.get(f"/screenshots/{second_batch}").data)
        self.decide(self.item(second_batch))
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT sum(amount) FROM transactions WHERE status='confirmed'").fetchone()[0], 8470)

    def test_unreadable_item_cannot_be_confirmed_but_can_be_corrected(self):
        bid = self.upload(PARTIAL)
        item = self.item(bid)
        self.decide(item)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 0)
        self.decide(item, action="reparse", text=BODY)
        self.decide(item)  # The stale version must not be accepted.
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 0)
        corrected = self.item(bid)
        self.assertEqual(corrected["original_text"], PARTIAL)
        self.assertEqual(corrected["edited_text"], BODY)
        self.decide(corrected)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 1)

    def test_failed_ocr_can_retry_without_losing_private_image(self):
        with patch("drbudget.screenshots.local_ocr", side_effect=ValueError("fictional timeout")):
            response = self.post("/screenshots", {"screenshot": (io.BytesIO(fixture_image()), "test.png")})
        bid = response.location.rsplit("/", 1)[-1]
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT state FROM screenshot_batches").fetchone()[0], "failed")
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 0)
        self.assertTrue((self.root / "screenshots" / (bid + ".png")).is_file())
        with patch("drbudget.screenshots.local_ocr", return_value=BODY):
            self.post(f"/screenshots/{bid}/retry")
        self.assertIsNotNone(self.item(bid)["parsed_json"])

    def test_saving_for_review_and_dismissing_do_not_add_confirmed_spending(self):
        bid = self.upload(BODY + "\n" + PARTIAL)
        self.decide(self.item(bid), action="review")
        with self.db() as connection:
            partial = dict(connection.execute("SELECT * FROM screenshot_items WHERE batch_id=? AND state='pending'", (bid,)).fetchone())
        self.decide(partial, action="dismiss")
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions WHERE status='confirmed'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT state FROM screenshot_batches").fetchone()[0], "complete")

    def test_screenshot_routes_require_auth_csrf_and_household_membership(self):
        bid = self.upload()
        item = self.item(bid)
        self.assertEqual(self.client.post("/screenshots").status_code, 400)
        other = self.app.test_client()
        self.assertEqual(other.get(f"/screenshots/{bid}/image").status_code, 302)
        other.get("/register")
        self.post("/register", dict(name="Other fictional household", email="other-screen@example.test", password="fictional-other-password"), client=other)
        for path in (f"/screenshots/{bid}", f"/screenshots/{bid}/image"):
            self.assertEqual(other.get(path).status_code, 404)
        self.assertEqual(self.post(f"/screenshots/{bid}/retry", client=other).status_code, 404)
        response = self.post("/screenshot-items/" + item["id"], dict(version="1", action="confirm", category_id=self.cat), client=other)
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
