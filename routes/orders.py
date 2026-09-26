import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request


def create_orders_blueprint(services):
    blueprint = Blueprint("orders", __name__)

    @blueprint.post("/api/payments/razorpay/order")
    def create_razorpay_order():
        data = request.get_json(silent=True) or {}
        customer = str(data.get("customer", "")).strip()
        email = services.normalize_email(data.get("email"))
        verification_token = str(data.get("verification_token", "")).strip()
        address = services.normalize_address(data.get("address"))
        raw_items = data.get("items", [])
        if not customer or not email or not address or not raw_items:
            return jsonify({"error": "Enter a valid name, email address, complete address, city, and PIN code."}), 400
        try:
            verified_email = services.otp_serializer.loads(
                verification_token,
                max_age=services.OTP_VERIFICATION_TOKEN_SECONDS,
            ).get("email")
        except (services.BadSignature, services.SignatureExpired):
            return jsonify({"error": "Verify your email address before continuing to payment."}), 401
        if verified_email != email:
            return jsonify({"error": "The verified email address does not match checkout."}), 401

        try:
            items = [{"product_id": int(item["product_id"]), "quantity": int(item.get("quantity", 1))} for item in raw_items]
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "The cart contains invalid items."}), 400
        if any(item["quantity"] < 1 or item["quantity"] > 20 for item in items):
            return jsonify({"error": "Item quantity must be between 1 and 20."}), 400

        ids = [item["product_id"] for item in items]
        conn = services.db()
        cur = conn.cursor()
        try:
            placeholders = ",".join(["?"] * len(ids))
            cur.execute(f"SELECT id, price FROM products WHERE id IN ({placeholders})", ids)
            rows = cur.fetchall()
            prices = {row[0]: row[1] for row in rows}
            if len(prices) != len(set(ids)):
                return jsonify({"error": "One or more products are unavailable."}), 400

            total = sum(prices[item["product_id"]] * item["quantity"] for item in items)
            receipt = f"pandu-{uuid.uuid4().hex[:24]}"
            try:
                razorpay_order = services.razorpay_request("POST", "/orders", {
                    "amount": total * 100,
                    "currency": "INR",
                    "receipt": receipt,
                    "notes": {"store": "Pandu"},
                })
            except RuntimeError as error:
                status = 503 if not services.RAZORPAY_KEY_ID or not services.RAZORPAY_KEY_SECRET else 502
                return jsonify({"error": str(error)}), status
            if not razorpay_order.get("id") or razorpay_order.get("amount") != total * 100:
                return jsonify({"error": "Razorpay returned an invalid order. Please try again."}), 502

            cur.execute(
                """INSERT INTO payment_sessions
                   (razorpay_order_id, customer, email, mobile, address, amount, items, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (razorpay_order["id"], customer, email, "", address, total, json.dumps(items),
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return jsonify({
                "key_id": services.RAZORPAY_KEY_ID,
                "order_id": razorpay_order["id"],
                "amount": total * 100,
                "currency": "INR",
                "prefill": {"name": customer, "email": email},
            }), 201
        finally:
            cur.close()
            conn.close()

    @blueprint.post("/api/orders")
    def create_order():
        data = request.get_json(silent=True) or {}
        razorpay_order_id = str(data.get("razorpay_order_id", "")).strip()
        razorpay_payment_id = str(data.get("razorpay_payment_id", "")).strip()
        razorpay_signature = str(data.get("razorpay_signature", "")).strip()
        if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
            return jsonify({"error": "A completed Razorpay payment is required."}), 400

        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM payment_sessions WHERE razorpay_order_id = ?", (razorpay_order_id,))
            payment_session = cur.fetchone()
            if not payment_session:
                return jsonify({"error": "Payment session not found. Please restart checkout."}), 404
            if payment_session["completed"]:
                return jsonify({"error": "This payment has already been used."}), 409

            expected_signature = hmac.new(
                services.RAZORPAY_KEY_SECRET.encode(),
                f"{payment_session['razorpay_order_id']}|{razorpay_payment_id}".encode(),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected_signature, razorpay_signature):
                return jsonify({"error": "Payment verification failed."}), 400

            try:
                payment = services.razorpay_request("GET", f"/payments/{razorpay_payment_id}")
            except RuntimeError as error:
                return jsonify({"error": str(error)}), 502
            if (payment.get("order_id") != payment_session["razorpay_order_id"] or
                    payment.get("amount") != payment_session["amount"] * 100):
                return jsonify({"error": "Payment details do not match this order."}), 400
            if payment.get("status") != "captured":
                return jsonify({"error": "Payment is still being processed. Please check again shortly."}), 409

            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT completed FROM payment_sessions WHERE razorpay_order_id = ?", (razorpay_order_id,))
            current_session = cur.fetchone()
            if not current_session:
                conn.rollback()
                return jsonify({"error": "Payment session not found. Please restart checkout."}), 404
            if current_session["completed"]:
                conn.rollback()
                return jsonify({"error": "This payment has already been used."}), 409

            cur.execute(
                """INSERT INTO orders
                   (customer, email, mobile, address, total, items, created_at, razorpay_order_id, razorpay_payment_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (payment_session["customer"], payment_session["email"], payment_session["mobile"],
                 payment_session["address"], payment_session["amount"],
                 payment_session["items"] if isinstance(payment_session["items"], str) else json.dumps(payment_session["items"]),
                 datetime.now(timezone.utc).isoformat(), payment_session["razorpay_order_id"], razorpay_payment_id),
            )
            order_id = cur.fetchone()["id"]
            cur.execute("UPDATE payment_sessions SET completed = TRUE WHERE razorpay_order_id = ?", (razorpay_order_id,))
            conn.commit()
            notification_sent = services.notify_order_status(payment_session["mobile"], order_id, "confirmed")
            return jsonify({
                "order_id": order_id,
                "total": payment_session["amount"],
                "status": "confirmed",
                "notification_sent": notification_sent,
            }), 201
        finally:
            cur.close()
            conn.close()

    @blueprint.get("/api/admin/orders")
    @services.require_admin
    def admin_orders():
        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, customer, email, mobile, address, total, items, created_at, status FROM orders ORDER BY id DESC")
            orders = [dict(row) for row in cur.fetchall()]
            for order in orders:
                order["items"] = json.loads(order["items"])
            return jsonify(orders)
        finally:
            cur.close()
            conn.close()

    @blueprint.patch("/api/admin/orders/<int:order_id>")
    @services.require_admin
    def admin_update_order(order_id):
        status = str((request.get_json(silent=True) or {}).get("status", "")).strip().lower()
        if status not in services.ORDER_STATUSES:
            return jsonify({"error": f"Status must be one of: {', '.join(sorted(services.ORDER_STATUSES))}."}), 400

        conn = services.db()
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT id, mobile, status FROM orders WHERE id = ?", (order_id,))
            order = cur.fetchone()
            if not order:
                conn.rollback()
                return jsonify({"error": "Order not found."}), 404
            changed = order["status"] != status
            if changed:
                cur.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
                conn.commit()
            else:
                conn.rollback()
            notification_sent = services.notify_order_status(order["mobile"], order_id, status) if changed else False
            return jsonify({"order_id": order_id, "status": status, "notification_sent": notification_sent})
        finally:
            cur.close()
            conn.close()

    return blueprint