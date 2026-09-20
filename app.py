from __future__ import annotations

import json
import hashlib
import hmac
import base64
import os
import re
import secrets
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import boto3
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from storage import StorageError, build_storage
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
# Keep the former root location as a fallback for existing local installations.
load_dotenv(BASE_DIR.parent / ".env")
# PostgreSQL connection string
DB_CONNECTION_STRING = os.environ.get("DATABASE_URL", "postgresql://localhost/cloth_store_db")
IMAGE_SLOTS = ["front", "back", "side", "closeup", "model", "fit"]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "10")) * 1024 * 1024
cors_origins = [origin.strip() for origin in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if origin.strip()]
CORS(app, resources={r"/api/*": {"origins": cors_origins}})
storage = build_storage(BASE_DIR)

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = generate_password_hash(os.environ.get("ADMIN_PASSWORD", "admin123"))
ADMIN_TOKEN_MAX_AGE = int(os.environ.get("ADMIN_TOKEN_MAX_AGE", "43200"))
token_serializer = URLSafeTimedSerializer(
    os.environ.get("ADMIN_TOKEN_SECRET", "pandu-local-development-secret"),
    salt="admin-auth",
)

OTP_SMS_BACKEND = os.environ.get("OTP_SMS_BACKEND", "console").strip().lower()
OTP_EXPIRY_SECONDS = int(os.environ.get("OTP_EXPIRY_SECONDS", "300"))
OTP_RESEND_SECONDS = int(os.environ.get("OTP_RESEND_SECONDS", "30"))
OTP_MAX_ATTEMPTS = int(os.environ.get("OTP_MAX_ATTEMPTS", "5"))
OTP_SECRET = os.environ.get("OTP_SECRET", os.environ.get("ADMIN_TOKEN_SECRET", "pandu-local-development-secret"))
otp_serializer = URLSafeTimedSerializer(OTP_SECRET, salt="mobile-verification")
otp_challenges = {}
otp_last_sent = {}
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()


def normalize_mobile(value):
    """Return an Indian mobile number in E.164 format, or None when invalid."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        return None
    return f"+91{digits}"


def normalize_address(value):
    if not isinstance(value, dict):
        return None
    door = str(value.get("door", "")).strip()
    line1 = str(value.get("line1", "")).strip()
    line2 = str(value.get("line2", "")).strip()
    city = str(value.get("city", "")).strip()
    pincode = re.sub(r"\D", "", str(value.get("pincode", "")))
    if not door or not line1 or not city or not re.fullmatch(r"[1-9]\d{5}", pincode):
        return None
    lines = [door, line1]
    if line2:
        lines.append(line2)
    lines.extend([city, pincode])
    return ", ".join(lines)


def otp_digest(challenge_id, code):
    message = f"{challenge_id}:{code}".encode()
    return hmac.new(OTP_SECRET.encode(), message, hashlib.sha256).hexdigest()


def send_otp_sms(mobile, code):
    expiry_minutes = max(1, (OTP_EXPIRY_SECONDS + 59) // 60)
    message = f"Your Pandu verification code is {code}. It expires in {expiry_minutes} minutes. Do not share it."
    if OTP_SMS_BACKEND == "console":
        app.logger.warning("Development OTP for %s: %s", mobile, code)
        return
    if OTP_SMS_BACKEND == "sns":
        boto3.client("sns", region_name=os.environ.get("OTP_AWS_REGION", "ap-south-1")).publish(
            PhoneNumber=mobile,
            Message=message,
        )
        return
    raise RuntimeError("OTP_SMS_BACKEND must be 'console' or 'sns'.")


def razorpay_request(method, path, payload=None):
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RuntimeError("Razorpay is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to .env.")
    credentials = base64.b64encode(f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}".encode()).decode()
    body = json.dumps(payload).encode() if payload is not None else None
    api_request = Request(
        f"https://api.razorpay.com/v1{path}",
        data=body,
        method=method,
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(api_request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            detail = json.load(error).get("error", {}).get("description")
        except (ValueError, AttributeError):
            detail = None
        raise RuntimeError(detail or "Razorpay rejected the payment request.") from error
    except URLError as error:
        raise RuntimeError("Could not connect to Razorpay. Please try again.") from error


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else ""
        try:
            payload = token_serializer.loads(token, max_age=ADMIN_TOKEN_MAX_AGE)
        except (BadSignature, SignatureExpired):
            return jsonify({"error": "Unauthorized"}), 401
        if payload.get("username") != ADMIN_USERNAME:
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


def save_image(file, slot):
    try:
        return storage.save(file, slot), None
    except ValueError as exc:
        return None, str(exc)


def delete_uploaded_file(path):
    storage.delete(path)

PRODUCTS = [
    ("Linen Ease Shirt", "Women", 2499, 2999, "https://images.unsplash.com/photo-1596755094514-f87e34085b2c?auto=format&fit=crop&w=900&q=85", "Bestseller", "Oat", "A breezy linen-blend shirt with an easy, oversized silhouette.", "https://images.unsplash.com/photo-1596755094514-f87e34085b2c?auto=format&fit=crop&w=900&q=85"),
    ("Textured Knit Polo", "Men", 2899, None, "https://images.unsplash.com/photo-1617137968427-85924c800a22?auto=format&fit=crop&w=900&q=85", "New", "Sand", "A softly structured knit polo made for dressed-down days.", "https://images.unsplash.com/photo-1617137968427-85924c800a22?auto=format&fit=crop&w=900&q=85"),
    ("Sculpted Midi Dress", "Women", 3999, 4799, "https://images.unsplash.com/photo-1595777457583-95e059d581b8?auto=format&fit=crop&w=900&q=85", "Limited", "Terracotta", "Fluid lines and thoughtful tailoring in a day-to-evening dress.", "https://images.unsplash.com/photo-1595777457583-95e059d581b8?auto=format&fit=crop&w=900&q=85"),
    ("Relaxed Pleat Trousers", "Women", 3199, None, "https://images.unsplash.com/photo-1594633312681-425c7b97ccd1?auto=format&fit=crop&w=900&q=85", None, "Stone", "High-rise trousers with soft front pleats and an elegant drape.", "https://images.unsplash.com/photo-1594633312681-425c7b97ccd1?auto=format&fit=crop&w=900&q=85"),
    ("Everyday Overshirt", "Men", 3499, 3999, "https://images.unsplash.com/photo-1603252109303-2751441dd157?auto=format&fit=crop&w=900&q=85", "Bestseller", "Olive", "A versatile mid-weight layer with clean utility details.", "https://images.unsplash.com/photo-1603252109303-2751441dd157?auto=format&fit=crop&w=900&q=85"),
    ("Soft Rib Co-ord", "Women", 3599, None, "https://images.unsplash.com/photo-1551488831-00ddcb6c6bd3?auto=format&fit=crop&w=900&q=85", "New", "Cocoa", "An effortlessly polished ribbed set designed for all-day comfort.", "https://images.unsplash.com/photo-1551488831-00ddcb6c6bd3?auto=format&fit=crop&w=900&q=85"),
    ("Classic Camp Collar", "Men", 2299, 2699, "https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?auto=format&fit=crop&w=900&q=85", None, "Ivory", "A refined warm-weather shirt cut from breathable cotton.", "https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?auto=format&fit=crop&w=900&q=85"),
    ("Drape Studio Blazer", "Women", 4499, None, "https://images.unsplash.com/photo-1591369822096-ffd140ec948f?auto=format&fit=crop&w=900&q=85", "Limited", "Camel", "Relaxed tailoring with a soft shoulder and modern proportions.", "https://images.unsplash.com/photo-1591369822096-ffd140ec948f?auto=format&fit=crop&w=900&q=85"),
]


def db():
    """Get a PostgreSQL connection with RealDictRow factory"""
    connection = psycopg2.connect(DB_CONNECTION_STRING)
    connection.autocommit = False
    return connection


def init_db():
    """Initialize PostgreSQL database with tables"""
    conn = psycopg2.connect(DB_CONNECTION_STRING)
    cur = conn.cursor()
    try:
        # Create products table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                price INTEGER NOT NULL,
                old_price INTEGER,
                image TEXT NOT NULL,
                badge TEXT,
                color TEXT NOT NULL,
                description TEXT NOT NULL,
                image_front TEXT,
                image_back TEXT,
                image_side TEXT,
                image_closeup TEXT,
                image_model TEXT,
                image_fit TEXT
            );
        """)
        
        # Create wishlist table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS wishlist (
                product_id INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE
            );
        """)
        
        # Create orders table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                customer TEXT NOT NULL,
                email TEXT,
                mobile TEXT,
                total INTEGER NOT NULL,
                items JSONB NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS mobile TEXT;")
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS address TEXT;")
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS razorpay_order_id TEXT;")
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS razorpay_payment_id TEXT;")
        cur.execute("ALTER TABLE orders ALTER COLUMN email DROP NOT NULL;")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS orders_razorpay_payment_id_idx ON orders(razorpay_payment_id) WHERE razorpay_payment_id IS NOT NULL;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payment_sessions (
                razorpay_order_id TEXT PRIMARY KEY,
                customer TEXT NOT NULL,
                email TEXT,
                mobile TEXT NOT NULL,
                address TEXT NOT NULL,
                amount INTEGER NOT NULL,
                items JSONB NOT NULL,
                completed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TEXT NOT NULL
            );
        """)
        cur.execute("ALTER TABLE payment_sessions ALTER COLUMN email DROP NOT NULL;")
        
        conn.commit()
        
        # Insert sample products if table is empty
        cur.execute("SELECT COUNT(*) FROM products;")
        count = cur.fetchone()[0]
        if count == 0:
            for product in PRODUCTS:
                cur.execute("""
                    INSERT INTO products 
                    (name, category, price, old_price, image, badge, color, description, image_front)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, product)
            cur.execute("UPDATE products SET image_front = image WHERE image_front IS NULL;")
            conn.commit()
    finally:
        cur.close()
        conn.close()


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/otp/send")
def send_otp():
    mobile = normalize_mobile((request.get_json(silent=True) or {}).get("mobile"))
    if not mobile:
        return jsonify({"error": "Enter a valid 10-digit Indian mobile number."}), 400

    now = time.time()
    retry_after = int(otp_last_sent.get(mobile, 0) + OTP_RESEND_SECONDS - now)
    if retry_after > 0:
        return jsonify({"error": f"Please wait {retry_after} seconds before requesting another OTP.", "retry_after": retry_after}), 429

    for key, challenge in list(otp_challenges.items()):
        if challenge["expires_at"] <= now or challenge["mobile"] == mobile:
            otp_challenges.pop(key, None)

    challenge_id = str(uuid.uuid4())
    code = f"{secrets.randbelow(1_000_000):06d}"
    otp_challenges[challenge_id] = {
        "mobile": mobile,
        "digest": otp_digest(challenge_id, code),
        "expires_at": now + OTP_EXPIRY_SECONDS,
        "attempts": 0,
    }
    try:
        send_otp_sms(mobile, code)
    except Exception:
        otp_challenges.pop(challenge_id, None)
        app.logger.exception("Could not send OTP")
        return jsonify({"error": "Could not send the OTP. Please try again."}), 502

    otp_last_sent[mobile] = now
    response = {"challenge_id": challenge_id, "expires_in": OTP_EXPIRY_SECONDS}
    if OTP_SMS_BACKEND == "console":
        response["development_otp"] = code
    return jsonify(response), 201


@app.post("/api/otp/verify")
def verify_otp():
    data = request.get_json(silent=True) or {}
    challenge_id = str(data.get("challenge_id", ""))
    code = str(data.get("otp", "")).strip()
    challenge = otp_challenges.get(challenge_id)
    if not challenge:
        return jsonify({"error": "This OTP request is invalid. Please request a new code."}), 400
    if challenge["expires_at"] <= time.time():
        otp_challenges.pop(challenge_id, None)
        return jsonify({"error": "This OTP has expired. Please request a new code."}), 400
    if challenge["attempts"] >= OTP_MAX_ATTEMPTS:
        otp_challenges.pop(challenge_id, None)
        return jsonify({"error": "Too many incorrect attempts. Please request a new code."}), 429

    challenge["attempts"] += 1
    if not re.fullmatch(r"\d{6}", code) or not hmac.compare_digest(challenge["digest"], otp_digest(challenge_id, code)):
        return jsonify({"error": "The OTP is incorrect. Please try again."}), 400

    mobile = challenge["mobile"]
    otp_challenges.pop(challenge_id, None)
    return jsonify({"mobile": mobile, "verification_token": otp_serializer.dumps({"mobile": mobile})})


@app.errorhandler(StorageError)
def storage_error(error):
    app.logger.exception("Object storage operation failed")
    return jsonify({"error": str(error)}), 502


@app.get("/uploads/<path:key>")
def uploaded_file(key):
    return storage.response(key)


@app.post("/api/admin/login")
def admin_login():
    data = request.get_json(silent=True) or {}
    username, password = data.get("username", ""), data.get("password", "")
    if username != ADMIN_USERNAME or not check_password_hash(ADMIN_PASSWORD_HASH, password):
        return jsonify({"error": "Invalid username or password"}), 401
    token = token_serializer.dumps({"username": ADMIN_USERNAME})
    return jsonify({"token": token})


@app.post("/api/admin/logout")
@require_admin
def admin_logout():
    return "", 204


@app.post("/api/admin/products")
@require_admin
def admin_create_product():
    form = request.form
    name, category, color, description = (form.get("name", "").strip(), form.get("category", "").strip(),
                                            form.get("color", "").strip(), form.get("description", "").strip())
    badge = form.get("badge", "").strip() or None
    if not name or category not in ("Women", "Men") or not color or not description:
        return jsonify({"error": "Name, category, color, and description are required."}), 400
    try:
        price = int(form.get("price", ""))
        old_price = int(form["old_price"]) if form.get("old_price") else None
    except ValueError:
        return jsonify({"error": "Price must be a valid number."}), 400

    image_paths = {}
    for slot in IMAGE_SLOTS:
        file = request.files.get(slot)
        if not file or not file.filename:
            return jsonify({"error": f"The '{slot}' image is required."}), 400
        saved, error = save_image(file, slot)
        if error:
            return jsonify({"error": error}), 400
        image_paths[slot] = saved

    conn = db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """INSERT INTO products (name, category, price, old_price, image, badge, color, description,
                image_front, image_back, image_side, image_closeup, image_model, image_fit)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (name, category, price, old_price, image_paths["front"], badge, color, description,
             image_paths["front"], image_paths["back"], image_paths["side"],
             image_paths["closeup"], image_paths["model"], image_paths["fit"]),
        )
        row = cur.fetchone()
        conn.commit()
        return jsonify(dict(row)), 201
    finally:
        cur.close()
        conn.close()


@app.put("/api/admin/products/<int:product_id>")
@require_admin
def admin_update_product(product_id):
    conn = db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        existing = cur.fetchone()
        if not existing:
            return jsonify({"error": "Product not found"}), 404

        form = request.form
        name, category, color, description = (form.get("name", "").strip(), form.get("category", "").strip(),
                                                form.get("color", "").strip(), form.get("description", "").strip())
        badge = form.get("badge", "").strip() or None
        if not name or category not in ("Women", "Men") or not color or not description:
            return jsonify({"error": "Name, category, color, and description are required."}), 400
        try:
            price = int(form.get("price", ""))
            old_price = int(form["old_price"]) if form.get("old_price") else None
        except ValueError:
            return jsonify({"error": "Price must be a valid number."}), 400

        image_paths = {}
        for slot in IMAGE_SLOTS:
            file = request.files.get(slot)
            if file and file.filename:
                saved, error = save_image(file, slot)
                if error:
                    return jsonify({"error": error}), 400
                old_path = existing[f"image_{slot}"]
                if old_path:
                    delete_uploaded_file(old_path)
                image_paths[slot] = saved
            else:
                image_paths[slot] = existing[f"image_{slot}"]

        cur.execute(
            """UPDATE products SET name = %s, category = %s, price = %s, old_price = %s, image = %s, badge = %s,
                color = %s, description = %s, image_front = %s, image_back = %s, image_side = %s,
                image_closeup = %s, image_model = %s, image_fit = %s WHERE id = %s
                RETURNING *""",
            (name, category, price, old_price, image_paths["front"], badge, color, description,
             image_paths["front"], image_paths["back"], image_paths["side"],
             image_paths["closeup"], image_paths["model"], image_paths["fit"], product_id),
        )
        row = cur.fetchone()
        conn.commit()
        return jsonify(dict(row))
    finally:
        cur.close()
        conn.close()


