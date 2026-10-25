"""SQLite ledger. Monetary values are always integer minor units."""

from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import json
import sqlite3
from uuid import uuid4


KINDS = {
    "expense": "Spending", "refund": "Refund", "salary": "Salary income",
    "support": "Government / family support", "interest": "Interest income",
    "transfer": "Internal transfer", "card_payment": "Card repayment",
    "cash": "Cash withdrawal — needs explanation", "offset": "Separate credit / offset",
}
CURRENCIES = ("AED", "EUR", "USD", "GBP")
EXCLUDED = {"transfer", "card_payment", "offset"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS households (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'AED',
 timezone TEXT NOT NULL DEFAULT 'Asia/Dubai', start_date TEXT NOT NULL,
 monthly_budget INTEGER, period TEXT NOT NULL DEFAULT 'month',
 week_start INTEGER NOT NULL DEFAULT 0, featured TEXT NOT NULL DEFAULT '[]',
 demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, household_id TEXT NOT NULL REFERENCES households(id),
 email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
 id TEXT PRIMARY KEY, household_id TEXT NOT NULL REFERENCES households(id),
 name TEXT NOT NULL, kind TEXT NOT NULL, rollover INTEGER NOT NULL DEFAULT 0,
 UNIQUE(household_id, name)
);
CREATE TABLE IF NOT EXISTS allocations (
 category_id TEXT NOT NULL REFERENCES categories(id), month TEXT NOT NULL,
 amount INTEGER, PRIMARY KEY(category_id, month)
);
CREATE TABLE IF NOT EXISTS transactions (
 id TEXT PRIMARY KEY, household_id TEXT NOT NULL REFERENCES households(id),
 date TEXT NOT NULL, merchant TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount > 0),
 currency TEXT NOT NULL, kind TEXT NOT NULL,
 category_id TEXT REFERENCES categories(id), status TEXT NOT NULL,
 account TEXT NOT NULL DEFAULT '', source_key TEXT,
 manual INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(household_id, source_key)
);
CREATE INDEX IF NOT EXISTS transactions_household_date ON transactions(household_id, date);
CREATE TABLE IF NOT EXISTS notification_sources (
 transaction_id TEXT PRIMARY KEY REFERENCES transactions(id),
 household_id TEXT NOT NULL REFERENCES households(id),
 occurred_at TEXT NOT NULL, card_suffix TEXT NOT NULL, parser_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS screenshot_batches (
 id TEXT PRIMARY KEY, household_id TEXT NOT NULL REFERENCES households(id),
 image_hash TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at REAL,
 attempt INTEGER NOT NULL DEFAULT 0, error TEXT, ocr_text TEXT,
 UNIQUE(household_id, image_hash)
);
CREATE TABLE IF NOT EXISTS screenshot_items (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES screenshot_batches(id),
 household_id TEXT NOT NULL REFERENCES households(id), position INTEGER NOT NULL,
 original_text TEXT NOT NULL, edited_text TEXT, parsed_json TEXT, error TEXT,
 state TEXT NOT NULL DEFAULT 'pending', version INTEGER NOT NULL DEFAULT 1,
 transaction_id TEXT REFERENCES transactions(id)
);
CREATE INDEX IF NOT EXISTS screenshot_items_batch ON screenshot_items(batch_id, position);
CREATE TABLE IF NOT EXISTS merchant_rules (
 household_id TEXT NOT NULL REFERENCES households(id), merchant TEXT NOT NULL,
 category_id TEXT NOT NULL REFERENCES categories(id), PRIMARY KEY(household_id, merchant)
);
CREATE TABLE IF NOT EXISTS corrections (
 id TEXT PRIMARY KEY, household_id TEXT NOT NULL REFERENCES households(id),
 transaction_id TEXT NOT NULL REFERENCES transactions(id),
 user_id TEXT NOT NULL REFERENCES users(id), before_json TEXT NOT NULL,
 after_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS login_attempts (
 identifier TEXT PRIMARY KEY, failures INTEGER NOT NULL, first_attempt REAL NOT NULL
);
"""


@contextmanager
def database(path):
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def amount(value, optional=False):
    if optional and str(value).strip() == "":
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > 1_000_000_000:
            raise ValueError
        minor = number * 100
        if minor != minor.to_integral_value():
            raise ValueError
        return int(minor)
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a non-negative amount with at most two decimal places.") from None


def valid_date(value, month=False):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or parsed.year < 2000 or parsed.year > 2100:
            raise ValueError
        if month and parsed.day != 1:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise ValueError("Use YYYY-MM-DD, with the first day of the month for allocations.") from None


def categories(connection, household):
    return [dict(row) for row in connection.execute(
        "SELECT * FROM categories WHERE household_id=? ORDER BY rowid", (household,))]


def category_belongs(connection, household, category_id):
    return connection.execute("SELECT 1 FROM categories WHERE id=? AND household_id=?",
                              (category_id, household)).fetchone() is not None


def add_category(connection, household, name, kind, rollover, allocation, month):
    name = name.strip()
    if not name or len(name) > 80 or kind not in ("spending", "savings"):
        raise ValueError("Use a category name of 1–80 characters and a valid category type.")
    valid_date(month, month=True)
    cid = str(uuid4())
    connection.execute("INSERT INTO categories VALUES (?, ?, ?, ?, ?)",
                       (cid, household, name, kind, int(rollover)))
    connection.execute("INSERT INTO allocations VALUES (?, ?, ?)", (cid, month, allocation))
    return cid


def normalize_merchant(merchant):
    return " ".join(merchant.casefold().split())


def add_transaction(connection, household, values, source_key=None):
    # A known external identifier is idempotent and never overwrites manual edits.
    if source_key:
        existing = connection.execute(
            "SELECT id FROM transactions WHERE household_id=? AND source_key=?",
            (household, source_key)).fetchone()
        if existing:
            return existing["id"], False
    day = valid_date(values.get("date", "")).isoformat()
    merchant = values.get("merchant", "").strip()
    total = amount(values.get("amount", ""))
    currency = values.get("currency", "").upper()
    kind = values.get("kind", "expense")
    status = values.get("status", "review")
    account = values.get("account", "").strip()
    cid = values.get("category_id") or None
    if not merchant or len(merchant) > 240 or len(account) > 80 or not total:
        raise ValueError("Provide a description (up to 240 characters) and a positive amount.")
    if currency not in CURRENCIES or kind not in KINDS or status not in ("confirmed", "review", "duplicate"):
        raise ValueError("Choose a supported currency, transaction type, and review status.")
    if cid and not category_belongs(connection, household, cid):
        raise ValueError("Choose a category from your household.")
    if not cid and kind in ("expense", "refund"):
        rule = connection.execute(
            "SELECT category_id FROM merchant_rules WHERE household_id=? AND merchant=?",
            (household, normalize_merchant(merchant))).fetchone()
        cid = rule["category_id"] if rule else None
    if (kind in ("expense", "refund") and not cid) or kind == "cash":
        status = "review"
    similar = connection.execute(
        "SELECT 1 FROM transactions WHERE household_id=? AND date=? AND merchant=? "
        "AND amount=? AND currency=? AND account=? AND kind=?",
        (household, day, merchant, total, currency, account, kind)).fetchone()
    if similar:
        status = "review"
    tid = str(uuid4())
    connection.execute(
        "INSERT INTO transactions (id, household_id, date, merchant, amount, currency, "
        "kind, category_id, status, account, source_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, household, day, merchant, total, currency, kind, cid, status, account, source_key))
    return tid, True


def correct_transaction(connection, household, user, tid, cid, kind, status, version, remember=False):
    row = connection.execute("SELECT * FROM transactions WHERE id=? AND household_id=?",
                             (tid, household)).fetchone()
    if not row:
        raise LookupError("Transaction not found.")
    if not connection.execute("SELECT 1 FROM users WHERE id=? AND household_id=?",
                              (user, household)).fetchone():
        raise LookupError("Household member not found.")
    cid = cid or None
    if cid and not category_belongs(connection, household, cid):
        raise ValueError("Choose a category from your household.")
    if kind not in KINDS or status not in ("confirmed", "review", "duplicate"):
        raise ValueError("Choose a valid type and review status.")
    if status == "confirmed" and ((kind in ("expense", "refund") and not cid) or kind == "cash"):
        raise ValueError("Explain the transaction type and choose a category before confirming spending.")
    after = {"category_id": cid, "kind": kind, "status": status}
    before = {key: row[key] for key in after}
    updated = connection.execute(
        "UPDATE transactions SET category_id=?, kind=?, status=?, manual=1, version=version+1 "
        "WHERE id=? AND household_id=? AND version=?",
        (cid, kind, status, tid, household, version))
    if updated.rowcount != 1:
        raise ValueError("This transaction changed in another session. Reload before saving.")
    connection.execute("INSERT INTO corrections (id, household_id, transaction_id, user_id, "
                       "before_json, after_json) VALUES (?, ?, ?, ?, ?, ?)",
                       (str(uuid4()), household, tid, user, json.dumps(before), json.dumps(after)))
    if remember:
        if not cid or kind not in ("expense", "refund"):
            raise ValueError("Merchant rules need a spending or refund category.")
        connection.execute("INSERT INTO merchant_rules VALUES (?, ?, ?) ON CONFLICT(household_id, merchant) "
                           "DO UPDATE SET category_id=excluded.category_id",
                           (household, normalize_merchant(row["merchant"]), cid))


def next_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def report(connection, household, today):
    start = date.fromisoformat(household["start_date"])
    month = today.replace(day=1)
    period_start = month if household["period"] == "month" else today - timedelta(
        days=(today.weekday() - household["week_start"]) % 7)
    txns = [dict(row) for row in connection.execute(
        "SELECT t.*, c.name AS category_name FROM transactions t LEFT JOIN categories c ON c.id=t.category_id "
        "WHERE t.household_id=? AND t.date<=? ORDER BY t.date DESC, t.rowid DESC",
        (household["id"], today.isoformat()))]
    eligible = [t for t in txns if t["status"] == "confirmed" and t["date"] >= start.isoformat()]
    rows = []
    for category in categories(connection, household["id"]):
        history = list(connection.execute("SELECT month, amount FROM allocations WHERE category_id=? ORDER BY month",
                                          (category["id"],)))
        spending = [t for t in eligible if t["category_id"] == category["id"]
                    and t["currency"] == household["currency"] and t["kind"] in ("expense", "refund")]

        def spent(since, until):
            return sum(t["amount"] * (-1 if t["kind"] == "refund" else 1) for t in spending
                       if since.isoformat() <= t["date"] <= until.isoformat())

        carry, current_allocation, remaining = 0, None, None
        cursor = start.replace(day=1)
        while cursor <= month:
            applicable = [r for r in history if r["month"] <= cursor.isoformat()]
            cap = applicable[-1]["amount"] if applicable else None
            end = min(next_month(cursor) - timedelta(days=1), today)
            remainder = None if cap is None else cap + (carry if category["rollover"] else 0) - spent(cursor, end)
            carry = remainder if remainder is not None else 0
            if cursor == month:
                current_allocation, remaining = cap, remainder
            cursor = next_month(cursor)
        rows.append({**category, "allocation": current_allocation, "remaining": remaining,
                     "month_spent": spent(month, today), "period_spent": spent(max(start, period_start), today)})
    lookup = {r["id"]: r for r in rows}
    chosen = json.loads(household["featured"])
    featured = [lookup[cid] for cid in chosen if cid in lookup] if chosen else rows[:3]
    foreign = {}
    incomes = {}
    for txn in eligible:
        if txn["date"] < month.isoformat():
            continue
        if txn["kind"] in ("salary", "support", "interest"):
            key = (txn["currency"], txn["kind"])
            incomes[key] = incomes.get(key, 0) + txn["amount"]
        if txn["currency"] != household["currency"] and txn["kind"] in ("expense", "refund"):
            key = (txn["currency"], txn["category_name"] or "Uncategorized")
            foreign[key] = foreign.get(key, 0) + txn["amount"] * (-1 if txn["kind"] == "refund" else 1)
    return {"rows": rows, "featured": featured, "transactions": txns,
            "yesterday": [t for t in txns if t["date"] == (today - timedelta(days=1)).isoformat()],
            "yesterday_date": today - timedelta(days=1), "today": today,
            "period_label": "Month to date" if household["period"] == "month" else "Week to date",
            "period_start": period_start, "foreign": foreign, "incomes": incomes,
            "pending": sum(t["status"] == "review" for t in txns),
            "allocated": sum(r["allocation"] or 0 for r in rows)}
