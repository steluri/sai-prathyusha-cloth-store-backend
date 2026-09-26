import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/cloth_store_db")


def get_connection():
    connection = psycopg2.connect(DATABASE_URL)
    connection.autocommit = False
    return connection