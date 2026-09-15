from pathlib import Path

from dotenv import load_dotenv

from storage import S3Storage


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    storage = S3Storage()
    created = storage.create_bucket()
    action = "Created" if created else "Found existing"
    print(f"{action} bucket: {storage.bucket}")
