"""Vault proof of concept: staff intake + customer collection app.

Run locally:
    python3 scripts/seed.py          # create the database, load the catalog, add demo customers
    python3 app.py                   # http://localhost:5000

Not production-ready: sign-in is a demo picker and a shared staff passcode.
"""
import logging
import os
import secrets
from datetime import date, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

from vault import db, jobs
from vault.imaging import process_card
from vault.pricing import item_value

ROOT = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.environ.get("VAULT_UPLOADS", os.path.join(ROOT, "uploads"))
STAFF_PASSCODE = os.environ.get("STAFF_PASSCODE", "vault-demo")
RANGES = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024  # two scans per card

# Hosted mode (gunicorn on Railway): prepare the database on start and run the daily job in-process.
if os.environ.get("RUN_SCHEDULER") == "1":
    logging.basicConfig(level=logging.INFO)
    if not os.environ.get("SECRET_KEY") or STAFF_PASSCODE == "vault-demo":
        logging.getLogger("vault").warning("Set SECRET_KEY and STAFF_PASSCODE for a hosted deployment.")
    os.makedirs(UPLOADS, exist_ok=True)
    jobs.ensure_ready()
    jobs.start_scheduler()


# ---------- helpers ----------

def conn():
    if "conn" not in g:
        g.conn = db.connect()
    return g.conn


@app.teardown_appcontext
def close_conn(_exc):
    c = g.pop("conn", None)
    if c is not None:
        c.close()


@app.template_filter("money")
def money(v):
    return "—" if v is None else f"${v:,.2f}"


@app.context_processor
def inject():
    return {"CONDITION_NAMES": db.CONDITION_NAMES, "staff": session.get("staff", False)}


def staff_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("staff"):
            return redirect(url_for("staff_login", next=request.path))
        return f(*a, **kw)
    return wrapper


def customer_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        cid = session.get("customer_id")
        if not cid:
            return redirect(url_for("home"))
        g.customer = conn().execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
        if g.customer is None:
            session.pop("customer_id", None)
            return redirect(url_for("home"))
        return f(*a, **kw)
    return wrapper


def card_label(row):
    parts = [row["name"]]
    if row["variant"]:
        parts.append(f"({row['variant']})")
    return " ".join(parts)


def set_line(row):
    bits = [row["set_name"]]
    if row["number"]:
        bits.append(row["number"])
    bits.append(row["language"])
    return " · ".join(b for b in bits if b)


def item_rows(customer_id, statuses=("in_vault", "ship_requested")):
    q = f"""SELECT v.*, c.name, c.variant, c.set_name, c.set_code, c.number, c.language, c.rarity
            FROM vault_items v JOIN catalog_cards c ON c.id = v.card_id
            WHERE v.customer_id = ? AND v.status IN ({",".join("?" * len(statuses))})
            ORDER BY v.intake_at DESC"""
    return conn().execute(q, (customer_id, *statuses)).fetchall()


def history(item_ids, days):
    """Daily totals for the given items over the last `days` days, from stored snapshots."""
    if not item_ids:
        return []
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    q = f"""SELECT day, SUM(value) AS total, COUNT(value) AS priced FROM value_snapshots
            WHERE vault_item_id IN ({",".join("?" * len(item_ids))}) AND day >= ?
            GROUP BY day ORDER BY day"""
    return [(r["day"], r["total"]) for r in conn().execute(q, (*item_ids, start)).fetchall() if r["total"] is not None]


def chart(points, width=350, height=140, pad=8):
    """SVG polyline/polygon points for a series of (day, value)."""
    if len(points) < 2:
        return None
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    coords = []
    for i, v in enumerate(vals):
        x = i / (len(vals) - 1) * width
        y = pad + (1 - (v - lo) / span) * (height - 2 * pad)
        coords.append(f"{x:.1f},{y:.1f}")
    line = " ".join(coords)
    return {"line": line, "area": f"0,{height} {line} {width},{height}", "width": width, "height": height}


def change(points):
    if len(points) < 2:
        return None
    first, last = points[0][1], points[-1][1]
    diff = last - first
    pct = (diff / first * 100) if first else 0
    return {"diff": diff, "pct": pct, "up": diff >= 0}


