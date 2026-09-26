from flask import Blueprint, jsonify


def create_system_blueprint(storage):
    blueprint = Blueprint("system", __name__)

    @blueprint.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @blueprint.get("/uploads/<path:key>")
    def uploaded_file(key):
        return storage.response(key)

    return blueprint