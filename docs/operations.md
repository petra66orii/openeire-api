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
- For Prodigi print orders, confirm the provider can load the image asset in sandbox/production. Physical fulfillment now prefers signed private-storage URLs for `high_res_file` assets.
- Confirm `PRODIGI_CALLBACK_BASE_URL` points at a public backend origin and `PRODIGI_CALLBACK_TOKEN` is configured. Tracking callbacks are intentionally disabled unless both are present.
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
  - If only one of the Google env vars is set, startup should fail fast with `ImproperlyConfigured`.

### Order created but fulfillment/email failed
- Cause: downstream provider issue (Prodigi/SMTP).
- Fix: inspect logs, verify provider credentials and network access, retry manually where appropriate.

### Prodigi order exists but image/cost remains pending
- Cause: Prodigi is still fetching or processing the print asset, or sandbox pricing has not finalized yet.
- Fix:
  1. Verify the order image loads in the Prodigi dashboard.
  2. Confirm private R2 credentials are valid so signed asset URLs can be generated.
  3. Keep `SITE_URL` set to a real public origin as fallback, but expect production fulfillment to prefer storage-generated signed URLs.

### Prodigi shipment tracking email did not send
- Cause: Prodigi callback not configured, callback rejected, callback arrived before tracking was available, or email delivery failed.
- Fix:
  1. Verify `PRODIGI_CALLBACK_BASE_URL` resolves publicly to `/api/checkout/prodigi/callback/`.
  2. Confirm `PRODIGI_CALLBACK_TOKEN` is configured and the callback URL includes the same token. If the token is missing, the API will not attach a callback URL to Prodigi orders.
  3. Treat `PRODIGI_CALLBACK_TOKEN` as a high-entropy shared secret and rotate it if callback URLs or proxy logs may have exposed query strings.
  4. Where possible, configure proxy/CDN access logs to avoid storing full callback query strings.
  5. Check the order in admin or shell for:
     - `prodigi_order_id`
     - `prodigi_status`
     - `prodigi_shipments`
     - `tracking_email_sent_at`
     - `tracking_email_signature`
  6. If `prodigi_shipments` is present but has no tracking URL/number yet, no customer email should be sent yet.
  7. If tracking exists but no email was sent, inspect SMTP/email logs and the application logs for `Failed to send tracking email after Prodigi callback`.

### Digital downloads denied unexpectedly
- Cause: missing purchase linkage or wrong user context.
- Fix: verify `OrderItem` references and user identity in order history.
