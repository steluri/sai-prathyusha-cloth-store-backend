import os
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

from database import IS_POSTGRES, get_connection
from schema import initialize_schema


EMAIL = "demo@pandu.local"
PASSWORD = "PanduTest123"
NAME = "Pandu Demo"


def seed_dummy_user():
    if IS_POSTGRES and os.environ.get("ALLOW_DUMMY_USER_SEED", "").strip().lower() not in {"1", "true", "yes"}:
        raise RuntimeError("Dummy users are disabled for PostgreSQL. Set ALLOW_DUMMY_USER_SEED=true only in a test environment.")

    initialize_schema()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """INSERT INTO users (name, email, password_hash, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET name = ?, password_hash = ?""",
            (NAME, EMAIL, generate_password_hash(PASSWORD), datetime.now(timezone.utc).isoformat(), NAME, generate_password_hash(PASSWORD)),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    seed_dummy_user()
    print(f"Dummy customer ready: {EMAIL} / {PASSWORD}")