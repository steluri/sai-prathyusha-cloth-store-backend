# AWS SNS OTP Setup

The backend already sends OTP SMS directly to the customer's mobile number with
Amazon SNS. `SMS_BACKEND` is now set to `sns`. Live delivery still requires the
AWS access and SMS account setup below. No AWS SDK credentials are currently
available in this development environment.

## Required AWS Setup

- **AWS account and region:** The backend currently uses `AWS_REGION=ap-south-1`.
  Use a region that supports SMS and keep the SNS client and any SNS topic in
  that same region. Find regions in the AWS Region selector and the AWS End User
  Messaging SMS supported-regions documentation.
- **AWS credentials:** Give the backend an identity allowed to publish SMS.
  For a deployed backend, use an IAM role attached to its compute service. For
  local development, configure an AWS CLI profile with `aws configure --profile
  cloth-store`, then add `AWS_PROFILE=cloth-store` to `.env`. Boto3 reads this
  profile through its standard credential chain. Find or create identities in
  the AWS Console under **IAM**; do not commit access keys or put them in the
  frontend.
- **IAM permission:** The identity needs `sns:Publish` for direct SMS. Create a
  least-privilege policy in **IAM > Policies** and attach it to the user or role.
  Direct SMS publishing may require `Resource: "*"`; scope topic publishing to
  the specific topic ARN where possible.
- **SMS account access and budget:** In the AWS Console, open **Amazon SNS >
  Text messaging (SMS)** (or **AWS End User Messaging SMS**) in the configured
  region. Review the account's SMS sandbox, spending limit, and delivery
  settings. In the sandbox, verify recipient numbers first. Request production
  access before sending to unverified customers.
- **OTP signing secret:** Set `OTP_SECRET` in `.env` to a long, random secret.
  Generate one locally with `openssl rand -hex 32`. Keep it private and stable
  across restarts so active verification tokens remain valid.

The SNS topic ARN is **not required for OTPs**: OTPs go directly to the phone
number and must not be broadcast to a topic. `ORDER_STATUS_SNS_TOPIC_ARN` is only
for optional order-status topic notifications. The current local topic ARN is
in `ap-south-2`, while `AWS_REGION` is `ap-south-1`; SNS topics are regional, so
make these regions match if order-topic publishing is needed. Direct customer
order SMS does not require that topic.

## India SMS Registration

This store accepts Indian mobile numbers. For India local-route SMS, AWS's
current guidance requires TRAI DLT registration for the business/use case and
message template. The Entity ID and Template ID are obtained from the DLT
registration portal; AWS End User Messaging SMS documents the process and
sender ID registration. The OTP text must match the registered template.

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
3. Start the backend and send a request to `/api/otp/send` with a test mobile
   number. In SNS mode the response does not reveal the OTP; check the phone and
   backend logs if delivery fails.

SMS is a billable AWS service. Check current per-message pricing and the account
spending limit in the AWS Console before testing.