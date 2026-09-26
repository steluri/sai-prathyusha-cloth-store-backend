import psycopg2
import psycopg2.extras
from flask import Blueprint, jsonify, request


def create_catalog_blueprint(services):
    blueprint = Blueprint("catalog", __name__)

    @blueprint.get("/api/products")
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

        conn = services.db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute(query, params)
            return jsonify([dict(row) for row in cur.fetchall()])
        finally:
            cur.close()
            conn.close()

    @blueprint.get("/api/wishlist")
    def get_wishlist():
        conn = services.db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute("SELECT p.* FROM products p JOIN wishlist w ON p.id = w.product_id ORDER BY p.id")
            return jsonify([dict(row) for row in cur.fetchall()])
        finally:
            cur.close()
            conn.close()

    @blueprint.post("/api/wishlist/<int:product_id>")
    def add_wishlist(product_id):
        conn = services.db()
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

    @blueprint.delete("/api/wishlist/<int:product_id>")
    def remove_wishlist(product_id):
        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM wishlist WHERE product_id = %s", (product_id,))
            conn.commit()
            return "", 204
        finally:
            cur.close()
            conn.close()

    return blueprint