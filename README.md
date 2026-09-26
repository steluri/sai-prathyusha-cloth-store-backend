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

SQLite stores application data in `store.db` inside this directory. The file is
created automatically on startup and ignored by Git, so no separate database
server or database package is required. Override its location with
`SQLITE_DATABASE_PATH` if needed. Local uploads are created inside this
directory and ignored by Git.

Flask setup and shared services live in `app.py`. PostgreSQL connection setup
is in `database.py`; table creation and initial product seeding are in
`schema.py`. HTTP handlers are grouped as blueprints under `routes/`: OTP,
catalog and wishlist, admin products, checkout and orders, and system/uploads.

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

## Email verification with AWS SES

Checkout sends email verification requests to `/api/send-email`; OTP verification
uses `/api/otp/verify`. The API accepts `email`, `subject`, and `message`. For
checkout OTPs, include `name` and use `{otp}` in `message`; the backend
generates and substitutes the code. The legacy send endpoint `/api/otp/send`
remains available.
OTP codes are sent by SES, time-limited, single-use, and attempt-limited. Verify
the sender address or domain in SES and set the sender in `.env`:

```env
OTP_EMAIL_BACKEND=ses
SES_FROM_EMAIL=verified-sender@example.com
AWS_REGION=ap-south-1
AWS_PROFILE=cloth-store
OTP_SECRET=use-a-strong-random-secret
OTP_VERIFICATION_TOKEN_SECONDS=1800
```

Configure the local profile with `aws configure --profile cloth-store`; Boto3
uses it through the standard AWS credential chain. In production, use an
attached IAM role instead. Grant the identity `ses:SendEmail` permission. SES
sandbox accounts can send only to verified recipients; request production
access before sending to customers. Set `OTP_EMAIL_BACKEND=console` for local
testing; that mode returns the development OTP in the send response.

OTP messages are sent directly to the verified checkout email address.

## SMS order notifications with AWS SNS

Set `SMS_BACKEND=sns`, `AWS_REGION`, and optionally
`ORDER_STATUS_SNS_TOPIC_ARN` to send order status updates by SMS/topic. The
checkout does not collect mobile numbers, so direct customer SMS is not sent for
new orders. Topic notifications can still be sent to configured subscribers.
Configure the identity with `sns:Publish` permission. The default
`SMS_BACKEND=console` keeps SMS notifications local.

Order status changes are managed from the admin console's Orders tab. Supported
statuses are `confirmed`, `processing`, `shipped`, `delivered`, and `cancelled`.
New checkout orders do not collect a mobile number, so order updates are sent
only to configured SNS topic subscribers. Older orders with a saved mobile
number can still receive direct SMS updates.
