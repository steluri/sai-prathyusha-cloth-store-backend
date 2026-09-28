from flask import Blueprint, jsonify, request
from werkzeug.security import check_password_hash


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

    @blueprint.post("/api/admin/products")
    @services.require_admin
    def admin_create_product():
        form = request.form
        name, category, color, description = (
            form.get("name", "").strip(), form.get("category", "").strip(),
            form.get("color", "").strip(), form.get("description", "").strip(),
        )
        badge = form.get("badge", "").strip() or None
        if not name or category not in ("Women", "Men") or not color or not description:
            return jsonify({"error": "Name, category, color, and description are required."}), 400
        try:
            price = int(form.get("price", ""))
            old_price = int(form["old_price"]) if form.get("old_price") else None
        except ValueError:
            return jsonify({"error": "Price must be a valid number."}), 400

        primary_file = request.files.get("front")
        if not primary_file or not primary_file.filename:
            return jsonify({"error": "The primary product image is required."}), 400
        additional_files = [file for file in request.files.getlist("additional_images") if file and file.filename]
        if len(additional_files) > len(services.IMAGE_SLOTS) - 1:
            return jsonify({"error": f"A maximum of {len(services.IMAGE_SLOTS)} product images is allowed."}), 400

        image_paths = {slot: None for slot in services.IMAGE_SLOTS}
        files_by_slot = [("front", primary_file)] + list(zip(services.IMAGE_SLOTS[1:], additional_files))
        for slot, file in files_by_slot:
            saved, error = services.save_image(file, slot)
            if error:
                return jsonify({"error": error}), 400
            image_paths[slot] = saved

        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO products (name, category, price, old_price, image, badge, color, description,
                    image_front, image_back, image_side, image_closeup, image_model, image_fit)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            name, category, color, description = (
                form.get("name", "").strip(), form.get("category", "").strip(),
                form.get("color", "").strip(), form.get("description", "").strip(),
            )
            badge = form.get("badge", "").strip() or None
            if not name or category not in ("Women", "Men") or not color or not description:
                return jsonify({"error": "Name, category, color, and description are required."}), 400
            try:
                price = int(form.get("price", ""))
                old_price = int(form["old_price"]) if form.get("old_price") else None
            except ValueError:
                return jsonify({"error": "Price must be a valid number."}), 400

            image_paths = {slot: existing[f"image_{slot}"] for slot in services.IMAGE_SLOTS}
            if not image_paths["front"]:
                image_paths["front"] = existing["image"]
            primary_file = request.files.get("front")
            additional_files = [file for file in request.files.getlist("additional_images") if file and file.filename]
            available_slots = [slot for slot in services.IMAGE_SLOTS[1:] if not image_paths[slot]]
            if len(additional_files) > len(available_slots):
                return jsonify({"error": f"Only {len(available_slots)} optional image slots are available for this product."}), 400

            files_by_slot = []
            if primary_file and primary_file.filename:
                files_by_slot.append(("front", primary_file))
            files_by_slot.extend(zip(available_slots, additional_files))
            for slot, file in files_by_slot:
                saved, error = services.save_image(file, slot)
                if error:
                    return jsonify({"error": error}), 400
                old_path = image_paths[slot]
                image_paths[slot] = saved
                if old_path:
                    services.delete_uploaded_file(old_path)

            cur.execute(
                """UPDATE products SET name = ?, category = ?, price = ?, old_price = ?, image = ?, badge = ?,
                    color = ?, description = ?, image_front = ?, image_back = ?, image_side = ?,
                    image_closeup = ?, image_model = ?, image_fit = ? WHERE id = ?
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