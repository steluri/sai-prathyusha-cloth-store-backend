import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import schema
from routes.admin import create_admin_blueprint
from routes.catalog import create_catalog_blueprint


class AdminProductTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.directory.name) / "products.db"
        self.schema_connection = patch.object(schema, "get_connection", self.connection)
        self.schema_backend = patch.object(schema, "IS_POSTGRES", False)
        self.schema_connection.start()
        self.schema_backend.start()
        schema.initialize_schema()
        self.deleted_paths = []
        services = SimpleNamespace(
            require_admin=lambda function: function,
            db=self.connection,
            DATABASE_DISABLED=False,
            IMAGE_SLOTS=["front", "back", "side", "closeup", "model", "fit"],
            save_image=lambda file, slot: (f"/uploads/Product_images/{slot}-test.webp", None),
            delete_uploaded_file=self.deleted_paths.append,
        )
        app = Flask(__name__)
        app.register_blueprint(create_admin_blueprint(services))
        app.register_blueprint(create_catalog_blueprint(services))
        self.client = app.test_client()

    def tearDown(self):
        self.schema_backend.stop()
        self.schema_connection.stop()
        self.directory.cleanup()

    def connection(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def test_create_persists_child_category_and_item_type(self):
        response = self.client.post("/api/admin/products", data={
            "name": "Kids Cotton Shirt",
            "category": "Boy-Kid",
            "item_type": "Shirts",
            "sizes": ["1-2Y", "2-3Y"],
            "color": "Blue",
            "price": "1999",
            "description": "Soft cotton shirt for everyday wear.",
            "front_path": "/uploads/Product_images/front-test.webp",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["category"], "Boy-Kid")
        self.assertEqual(response.json["item_type"], "Shirts")
        self.assertEqual(response.json["sizes"], ["1-2Y", "2-3Y"])

        catalog = self.client.get("/api/products").json
        saved_product = next(item for item in catalog if item["id"] == response.json["id"])
        self.assertEqual(saved_product["sizes"], ["1-2Y", "2-3Y"])

        updated = self.client.put(f"/api/admin/products/{response.json['id']}", data={
            "name": "Kids Festive Lehenga",
            "category": "Girl-Kid",
            "item_type": "Lehengas",
            "sizes": ["4-5Y", "5-6Y"],
            "color": "Red",
            "price": "2999",
            "description": "Festive lehenga for kids.",
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json["category"], "Girl-Kid")
        self.assertEqual(updated.json["item_type"], "Lehengas")
        self.assertEqual(updated.json["sizes"], ["4-5Y", "5-6Y"])

    def test_adult_lower_body_accepts_inches_20_to_40(self):
        response = self.client.post("/api/admin/products", data={
            "name": "Straight Fit Trousers",
            "category": "Men",
            "item_type": "Trousers",
            "sizes": ["20 in", "40 in"],
            "color": "Black",
            "price": "2499",
            "description": "Straight-fit cotton trousers.",
            "front_path": "/uploads/Product_images/front-trousers.webp",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["sizes"], ["20 in", "40 in"])

    def test_create_rejects_item_type_not_available_for_category(self):
        response = self.client.post("/api/admin/products", data={
            "name": "Kids Dhoti",
            "category": "Girl-Kid",
            "item_type": "Dhotis",
            "color": "White",
            "price": "999",
            "description": "Cotton dhoti.",
        })
        self.assertEqual(response.status_code, 400)

    def test_delete_staged_image_removes_it_from_storage(self):
        path = "/uploads/Product_images/back-test.webp"
        response = self.client.delete("/api/admin/product-images", json={"path": path})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.deleted_paths, [path])

    def test_existing_database_gains_item_type_without_losing_products(self):
        legacy_path = Path(self.directory.name) / "legacy-products.db"

        def legacy_connection():
            connection = sqlite3.connect(legacy_path)
            connection.row_factory = sqlite3.Row
            return connection

        with patch.object(schema, "get_connection", legacy_connection):
            connection = legacy_connection()
            connection.execute("""
                CREATE TABLE products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                )
            """)
            connection.execute(
                "INSERT INTO products (name, category, price, image, color, description) VALUES (?, ?, ?, ?, ?, ?)",
                ("Legacy Shirt", "Women", 1000, "/uploads/legacy.webp", "Blue", "Existing product"),
            )
            connection.commit()
            connection.close()

            schema.initialize_schema()

            connection = legacy_connection()
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(products)")}
            product = connection.execute("SELECT name, item_type FROM products WHERE id = 1").fetchone()
            connection.close()

        self.assertIn("item_type", columns)
        self.assertIn("sizes", columns)
        self.assertEqual(product["name"], "Legacy Shirt")
        self.assertIsNone(product["item_type"])


if __name__ == "__main__":
    unittest.main()