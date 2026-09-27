from __future__ import annotations

import json
import hashlib
import hmac
import base64
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import boto3
from functools import wraps
from types import SimpleNamespace

from flask import Flask, jsonify, request
from flask_cors import CORS
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from storage import StorageError, build_storage
from werkzeug.security import generate_password_hash
from database import BASE_DIR, get_connection as db
from routes.admin import create_admin_blueprint
from routes.catalog import create_catalog_blueprint
from routes.orders import create_orders_blueprint
from routes.otp import create_otp_blueprint
from routes.system import create_system_blueprint
from schema import initialize_schema

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

SMS_BACKEND = os.environ.get("SMS_BACKEND", os.environ.get("OTP_SMS_BACKEND", "console")).strip().lower()
OTP_EMAIL_BACKEND = os.environ.get("OTP_EMAIL_BACKEND", "ses").strip().lower()
SES_FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "").strip()
OTP_EXPIRY_SECONDS = int(os.environ.get("OTP_EXPIRY_SECONDS", "300"))
OTP_VERIFICATION_TOKEN_SECONDS = int(os.environ.get("OTP_VERIFICATION_TOKEN_SECONDS", "1800"))
OTP_RESEND_SECONDS = int(os.environ.get("OTP_RESEND_SECONDS", "30"))
OTP_MAX_ATTEMPTS = int(os.environ.get("OTP_MAX_ATTEMPTS", "5"))
OTP_SECRET = os.environ.get("OTP_SECRET", os.environ.get("ADMIN_TOKEN_SECRET", "pandu-local-development-secret"))
otp_serializer = URLSafeTimedSerializer(OTP_SECRET, salt="email-verification")
otp_challenges = {}
otp_last_sent = {}
SNS_REGION = os.environ.get("AWS_REGION", os.environ.get("OTP_AWS_REGION", "ap-south-1"))
ORDER_STATUS_SNS_TOPIC_ARN = os.environ.get("ORDER_STATUS_SNS_TOPIC_ARN", "").strip()
ORDER_STATUSES = {"confirmed", "processing", "shipped", "delivered", "cancelled"}
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()


