from __future__ import annotations

import json
import os
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
                email TEXT NOT NULL,
                total INTEGER NOT NULL,
                items JSONB NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        
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


@app.post("/api/orders")
def create_order():
    data = request.get_json(silent=True) or {}
    customer, email, items = data.get("customer", "").strip(), data.get("email", "").strip(), data.get("items", [])
    if not customer or "@" not in email or not items:
        return jsonify({"error": "Name, valid email, and cart items are required."}), 400
    ids = [item.get("product_id") for item in items]
    
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
        cur.execute(
            "INSERT INTO orders(customer, email, total, items, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (customer, email, total, json.dumps(items), datetime.now(timezone.utc).isoformat()),
        )
        order_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({"order_id": order_id, "total": total, "status": "confirmed"}), 201
    finally:
        cur.close()
        conn.close()


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
