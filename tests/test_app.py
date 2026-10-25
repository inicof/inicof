"""Accounting, access control, and workflow checks using fictional data only."""

from datetime import date, datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from drbudget import store
from drbudget.web import create_app


NOW = datetime(2026, 3, 3, 8, tzinfo=timezone.utc)
FICTIONAL_NOTIFICATION = (
    "Credit Card XX0000 was used for AED42.35 on 02/03/2026 10:11:12 "
    "at FICTIONAL Example Market, Test City-AE. Available limit AED98765.43"
)


class AppTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "private"
        self.app = create_app(self.root, {"TESTING": True, "NOW": lambda: NOW})
        self.client = self.app.test_client()
        self.client.get("/register")
        response = self.post("/register", {"name": "Fictional Test Household", "email": "fictional@example.test",
                                           "password": "fictional-test-password"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.uid = session["user_id"]
        with self.db() as connection:
            self.hid = connection.execute("SELECT household_id FROM users WHERE id=?", (self.uid,)).fetchone()[0]
            connection.execute("UPDATE households SET start_date='2026-01-01' WHERE id=?", (self.hid,))
            self.food = store.add_category(connection, self.hid, "Fictional Food", "spending", True, 10000, "2026-01-01")
            self.travel = store.add_category(connection, self.hid, "Fictional Travel", "spending", False, 20000, "2026-01-01")
            self.health = store.add_category(connection, self.hid, "Fictional Uncapped", "spending", False, None, "2026-01-01")

    def db(self):
        return store.database(self.app.config["DATABASE"])

    def post(self, path, data=None, client=None, **kwargs):
        client = client or self.client
        with client.session_transaction() as session:
            csrf = session.get("csrf", "")
        return client.post(path, data={**(data or {}), "csrf": csrf}, **kwargs)

    def add(self, **values):
        payload = dict(date="2026-03-02", merchant="FICTIONAL Market", amount="12.50", currency="AED",
                       kind="expense", category_id=self.food, status="confirmed")
        payload.update(values)
        with self.db() as connection:
            return store.add_transaction(connection, self.hid, payload)[0]

    def report(self, day=date(2026, 3, 3)):
        with self.db() as connection:
            household = dict(connection.execute("SELECT * FROM households WHERE id=?", (self.hid,)).fetchone())
            return store.report(connection, household, day)

    def test_amounts_reject_rounding_and_nonfinite_numbers(self):
        self.assertEqual(store.amount("0.29"), 29)
        for value in ("1.001", "-1", "NaN", "Infinity", "not money"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                store.amount(value)

    def test_rollover_and_effective_dated_allocations(self):
        self.add(date="2026-01-12", amount="75")
        self.add(date="2026-02-10", amount="30")
        self.add(date="2026-02-15", amount="10", kind="refund")
        with self.db() as connection:
            connection.execute("INSERT INTO allocations VALUES (?, '2026-02-01', 12000)", (self.food,))
        report = self.report(date(2026, 2, 20))
        row = next(r for r in report["rows"] if r["id"] == self.food)
        self.assertEqual(row["remaining"], 12500)
        self.assertEqual(row["month_spent"], 2000)
        self.assertEqual(row["allocation"], 12000)
        self.assertEqual(self.report(date(2026, 1, 31))["rows"][0]["remaining"], 2500)

    def test_no_rollover_uncapped_and_foreign_currency(self):
        self.add(date="2026-02-01", amount="50", category_id=self.travel)
        self.add(amount="30", category_id=self.travel)
        self.add(amount="90", currency="EUR", category_id=self.travel)
        self.add(amount="15", category_id=self.health)
        report = self.report()
        lookup = {r["id"]: r for r in report["rows"]}
        self.assertEqual(lookup[self.travel]["remaining"], 17000)
        self.assertIsNone(lookup[self.health]["remaining"])
        self.assertEqual(report["foreign"][("EUR", "Fictional Travel")], 9000)

    def test_transfers_card_payments_offsets_pending_and_duplicates_excluded(self):
        for kind in ("transfer", "card_payment", "offset", "cash"):
            self.add(kind=kind, amount="90", merchant=kind)
        self.add(status="review", merchant="review")
        self.add(status="duplicate", merchant="duplicate")
        self.assertEqual(self.report()["rows"][0]["month_spent"], 0)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT status FROM transactions WHERE kind='cash'").fetchone()[0], "review")

    def test_income_types_separate_and_refunds_not_income(self):
        for kind in ("salary", "support", "interest"):
            self.add(kind=kind, category_id="", merchant=kind)
        self.add(kind="refund", amount="2")
        report = self.report()
        self.assertEqual(len(report["incomes"]), 3)
        self.assertEqual(report["incomes"][("AED", "salary")], 1250)
        self.assertEqual(report["rows"][0]["month_spent"], -200)

    def test_week_crossing_month_does_not_change_monthly_remaining(self):
        self.add(date="2026-02-28", amount="20", category_id=self.travel)
        self.add(date="2026-03-01", amount="10", category_id=self.travel)
        with self.db() as connection:
            connection.execute("UPDATE households SET period='week', week_start=0 WHERE id=?", (self.hid,))
        row = next(r for r in self.report(date(2026, 3, 1))["rows"] if r["id"] == self.travel)
        self.assertEqual(row["period_spent"], 3000)
        self.assertEqual(row["month_spent"], 1000)
        self.assertEqual(row["remaining"], 19000)

    def test_correction_moves_totals_once_preserves_source_and_records_audit(self):
        tid = self.add(category_id=self.travel)
        response = self.post(f"/transactions/{tid}", dict(category_id=self.food, kind="expense", status="confirmed", version="1"))
        self.assertEqual(response.status_code, 302)
        report = self.report()
        self.assertEqual(report["rows"][0]["month_spent"], 1250)
        self.assertEqual(report["rows"][1]["month_spent"], 0)
        with self.db() as connection:
            row = connection.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
            self.assertEqual((row["merchant"], row["amount"], row["currency"], row["manual"]),
                             ("FICTIONAL Market", 1250, "AED", 1))
            self.assertEqual(connection.execute("SELECT count(*) FROM corrections").fetchone()[0], 1)

    def test_old_form_cannot_overwrite_a_newer_correction(self):
        tid = self.add()
        payload = dict(category_id=self.travel, kind="expense", status="confirmed", version="1")
        self.post(f"/transactions/{tid}", payload)
        response = self.post(f"/transactions/{tid}", {**payload, "category_id": self.health})
        self.assertIn(b"another session", response.data)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT category_id FROM transactions WHERE id=?", (tid,)).fetchone()[0], self.travel)

    def test_link_scanning_does_not_modify_data(self):
        tid = self.add()
        self.client.get(f"/transactions/{tid}")
        self.client.head(f"/transactions/{tid}")
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT version FROM transactions WHERE id=?", (tid,)).fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM corrections").fetchone()[0], 0)

    def test_authentication_and_return_to_transaction_after_login(self):
        tid = self.add()
        anon = self.app.test_client()
        response = anon.get(f"/transactions/{tid}")
        self.assertEqual(response.status_code, 302)
        anon.get(response.location)
        response = self.post("/login", dict(email="fictional@example.test", password="fictional-test-password",
                                           next=f"/transactions/{tid}"), client=anon)
        self.assertTrue(response.location.endswith(tid))
        self.assertEqual(anon.get(response.location).status_code, 200)

    def test_other_household_cannot_view_edit_or_reference_categories(self):
        tid = self.add()
        other = self.app.test_client()
        other.get("/register")
        self.post("/register", dict(name="Other Fictional Household", email="other@example.test",
                                    password="another-fictional-password"), client=other)
        self.assertEqual(other.get(f"/transactions/{tid}").status_code, 404)
        self.assertEqual(self.post(f"/transactions/{tid}", dict(category_id=self.food, kind="expense", status="confirmed", version="1"), client=other).status_code, 404)
        response = self.post("/transactions", dict(date="2026-03-02", merchant="FICTIONAL", amount="1", currency="AED", kind="expense", category_id=self.food, status="confirmed"), client=other)
        self.assertIn(b"Choose a category from your household", response.data)

    def test_csrf_required_on_authenticated_and_login_forms(self):
        self.assertEqual(self.client.post("/demo").status_code, 400)
        anon = self.app.test_client()
        self.assertEqual(anon.post("/login", data={"email": "fictional@example.test"}).status_code, 400)

    def test_login_throttling_and_safe_redirect(self):
        anon = self.app.test_client()
        anon.get("/login")
        for _ in range(10):
            self.post("/login", dict(email="nobody@example.test", password="wrong"), client=anon)
        self.assertEqual(self.post("/login", dict(email="nobody@example.test", password="wrong"), client=anon).status_code, 429)
        response = self.client.get("/login?next=https://attacker.example")
        self.assertNotIn(b"https://attacker.example", response.data)

    def test_merchant_html_is_escaped_in_dashboard_and_email(self):
        self.add(merchant="<script>alert('fictional')</script>")
        for url in ("/", "/email-preview"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b"<script>", response.data)
            self.assertIn(b"&lt;script&gt;", response.data)
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_featured_order_and_complete_email(self):
        with self.db() as connection:
            connection.execute("UPDATE households SET featured=? WHERE id=?",
                               (json.dumps([self.health, self.food, self.travel]), self.hid))
            store.add_category(connection, self.hid, "Fictional Fourth Category", "savings", False, 60000, "2026-01-01")
        html = self.client.get("/email-preview").data.decode()
        first = html.split("Your complete budget")[0]
        self.assertLess(first.index("Fictional Uncapped"), first.index("Fictional Food"))
        self.assertLess(first.index("Fictional Food"), first.index("Fictional Travel"))
        self.assertIn("Fictional Fourth Category", html)
        self.assertNotIn("Fictional Fourth Category", first)

    def test_yesterday_uses_household_timezone(self):
        self.app.config["NOW"] = lambda: datetime(2026, 3, 2, 22, tzinfo=timezone.utc)
        self.add(merchant="FICTIONAL Yesterday", date="2026-03-02")
        self.add(merchant="FICTIONAL Two days ago", date="2026-03-01")
        html = self.client.get("/email-preview").data.decode()
        self.assertIn("FICTIONAL Yesterday", html)
        self.assertNotIn("FICTIONAL Two days ago", html)

    def test_import_is_atomic_and_idempotent_with_manual_override(self):
        header = "date,merchant,amount,currency,kind,account,source_id,category\n"
        record = "2026-03-02,FICTIONAL Imported Store,10.25,AED,expense,fake-account,fixture-1,Fictional Travel\n"
        def upload(contents):
            return self.post("/import", {"file": (io.BytesIO(contents.encode()), "fictional.csv")})
        upload(header + record)
        with self.db() as connection:
            txn = dict(connection.execute("SELECT * FROM transactions").fetchone())
        self.assertEqual(txn["status"], "review")
        self.post(f"/transactions/{txn['id']}", dict(category_id=self.food, kind="expense", status="confirmed", version="1"))
        upload(header + record)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT category_id FROM transactions").fetchone()[0], self.food)
        bad = record.replace("fixture-1", "fixture-2") + record.replace("fixture-1", "fixture-3").replace("10.25", "oops")
        upload(header + bad)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 1)
        for archived in (self.root / "statements").glob("*.csv"):
            self.assertEqual(archived.stat().st_mode & 0o777, 0o600)

    def test_similar_transaction_requires_review_not_silent_deletion(self):
        self.add()
        self.add()
        with self.db() as connection:
            rows = connection.execute("SELECT status FROM transactions ORDER BY rowid").fetchall()
        self.assertEqual([r[0] for r in rows], ["confirmed", "review"])

    def test_remembered_merchant_rule_requires_explicit_opt_in(self):
        tid = self.add(category_id=self.travel)
        self.post(f"/transactions/{tid}", dict(category_id=self.food, kind="expense", status="confirmed", version="1", remember="on"))
        new = self.add(category_id="", amount="11", date="2026-03-03")
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT category_id FROM transactions WHERE id=?", (new,)).fetchone()[0], self.food)

    def test_private_storage_is_not_served(self):
        for url in ("/finance.sqlite3", "/session.key", "/config/budget.json", "/statements/file.csv"):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_preferences_form_changes_featured_order_and_reporting_period(self):
        response = self.post("/settings", dict(currency="AED", start_date="2026-01-01",
            timezone="Asia/Dubai", monthly_budget="4000", period="week", week_start="6",
            featured_0=self.health, featured_1=self.travel, featured_2=self.food))
        self.assertEqual(response.status_code, 302)
        report = self.report()
        self.assertEqual([row["id"] for row in report["featured"]], [self.health, self.travel, self.food])
        self.assertEqual(report["period_label"], "Week to date")
        self.assertEqual(report["period_start"], date(2026, 3, 1))

    def test_allocation_form_preserves_earlier_months(self):
        response = self.post(f"/allocations/{self.food}", dict(month="2026-03-01", allocation="150"))
        self.assertEqual(response.status_code, 302)
        earlier = self.report(date(2026, 2, 28))["rows"][0]
        current = self.report()["rows"][0]
        self.assertEqual(earlier["allocation"], 10000)
        self.assertEqual(current["allocation"], 15000)
        self.assertEqual(current["remaining"], 35000)

    def test_pasted_notification_is_private_and_needs_review(self):
        response = self.post("/notifications", {"notification": FICTIONAL_NOTIFICATION})
        self.assertEqual(response.status_code, 302)
        with self.db() as connection:
            transaction = dict(connection.execute("SELECT * FROM transactions").fetchone())
            source = dict(connection.execute("SELECT * FROM notification_sources").fetchone())
            dump = "\n".join(connection.iterdump())
        self.assertEqual(transaction["amount"], 4235)
        self.assertEqual(transaction["status"], "review")
        self.assertEqual(transaction["household_id"], self.hid)
        self.assertEqual(source["occurred_at"], "2026-03-02T10:11:12+04:00")
        self.assertEqual(source["card_suffix"], "0000")
        self.assertNotIn("98765.43", dump)
        page = self.client.get(response.location)
        self.assertIn(b"Bank timestamp:", page.data)
        self.assertNotIn(b"98765.43", page.data)
        self.assertEqual(self.report()["rows"][0]["month_spent"], 0)
        self.post(response.location, dict(category_id=self.food, kind="expense", status="confirmed", version="1"))
        self.assertEqual(self.report()["rows"][0]["month_spent"], 4235)

    def test_repeated_paste_does_not_overwrite_manual_correction_or_inflate_totals(self):
        first = self.post("/notifications", {"notification": FICTIONAL_NOTIFICATION})
        self.post(first.location, dict(category_id=self.food, kind="expense", status="confirmed", version="1"))
        second = self.post("/notifications", {"notification": FICTIONAL_NOTIFICATION})
        self.assertNotEqual(first.location, second.location)
        with self.db() as connection:
            statuses = connection.execute("SELECT status, manual FROM transactions ORDER BY rowid").fetchall()
        self.assertEqual([tuple(row) for row in statuses], [("confirmed", 1), ("review", 0)])
        self.assertEqual(self.report()["rows"][0]["month_spent"], 4235)

    def test_invalid_notification_saves_nothing_and_does_not_echo_input(self):
        text = "FICTIONAL security code 000000"
        response = self.post("/notifications", {"notification": text}, follow_redirects=True)
        self.assertIn(b"Nothing was imported", response.data)
        self.assertNotIn(text.encode(), response.data)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM notification_sources").fetchone()[0], 0)

    def test_notification_import_requires_sign_in_and_csrf(self):
        self.assertEqual(self.client.post("/notifications", data={"notification": FICTIONAL_NOTIFICATION}).status_code, 400)
        anonymous = self.app.test_client()
        anonymous.get("/login")
        response = self.post("/notifications", {"notification": FICTIONAL_NOTIFICATION}, client=anonymous)
        self.assertIn("/login", response.location)
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM transactions").fetchone()[0], 0)

    def test_saved_merchant_rule_suggests_category_but_still_requires_review(self):
        with self.db() as connection:
            connection.execute("INSERT INTO merchant_rules VALUES (?, ?, ?)",
                               (self.hid, "fictional example market, test city-ae", self.food))
        self.post("/notifications", {"notification": FICTIONAL_NOTIFICATION})
        with self.db() as connection:
            row = connection.execute("SELECT category_id, status FROM transactions").fetchone()
        self.assertEqual(tuple(row), (self.food, "review"))


if __name__ == "__main__":
    unittest.main()
