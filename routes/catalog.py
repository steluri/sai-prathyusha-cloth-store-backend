import json

from flask import Blueprint, jsonify, request
from schema import PRODUCTS, PRODUCT_OPTIONAL_IMAGES


def product_data(row):
    product = dict(row)
    try:
        sizes = json.loads(product.get("sizes") or "[]")
    except (TypeError, ValueError):
        sizes = []
    product["sizes"] = sizes if isinstance(sizes, list) else []
    return product


def create_catalog_blueprint(services):
    blueprint = Blueprint("catalog", __name__)
    sample_products = [
        {
            "id": product_id,
            "name": name,
            "category": category,
            "item_type": None,
            "sizes": [],
            "price": price,
            "old_price": old_price,
            "image": image,
            "badge": badge,
            "color": color,
            "description": description,
            "image_front": image_front,
            **PRODUCT_OPTIONAL_IMAGES[product_id],
        }
        for product_id, (name, category, price, old_price, image, badge, color, description, image_front)
        in enumerate(PRODUCTS, start=1)
    ]
    sample_products_by_id = {product["id"]: product for product in sample_products}
    sample_wishlist = set()

    @blueprint.get("/api/products")
    def products():
        category = request.args.get("category")
        search = request.args.get("search", "").strip()
        if services.DATABASE_DISABLED:
            results = sample_products
            if category and category != "All":
                results = [product for product in results if product["category"] == category]
            if search:
                search_term = search.casefold()
                results = [
                    product for product in results
                    if search_term in " ".join((product["name"], product["description"], product["color"])).casefold()
                ]
            return jsonify(results)

        query = "SELECT * FROM products WHERE 1=1"
        params = []

        if category and category != "All":
            query += " AND category = ?"
            params.append(category)
        if search:
            query += " AND (name LIKE ? OR description LIKE ? OR color LIKE ?)"
            params.extend([f"%{search}%"] * 3)
        query += " ORDER BY id"

        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute(query, params)
            return jsonify([product_data(row) for row in cur.fetchall()])
        finally:
            cur.close()
            conn.close()

    @blueprint.get("/api/wishlist")
    def get_wishlist():
        if services.DATABASE_DISABLED:
            return jsonify([product for product in sample_products if product["id"] in sample_wishlist])

        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT p.* FROM products p JOIN wishlist w ON p.id = w.product_id ORDER BY p.id")
            return jsonify([product_data(row) for row in cur.fetchall()])
        finally:
            cur.close()
            conn.close()

    @blueprint.post("/api/wishlist/<int:product_id>")
    def add_wishlist(product_id):
        if services.DATABASE_DISABLED:
            if product_id not in sample_products_by_id:
                return jsonify({"error": "Product not found"}), 404
            sample_wishlist.add(product_id)
            return jsonify({"product_id": product_id, "saved": True}), 201

        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1 FROM products WHERE id = ?", (product_id,))
            if not cur.fetchone():
                return jsonify({"error": "Product not found"}), 404
            cur.execute("INSERT INTO wishlist(product_id) VALUES (?) ON CONFLICT DO NOTHING", (product_id,))
            conn.commit()
            return jsonify({"product_id": product_id, "saved": True}), 201
        finally:
            cur.close()
            conn.close()

    @blueprint.delete("/api/wishlist/<int:product_id>")
    def remove_wishlist(product_id):
        if services.DATABASE_DISABLED:
            sample_wishlist.discard(product_id)
            return "", 204

        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM wishlist WHERE product_id = ?", (product_id,))
            conn.commit()
            return "", 204
        finally:
            cur.close()
            conn.close()

    return blueprint