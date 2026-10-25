"""Private screenshot ingestion, local OCR, and review staging. No auto-posting."""

from dataclasses import asdict
from datetime import datetime
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4
import warnings

from PIL import Image, ImageOps, ImageStat, UnidentifiedImageError

from . import store
from .notifications import CardPurchase, PARSER_VERSION, PURCHASE, parse_card_purchase


MAX_UPLOAD_BYTES = 6 * 1024 * 1024
MAX_PIXELS = 12_000_000
MAX_ITEMS = 40


def normalize_image(raw):
    """Decode validated raster pixels, orient, and re-encode without metadata."""
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Upload a PNG, JPEG, or WebP image no larger than 6 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in ("PNG", "JPEG", "WEBP") or getattr(source, "n_frames", 1) != 1:
                    raise ValueError("Use a single PNG, JPEG, or WebP image, not an animation.")
                if source.width * source.height > MAX_PIXELS or min(source.size) < 100 or max(source.size) > 16000:
                    raise ValueError("Use a clear screenshot between 100 pixels per side and 12 megapixels.")
                source.load()
                oriented = ImageOps.exif_transpose(source).convert("RGBA")
                flattened = Image.new("RGBA", oriented.size, "white")
                flattened.alpha_composite(oriented)
                pixels = flattened.convert("RGB")
                pixels.info.clear()
                output = io.BytesIO()
                pixels.save(output, format="PNG")
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("The image could not be safely decoded. Export a fresh PNG or JPEG screenshot.") from None


def prepare_ocr(image_bytes):
    with Image.open(io.BytesIO(image_bytes)) as source:
        image = ImageOps.grayscale(source)
        # Dark notification themes become dark text on a light background.
        if ImageStat.Stat(image).mean[0] < 127:
            image = ImageOps.invert(image)
        image = ImageOps.autocontrast(image)
        scale = min(2, 1400 / image.width, (8_000_000 / (image.width * image.height)) ** .5)
        if scale != 1:
            image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


def local_ocr(image_bytes):
    """A bounded local process; bank image contents never leave this machine."""
    try:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6", "tsv"],
            input=prepare_ocr(image_bytes), capture_output=True, timeout=25,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"}, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Text extraction failed or timed out. Your image is saved; retry or use a clearer screenshot.") from None
    lines = {}
    for word in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", errors="replace")), delimiter="\t"):
        if word.get("level") != "5" or not (word.get("text") or "").strip():
            continue
        key = tuple(word.get(field) for field in ("page_num", "block_num", "par_num", "line_num"))
        lines.setdefault(key, []).append(word["text"])
    text = "\n".join(" ".join(words) for words in lines.values())
    if len(text) > 50000:
        raise ValueError("Too much text in one image. Upload smaller screenshots.")
    return text


def serialized_purchase(purchase):
    return json.dumps({**asdict(purchase), "occurred_at": purchase.occurred_at.isoformat()})


def restored_purchase(data):
    fields = json.loads(data)
    fields["occurred_at"] = datetime.fromisoformat(fields["occurred_at"])
    return CardPurchase(**fields)


def extract_candidates(text):
    """Preserve incomplete financial fragments alongside complete purchases."""
    normalized = " ".join(text.split())
    starts = list(re.finditer(r"\bCredit\s+Card\b", normalized, re.IGNORECASE))
    candidates = []
    bank_hint = re.compile(r"Available\s+limit|was\s+used|(?:AED|EUR|USD|GBP)\s*[0-9]", re.IGNORECASE)

    def append(fragment, try_parse=True):
        if not fragment.strip():
            return
        try:
            purchase = parse_card_purchase(fragment) if try_parse else None
            if purchase is None:
                raise ValueError("This part is incomplete or unsupported. Compare it with the image or add a manual transaction.")
            candidates.append((fragment, serialized_purchase(purchase), None))
        except ValueError as error:
            candidates.append((fragment, None, str(error)))

    if not starts:
        append(normalized or "No readable transaction text found.", try_parse=False)
    else:
        if bank_hint.search(normalized[:starts[0].start()]):
            append(normalized[:starts[0].start()], try_parse=False)
        for index, start in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(normalized)
            fragment = normalized[start.start():end]
            match = PURCHASE.match(fragment)
            if match:
                append(match.group())
                remainder = fragment[match.end():]
                if bank_hint.search(remainder):
                    append(remainder, try_parse=False)
            else:
                append(fragment)
    if len(candidates) > MAX_ITEMS:
        raise ValueError("Too many entries in one image. Upload smaller screenshots.")
    return candidates


def stage_upload(database_path, private_root, household, raw):
    normalized = normalize_image(raw)
    digest = hashlib.sha256(normalized).hexdigest()
    bid = str(uuid4())
    with store.database(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT id FROM screenshot_batches WHERE household_id=? AND image_hash=?",
                                      (household, digest)).fetchone()
        if existing:
            return existing["id"], False
        path = Path(private_root) / "screenshots" / f"{bid}.png"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(normalized)
            connection.execute("INSERT INTO screenshot_batches (id, household_id, image_hash) VALUES (?, ?, ?)",
                               (bid, household, digest))
        except Exception:
            path.unlink(missing_ok=True)  # Only this newly created, uncommitted image.
            raise
    return bid, True


