import json
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, request
from itsdangerous import BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from psycopg2 import IntegrityError as PostgresIntegrityError
except ImportError:
    class PostgresIntegrityError(Exception):
        pass


def create_auth_blueprint(services):
    blueprint = Blueprint("auth", __name__)

    def current_user_id():
        auth = request.headers.get("Authorization", "")
        token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else ""
        try:
            return int(services.customer_token_serializer.loads(
                token, max_age=services.CUSTOMER_TOKEN_MAX_AGE
            )["user_id"])
        except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
            return None

    def require_customer(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            user_id = current_user_id()
            if user_id is None:
                return jsonify({"error": "Unauthorized"}), 401
            return function(user_id, *args, **kwargs)
        return wrapper

    def account_response(user):
        return {
            "token": services.customer_token_serializer.dumps({"user_id": user["id"]}),
            "user": {"id": user["id"], "name": user["name"], "email": user["email"]},
        }

    @blueprint.post("/api/auth/register")
    def register():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        email = services.normalize_email(data.get("email"))
        password = str(data.get("password", ""))
        if len(name) < 2 or len(name) > 100:
            return jsonify({"error": "Name must be between 2 and 100 characters."}), 400
        if not email:
            return jsonify({"error": "Enter a valid email address."}), 400
        if len(password) < 8 or len(password) > 128:
            return jsonify({"error": "Password must be between 8 and 128 characters."}), 400

        connection = services.db()
        cursor = connection.cursor()
        try:
            try:
                cursor.execute(
                    """INSERT INTO users (name, email, password_hash, created_at)
                       VALUES (?, ?, ?, ?) RETURNING id, name, email""",
                    (name, email, generate_password_hash(password), datetime.now(timezone.utc).isoformat()),
                )
                user = cursor.fetchone()
                connection.commit()
            except (sqlite3.IntegrityError, PostgresIntegrityError):
                connection.rollback()
                return jsonify({"error": "An account with this email already exists."}), 409
            return jsonify(account_response(user)), 201
        finally:
            cursor.close()
            connection.close()

    @blueprint.post("/api/auth/login")
    def login():
        data = request.get_json(silent=True) or {}
        email = services.normalize_email(data.get("email"))
        password = str(data.get("password", ""))
        if not email or not password:
            return jsonify({"error": "Enter your email and password."}), 400

        connection = services.db()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT id, name, email, password_hash FROM users WHERE email = ?", (email,))
            user = cursor.fetchone()
            if not user or not check_password_hash(user["password_hash"], password):
                return jsonify({"error": "Email or password is incorrect."}), 401
            return jsonify(account_response(user))
        finally:
            cursor.close()
            connection.close()

    @blueprint.get("/api/auth/me")
    @require_customer
    def me(user_id):
        connection = services.db()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()
            if not user:
                return jsonify({"error": "Unauthorized"}), 401
            return jsonify({"user": dict(user)})
        finally:
            cursor.close()
            connection.close()

    @blueprint.get("/api/account/orders")
    @require_customer
    def account_orders(user_id):
        connection = services.db()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT id, customer, email, address, total, items, created_at, status
                   FROM orders WHERE user_id = ? ORDER BY id DESC""",
                (user_id,),
            )
            orders = [dict(order) for order in cursor.fetchall()]
            for order in orders:
                order["items"] = json.loads(order["items"])
            return jsonify(orders)
        finally:
            cursor.close()
            connection.close()

    return blueprint