import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")
load_dotenv(BASE_DIR / ".env.aws")

USE_SQLITE = os.environ.get("USE_SQLITE", "").strip().lower() in {"1", "true", "yes", "on"}
DATABASE_URL = "" if USE_SQLITE else os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
DISABLE_DATABASE = os.environ.get("DISABLE_DATABASE", "").strip().lower() in {"1", "true", "yes", "on"}


class DatabaseDisabledError(RuntimeError):
    pass

DATABASE_PATH = Path(os.environ.get("SQLITE_DATABASE_PATH", BASE_DIR / "store.db")).expanduser()
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = BASE_DIR / DATABASE_PATH


def get_connection():
    if DISABLE_DATABASE:
        raise DatabaseDisabledError("Database access is disabled by DISABLE_DATABASE.")

    if DATABASE_URL:
        if not IS_POSTGRES:
            raise RuntimeError("DATABASE_URL must use the postgres:// or postgresql:// scheme.")
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
        except ImportError as error:
            raise RuntimeError("PostgreSQL is configured but psycopg2 is not installed.") from error

        class CompatibleCursor(RealDictCursor):
            def execute(self, query, vars=None):
                if query.strip().upper() == "BEGIN IMMEDIATE":
                    query = "BEGIN"
                return super().execute(query.replace("?", "%s"), vars)

        return psycopg2.connect(
            DATABASE_URL,
            cursor_factory=CompatibleCursor,
            connect_timeout=int(os.environ.get("DATABASE_CONNECT_TIMEOUT", "5")),
        )

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection