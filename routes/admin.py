import json

from flask import Blueprint, jsonify, request
from werkzeug.security import check_password_hash
from storage import key_from_upload_path

PRODUCT_ITEM_TYPES = {
    "Men": {"Shirts", "Jeans", "Trousers", "T-shirts", "Kurtas", "Dhotis", "Inners"},
    "Women": {"Sarees", "Lehengas", "Kurtis", "Dresses", "Tops", "Jeans", "Chudidars", "Inners"},
    "Boy-Kid": {"Shirts", "Jeans", "Trousers", "T-shirts", "Kurtas", "Dhotis", "Inners"},
    "Girl-Kid": {"Sarees", "Lehengas", "Kurtis", "Dresses", "Tops", "Jeans", "Chudidars", "Inners"},
}
ADULT_SIZES = {"XS", "S", "M", "L", "XL", "XXL", "Free Size"}
KID_SIZES = {"1-2Y", "2-3Y", "3-4Y", "4-5Y", "5-6Y", "6-7Y", "7-8Y", "8-9Y", "9-10Y", "10-11Y", "11-12Y", "12-13Y", "13-14Y"}
INCH_SIZES = {f"{size} in" for size in range(20, 41)}
LOWER_BODY_TYPES = {"Jeans", "Trousers", "Dhotis", "Chudidars"}


def size_options_for(category, item_type):
    if category in {"Boy-Kid", "Girl-Kid"}:
        return KID_SIZES
    if item_type in LOWER_BODY_TYPES:
        return INCH_SIZES
    return ADULT_SIZES


def _is_product_image_path(path):
    key = key_from_upload_path(path or "")
    return bool(key and key.startswith("Product_images/"))


def create_admin_blueprint(services):
    blueprint = Blueprint("admin", __name__)

    @blueprint.post("/api/admin/login")
    def admin_login():
        data = request.get_json(silent=True) or {}
        username, password = data.get("username", ""), data.get("password", "")
        if username != services.ADMIN_USERNAME or not check_password_hash(services.ADMIN_PASSWORD_HASH, password):
            return jsonify({"error": "Invalid username or password"}), 401
        token = services.token_serializer.dumps({"username": services.ADMIN_USERNAME})
        return jsonify({"token": token})

    @blueprint.post("/api/admin/logout")
    @services.require_admin
    def admin_logout():
        return "", 204

    @blueprint.post("/api/admin/product-images")
    @services.require_admin
    def upload_product_image():
        file = request.files.get("file")
        slot = request.form.get("slot", "").strip()
        if slot not in services.IMAGE_SLOTS:
            return jsonify({"error": "Choose a valid product image slot."}), 400
        if not file or not file.filename:
            return jsonify({"error": "Choose an image to upload."}), 400
        path, error = services.save_image(file, slot)
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"path": path}), 201

    @blueprint.delete("/api/admin/product-images")
    @services.require_admin
    def delete_staged_product_image():
        path = str((request.get_json(silent=True) or {}).get("path", "")).strip()
        if not _is_product_image_path(path):
            return jsonify({"error": "Invalid product image path."}), 400

        conn = services.db()
        cur = conn.cursor()
        try:
            image_columns = ["image", *(f"image_{slot}" for slot in services.IMAGE_SLOTS)]
            conditions = " OR ".join(f"{column} = ?" for column in image_columns)
            cur.execute(f"SELECT 1 FROM products WHERE {conditions} LIMIT 1", [path] * len(image_columns))
            if cur.fetchone():
                return jsonify({"error": "This image is already assigned to a product."}), 409
        finally:
            cur.close()
            conn.close()

        services.delete_uploaded_file(path)
        return "", 204

    @blueprint.post("/api/admin/products")
    @services.require_admin
    def admin_create_product():
        form = request.form
        name, category, item_type, color, description = (
            form.get("name", "").strip(), form.get("category", "").strip(),
            form.get("item_type", "").strip(),
            form.get("color", "").strip(), form.get("description", "").strip(),
        )
        sizes = list(dict.fromkeys(form.getlist("sizes")))
        badge = form.get("badge", "").strip() or None
        if not name or category not in PRODUCT_ITEM_TYPES or item_type not in PRODUCT_ITEM_TYPES.get(category, set()) or not color or not description:
            return jsonify({"error": "Name, a valid category and item type, color, and description are required."}), 400
        if not sizes or any(size not in size_options_for(category, item_type) for size in sizes):
            return jsonify({"error": "Select one or more valid sizes for this category."}), 400
        sizes_json = json.dumps(sizes)
        try:
            price = int(form.get("price", ""))
            old_price = int(form["old_price"]) if form.get("old_price") else None
        except ValueError:
            return jsonify({"error": "Price must be a valid number."}), 400

        primary_path = str(form.get("front_path", "")).strip()
        primary_file = request.files.get("front")
        if primary_file and primary_file.filename:
            if primary_path:
                return jsonify({"error": "Provide either an uploaded primary image or a primary image path, not both."}), 400
            primary_path, error = services.save_image(primary_file, "front")
            if error:
                return jsonify({"error": error}), 400
        if not _is_product_image_path(primary_path):
            return jsonify({"error": "The primary product image is required."}), 400
        additional_paths = [str(path).strip() for path in form.getlist("additional_image_paths") if str(path).strip()]
        additional_files = [file for file in request.files.getlist("additional_images") if file and file.filename]
        if any(not _is_product_image_path(path) for path in additional_paths):
            return jsonify({"error": "One or more optional image paths are invalid."}), 400
        if len(additional_paths) + len(additional_files) > len(services.IMAGE_SLOTS) - 1:
            return jsonify({"error": f"A maximum of {len(services.IMAGE_SLOTS)} product images is allowed."}), 400

        image_paths = {slot: None for slot in services.IMAGE_SLOTS}
        image_paths["front"] = primary_path
        for slot, path in zip(services.IMAGE_SLOTS[1:], additional_paths):
            image_paths[slot] = path
        remaining_slots = services.IMAGE_SLOTS[1 + len(additional_paths):]
        for slot, file in zip(remaining_slots, additional_files):
            saved, error = services.save_image(file, slot)
            if error:
                return jsonify({"error": error}), 400
            image_paths[slot] = saved

        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute(
                     """INSERT INTO products (name, category, item_type, sizes, price, old_price, image, badge, color, description,
                    image_front, image_back, image_side, image_closeup, image_model, image_fit)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   RETURNING *""",
                     (name, category, item_type, sizes_json, price, old_price, image_paths["front"], badge, color, description,
                 image_paths["front"], image_paths["back"], image_paths["side"],
                 image_paths["closeup"], image_paths["model"], image_paths["fit"]),
            )
            row = cur.fetchone()
            conn.commit()
            saved_product = dict(row)
            saved_product["sizes"] = sizes
            return jsonify(saved_product), 201
        finally:
            cur.close()
            conn.close()

    @blueprint.put("/api/admin/products/<int:product_id>")
    @services.require_admin
    def admin_update_product(product_id):
        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM products WHERE id = ?", (product_id,))
            existing = cur.fetchone()
            if not existing:
                return jsonify({"error": "Product not found"}), 404

            form = request.form
            name, category, item_type, color, description = (
                form.get("name", "").strip(), form.get("category", "").strip(),
                form.get("item_type", "").strip(),
                form.get("color", "").strip(), form.get("description", "").strip(),
            )
            sizes = list(dict.fromkeys(form.getlist("sizes")))
            badge = form.get("badge", "").strip() or None
            if not name or category not in PRODUCT_ITEM_TYPES or item_type not in PRODUCT_ITEM_TYPES.get(category, set()) or not color or not description:
                return jsonify({"error": "Name, a valid category and item type, color, and description are required."}), 400
            if not sizes or any(size not in size_options_for(category, item_type) for size in sizes):
                return jsonify({"error": "Select one or more valid sizes for this category."}), 400
            sizes_json = json.dumps(sizes)
            try:
                price = int(form.get("price", ""))
                old_price = int(form["old_price"]) if form.get("old_price") else None
            except ValueError:
                return jsonify({"error": "Price must be a valid number."}), 400

            image_paths = {slot: existing[f"image_{slot}"] for slot in services.IMAGE_SLOTS}
            if not image_paths["front"]:
                image_paths["front"] = existing["image"]
            primary_path = str(form.get("front_path", "")).strip()
            primary_file = request.files.get("front")
            if primary_file and primary_file.filename:
                if primary_path:
                    return jsonify({"error": "Provide either an uploaded primary image or a primary image path, not both."}), 400
                primary_path, error = services.save_image(primary_file, "front")
                if error:
                    return jsonify({"error": error}), 400
            if primary_path and not _is_product_image_path(primary_path):
                return jsonify({"error": "The primary product image path is invalid."}), 400
            additional_paths = [str(path).strip() for path in form.getlist("additional_image_paths") if str(path).strip()]
            additional_files = [file for file in request.files.getlist("additional_images") if file and file.filename]
            if any(not _is_product_image_path(path) for path in additional_paths):
                return jsonify({"error": "One or more optional image paths are invalid."}), 400
            available_slots = [slot for slot in services.IMAGE_SLOTS[1:] if not image_paths[slot]]
            if len(additional_paths) + len(additional_files) > len(available_slots):
                return jsonify({"error": f"Only {len(available_slots)} optional image slots are available for this product."}), 400

            files_by_slot = []
            if primary_path:
                files_by_slot.append(("front", primary_path))
            files_by_slot.extend(zip(available_slots, additional_paths))
            for slot, image_path in files_by_slot:
                old_path = image_paths[slot]
                image_paths[slot] = image_path
                if old_path and old_path != image_path:
                    services.delete_uploaded_file(old_path)

            for slot, file in zip(available_slots[len(additional_paths):], additional_files):
                saved, error = services.save_image(file, slot)
                if error:
                    return jsonify({"error": error}), 400
                old_path = image_paths[slot]
                image_paths[slot] = saved
                if old_path and old_path != saved:
                    services.delete_uploaded_file(old_path)

            cur.execute(
                """UPDATE products SET name = ?, category = ?, item_type = ?, sizes = ?, price = ?, old_price = ?, image = ?, badge = ?,
                    color = ?, description = ?, image_front = ?, image_back = ?, image_side = ?,
                    image_closeup = ?, image_model = ?, image_fit = ? WHERE id = ?
                    RETURNING *""",
                (name, category, item_type, sizes_json, price, old_price, image_paths["front"], badge, color, description,
                 image_paths["front"], image_paths["back"], image_paths["side"],
                 image_paths["closeup"], image_paths["model"], image_paths["fit"], product_id),
            )
            row = cur.fetchone()
            conn.commit()
            saved_product = dict(row)
            saved_product["sizes"] = sizes
            return jsonify(saved_product)
        finally:
            cur.close()
            conn.close()

    @blueprint.delete("/api/admin/products/<int:product_id>")
    @services.require_admin
    def admin_delete_product(product_id):
        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM products WHERE id = ?", (product_id,))
            existing = cur.fetchone()
            if not existing:
                return jsonify({"error": "Product not found"}), 404
            cur.execute("DELETE FROM wishlist WHERE product_id = ?", (product_id,))
            cur.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
        finally:
            cur.close()
            conn.close()

        for slot in services.IMAGE_SLOTS:
            services.delete_uploaded_file(existing[f"image_{slot}"])
        return "", 204

    return blueprint