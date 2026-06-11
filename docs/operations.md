# Operations Guide

## Runtime Commands

### Start API server
```bash
python manage.py runserver
```

### System checks
```bash
python manage.py check
```

### Apply migrations
```bash
python manage.py migrate --noinput
```

### Run tests
```bash
python manage.py test
```

### Collect static files
```bash
python manage.py collectstatic --no-input
```

## Background Processing

### Celery workers
Celery is not configured in this repository (`Configuration Required`).

If you add Celery later, document and operate:
- worker start command
- beat scheduler command
- queue names and retry strategy

### Current internal worker pattern
An internal AI worker can poll/update licensing drafts through protected endpoints:
- `GET /api/internal/draft-queue/`
- `POST /api/internal/draft-update/<pk>/`

Commercial licensing flow summary:
- `SUBMITTED` / `NEEDS_INFO` / `APPROVED`: pre-negotiation review and scope refinement
- Negotiation draft generated and reviewed in admin
- Negotiation email sent from admin
- `AWAITING_CLIENT_CONFIRMATION`: waiting for explicit client agreement outside the system
- Admin marks client confirmed, which freezes the agreed commercial scope snapshot
- Admin explicitly generates a Stripe-backed payment offer only after confirmation
- Payment offers expire and must be regenerated before a payment email can be drafted/sent again
- Payment email draft is generated from the current valid offer and receives the offer expiry timestamp from the draft queue payload
- Payment email sent from admin
- `PAYMENT_PENDING`: waiting for Stripe checkout completion
- Stripe webhook transitions request to `PAID`, generates licence documents, emails delivery package, then transitions to `DELIVERED`

Operator guardrails:
- Use `Reset Client Confirmation` before editing scope or quoted price after client agreement has been frozen.
- Use `Regenerate Payment Offer` when the prior confirmed offer has expired or a fresh Stripe link is needed.
- Expired offers are treated as non-sendable in admin and are not queued for `payment_link` AI drafting.
- Payment links are time-limited, the expiry is communicated in the payment email, and expired offers must be regenerated before sending.

Operational requirements:
- Set strong `AI_WORKER_SECRET`.
- Optionally set `AI_WORKER_IP_ALLOWLIST` and `AI_WORKER_TRUSTED_PROXY_IPS`.

## Failed Job / Failure Handling

No task queue retry store currently exists for API jobs. Failures are handled inline by:
- Logging exceptions in views/service modules.
- Persisting Stripe webhook processing status in `StripeWebhookEvent`.
- Using idempotent Stripe event handling and Prodigi idempotency key based on order number.

Recommended operator checks:
- Inspect `StripeWebhookEvent` records for `FAILED`.
- Re-deliver Stripe webhook events from Stripe dashboard when needed.
- Verify `LicenseRequest` status transitions and audit logs in admin.
- Review `NewsletterSubscriber` records in admin for `brevo_sync_status`, `brevo_synced_at`, and `brevo_sync_error` after signup/import activity.
- Review `Order.discount_code`, `Order.discount_amount`, and any related `DiscountRedemption` when validating launch discount usage.
- For Prodigi print orders, confirm the provider can load the image asset in sandbox/production. Physical fulfillment now prefers signed private-storage URLs for `high_res_file` assets.
- Confirm `PRODIGI_CALLBACK_BASE_URL` points at a public backend origin. Tracking callbacks are disabled unless it is set.
- Set `PRODIGI_CALLBACK_TOKEN` if you want the callback endpoint protected with a shared secret; when configured, the generated Prodigi callback URL appends `?token=...`.
- The callback destination is sent to Prodigi per order using the `callbackUrl` field in the order creation payload. It is not configured elsewhere in this backend.
- Check `Order.prodigi_status`, `Order.prodigi_shipments`, and `tracking_email_sent_at` when investigating shipping/tracking issues.

## Logging and Monitoring

The code uses Python logging across views/services (`logger = logging.getLogger(__name__)`).
Logging sink/format/retention is `Configuration Required` (platform-level).

Minimum monitoring targets:
- 5xx response rates
- Stripe webhook failure counts
- Payment intent creation failures
- Email send failures
- Throttle backend availability warnings

## Cache/Throttle Operations

- Shared throttling relies on Django cache alias `throttle`.
- In production, use Redis and set:
  - `CACHE_REDIS_URL` or `REDIS_URL`
  - `REQUIRE_SHARED_THROTTLE_CACHE=True`