def normalize_mobile(value):
    """Return an Indian mobile number in E.164 format, or None when invalid."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        return None
    return f"+91{digits}"


def normalize_email(value):
    email = str(value or "").strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return None
    return email


def normalize_address(value):
    if not isinstance(value, dict):
        return None
    door = str(value.get("door", "")).strip()
    line1 = str(value.get("line1", "")).strip()
    line2 = str(value.get("line2", "")).strip()
    city = str(value.get("city", "")).strip()
    pincode = re.sub(r"\D", "", str(value.get("pincode", "")))
    if not door or not line1 or not city or not re.fullmatch(r"[1-9]\d{5}", pincode):
        return None
    lines = [door, line1]
    if line2:
        lines.append(line2)
    lines.extend([city, pincode])
    return ", ".join(lines)


def otp_digest(challenge_id, code):
    message = f"{challenge_id}:{code}".encode()
    return hmac.new(OTP_SECRET.encode(), message, hashlib.sha256).hexdigest()


def send_sms(mobile, message):
    if SMS_BACKEND == "console":
        app.logger.info("Development SMS for %s: %s", mobile, message)
        return False
    if SMS_BACKEND == "sns":
        boto3.client("sns", region_name=SNS_REGION).publish(PhoneNumber=mobile, Message=message)
        return True
    raise RuntimeError("SMS_BACKEND must be 'console' or 'sns'.")


def send_email(email, subject, message):
    if OTP_EMAIL_BACKEND == "console":
        app.logger.info("Development email to %s, subject %s: %s", email, subject, message)
        return False
    if OTP_EMAIL_BACKEND == "ses":
        if not SES_FROM_EMAIL:
            raise RuntimeError("Set SES_FROM_EMAIL to a verified SES sender address.")
        boto3.client("ses", region_name=SNS_REGION).send_email(
            Source=SES_FROM_EMAIL,
            Destination={"ToAddresses": [email]},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": message}},
            },
        )
        return True
    raise RuntimeError("OTP_EMAIL_BACKEND must be 'console' or 'ses'.")


def send_otp_email(email, code, subject="OTP for verification", message_template="{otp} for the verification, do not share with anyone"):
    return send_email(email, subject, message_template.replace("{otp}", code))


def notify_order_status(mobile, order_id, status):
    if not mobile and not ORDER_STATUS_SNS_TOPIC_ARN:
        return False
    message = f"Pandu order #{order_id}: your order is {status}. We will keep you updated."
    sms_sent = False
    if mobile:
        try:
            sms_sent = send_sms(mobile, message)
        except Exception:
            app.logger.exception("Could not send order status SMS to customer for order %s", order_id)
    if ORDER_STATUS_SNS_TOPIC_ARN:
        try:
            boto3.client("sns", region_name=SNS_REGION).publish(
                TopicArn=ORDER_STATUS_SNS_TOPIC_ARN,
                Subject=f"Pandu order #{order_id} {status}",
                Message=message,
            )
        except Exception:
            app.logger.exception("Could not publish order status for order %s", order_id)
    return sms_sent


def razorpay_request(method, path, payload=None):
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise RuntimeError("Razorpay is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to .env.")
    credentials = base64.b64encode(f"{RAZORPAY_KEY_ID}:{RAZORPAY_KEY_SECRET}".encode()).decode()
    body = json.dumps(payload).encode() if payload is not None else None
    api_request = Request(
        f"https://api.razorpay.com/v1{path}",
        data=body,
        method=method,
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(api_request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            detail = json.load(error).get("error", {}).get("description")
        except (ValueError, AttributeError):
            detail = None
        raise RuntimeError(detail or "Razorpay rejected the payment request.") from error
    except URLError as error:
        raise RuntimeError("Could not connect to Razorpay. Please try again.") from error


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

@app.errorhandler(StorageError)
def storage_error(error):
    app.logger.exception("Object storage operation failed")
    return jsonify({"error": str(error)}), 502


services = SimpleNamespace(
    ADMIN_PASSWORD_HASH=ADMIN_PASSWORD_HASH,
    ADMIN_USERNAME=ADMIN_USERNAME,
    IMAGE_SLOTS=IMAGE_SLOTS,
    OTP_EXPIRY_SECONDS=OTP_EXPIRY_SECONDS,
    OTP_MAX_ATTEMPTS=OTP_MAX_ATTEMPTS,
    OTP_RESEND_SECONDS=OTP_RESEND_SECONDS,
    OTP_VERIFICATION_TOKEN_SECONDS=OTP_VERIFICATION_TOKEN_SECONDS,
    ORDER_STATUSES=ORDER_STATUSES,
    OTP_EMAIL_BACKEND=OTP_EMAIL_BACKEND,
    SES_FROM_EMAIL=SES_FROM_EMAIL,
    RAZORPAY_KEY_ID=RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET=RAZORPAY_KEY_SECRET,
    SMS_BACKEND=SMS_BACKEND,
    BadSignature=BadSignature,
    SignatureExpired=SignatureExpired,
    db=db,
    delete_uploaded_file=delete_uploaded_file,
    normalize_address=normalize_address,
    normalize_email=normalize_email,
    normalize_mobile=normalize_mobile,
    notify_order_status=notify_order_status,
    otp_challenges=otp_challenges,
    otp_digest=otp_digest,
    otp_last_sent=otp_last_sent,
    otp_serializer=otp_serializer,
    razorpay_request=razorpay_request,
    require_admin=require_admin,
    save_image=save_image,
    send_otp_sms=send_otp_sms,
    token_serializer=token_serializer,
    send_email=send_email,
    send_otp_email=send_otp_email,
)

app.register_blueprint(create_system_blueprint(storage))
app.register_blueprint(create_otp_blueprint(services))
app.register_blueprint(create_catalog_blueprint(services))
app.register_blueprint(create_admin_blueprint(services))
app.register_blueprint(create_orders_blueprint(services))


initialize_schema()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
