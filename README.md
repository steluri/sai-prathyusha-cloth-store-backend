# Backend

Flask REST API for products, wishlist, orders, admin authentication, and product-image storage.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

The API runs at `http://localhost:5001`. Configure allowed frontend origins with the comma-separated `CORS_ORIGINS` value in `.env`.

SQLite data and local uploads are created inside this directory and ignored by Git.
