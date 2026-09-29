import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import schema
from routes.orders import create_orders_blueprint


class UpiOrderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.directory.name) / "orders.db"
        self.schema_connection = patch.object(schema, "get_connection", self.connection)
        self.schema_backend = patch.object(schema, "IS_POSTGRES", False)
        self.schema_connection.start()
        self.schema_backend.start()
        schema.initialize_schema()
        services = SimpleNamespace(
            db=self.connection,
            normalize_email=lambda value: value if value and "@" in value else None,
            normalize_address=lambda value: "Test address" if value else None,
            require_admin=lambda function: function,
            ORDER_STATUSES={"confirmed", "processing", "shipped", "delivered", "cancelled"},
            IS_POSTGRES=False,
            notify_order_status=lambda *args: False,
        )
        app = Flask(__name__)
        app.register_blueprint(create_orders_blueprint(services))
        self.client = app.test_client()
        self.order = {
            "customer": "Test Customer", "email": "test@example.com", "address": {"door": "4"},
            "items": [{"product_id": 1, "quantity": 2}], "utr": "123456789012", "expected_total": 4998,
        }

    def tearDown(self):
        self.schema_backend.stop()
        self.schema_connection.stop()
        self.directory.cleanup()

    def connection(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def test_pending_order_contains_customer_items_and_utr(self):
        response = self.client.post("/api/orders/upi", json=self.order)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["status"], "pending_verification")
        saved = self.client.get("/api/admin/orders").json[0]
        self.assertEqual(saved["customer"], self.order["customer"])
        self.assertEqual(saved["total"], 4998)
        self.assertEqual(saved["upi_utr"], self.order["utr"])
        self.assertEqual(saved["items"][0]["name"], "Linen Ease Shirt")
        self.assertEqual(self.client.post("/api/orders/upi", json=self.order).status_code, 409)

    def test_amount_and_reference_are_checked(self):
        self.assertEqual(self.client.post("/api/orders/upi", json={**self.order, "expected_total": 1}).status_code, 409)
        self.assertEqual(self.client.post("/api/orders/upi", json={**self.order, "utr": "invalid"}).status_code, 400)
        self.assertEqual(self.client.get("/api/admin/orders").json, [])

    def test_delivery_requires_manual_confirmation(self):
        order_id = self.client.post("/api/orders/upi", json=self.order).json["order_id"]
        url = f"/api/admin/orders/{order_id}"
        self.assertEqual(self.client.patch(url, json={"status": "shipped"}).status_code, 409)
        self.assertEqual(self.client.patch(url, json={"status": "confirmed"}).status_code, 200)
        self.assertEqual(self.client.patch(url, json={"status": "shipped"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()