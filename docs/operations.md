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

### Order created but fulfillment/email failed
- Cause: downstream provider issue (Prodigi/SMTP).
- Fix: inspect logs, verify provider credentials and network access, retry manually where appropriate.

### Prodigi order exists but image/cost remains pending
- Cause: Prodigi is still fetching or processing the print asset, or sandbox pricing has not finalized yet.
- Fix:
  1. Verify the order image loads in the Prodigi dashboard.
  2. Confirm private R2 credentials are valid so signed asset URLs can be generated.
  3. Keep `SITE_URL` set to a real public origin as fallback, but expect production fulfillment to prefer storage-generated signed URLs.

### Digital downloads denied unexpectedly
- Cause: missing purchase linkage or wrong user context.
- Fix: verify `OrderItem` references and user identity in order history.