@app.delete("/api/admin/products/<int:product_id>")
@require_admin
def admin_delete_product(product_id):
    conn = db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        existing = cur.fetchone()
        if not existing:
            return jsonify({"error": "Product not found"}), 404
        
        cur.execute("DELETE FROM wishlist WHERE product_id = %s", (product_id,))
        cur.execute("DELETE FROM products WHERE id = %s", (product_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    
    for slot in IMAGE_SLOTS:
        delete_uploaded_file(existing[f"image_{slot}"])
    return "", 204


@app.get("/api/products")
def products():
    category = request.args.get("category")
    search = request.args.get("search", "").strip()
    query = "SELECT * FROM products WHERE 1=1"
    params = []
    
    if category and category != "All":
        query += " AND category = %s"
        params.append(category)
    if search:
        query += " AND (name ILIKE %s OR description ILIKE %s OR color ILIKE %s)"
        params.extend([f"%{search}%"] * 3)
    query += " ORDER BY id"
    
    conn = db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(query, params)
        rows = cur.fetchall()
        return jsonify([dict(row) for row in rows])
    finally:
        cur.close()
        conn.close()


@app.get("/api/wishlist")
def get_wishlist():
    conn = db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT p.* FROM products p JOIN wishlist w ON p.id = w.product_id ORDER BY p.id")
        rows = cur.fetchall()
        return jsonify([dict(row) for row in rows])
    finally:
        cur.close()
        conn.close()


@app.post("/api/wishlist/<int:product_id>")
def add_wishlist(product_id):
    conn = db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM products WHERE id = %s", (product_id,))
        if not cur.fetchone():
            return jsonify({"error": "Product not found"}), 404
        cur.execute("INSERT INTO wishlist(product_id) VALUES (%s) ON CONFLICT DO NOTHING", (product_id,))
        conn.commit()
        return jsonify({"product_id": product_id, "saved": True}), 201
    finally:
        cur.close()
        conn.close()


@app.delete("/api/wishlist/<int:product_id>")
def remove_wishlist(product_id):
    conn = db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM wishlist WHERE product_id = %s", (product_id,))
        conn.commit()
        return "", 204
    finally:
        cur.close()
        conn.close()


@app.post("/api/payments/razorpay/order")
def create_razorpay_order():
    data = request.get_json(silent=True) or {}
    customer = str(data.get("customer", "")).strip()
    mobile = normalize_mobile(data.get("mobile"))
    address = normalize_address(data.get("address"))
    raw_items = data.get("items", [])
    if not customer or not mobile or not address or not raw_items:
        return jsonify({"error": "Enter a valid name, mobile number, complete address, city, and PIN code."}), 400

    try:
        items = [{"product_id": int(item["product_id"]), "quantity": int(item.get("quantity", 1))} for item in raw_items]
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "The cart contains invalid items."}), 400
    if any(item["quantity"] < 1 or item["quantity"] > 20 for item in items):
        return jsonify({"error": "Item quantity must be between 1 and 20."}), 400

    ids = [item["product_id"] for item in items]
    conn = db()
    cur = conn.cursor()
    try:
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(f"SELECT id, price FROM products WHERE id IN ({placeholders})", ids)
        rows = cur.fetchall()
        prices = {row[0]: row[1] for row in rows}
        
        if len(prices) != len(set(ids)):
            return jsonify({"error": "One or more products are unavailable."}), 400

        total = sum(prices[item["product_id"]] * max(1, int(item.get("quantity", 1))) for item in items)
        receipt = f"pandu-{uuid.uuid4().hex[:24]}"
        try:
            razorpay_order = razorpay_request("POST", "/orders", {
                "amount": total * 100,
                "currency": "INR",
                "receipt": receipt,
                "notes": {"store": "Pandu"},
            })
        except RuntimeError as error:
            status = 503 if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET else 502
            return jsonify({"error": str(error)}), status
        if not razorpay_order.get("id") or razorpay_order.get("amount") != total * 100:
            return jsonify({"error": "Razorpay returned an invalid order. Please try again."}), 502

        cur.execute(
            """INSERT INTO payment_sessions
               (razorpay_order_id, customer, email, mobile, address, amount, items, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (razorpay_order["id"], customer, None, mobile, address, total, json.dumps(items),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return jsonify({
            "key_id": RAZORPAY_KEY_ID,
            "order_id": razorpay_order["id"],
            "amount": total * 100,
            "currency": "INR",
            "prefill": {"name": customer, "contact": mobile},
        }), 201
    finally:
        cur.close()
        conn.close()


@app.post("/api/orders")
def create_order():
    data = request.get_json(silent=True) or {}
    razorpay_order_id = str(data.get("razorpay_order_id", "")).strip()
    razorpay_payment_id = str(data.get("razorpay_payment_id", "")).strip()
    razorpay_signature = str(data.get("razorpay_signature", "")).strip()
    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        return jsonify({"error": "A completed Razorpay payment is required."}), 400

    conn = db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM payment_sessions WHERE razorpay_order_id = %s FOR UPDATE", (razorpay_order_id,))
        payment_session = cur.fetchone()
        if not payment_session:
            return jsonify({"error": "Payment session not found. Please restart checkout."}), 404
        if payment_session["completed"]:
            return jsonify({"error": "This payment has already been used."}), 409

        expected_signature = hmac.new(
            RAZORPAY_KEY_SECRET.encode(),
            f"{payment_session['razorpay_order_id']}|{razorpay_payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_signature, razorpay_signature):
            return jsonify({"error": "Payment verification failed."}), 400

        try:
            payment = razorpay_request("GET", f"/payments/{razorpay_payment_id}")
        except RuntimeError as error:
            return jsonify({"error": str(error)}), 502
        if (payment.get("order_id") != payment_session["razorpay_order_id"] or
                payment.get("amount") != payment_session["amount"] * 100):
            return jsonify({"error": "Payment details do not match this order."}), 400
        if payment.get("status") != "captured":
            return jsonify({"error": "Payment is still being processed. Please check again shortly."}), 409

        cur.execute(
            """INSERT INTO orders
               (customer, email, mobile, address, total, items, created_at, razorpay_order_id, razorpay_payment_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (payment_session["customer"], payment_session["email"], payment_session["mobile"],
             payment_session["address"], payment_session["amount"], json.dumps(payment_session["items"]),
             datetime.now(timezone.utc).isoformat(), payment_session["razorpay_order_id"], razorpay_payment_id),
        )
        order_id = cur.fetchone()["id"]
        cur.execute("UPDATE payment_sessions SET completed = TRUE WHERE razorpay_order_id = %s", (razorpay_order_id,))
        conn.commit()
        return jsonify({"order_id": order_id, "total": payment_session["amount"], "status": "confirmed"}), 201
    finally:
        cur.close()
        conn.close()


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
