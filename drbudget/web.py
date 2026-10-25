"""Authenticated local-first web interface. No bank connections or email sending."""

import csv
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from scripts.init_private_storage import check_regular_file, initialize
from . import store


def create_app(data_dir=None, config=None):
    root = initialize(Path(data_dir or os.environ["DRBUDGET_DATA_DIR"]))
    secret_path = root / "session.key"
    check_regular_file(secret_path)
    if not secret_path.exists():
        try:
            descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(secrets.token_bytes(48))
        except FileExistsError:
            pass
    secret_path.chmod(0o600)
    secret = secret_path.read_bytes()
    if len(secret) < 32:
        raise ValueError("The private session key is invalid.")
    app = Flask(__name__)
    public_url = os.environ.get("DRBUDGET_PUBLIC_URL", "").rstrip("/")
    if public_url:
        parsed = urlparse(public_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError("DRBUDGET_PUBLIC_URL must be a trusted HTTPS origin without a path.")
    app.config.update(
        SECRET_KEY=secret, DATABASE=root / "finance.sqlite3", DATA_DIR=root,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(public_url), PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024, PUBLIC_URL=public_url,
        ALLOW_REGISTRATION=os.environ.get("DRBUDGET_ALLOW_REGISTRATION", "1") == "1",
        NOW=lambda: datetime.now(timezone.utc),
        TRUSTED_HOSTS=["localhost", "127.0.0.1"] + ([urlparse(public_url).hostname] if public_url else []),
    )
    if config:
        app.config.update(config)
    with store.database(app.config["DATABASE"]) as connection:
        connection.executescript(store.SCHEMA)
    dummy_hash = generate_password_hash(secrets.token_hex(24))

    def db():
        return store.database(app.config["DATABASE"])

    def current_household():
        with db() as connection:
            return dict(connection.execute("SELECT * FROM households WHERE id=?", (g.user["household_id"],)).fetchone())

    def current_report():
        household = current_household()
        today = app.config["NOW"]().astimezone(ZoneInfo(household["timezone"])).date()
        with db() as connection:
            recap = store.report(connection, household, today)
        return household, recap

    def safe_next(value):
        return value if re.fullmatch(r"/transactions/[0-9a-f-]{36}", value or "") else "/"

    def authenticated(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            if not g.user:
                return redirect(url_for("login", next=safe_next(request.path)))
            return function(*args, **kwargs)
        return wrapper

    @app.before_request
    def protect_request():
        g.user = None
        if session.get("user_id"):
            with db() as connection:
                row = connection.execute("SELECT id, household_id, email FROM users WHERE id=?",
                                         (session["user_id"],)).fetchone()
                g.user = dict(row) if row else None
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        if request.method == "POST" and not hmac.compare_digest(
                session["csrf"].encode(), request.form.get("csrf", "").encode()):
            abort(400, description="This form expired. Reload the page and try again.")

    @app.after_request
    def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "script-src 'none'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.context_processor
    def shared_context():
        return {"csrf": session.get("csrf"), "user": g.user, "kinds": store.KINDS,
                "currencies": store.CURRENCIES}

    @app.template_filter("money")
    def money(value):
        return "Uncapped" if value is None else f"{value / 100:,.2f}"

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(429)
    def safe_error(error):
        messages = {400: "The form could not be processed. Reload and try again.",
                    403: "This action is not available.", 404: "This item is unavailable or belongs to another household.",
                    413: "Use a file smaller than 2 MB.", 429: "Too many sign-in attempts. Try again in 15 minutes."}
        return render_template("error.html", message=messages[error.code]), error.code

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if not app.config["ALLOW_REGISTRATION"]:
            abort(403)
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            name = request.form.get("name", "").strip()
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254 or not 12 <= len(password) <= 200 or not 1 <= len(name) <= 80:
                flash("Use a household name, valid email, and a password of 12–200 characters.", "error")
            else:
                hid, uid = str(uuid4()), str(uuid4())
                today = app.config["NOW"]().astimezone(ZoneInfo("Asia/Dubai")).date()
                try:
                    with db() as connection:
                        connection.execute("INSERT INTO households (id, name, start_date) VALUES (?, ?, ?)",
                                           (hid, name, today.replace(day=1).isoformat()))
                        connection.execute("INSERT INTO users VALUES (?, ?, ?, ?)",
                                           (uid, hid, email, generate_password_hash(password)))
                    session.clear()
                    session.update(user_id=uid, csrf=secrets.token_urlsafe(32))
                    session.permanent = True
                    return redirect(url_for("settings"))
                except sqlite3.IntegrityError:
                    flash("Could not create that account. Try signing in instead.", "error")
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        destination = safe_next(request.values.get("next", ""))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()[:254]
            # Limit both per address and per account; rotation of one cannot bypass both.
            identifiers = ["email:" + hashlib.sha256(email.encode()).hexdigest(), "ip:" + (request.remote_addr or "local")]
            now = time.time()
            with db() as connection:
                connection.execute("DELETE FROM login_attempts WHERE first_attempt<?", (now - 900,))
                for identifier in identifiers:
                    attempt = connection.execute("SELECT failures FROM login_attempts WHERE identifier=?", (identifier,)).fetchone()
                    if attempt and attempt["failures"] >= 10:
                        abort(429)
                row = connection.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                password = request.form.get("password", "")
                good = len(password) <= 200 and check_password_hash(row["password_hash"] if row else dummy_hash, password)
                if row and good:
                    for identifier in identifiers:
                        connection.execute("DELETE FROM login_attempts WHERE identifier=?", (identifier,))
                    session.clear()
                    session.update(user_id=row["id"], csrf=secrets.token_urlsafe(32))
                    session.permanent = True
                    return redirect(destination)
                for identifier in identifiers:
                    connection.execute("INSERT INTO login_attempts VALUES (?, 1, ?) ON CONFLICT(identifier) "
                                       "DO UPDATE SET failures=failures+1", (identifier, now))
            flash("Email or password was not recognized.", "error")
        return render_template("auth.html", mode="login", next=destination,
                               registration=app.config["ALLOW_REGISTRATION"])

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @authenticated
    def dashboard():
        household, recap = current_report()
        return render_template("dashboard.html", household=household, recap=recap)

    @app.route("/settings", methods=["GET", "POST"])
    @authenticated
    def settings():
        household, recap = current_report()
        if request.method == "POST":
            try:
                currency = request.form.get("currency")
                start = store.valid_date(request.form.get("start_date"), month=True).isoformat()
                zone = request.form.get("timezone", "")
                ZoneInfo(zone)
                period = request.form.get("period")
                week_start = int(request.form.get("week_start", "0"))
                featured = [request.form.get(f"featured_{i}", "") for i in range(3)]
                featured = [cid for cid in featured if cid]
                total = store.amount(request.form.get("monthly_budget", ""), optional=True)
                if currency not in store.CURRENCIES or period not in ("week", "month") or week_start not in range(7):
                    raise ValueError("Choose valid currency and reporting options.")
                if len(featured) != len(set(featured)) or (len(recap["rows"]) >= 3 and len(featured) != 3):
                    raise ValueError("Choose three different featured categories.")
                with db() as connection:
                    if any(not store.category_belongs(connection, household["id"], cid) for cid in featured):
                        raise ValueError("Choose your household's categories.")
                    has_transactions = connection.execute("SELECT 1 FROM transactions WHERE household_id=?", (household["id"],)).fetchone()
                    if has_transactions and (currency != household["currency"] or start != household["start_date"]):
                        raise ValueError("Start date and base currency are locked once transactions exist.")
                    connection.execute("UPDATE households SET currency=?, start_date=?, timezone=?, monthly_budget=?, "
                                       "period=?, week_start=?, featured=? WHERE id=?",
                                       (currency, start, zone, total, period, week_start, json.dumps(featured), household["id"]))
                flash("Budget and recap preferences saved.", "success")
                return redirect(url_for("settings"))
            except (ValueError, ZoneInfoNotFoundError):
                flash("Check your settings: choose three distinct categories when available, a valid timezone and first-of-month date. Start date and base currency cannot change after transactions exist.", "error")
        selected = json.loads(household["featured"]) or [r["id"] for r in recap["rows"][:3]]
        return render_template("settings.html", household=household, recap=recap, selected=selected)

    @app.post("/categories")
    @authenticated
    def new_category():
        household = current_household()
        try:
            with db() as connection:
                store.add_category(connection, household["id"], request.form.get("name", ""),
                                   request.form.get("kind"), request.form.get("rollover") == "on",
                                   store.amount(request.form.get("allocation", ""), optional=True),
                                   request.form.get("month", ""))
            flash("Category added.", "success")
        except (ValueError, sqlite3.IntegrityError):
            flash("Check the category name, amount, and effective month. Names must be unique.", "error")
        return redirect(url_for("settings"))

    @app.post("/allocations/<cid>")
    @authenticated
    def update_allocation(cid):
        household = current_household()
        try:
            month = store.valid_date(request.form.get("month"), month=True).isoformat()
            total = store.amount(request.form.get("allocation", ""), optional=True)
            with db() as connection:
                if not store.category_belongs(connection, household["id"], cid):
                    abort(404)
                connection.execute("INSERT INTO allocations VALUES (?, ?, ?) ON CONFLICT(category_id, month) "
                                   "DO UPDATE SET amount=excluded.amount", (cid, month, total))
            flash("Allocation saved from the selected month. Earlier allocations are preserved.", "success")
        except ValueError:
            flash("Use a valid amount and the first day of the effective month.", "error")
        return redirect(url_for("settings"))

    @app.route("/transactions", methods=["GET", "POST"])
    @authenticated
    def transactions():
        household, recap = current_report()
        if request.method == "POST":
            try:
                with db() as connection:
                    tid, _ = store.add_transaction(connection, household["id"], request.form)
                flash("Transaction recorded. Review its category and status below.", "success")
                return redirect(url_for("transaction", tid=tid))
            except ValueError as error:
                flash(str(error), "error")
        return render_template("transactions.html", household=household, recap=recap)

    @app.route("/transactions/<tid>", methods=["GET", "POST"])
    @authenticated
    def transaction(tid):
        household = current_household()
        if request.method == "POST":
            try:
                with db() as connection:
                    store.correct_transaction(connection, household["id"], g.user["id"], tid,
                                              request.form.get("category_id"), request.form.get("kind"),
                                              request.form.get("status"), int(request.form.get("version", "0")),
                                              request.form.get("remember") == "on")
                flash("Correction saved. Budget totals and future recaps are updated.", "success")
                return redirect(url_for("transaction", tid=tid))
            except LookupError:
                abort(404)
            except ValueError as error:
                flash(str(error), "error")
        with db() as connection:
            row = connection.execute("SELECT * FROM transactions WHERE id=? AND household_id=?", (tid, household["id"])).fetchone()
            if not row:
                abort(404)
            cats = store.categories(connection, household["id"])
            changes = connection.execute("SELECT created_at FROM corrections WHERE transaction_id=? AND household_id=? ORDER BY rowid DESC",
                                         (tid, household["id"])).fetchall()
        return render_template("transaction.html", household=household, txn=row, categories=cats, changes=changes)

    @app.post("/import")
    @authenticated
    def import_csv():
        household = current_household()
        upload = request.files.get("file")
        try:
            if not upload:
                raise ValueError
            raw = upload.read()
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
            required = {"date", "merchant", "amount", "currency", "kind", "account", "source_id"}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError
            inserted, skipped = 0, 0
            with db() as connection:
                cat_map = {c["name"].casefold(): c["id"] for c in store.categories(connection, household["id"])}
                for index, record in enumerate(reader):
                    if index >= 5000 or not record.get("account") or not record.get("source_id"):
                        raise ValueError
                    values = dict(record)
                    values["category_id"] = cat_map.get((record.get("category") or "").strip().casefold())
                    values["status"] = "review"  # Every new import requires explicit confirmation.
                    key = json.dumps([record["account"], record["source_id"]])
                    _, new = store.add_transaction(connection, household["id"], values, source_key=key)
                    inserted += int(new)
                    skipped += int(not new)
                if not inserted and not skipped:
                    raise ValueError
                # Original uploads are private, randomly named, and owner-readable only.
                archive = app.config["DATA_DIR"] / "statements" / f"{uuid4()}.csv"
                descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(raw)
            flash(f"Imported {inserted} transactions for review; skipped {skipped} existing records.", "success")
        except (ValueError, UnicodeError, csv.Error, TypeError):
            flash("Import rejected. Use the documented CSV columns, positive amounts, valid dates, and stable account/source identifiers. No transactions were imported.", "error")
        return redirect(url_for("transactions"))

    @app.get("/email-preview")
    @authenticated
    def email_preview():
        household, recap = current_report()
        return render_template("email.html", household=household, recap=recap,
                               public_url=app.config["PUBLIC_URL"],
                               generated=app.config["NOW"]().astimezone(ZoneInfo(household["timezone"])))

    @app.post("/demo")
    @authenticated
    def load_demo():
        household, recap = current_report()
        with db() as connection:
            if recap["rows"] or connection.execute("SELECT 1 FROM transactions WHERE household_id=?", (household["id"],)).fetchone():
                flash("Demo data is available only in an empty household.", "error")
                return redirect(url_for("dashboard"))
            month = recap["yesterday_date"].replace(day=1).isoformat()
            ids = [store.add_category(connection, household["id"], name, "spending", True, cap, month)
                   for name, cap in (("Food at home", 90000), ("Getting around", 65000), ("Home essentials", 40000))]
            store.add_category(connection, household["id"], "Long-term savings", "savings", False, 50000, month)
            store.add_category(connection, household["id"], "Wellbeing", "spending", False, None, month)
            connection.execute("UPDATE households SET demo=1, start_date=?, monthly_budget=245000, featured=? WHERE id=?",
                               (month, json.dumps(ids), household["id"]))
            for merchant, value, cid, kind, currency in (
                ("FICTIONAL • Orchard Market", "132.50", ids[1], "expense", household["currency"]),
                ("FICTIONAL • City Rail", "24.00", ids[1], "expense", household["currency"]),
                ("FICTIONAL • Home Supply", "85.25", ids[2], "expense", household["currency"]),
                ("FICTIONAL • Sample Salary", "2450.00", None, "salary", household["currency"]),
                ("FICTIONAL • Abroad Café", "12.50", ids[0], "expense", "EUR" if household["currency"] != "EUR" else "USD"),
            ):
                store.add_transaction(connection, household["id"], dict(
                    date=recap["yesterday_date"].isoformat(), merchant=merchant, amount=value,
                    category_id=cid, kind=kind, currency=currency, status="confirmed"))
        flash("Fictional demo loaded. Try moving Orchard Market into Food at home.", "success")
        return redirect(url_for("dashboard"))

    return app
