# AWS Email and SMS Setup

Checkout sends a test email through Amazon SES without verifying email
ownership. SNS is only used for optional order-status SMS and topic
notifications. Live delivery requires AWS credentials and verified service
identities.

## Required AWS Setup

- **AWS account and region:** The backend uses `AWS_REGION=ap-south-1`. Verify
  the SES sender in this region. SNS topics must also be in the region used by
  the backend; the current optional topic ARN is in `ap-south-2` and will need
  a matching `AWS_REGION` if topic publishing is enabled.
- **AWS credentials:** Give the backend an identity allowed to publish SMS.
  For a deployed backend, use an IAM role attached to its compute service. For
  local development, configure an AWS CLI profile with `aws configure --profile
  cloth-store`, then add `AWS_PROFILE=cloth-store` to `.env`. Boto3 reads this
  profile through its standard credential chain. Find or create identities in
  the AWS Console under **IAM**; do not commit access keys or put them in the
  frontend.
- **SES sender:** Set `SES_FROM_EMAIL` to an email address or domain verified in
  Amazon SES. In the SES sandbox, recipient addresses must also be verified;
  request production access before sending to customers.
- **IAM permission:** Email sending requires `ses:SendEmail`. Optional SNS order
  notifications require `sns:Publish`. Create a least-privilege policy in
  **IAM > Policies** and attach it to the user or role; scope topic publishing
  to the specific topic ARN where possible.
- **SMS account access and budget:** In the AWS Console, open **Amazon SNS >
  Text messaging (SMS)** (or **AWS End User Messaging SMS**) in the configured
  region. Review the account's SMS sandbox, spending limit, and delivery
  settings. In the sandbox, verify recipient numbers first. Request production
  access before sending to unverified customers.
- **OTP signing secret:** Set `OTP_SECRET` in `.env` to a long, random secret.
  Generate one locally with `openssl rand -hex 32`. Keep it private and stable
  across restarts so active verification tokens remain valid.

OTP email does not use SNS or the SNS topic. `ORDER_STATUS_SNS_TOPIC_ARN` is
only for optional order-status topic notifications.

## Optional India SMS Registration

Checkout does not collect mobile numbers, so this registration is not needed for
email OTP or new checkout orders. If direct SMS is added later, AWS's current
guidance for India local-route SMS requires TRAI DLT registration for the
business/use case and message template. The Entity ID and Template ID are
obtained from the DLT registration portal; AWS End User Messaging SMS documents
the process and sender ID registration.

The current integration calls SNS `Publish` with a phone number and message; it
does not pass India DLT Entity ID or Template ID values. Confirm an approved
route with AWS before relying on it for Indian delivery. If local-route DLT
delivery is required, the backend needs an integration that supplies those IDs
through the AWS-supported SMS API.

Where to get the details:

- AWS account, IAM identity, role, and policy: [AWS IAM Console](https://console.aws.amazon.com/iam/)
- Region availability and SMS setup: [AWS End User Messaging SMS documentation](https://docs.aws.amazon.com/sms-voice/latest/userguide/what-is-service.html)
- SNS topic ARN: **Amazon SNS > Topics**, with the console region set to match `AWS_REGION`; copy the topic ARN from the topic details.
- India DLT and sender ID requirements: [AWS India sender ID registration guide](https://docs.aws.amazon.com/sms-voice/latest/userguide/registrations-sms-senderid-india.html)
- India entity/template IDs: your organization's DLT registration portal account, after registration is approved.

## Verify Setup

1. Configure the AWS profile or deployment role and confirm it resolves with
   `aws sts get-caller-identity`.
2. Confirm the IAM identity has `sns:Publish`, the region is correct, and the
   recipient is verified if the account is still in the SMS sandbox.
3. Start the backend and POST `email`, `subject`, and `message` fields to
  `/api/send-email`. The backend uses `SES_FROM_EMAIL` as the sender. Confirm
  delivery in the recipient inbox and check backend logs if sending fails.

SES email and SNS SMS are billable AWS services. Check current pricing and
account limits in the AWS Console before testing.