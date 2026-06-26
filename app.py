"""
AlgoQuant License Server
Flask backend that the MT5 EA pings to verify license status.
"""

from flask import Flask, request, jsonify
import sqlite3
import secrets
import string
from datetime import datetime, timezone
import os

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "keys.db")


# ──────────────────────────────────────────────────────────────────
# DATABASE SETUP
# ──────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            key_id TEXT PRIMARY KEY,
            account_number TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            product TEXT DEFAULT 'TripleEMA_ATR',
            email TEXT,
            created_at TEXT,
            last_seen TEXT,
            ping_count INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ──────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────
def generate_key_id():
    """Generate a unique key like AQ-7X9K2M"""
    chars = string.ascii_uppercase + string.digits
    suffix = ''.join(secrets.choice(chars) for _ in range(6))
    return f"AQ-{suffix}"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────
# EA-FACING ENDPOINT — this is what MQL5 WebRequest calls
# ──────────────────────────────────────────────────────────────────
@app.route("/verify", methods=["GET"])
def verify():
    key_id = request.args.get("key_id", "").strip()
    account = request.args.get("account", "").strip()

    if not key_id:
        return jsonify({"status": "invalid", "reason": "missing_key"}), 400

    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT * FROM keys WHERE key_id = ?", (key_id,)).fetchone()

    if row is None:
        conn.close()
        return jsonify({"status": "invalid", "reason": "unknown_key"})

    # First time this key is used — bind it to the account number
    if row["account_number"] is None or row["account_number"] == "":
        c.execute(
            "UPDATE keys SET account_number = ?, last_seen = ?, ping_count = ping_count + 1 WHERE key_id = ?",
            (account, now_iso(), key_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"status": row["status"]})

    # Key already bound — check it matches the same account
    if row["account_number"] != account:
        # Different account using the same key_id = sharing detected
        conn.close()
        return jsonify({"status": "burned", "reason": "account_mismatch"})

    # Normal heartbeat update
    c.execute(
        "UPDATE keys SET last_seen = ?, ping_count = ping_count + 1 WHERE key_id = ?",
        (now_iso(), key_id),
    )
    conn.commit()
    conn.close()

    return jsonify({"status": row["status"]})


# ──────────────────────────────────────────────────────────────────
# ADMIN-FACING ENDPOINTS — used by the Streamlit dashboard
# ──────────────────────────────────────────────────────────────────
ADMIN_TOKEN = os.environ.get("AQ_ADMIN_TOKEN", "change-this-token")


def check_admin(req):
    return req.headers.get("X-Admin-Token") == ADMIN_TOKEN


@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    if not check_admin(request):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True) or {}
    email = data.get("email", "")
    product = data.get("product", "TripleEMA_ATR")

    key_id = generate_key_id()
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO keys (key_id, account_number, status, product, email, created_at) VALUES (?,?,?,?,?,?)",
        (key_id, None, "active", product, email, now_iso()),
    )
    conn.commit()
    conn.close()

    return jsonify({"key_id": key_id, "status": "active"})


@app.route("/admin/list", methods=["GET"])
def admin_list():
    if not check_admin(request):
        return jsonify({"error": "unauthorized"}), 401

    conn = get_conn()
    rows = conn.execute("SELECT * FROM keys ORDER BY created_at DESC").fetchall()
    conn.close()

    return jsonify([dict(r) for r in rows])


@app.route("/admin/burn", methods=["POST"])
def admin_burn():
    if not check_admin(request):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True) or {}
    key_id = data.get("key_id")

    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE keys SET status = 'burned' WHERE key_id = ?", (key_id,))
    conn.commit()
    affected = c.rowcount
    conn.close()

    return jsonify({"updated": affected})


@app.route("/admin/reactivate", methods=["POST"])
def admin_reactivate():
    if not check_admin(request):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True) or {}
    key_id = data.get("key_id")

    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE keys SET status = 'active' WHERE key_id = ?", (key_id,))
    conn.commit()
    affected = c.rowcount
    conn.close()

    return jsonify({"updated": affected})


@app.route("/admin/delete", methods=["POST"])
def admin_delete():
    if not check_admin(request):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True) or {}
    key_id = data.get("key_id")

    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM keys WHERE key_id = ?", (key_id,))
    conn.commit()
    affected = c.rowcount
    conn.close()

    return jsonify({"deleted": affected})


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "AlgoQuant License Server running"})


# ──────────────────────────────────────────────────────────────────
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
