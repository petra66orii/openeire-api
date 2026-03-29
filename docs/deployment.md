# Deployment Guide

## Overview
This project is a Django backend intended to run behind HTTPS with static files served by WhiteNoise and media stored either locally (dev) or on S3-compatible storage (Cloudflare R2 in current settings).

## 1. Required Environment Configuration

At minimum for production startup:
- `DEBUG=False`
- `SECRET_KEY=<strong random key>`

Strongly recommended:
- `ENFORCE_STRONG_SECRET_KEY=True`
- SSL/cookie/HSTS env values aligned with your domain and proxy setup.

### Core Environment Variables
Set values for the variables used by settings and integrations:

- App/runtime:
  - `APP_ENV`, `RENDER_ENVIRONMENT`, `DJANGO_ADMIN_URL`
- Security:
  - `SECRET_KEY`, `ENFORCE_STRONG_SECRET_KEY`
  - `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`
  - `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE`, `CSRF_COOKIE_SAMESITE`
- Caching/throttling:
  - `CACHE_REDIS_URL` or `REDIS_URL`
  - `REQUIRE_SHARED_THROTTLE_CACHE`
  - `THROTTLE_FAIL_OPEN`
- Auth cookie mode (if enabled):
  - `JWT_USE_HTTPONLY_COOKIES`, `JWT_COOKIE_SECURE`, `JWT_COOKIE_SAMESITE`, `JWT_COOKIE_DOMAIN`
  - `JWT_COOKIE_CSRF_PROTECTION`, `JWT_CSRF_COOKIE_NAME`, `JWT_CSRF_HEADER_NAME`
- Storage:
  - `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_ENDPOINT_URL`
  - `R2_PRIVATE_BUCKET_NAME`, `R2_PRIVATE_ACCESS_KEY_ID`, `R2_PRIVATE_SECRET_ACCESS_KEY`
  - `R2_CUSTOM_DOMAIN`
- Email:
  - `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
- Stripe:
  - `STRIPE_PUBLIC_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
- Prodigi:
  - `PRODIGI_API_KEY`, `PRODIGI_SANDBOX`, `SITE_URL`
  - `SITE_URL` is a fallback only. In production, physical print assets should resolve through the private storage backend so Prodigi receives a short-lived signed URL instead of a permanent public media URL.
- Internal worker:
  - `AI_WORKER_SECRET` (+ optional IP allowlist vars)

If a variable is not set in code defaults and your environment requires it, treat it as `Configuration Required`.

## 2. Database Setup

Current `settings.py` uses SQLite:
- Engine: `django.db.backends.sqlite3`
- File: `db.sqlite3`

For production relational DB (e.g., PostgreSQL):
- `Configuration Required` in `settings.py` to read external DB connection settings.
- CI already provisions PostgreSQL service, but application runtime config is not switched automatically.

## 3. Redis and Throttling Setup

The app supports a shared Redis cache via Django cache backend:
- Configure `CACHE_REDIS_URL` (or `REDIS_URL`).
- Set `REQUIRE_SHARED_THROTTLE_CACHE=True` in non-debug environments to prevent accidental local-memory throttling.
- Throttle state uses cache alias `throttle` (`THROTTLE_CACHE_ALIAS`).

Without Redis:
- Cache falls back to local-memory backend (per-process counters), unless shared cache is required.

## 4. Celery Setup

Celery tasks/config are not present in this repository.
- Celery worker/beat deployment is `Configuration Required` if async task queueing is added later.
- Current async-like workflow for AI drafts uses internal protected HTTP endpoints instead.

## 5. Build and Release Steps

Observed from `build.sh`:
```bash
pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
```

Recommended release sequence:
1. Deploy code + environment variables.
2. Run `python manage.py check`.
3. Run `python manage.py migrate --noinput`.
4. Run `python manage.py collectstatic --no-input`.
5. Start/restart app process.

## 6. Static and Media Configuration

Static:
- Served via WhiteNoise.
- `STATIC_ROOT=staticfiles`.

Media:
- Debug/test: local filesystem (`MEDIA_ROOT`).
- Non-debug: S3-compatible storage backend through `django-storages`.
- Private digital assets use custom `PrivateAssetStorage` and separate private bucket settings.
- Prodigi print fulfillment prefers the storage backend URL for private `high_res_file` assets. With private R2 configured correctly, this should produce a temporary signed URL that Prodigi can fetch without exposing the original file publicly.

## 7. Deployment Platform Notes (Render)

This repository is compatible with Render-style deployment (environment-driven settings).
- Set all production env vars in Render dashboard (not in committed `.env`).
- Ensure Redis service URL is injected to `CACHE_REDIS_URL`/`REDIS_URL`.
- Configure HTTPS and trusted host/origin envs for frontend domain(s).

## 8. Post-Deploy Validation Checklist
- `GET /api/home/testimonials/` returns `200`.
- Auth login/refresh flow works in your selected mode (header token and/or cookie mode).
- Throttled public endpoints enforce expected `429` behavior across multiple instances.
- Stripe webhook endpoint receives signed events successfully.
- Order flow creates order records and sends confirmation email.
- License delivery token downloads expire/one-time-use behavior works.
