import hmac
import re
import secrets
import time
import uuid

from flask import Blueprint, current_app, jsonify, request


def create_otp_blueprint(services):
    blueprint = Blueprint("otp", __name__)

    @blueprint.post("/api/otp/send")
    def send_otp():
        mobile = services.normalize_mobile((request.get_json(silent=True) or {}).get("mobile"))
        if not mobile:
            return jsonify({"error": "Enter a valid 10-digit Indian mobile number."}), 400

        now = time.time()
        retry_after = int(services.otp_last_sent.get(mobile, 0) + services.OTP_RESEND_SECONDS - now)
        if retry_after > 0:
            return jsonify({
                "error": f"Please wait {retry_after} seconds before requesting another OTP.",
                "retry_after": retry_after,
            }), 429

        for key, challenge in list(services.otp_challenges.items()):
            if challenge["expires_at"] <= now or challenge["mobile"] == mobile:
                services.otp_challenges.pop(key, None)

        challenge_id = str(uuid.uuid4())
        code = f"{secrets.randbelow(1_000_000):06d}"
        services.otp_challenges[challenge_id] = {
            "mobile": mobile,
            "digest": services.otp_digest(challenge_id, code),
            "expires_at": now + services.OTP_EXPIRY_SECONDS,
            "attempts": 0,
        }
        try:
            services.send_otp_sms(mobile, code)
        except Exception:
            services.otp_challenges.pop(challenge_id, None)
            current_app.logger.exception("Could not send OTP")
            return jsonify({"error": "Could not send the OTP. Please try again."}), 502

        services.otp_last_sent[mobile] = now
        response = {"challenge_id": challenge_id, "expires_in": services.OTP_EXPIRY_SECONDS}
        if services.SMS_BACKEND == "console":
            response["development_otp"] = code
        return jsonify(response), 201

    @blueprint.post("/api/otp/verify")
    def verify_otp():
        data = request.get_json(silent=True) or {}
        challenge_id = str(data.get("challenge_id", ""))
        code = str(data.get("otp", "")).strip()
        challenge = services.otp_challenges.get(challenge_id)
        if not challenge:
            return jsonify({"error": "This OTP request is invalid. Please request a new code."}), 400
        if challenge["expires_at"] <= time.time():
            services.otp_challenges.pop(challenge_id, None)
            return jsonify({"error": "This OTP has expired. Please request a new code."}), 400
        if challenge["attempts"] >= services.OTP_MAX_ATTEMPTS:
            services.otp_challenges.pop(challenge_id, None)
            return jsonify({"error": "Too many incorrect attempts. Please request a new code."}), 429

        challenge["attempts"] += 1
        expected_digest = services.otp_digest(challenge_id, code) if re.fullmatch(r"\d{6}", code) else ""
        if not expected_digest or not hmac.compare_digest(challenge["digest"], expected_digest):
            return jsonify({"error": "The OTP is incorrect. Please try again."}), 400

        mobile = challenge["mobile"]
        services.otp_challenges.pop(challenge_id, None)
        return jsonify({"mobile": mobile, "verification_token": services.otp_serializer.dumps({"mobile": mobile})})

    return blueprint