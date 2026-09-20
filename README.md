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

PostgreSQL stores the application data. Local uploads are created inside this
directory and ignored by Git.

## Razorpay checkout

Add Razorpay test keys to `.env` before using checkout:

```env
RAZORPAY_KEY_ID=rzp_test_your_key_id
RAZORPAY_KEY_SECRET=your_key_secret
```

The backend calculates the amount from current product prices, creates the
Razorpay Order, verifies the payment signature and captured payment status, and
only then creates the store order. Never expose `RAZORPAY_KEY_SECRET` in the
frontend. Use test keys until the complete flow has been verified in Razorpay's
test mode.

Standard Checkout places UPI first and displays Razorpay's UPI/QR experience.
Available UPI apps and QR presentation depend on the device and the payment
methods enabled for the Razorpay account.