def save_upload(vault_id, side, original, processed):
    folder = os.path.join(UPLOADS, vault_id)
    os.makedirs(folder, exist_ok=True)
    paths = {}
    for name, data in (("original", original), ("image", processed["image"]), ("thumb", processed["thumb"])):
        filename = f"{side}-{name}.jpg"
        with open(os.path.join(folder, filename), "wb") as f:
            f.write(data)
        paths[name] = f"{vault_id}/{filename}"
    return paths


# ---------- landing + demo sign-in ----------

@app.route("/")
def home():
    customers = conn().execute(
        """SELECT cu.*, COUNT(v.id) AS cards FROM customers cu
           LEFT JOIN vault_items v ON v.customer_id = cu.id AND v.status != 'shipped'
           GROUP BY cu.id ORDER BY cu.name"""
    ).fetchall()
    return render_template("home.html", customers=customers)


@app.route("/demo-sign-in/<int:customer_id>", methods=["POST"])
def demo_sign_in(customer_id):
    session["customer_id"] = customer_id
    return redirect(url_for("collection"))


@app.route("/sign-out", methods=["POST"])
def sign_out():
    session.clear()
    return redirect(url_for("home"))


@app.route("/healthz")
def healthz():
    conn().execute("SELECT 1").fetchone()
    return {"ok": True}


@app.route("/media/<path:path>")
def media(path):
    return send_from_directory(UPLOADS, path)


# ---------- customer app ----------

@app.route("/app")
@customer_required
def collection():
    rng = request.args.get("range", "3M")
    days = RANGES.get(rng, 90)
    items = item_rows(g.customer["id"])
    rows, total, unpriced = [], 0.0, 0
    for it in items:
        value = item_value(conn(), it)
        if value is None:
            unpriced += 1
        elif it["status"] == "in_vault":
            total += value
        pts = history([it["id"]], days)
        rows.append({"item": it, "label": card_label(it), "set_line": set_line(it), "value": value, "change": change(pts)})
    in_vault_ids = [it["id"] for it in items if it["status"] == "in_vault"]
    pts = history(in_vault_ids, days)
    return render_template(
        "customer/collection.html", rows=rows, total=total, unpriced=unpriced, chart=chart(pts),
        change=change(pts), rng=rng, ranges=list(RANGES), in_vault=len(in_vault_ids),
        shipping=sum(1 for it in items if it["status"] == "ship_requested"),
    )


def own_item(vault_id):
    it = conn().execute(
        """SELECT v.*, c.name, c.variant, c.set_name, c.set_code, c.number, c.language, c.rarity
           FROM vault_items v JOIN catalog_cards c ON c.id = v.card_id
           WHERE v.vault_id = ? AND v.customer_id = ?""",
        (vault_id, g.customer["id"]),
    ).fetchone()
    if it is None:
        abort(404)
    return it


@app.route("/app/card/<vault_id>")
@customer_required
def card_detail(vault_id):
    it = own_item(vault_id)
    rng = request.args.get("range", "3M")
    pts = history([it["id"]], RANGES.get(rng, 90))
    value = item_value(conn(), it)
    shipment = conn().execute(
        "SELECT * FROM shipments WHERE vault_item_id = ? ORDER BY id DESC LIMIT 1", (it["id"],)
    ).fetchone()
    return render_template(
        "customer/card.html", it=it, label=card_label(it), set_line=set_line(it), value=value,
        chart=chart(pts, height=120), change=change(pts), rng=rng, ranges=list(RANGES), shipment=shipment,
    )


@app.route("/app/card/<vault_id>/ship", methods=["GET", "POST"])
@customer_required
def ship(vault_id):
    it = own_item(vault_id)
    if it["status"] != "in_vault":
        flash("This card already has a ship-out request.")
        return redirect(url_for("card_detail", vault_id=vault_id))
    if request.method == "POST":
        address = request.form.get("address", "").strip()
        if len(address) < 10:
            flash("Enter the full shipping address.")
            return redirect(url_for("ship", vault_id=vault_id))
        c = conn()
        c.execute(
            "INSERT INTO shipments (vault_item_id, customer_id, address, requested_at) VALUES (?, ?, ?, ?)",
            (it["id"], g.customer["id"], address, db.now_iso()),
        )
        c.execute("UPDATE vault_items SET status = 'ship_requested' WHERE id = ?", (it["id"],))
        db.log_activity(c, g.customer["id"], f"Ship-out: {card_label(it)}", f"{it['vault_id']} · insured, signature required", "Requested")
        c.commit()
        return render_template("customer/ship_done.html", it=it, label=card_label(it))
    return render_template("customer/ship.html", it=it, label=card_label(it), set_line=set_line(it), value=item_value(conn(), it))