- If Redis is unavailable:
  - behavior depends on `THROTTLE_FAIL_OPEN`:
    - `True`: allow request and log warning
    - `False`: return throttled response

## Secret Rotation

Rotate secrets by updating environment variables and restarting processes:
- `SECRET_KEY` (requires coordinated rollout)
- Stripe keys/webhook secret
- SMTP credentials
- R2 credentials
- `AI_WORKER_SECRET`

After rotation:
1. Run `python manage.py check`.
2. Validate auth, webhook, and storage operations.
3. Monitor logs for signature/auth failures.

## Migration Operations

For schema updates:
```bash
python manage.py makemigrations
python manage.py migrate --noinput
```

Operational practice:
- Take DB backup/snapshot before production migration.
- Apply migrations once per release.
- Verify admin/API behavior on changed models.

## Basic Troubleshooting

### `ImproperlyConfigured: Shared throttle cache is required...`
- Cause: `REQUIRE_SHARED_THROTTLE_CACHE=True` without `CACHE_REDIS_URL`/`REDIS_URL`.
- Fix: set Redis URL or disable strict requirement in non-production contexts.

### Stripe webhook signature failures
- Cause: invalid/missing `STRIPE_WEBHOOK_SECRET` or forwarded raw-body issues.
- Fix: verify webhook secret and ensure raw request body reaches Django unchanged.

### Google social login returns 503 or `SocialApp.DoesNotExist`
- Cause: Google OAuth is enabled in code, but `allauth` cannot find a configured provider app.
- Fix:
  1. Set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_SECRET` in the runtime environment.
  2. Redeploy or restart the service.
  3. Verify the Google OAuth client allows the production frontend origin and redirect flow you use.
- Notes:
  - This codebase supports settings-backed Google app config, so a database `SocialApp` entry is not required when env vars are present.
  - Do not configure both a Django admin `SocialApp` and env-based Google OAuth settings for the same provider at the same time.
  - If the error mentions multiple Google SocialApp records, remove duplicate Google `SocialApp` rows in admin and keep only one.
  - If only one of the Google env vars is set, startup should fail fast with `ImproperlyConfigured`.

### Order created but fulfillment/email failed
- Cause: downstream provider issue (Prodigi/SMTP).
- Fix: inspect logs, verify provider credentials and network access, retry manually where appropriate.

### Newsletter subscriber saved locally but not in Brevo
- Cause: `BREVO_ENABLED` is false, Brevo credentials/list id are missing, or the Brevo API returned an error.
- Fix:
  1. Check `NewsletterSubscriber.brevo_sync_status` and `brevo_sync_error` in admin.
  2. Verify:
     - `BREVO_ENABLED=true`
     - `BREVO_API_KEY`
     - `BREVO_NEWSLETTER_LIST_ID`
  3. Re-run the backfill:
     - `python manage.py sync_newsletter_subscribers_to_brevo`
     - or preview first with `--dry-run`

### Welcome code rejected unexpectedly
- Cause: invalid/disabled code, cart has no eligible physical art prints, or the normalized email has already redeemed the code on a successful paid order.
- Fix:
  1. Verify:
     - `WELCOME_DISCOUNT_ENABLED=true`
     - `WELCOME_DISCOUNT_CODE=WELCOME10`
     - `WELCOME_DISCOUNT_PERCENT=10`
  2. Check the cart contains a physical print variant.
  3. Check existing `DiscountRedemption` records for the customer's normalized email.

### Prodigi order exists but image/cost remains pending
- Cause: Prodigi is still fetching or processing the print asset, or sandbox pricing has not finalized yet.
- Fix:
  1. Verify the order image loads in the Prodigi dashboard.
  2. Confirm private R2 credentials are valid so signed asset URLs can be generated.
  3. Keep `SITE_URL` set to a real public origin as fallback, but expect production fulfillment to prefer storage-generated signed URLs.

### Prodigi shipment tracking email did not send
- Cause: Prodigi callback not configured, callback token rejected, callback content type rejected, callback has not been verified against Prodigi API, order matching failed, the order has not reached shipped/dispatched state yet, or email delivery failed.
- Fix:
  1. Verify `PRODIGI_CALLBACK_BASE_URL` resolves publicly to `/api/checkout/prodigi/callback/`.
  2. If callback protection is enabled, verify Prodigi is calling the exact generated URL including the `token` query parameter.
  3. Confirm Prodigi callbacks are hitting the backend without `415 Unsupported Media Type`; callbacks are expected as JSON CloudEvents.
  4. Review application logs for:
     - `Creating Prodigi order with callback URL ...`
     - or `Prodigi callback URL omitted from Prodigi order payload ...`
     - callback `event_type`
     - payload / fetched Prodigi shipped status
     - `prodigi_order_id`
     - `merchant_reference`
     - matched local order id / order number
     - whether the shipping email was sent or skipped and why
  5. Check the order in admin or shell for:
     - `prodigi_order_id`
     - `prodigi_status`
     - `prodigi_shipments`
     - `prodigi_last_callback_at`
     - `prodigi_last_polled_at`
     - `tracking_email_sent_at`
     - `tracking_email_signature`
  6. If `prodigi_shipments` is present with shipped/dispatched status but no tracking yet, OpenEire should still send a graceful dispatched email. If that does not happen, inspect application logs for the skip reason.
  7. If tracking exists but no email was sent, inspect SMTP/email logs and the application logs for `Failed to send tracking email after Prodigi callback`.
  8. If callbacks arrive but local status remains stale, inspect logs for `Prodigi callback could not verify order against Prodigi API`.

### Manual sandbox callback test
1. Ensure the backend is publicly reachable and `PRODIGI_CALLBACK_BASE_URL` is set to that origin.
2. In Render, verify:
   - `PRODIGI_API_KEY`
   - `PRODIGI_SANDBOX=true`
   - `PRODIGI_CALLBACK_BASE_URL=https://<your-backend-origin>`
   - `PRODIGI_CALLBACK_TOKEN=<shared-secret>` if you enabled callback protection
   - SMTP variables and `DEFAULT_FROM_EMAIL`
