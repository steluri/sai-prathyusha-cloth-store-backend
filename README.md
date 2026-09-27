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

## Product images in S3

Set `STORAGE_BACKEND=s3`, `OBJECT_STORAGE_BUCKET` to the bucket's exact name,
and `OBJECT_STORAGE_REGION` to its AWS region in `.env`. The backend uses
boto3's standard credential chain: use an AWS CLI profile locally or an
attached IAM role in deployment. Grant that identity `s3:PutObject`,
`s3:GetObject`, and `s3:DeleteObject` on the bucket's `products/*` objects.
Keep the bucket private; with `OBJECT_STORAGE_PUBLIC_URL` unset, the API serves
images through short-lived presigned URLs.

Each uploaded image gets a unique S3 object key such as
`products/front-<uuid>.jpg`. That key is the image reference ID; the product's
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

## SMS notifications with AWS SNS

The OTP send and verify endpoints are `/api/otp/send` and `/api/otp/verify`.
OTP codes are time-limited, single-use, and attempt-limited. Set the following
in `.env` to send OTPs and order status updates as SMS messages:

```env
SMS_BACKEND=sns
AWS_REGION=ap-south-1
ORDER_STATUS_SNS_TOPIC_ARN=arn:aws:sns:ap-south-1:123456789012:Order-status-topic
OTP_SECRET=use-a-strong-random-secret
OTP_VERIFICATION_TOKEN_SECONDS=1800
```

The application uses the standard AWS SDK credential chain; use an attached IAM
role in production or a local AWS profile for development. Grant the identity
`sns:Publish` permission. SMS delivery also depends on the AWS account's SNS
SMS origination settings, destination-country rules, and spending limits. The
default `SMS_BACKEND=console` keeps development local and returns the OTP in
the send response.

OTP messages are published directly to the verified checkout mobile number,
not to a topic, because topic messages are delivered to every subscriber.
`ORDER_STATUS_SNS_TOPIC_ARN` publishes order updates to the configured topic;
order updates are also sent directly to the customer by SMS.

Order status changes are managed from the admin console's Orders tab. Supported
statuses are `confirmed`, `processing`, `shipped`, `delivered`, and `cancelled`.
The customer receives an SMS after checkout confirmation and each actual status
change; an SMS failure is logged without undoing a paid order or saved status.