def process_batch(database_path, private_root, household, bid):
    with store.database(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        batch = connection.execute("SELECT * FROM screenshot_batches WHERE id=? AND household_id=?",
                                   (bid, household)).fetchone()
        if not batch:
            raise LookupError("Screenshot not found.")
        stale = batch["state"] == "processing" and (batch["started_at"] or 0) < time.time() - 60
        if batch["state"] not in ("queued", "failed") and not stale:
            return False
        attempt = batch["attempt"] + 1
        connection.execute("UPDATE screenshot_batches SET state='processing', started_at=?, attempt=?, error=NULL WHERE id=?",
                           (time.time(), attempt, bid))
    try:
        text = local_ocr((Path(private_root) / "screenshots" / f"{bid}.png").read_bytes())
        candidates = extract_candidates(text)
        with store.database(database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT attempt FROM screenshot_batches WHERE id=?", (bid,)).fetchone()
            if current["attempt"] != attempt:
                return False
            for index, (fragment, parsed, error) in enumerate(candidates):
                connection.execute("INSERT INTO screenshot_items (id, batch_id, household_id, position, original_text, parsed_json, error) "
                                   "VALUES (?, ?, ?, ?, ?, ?, ?)", (str(uuid4()), bid, household, index, fragment, parsed, error))
            connection.execute("UPDATE screenshot_batches SET state='review', ocr_text=?, error=NULL WHERE id=?", (text, bid))
    except (ValueError, OSError):
        with store.database(database_path) as connection:
            connection.execute("UPDATE screenshot_batches SET state='failed', error=? WHERE id=? AND attempt=?",
                               ("Text extraction did not complete. Retry, or upload a clearer image. No transactions were added.", bid, attempt))
        return False
    return True


def possible_matches(connection, household, purchase, household_timezone):
    values = purchase.ledger_values(household_timezone)
    rows = connection.execute(
        "SELECT t.*, n.occurred_at, n.card_suffix FROM transactions t LEFT JOIN notification_sources n ON n.transaction_id=t.id "
        "WHERE t.household_id=? AND t.amount=? AND t.currency=? AND (t.date=? OR n.occurred_at=?)",
        (household, purchase.amount_minor, purchase.currency, values["date"], purchase.occurred_at.isoformat())).fetchall()
    matches = []
    for row in rows:
        if store.normalize_merchant(row["merchant"]) != store.normalize_merchant(purchase.merchant):
            continue
        # Complete notification timestamps distinguish separate same-day purchases.
        if row["occurred_at"] and (row["occurred_at"] != purchase.occurred_at.isoformat() or row["card_suffix"] != purchase.card_suffix):
            continue
        matches.append(dict(row))
    return matches


def finish_batch_if_reviewed(connection, bid):
    remaining = connection.execute("SELECT 1 FROM screenshot_items WHERE batch_id=? AND state='pending'", (bid,)).fetchone()
    if not remaining:
        connection.execute("UPDATE screenshot_batches SET state='complete' WHERE id=?", (bid,))


def act_on_item(connection, household, user, item_id, form):
    """Serialized, versioned decisions prevent a double click from posting twice."""
    connection.execute("BEGIN IMMEDIATE")
    item = connection.execute("SELECT * FROM screenshot_items WHERE id=? AND household_id=?", (item_id, household["id"])).fetchone()
    if not item:
        raise LookupError("Screenshot entry not found.")
    if item["state"] != "pending":
        return item["batch_id"], item["transaction_id"]
    try:
        version = int(form.get("version", "0"))
    except (ValueError, TypeError):
        raise ValueError("Reload this review before saving your decision.") from None
    if version != item["version"]:
        raise ValueError("This entry changed in another tab. Reload before reviewing it.")
    action = form.get("action")
    if action == "reparse":
        text = form.get("text", "")
        purchase = parse_card_purchase(text)
        connection.execute("UPDATE screenshot_items SET edited_text=?, parsed_json=?, error=NULL, version=version+1 WHERE id=?",
                           (text, serialized_purchase(purchase), item_id))
        return item["batch_id"], None
    if action == "dismiss":
        connection.execute("UPDATE screenshot_items SET state='dismissed', version=version+1 WHERE id=?", (item_id,))
        finish_batch_if_reviewed(connection, item["batch_id"])
        return item["batch_id"], None
    if action not in ("confirm", "review", "link") or not item["parsed_json"]:
        raise ValueError("Correct the extracted text first, or dismiss this entry and add it manually.")
    purchase = restored_purchase(item["parsed_json"])
    matches = possible_matches(connection, household["id"], purchase, household["timezone"])
    if action == "link":
        tid = form.get("existing_transaction")
        if tid not in {match["id"] for match in matches}:
            raise ValueError("Choose an existing matching transaction from this household.")
        state = "linked"
    else:
        if matches and form.get("separate_purchase") != "on":
            raise ValueError("A possible match exists. Use the existing transaction or explicitly mark this as a separate purchase.")
        values = purchase.ledger_values(household["timezone"])
        values["category_id"] = form.get("category_id", "")
        tid, _ = store.add_transaction(connection, household["id"], values)
        connection.execute("INSERT INTO notification_sources VALUES (?, ?, ?, ?, ?)",
                           (tid, household["id"], purchase.occurred_at.isoformat(), purchase.card_suffix, PARSER_VERSION + "/screenshot"))
        if action == "confirm":
            store.correct_transaction(connection, household["id"], user, tid, values["category_id"],
                                      "expense", "confirmed", 1, form.get("remember") == "on")
        state = "saved"
    connection.execute("UPDATE screenshot_items SET state=?, transaction_id=?, version=version+1 WHERE id=?", (state, tid, item_id))
    finish_batch_if_reviewed(connection, item["batch_id"])
    return item["batch_id"], tid