@app.route("/app/activity")
@customer_required
def activity():
    rows = conn().execute(
        "SELECT * FROM activity WHERE customer_id = ? ORDER BY id DESC LIMIT 100", (g.customer["id"],)
    ).fetchall()
    return render_template("customer/activity.html", rows=rows)


# ---------- staff ----------

@app.route("/staff/login", methods=["GET", "POST"])
def staff_login():
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("passcode", ""), STAFF_PASSCODE):
            session["staff"] = True
            return redirect(request.args.get("next") or url_for("staff_intake"))
        flash("Wrong passcode.")
    return render_template("staff/login.html")


@app.route("/staff/intake", methods=["GET", "POST"])
@staff_required
def staff_intake():
    c = conn()
    if request.method == "POST":
        f = request.form
        errors = []
        card = c.execute("SELECT * FROM catalog_cards WHERE id = ?", (f.get("card_id") or 0,)).fetchone()
        if card is None:
            errors.append("Pick the card from the catalog search.")
        kind = f.get("kind", "raw")
        condition = f.get("condition") if kind == "raw" else None
        if kind == "raw" and condition not in db.CONDITIONS:
            errors.append("Choose a condition.")
        if kind == "graded" and not (f.get("grader") and f.get("grade")):
            errors.append("Enter the grading company and grade.")
        front = request.files.get("front")
        back = request.files.get("back")
        if not front or not front.filename:
            errors.append("Add a front scan or photo.")

        customer_id = f.get("customer_id")
        if customer_id == "new":
            name, email = f.get("new_name", "").strip(), f.get("new_email", "").strip().lower()
            if not name:
                errors.append("Enter the new customer's name.")
        processed = {}
        if not errors:
            try:
                front_bytes = front.read()
                processed["front"] = (front_bytes, process_card(front_bytes, kind))
                if back and back.filename:
                    back_bytes = back.read()
                    processed["back"] = (back_bytes, process_card(back_bytes, kind))
            except ValueError as e:
                errors.append(str(e))
        if errors:
            for e in errors:
                flash(e)
            return redirect(url_for("staff_intake"))

        if customer_id == "new":
            existing = c.execute("SELECT id FROM customers WHERE email = ?", (email,)).fetchone() if email else None
            if existing:
                customer_id = existing["id"]
            else:
                cur = c.execute("INSERT INTO customers (name, email, created_at) VALUES (?, ?, ?)", (name, email or None, db.now_iso()))
                customer_id = cur.lastrowid
        customer_id = int(customer_id)

        vault_id = db.next_vault_id(c)
        saved = {side: save_upload(vault_id, side, orig, proc) for side, (orig, proc) in processed.items()}
        flags = [f"{side}: {w}" for side, (_, proc) in processed.items() for w in proc["warnings"]]
        cur = c.execute(
            """INSERT INTO vault_items (vault_id, customer_id, card_id, kind, condition, grader, grade, cert_number,
                 front_image, back_image, front_thumb, front_original, back_original, image_flags, intake_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (vault_id, customer_id, card["id"], kind, condition, f.get("grader") or None, f.get("grade") or None,
             f.get("cert_number") or None, saved["front"]["image"], saved.get("back", {}).get("image"),
             saved["front"]["thumb"], saved["front"]["original"], saved.get("back", {}).get("original"),
             "\n".join(flags), db.now_iso(), f.get("notes") or None),
        )
        item = c.execute("SELECT * FROM vault_items WHERE id = ?", (cur.lastrowid,)).fetchone()
        value = item_value(c, item)
        c.execute("INSERT OR REPLACE INTO value_snapshots (vault_item_id, day, value) VALUES (?, ?, ?)", (item["id"], db.today(), value))
        db.log_activity(c, customer_id, f"Added to vault: {card_label(card)}", f"{vault_id} · {money(value)}", "In vault")
        c.commit()
        return redirect(url_for("staff_item", vault_id=vault_id))

    customers = c.execute("SELECT id, name, email FROM customers ORDER BY name").fetchall()
    recent = c.execute(
        """SELECT v.vault_id, v.image_flags, v.front_thumb, c.name, cu.name AS customer FROM vault_items v
           JOIN catalog_cards c ON c.id = v.card_id JOIN customers cu ON cu.id = v.customer_id
           ORDER BY v.id DESC LIMIT 8"""
    ).fetchall()
    return render_template("staff/intake.html", customers=customers, conditions=db.CONDITIONS, recent=recent)


@app.route("/staff/item/<vault_id>")
@staff_required
def staff_item(vault_id):
    it = conn().execute(
        """SELECT v.*, c.name, c.variant, c.set_name, c.set_code, c.number, c.language, c.rarity, cu.name AS customer
           FROM vault_items v JOIN catalog_cards c ON c.id = v.card_id JOIN customers cu ON cu.id = v.customer_id
           WHERE v.vault_id = ?""",
        (vault_id,),
    ).fetchone()
    if it is None:
        abort(404)
    return render_template("staff/item.html", it=it, label=card_label(it), set_line=set_line(it),
                           value=item_value(conn(), it), flags=[x for x in (it["image_flags"] or "").split("\n") if x])


@app.route("/staff/shipments", methods=["GET", "POST"])
@staff_required
def staff_shipments():
    c = conn()
    if request.method == "POST":
        sid = int(request.form["shipment_id"])
        tracking = request.form.get("tracking", "").strip()
        s = c.execute("SELECT * FROM shipments WHERE id = ? AND status = 'requested'", (sid,)).fetchone()
        if s and tracking:
            c.execute("UPDATE shipments SET status = 'shipped', tracking = ?, shipped_at = ? WHERE id = ?", (tracking, db.now_iso(), sid))
            c.execute("UPDATE vault_items SET status = 'shipped' WHERE id = ?", (s["vault_item_id"],))
            it = c.execute("SELECT v.vault_id, c.name, c.variant FROM vault_items v JOIN catalog_cards c ON c.id = v.card_id WHERE v.id = ?",
                           (s["vault_item_id"],)).fetchone()
            db.log_activity(c, s["customer_id"], f"Shipped: {card_label(it)}", f"{it['vault_id']} · tracking {tracking}", "Shipped")
            c.commit()
            flash(f"Marked {it['vault_id']} as shipped.")
        else:
            flash("Enter a tracking number.")
        return redirect(url_for("staff_shipments"))
    rows = c.execute(
        """SELECT s.*, v.vault_id, v.front_thumb, c.name, c.variant, cu.name AS customer FROM shipments s
           JOIN vault_items v ON v.id = s.vault_item_id JOIN catalog_cards c ON c.id = v.card_id
           JOIN customers cu ON cu.id = s.customer_id ORDER BY s.status = 'requested' DESC, s.id DESC LIMIT 100"""
    ).fetchall()
    return render_template("staff/shipments.html", rows=rows, label=card_label)


@app.route("/api/catalog/search")
@staff_required
def catalog_search():
    q = request.args.get("q", "").strip().lower()
    if len(q) < 2:
        return jsonify([])
    terms = q.split()
    where = " AND ".join("c.search_text LIKE ?" for _ in terms)
    rows = conn().execute(
        f"""SELECT c.*, (SELECT price FROM catalog_prices p WHERE p.card_id = c.id AND p.condition = 'NM') AS nm
            FROM catalog_cards c WHERE {where} ORDER BY nm DESC NULLS LAST LIMIT 20""",
        [f"%{t}%" for t in terms],
    ).fetchall()
    return jsonify([{"id": r["id"], "label": card_label(r), "set_line": set_line(r), "rarity": r["rarity"], "nm": r["nm"]} for r in rows])


@app.route("/api/preview-crop", methods=["POST"])
@staff_required
def preview_crop():
    """Run auto-crop without saving so staff can check it before adding the card."""
    f = request.files.get("image")
    if not f:
        return jsonify({"error": "No image"}), 400
    try:
        r = process_card(f.read(), request.form.get("kind", "raw"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    import base64
    return jsonify({"image": "data:image/jpeg;base64," + base64.b64encode(r["image"]).decode(), "warnings": r["warnings"], "method": r["method"]})


if __name__ == "__main__":
    jobs.ensure_ready()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))
