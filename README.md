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

SQLite stores application data in `store.db` inside this directory by default.
Set `DATABASE_URL` to a PostgreSQL connection URL to use PostgreSQL instead;
the backend loads local values from `.env`, the workspace `.env`, or the
git-ignored `.env.aws` file. PostgreSQL schema creation and product seeding run
on startup. Keep database credentials out of tracked files and use a VPC or
security-group rule that allows the backend host to reach the database. Override
the SQLite file location with `SQLITE_DATABASE_PATH` when using the local
fallback. Local uploads are created inside this directory and ignored by Git.

## Product images in S3

Set `STORAGE_BACKEND=s3`, `OBJECT_STORAGE_BUCKET` to the bucket's exact name,
and `OBJECT_STORAGE_REGION` to its AWS region in `.env`. The backend uses
boto3's standard credential chain: use an AWS CLI profile locally or an
attached IAM role in deployment. Grant that identity `s3:PutObject`,
`s3:GetObject`, and `s3:DeleteObject` on the bucket's `Product_images/*` objects.
Keep the bucket private; with `OBJECT_STORAGE_PUBLIC_URL` unset, the API serves
images through short-lived presigned URLs.

Each uploaded image gets a unique S3 object key such as
`Product_images/front-<uuid>.jpg`. That key is the image reference ID; the product's
`image_front`, `image_back`, and other `image_*` fields store it as
`/uploads/<object-key>`. The admin create/update response and product API return
those fields, and the `/uploads/...` route resolves them to the image.

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

## Email sending with AWS SES

Checkout sends a test email to `/api/send-email` with `email`, `subject`, and
`message`, then continues without verifying email ownership. The backend uses
`SES_FROM_EMAIL` as the SES sender. Standalone OTP endpoints `/api/otp/send` and
`/api/otp/verify` remain available separately.

Example request:

```json
{
  "email": "customer@example.com",
  "subject": "Test Email",
  "message": "Hello from my Flask application using Amazon SES."
}
```
Verify the sender address or domain in SES and set the sender in `.env`:

```env
OTP_EMAIL_BACKEND=ses
SES_FROM_EMAIL=verified-sender@example.com
AWS_REGION=ap-south-1
AWS_PROFILE=cloth-store
```

Configure the local profile with `aws configure --profile cloth-store`; Boto3
uses it through the standard AWS credential chain. In production, use an
attached IAM role instead. Grant the identity `ses:SendEmail` permission. SES
sandbox accounts can send only to verified recipients; request production
access before sending to customers. Configure `OTP_EMAIL_BACKEND` and
`OTP_SECRET` separately if using the standalone OTP endpoints.

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
