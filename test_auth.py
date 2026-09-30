import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from itsdangerous import URLSafeTimedSerializer

import schema
from routes.auth import create_auth_blueprint


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.directory.name) / "auth.db"
        self.schema_connection = patch.object(schema, "get_connection", self.connection)
        self.schema_backend = patch.object(schema, "IS_POSTGRES", False)
        self.schema_connection.start()
        self.schema_backend.start()
        schema.initialize_schema()
        services = SimpleNamespace(
            db=self.connection,
            normalize_email=lambda value: str(value).strip().lower() if value and "@" in str(value) else None,
            customer_token_serializer=URLSafeTimedSerializer("test-secret", salt="customer-auth"),
            CUSTOMER_TOKEN_MAX_AGE=3600,
        )
        app = Flask(__name__)
        app.register_blueprint(create_auth_blueprint(services))
        self.client = app.test_client()

    def tearDown(self):
        self.schema_backend.stop()
        self.schema_connection.stop()
        self.directory.cleanup()

    def connection(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def test_register_login_and_current_user(self):
        payload = {"name": "Asha Rao", "email": "ASHA@example.com", "password": "clothes123"}
        registered = self.client.post("/api/auth/register", json=payload)
        self.assertEqual(registered.status_code, 201)
        self.assertEqual(registered.json["user"]["email"], "asha@example.com")
        self.assertEqual(self.client.post("/api/auth/register", json=payload).status_code, 409)
        self.assertEqual(self.client.post("/api/auth/login", json={**payload, "password": "wrongpass"}).status_code, 401)

        logged_in = self.client.post("/api/auth/login", json=payload)
        headers = {"Authorization": f"Bearer {logged_in.json['token']}"}
        response = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["user"]["name"], "Asha Rao")
        connection = self.connection()
        connection.execute(
            """INSERT INTO orders (user_id, customer, email, total, items, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (registered.json["user"]["id"], "Asha Rao", "asha@example.com", 2499, "[]", "2026-09-30T00:00:00+00:00"),
        )
        connection.commit()
        connection.close()
        self.assertEqual(self.client.get("/api/account/orders", headers=headers).json[0]["total"], 2499)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_password_must_have_eight_characters(self):
        response = self.client.post("/api/auth/register", json={
            "name": "Asha Rao", "email": "asha@example.com", "password": "short",
        })
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()