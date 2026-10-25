"""Parse a supported ADCB credit-card purchase body; this does not capture alerts."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import re
from zoneinfo import ZoneInfo

from . import store


PARSER_VERSION = "adcb-card-purchase-v1"
MONEY = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)\.[0-9]{2}"
PURCHASE_START = r"Credit\s+Card\s+[X*•]{2,}\s*[0-9]{4}\s+was\s+used\s+for\b"
PURCHASE = re.compile(
    r"(?:ADCBAlert\s*:?\s*)?Credit\s+Card\s+[X*•]{2,}\s*(?P<card>[0-9]{4})"
    r"\s+was\s+used\s+for\s+(?P<currency>[A-Z]{3})\s*(?P<amount>" + MONEY + r")"
    r"\s+on\s+(?P<day>[0-9]{2}/[0-9]{2}/[0-9]{4})"
    r"\s+(?P<time>[0-9]{2}:[0-9]{2}:[0-9]{2})\s+at\s+(?P<merchant>.+?)"
    r"\.\s+Available\s+limit\s+[A-Z]{3}\s*" + MONEY + r"\.?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CardPurchase:
    amount_minor: int
    currency: str
    merchant: str
    card_suffix: str
    occurred_at: datetime

    def ledger_values(self, household_timezone):
        return {
            "amount": str(Decimal(self.amount_minor) / 100),
            "currency": self.currency,
            "merchant": self.merchant,
            "account": f"ADCB credit card ending {self.card_suffix}",
            "date": self.occurred_at.astimezone(ZoneInfo(household_timezone)).date().isoformat(),
            "kind": "expense",
            "status": "review",
        }


def parse_card_purchase(body, source_timezone="Asia/Dubai"):
    """Accept one complete supported purchase, never guess another alert type.

    Bank timestamps are interpreted in source_timezone, independently of the
    household's reporting timezone. The available credit limit is discarded.
    Exceptions contain no notification text or financial values.
    """
    if not isinstance(body, str) or not body.strip() or len(body) > 4096:
        raise ValueError("Paste one complete notification, up to 4096 characters.")
    text = " ".join(body.split())
    if len(re.findall(PURCHASE_START, text, re.IGNORECASE)) != 1:
        raise ValueError("Use one supported credit-card purchase notification at a time.")
    match = PURCHASE.fullmatch(text)
    if not match:
        raise ValueError("This notification is incomplete or uses an unsupported format.")
    currency = match["currency"].upper()
    merchant = match["merchant"].strip()
    if currency not in store.CURRENCIES or not merchant or len(merchant) > 240:
        raise ValueError("The currency or merchant description is unsupported.")
    try:
        occurred_at = datetime.strptime(match["day"] + " " + match["time"], "%d/%m/%Y %H:%M:%S")
        store.valid_date(occurred_at.date().isoformat())
        total = store.amount(match["amount"].replace(",", ""))
        if not total:
            raise ValueError
    except ValueError:
        raise ValueError("The purchase amount or transaction timestamp is invalid.") from None
    return CardPurchase(total, currency, merchant, match["card"],
                        occurred_at.replace(tzinfo=ZoneInfo(source_timezone)))
