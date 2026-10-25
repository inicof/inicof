"""Parser checks use wholly fictional values, never uploaded financial records."""

from datetime import timedelta
import unittest

from drbudget.notifications import parse_card_purchase


FICTIONAL_PURCHASE = (
    "Credit Card XX0000 was used for AED42.35 on 12/04/2025 10:11:12 "
    "at FICTIONAL Example Market, Test City-AE. Available limit AED98765.43"
)


class NotificationParserTests(unittest.TestCase):
    def test_purchase_amount_is_distinct_from_available_credit(self):
        purchase = parse_card_purchase(FICTIONAL_PURCHASE)
        self.assertEqual(purchase.amount_minor, 4235)
        self.assertEqual(purchase.currency, "AED")
        self.assertEqual(purchase.card_suffix, "0000")
        self.assertEqual(purchase.merchant, "FICTIONAL Example Market, Test City-AE")
        self.assertEqual(purchase.occurred_at.isoformat(), "2025-04-12T10:11:12+04:00")
        self.assertNotIn("98765.43", repr(purchase))

    def test_title_wrapping_grouped_amount_and_punctuation(self):
        text = "ADCBAlert\n" + FICTIONAL_PURCHASE.replace("AED42.35", "AED 1,234.56").replace(
            "Example Market", "Example.shop").replace(" was used for ", "\nwas used for\n")
        purchase = parse_card_purchase(text + ".")
        self.assertEqual(purchase.amount_minor, 123456)
        self.assertIn("Example.shop", purchase.merchant)

    def test_purchase_currency_can_differ_from_card_limit_currency(self):
        purchase = parse_card_purchase(FICTIONAL_PURCHASE.replace("AED42.35", "EUR42.35"))
        self.assertEqual(purchase.currency, "EUR")
        self.assertEqual(purchase.amount_minor, 4235)

    def test_household_date_converts_from_bank_timezone(self):
        purchase = parse_card_purchase(FICTIONAL_PURCHASE.replace("10:11:12", "01:11:12"))
        values = purchase.ledger_values("America/Los_Angeles")
        self.assertEqual(values["date"], "2025-04-11")
        self.assertEqual(values["amount"], "42.35")
        self.assertEqual(values["status"], "review")
        self.assertEqual(purchase.occurred_at.utcoffset(), timedelta(hours=4))

    def test_invalid_amounts_dates_and_times_fail_without_echoing_message(self):
        variants = [FICTIONAL_PURCHASE.replace("42.35", value) for value in
                    ("42.350", "4,2.35", "-42.35", "0.00", "NaN", "99999999999.00")]
        variants += [FICTIONAL_PURCHASE.replace("12/04/2025", "31/02/2025"),
                     FICTIONAL_PURCHASE.replace("10:11:12", "25:11:12")]
        for text in variants:
            with self.subTest(text=text), self.assertRaises(ValueError) as raised:
                parse_card_purchase(text)
            self.assertNotIn("Example Market", str(raised.exception))

    def test_truncated_or_stacked_notifications_are_rejected(self):
        for text in (FICTIONAL_PURCHASE.split(". Available")[0],
                     FICTIONAL_PURCHASE + "\n" + FICTIONAL_PURCHASE,
                     "at FICTIONAL Store. Available limit AED98765.43"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_card_purchase(text)

    def test_security_messages_and_other_transaction_types_are_not_purchases(self):
        for text in ("Your fictional verification code is 000000", "Credit Card payment received",
                     "Refund AED42.35", FICTIONAL_PURCHASE.replace("was used for", "was declined for"),
                     FICTIONAL_PURCHASE + " Your fictional OTP is 000000", "", "x" * 4097):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_card_purchase(text)

    def test_two_purchases_at_one_merchant_preserve_distinct_amounts_and_times(self):
        first = parse_card_purchase(FICTIONAL_PURCHASE)
        second = parse_card_purchase(FICTIONAL_PURCHASE.replace("42.35", "43.35").replace("10:11:12", "10:21:12"))
        self.assertEqual(first.merchant, second.merchant)
        self.assertNotEqual(first.amount_minor, second.amount_minor)
        self.assertNotEqual(first.occurred_at, second.occurred_at)


if __name__ == "__main__":
    unittest.main()
