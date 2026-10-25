"""Authenticated screenshot upload and review pages."""

from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from . import screenshots, store


pages = Blueprint("screenshots", __name__)


def db():
    return store.database(current_app.config["DATABASE"])


def household():
    with db() as connection:
        return dict(connection.execute("SELECT * FROM households WHERE id=?", (g.user["household_id"],)).fetchone())


@pages.before_request
def require_member():
    if not g.user:
        return redirect(url_for("login", next=request.path))


@pages.route("/screenshots", methods=["GET", "POST"])
def index():
    home = household()
    if request.method == "POST":
        uploaded = request.files.get("screenshot")
        try:
            if not uploaded:
                raise ValueError("Choose a screenshot to upload.")
            bid, created = screenshots.stage_upload(
                current_app.config["DATABASE"], current_app.config["DATA_DIR"], home["id"],
                uploaded.read(screenshots.MAX_UPLOAD_BYTES + 1))
            if created:
                screenshots.process_batch(current_app.config["DATABASE"], current_app.config["DATA_DIR"], home["id"], bid)
                flash("Screenshot saved privately. Review the extracted entries before they affect your budget.", "success")
            else:
                flash("This exact screenshot is already uploaded. Opening its existing review.", "success")
            return redirect(url_for("screenshots.review", bid=bid))
        except ValueError as error:
            flash(str(error), "error")
    with db() as connection:
        batches = connection.execute("SELECT b.*, (SELECT count(*) FROM screenshot_items i WHERE i.batch_id=b.id AND i.state='pending') "
                                     "AS pending FROM screenshot_batches b WHERE household_id=? ORDER BY created_at DESC, rowid DESC", (home["id"],)).fetchall()
    return render_template("screenshots.html", household=home, batches=batches)


@pages.get("/screenshots/<bid>")
def review(bid):
    home = household()
    with db() as connection:
        batch = connection.execute("SELECT * FROM screenshot_batches WHERE id=? AND household_id=?", (bid, home["id"])).fetchone()
        if not batch:
            abort(404)
        rows = connection.execute("SELECT * FROM screenshot_items WHERE batch_id=? AND household_id=? ORDER BY position", (bid, home["id"])).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["purchase"] = screenshots.restored_purchase(item["parsed_json"]) if item["parsed_json"] else None
            item["matches"] = screenshots.possible_matches(connection, home["id"], item["purchase"], home["timezone"]) if item["purchase"] and item["state"] == "pending" else []
            item["suggestion"] = None
            if item["purchase"]:
                rule = connection.execute("SELECT category_id FROM merchant_rules WHERE household_id=? AND merchant=?",
                                          (home["id"], store.normalize_merchant(item["purchase"].merchant))).fetchone()
                item["suggestion"] = rule["category_id"] if rule else None
            items.append(item)
        categories = store.categories(connection, home["id"])
    return render_template("screenshot-review.html", household=home, batch=batch, items=items, categories=categories)


@pages.get("/screenshots/<bid>/image")
def original_image(bid):
    with db() as connection:
        row = connection.execute("SELECT id FROM screenshot_batches WHERE id=? AND household_id=?", (bid, g.user["household_id"])).fetchone()
    if not row:
        abort(404)
    path = Path(current_app.config["DATA_DIR"]) / "screenshots" / f"{row['id']}.png"
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/png", download_name="private-screenshot.png", max_age=0)


@pages.post("/screenshots/<bid>/retry")
def retry(bid):
    try:
        screenshots.process_batch(current_app.config["DATABASE"], current_app.config["DATA_DIR"], g.user["household_id"], bid)
    except LookupError:
        abort(404)
    return redirect(url_for("screenshots.review", bid=bid))


@pages.post("/screenshot-items/<item_id>")
def decide(item_id):
    home = household()
    try:
        with db() as connection:
            bid, tid = screenshots.act_on_item(connection, home, g.user["id"], item_id, request.form)
        flash("Review decision saved. Only confirmed transactions count in the budget.", "success")
        return redirect(url_for("screenshots.review", bid=bid))
    except LookupError:
        abort(404)
    except ValueError as error:
        flash(str(error), "error")
        with db() as connection:
            item = connection.execute("SELECT batch_id FROM screenshot_items WHERE id=? AND household_id=?", (item_id, home["id"])).fetchone()
        if not item:
            abort(404)
        return redirect(url_for("screenshots.review", bid=item["batch_id"]))