3. Place a physical print test order and confirm the local `Order` has a `prodigi_order_id`.
4. In Prodigi sandbox, confirm the order callback URL resolves to:
   - `https://<your-backend-origin>/api/checkout/prodigi/callback/`
   - or `https://<your-backend-origin>/api/checkout/prodigi/callback/?token=<shared-secret>` when token protection is enabled
5. Trigger or wait for a shipped/dispatched callback in Prodigi sandbox.
6. In Django admin, open the order and inspect:
   - `prodigi_status`
   - `prodigi_shipments`
   - `prodigi_last_callback_at`
   - `tracking_email_sent_at`
   - `tracking_email_signature`
7. Confirm the customer receives:
   - a dispatched email even if tracking is absent
   - a follow-up shipping email with tracking if a later callback adds tracking details

### Manual refresh for missed Prodigi callbacks
If Prodigi shipped the order but no callback arrived:
1. Open the order in Django admin and confirm `prodigi_order_id` is present.
2. In the order changelist, select the affected order(s).
3. Run the admin action: `Refresh selected orders from Prodigi`.
4. Re-check:
   - `prodigi_status`
   - `prodigi_shipments`
   - `prodigi_last_polled_at`
   - `tracking_email_sent_at`
   - `tracking_email_signature`
5. If the order is shipped/dispatched, the refresh action should also send the customer shipping email.

### Scheduled fallback sync for missed Prodigi callbacks
Use this when Prodigi callbacks are delayed, unreliable, or absent, but you want OpenEire to keep polling recent physical orders and send shipping emails automatically.

Manual run:
1. Open a Render shell on the backend service.
2. Run:
   - `python manage.py sync_prodigi_shipments`
   - or `python manage.py sync_prodigi_shipments --days 90`
3. Review logs for:
   - candidate count
   - `order_number`
   - `prodigi_order_id`
   - old/new `prodigi_status`
   - whether shipping email was sent or skipped and why

Recommended schedule:
1. Add a Render Cron Job or equivalent scheduled task.
2. Command:
   - `python manage.py sync_prodigi_shipments`
3. Suggested cadence:
   - every 30 minutes for faster customer updates
   - or every 60 minutes for a lighter polling footprint

Operational notes:
- This command is a fallback, not a replacement for `POST /api/checkout/prodigi/callback/`.
- It only polls recent candidate orders with a `prodigi_order_id` and at least one physical print item.
- Duplicate shipping emails are still prevented via `tracking_email_signature`.
- Check `prodigi_last_polled_at` in admin to confirm the fallback command has touched an order recently.

### Digital downloads denied unexpectedly
- Cause: missing purchase linkage or wrong user context.
- Fix: verify `OrderItem` references and user identity in order history.
