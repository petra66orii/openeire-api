# OpenEire API (Django Backend)

## Overview
This repository contains the backend API for OpenEire Studios. It is a Django + Django REST Framework service that powers authentication, product catalog access, checkout, licensing, blog content, and home-page form endpoints.

## Backend Responsibilities
- User registration, verification, authentication, profile management, and account lifecycle.
- Product and gallery APIs for photos, videos, physical variants, reviews, and secure downloads.
- Checkout and payment workflows with Stripe webhooks and order history.
- Rights-managed licensing request flow, quote/payment handling, document generation, and delivery tokens.
- Blog post/comment APIs with server-side sanitization.
- Support APIs for testimonials, newsletter signup, and contact form submission.

## Tech Stack
- Python 3.12+
- Django 4.2
- Django REST Framework
- SimpleJWT (`djangorestframework-simplejwt`)
- Stripe SDK
- Redis cache backend (for shared throttling state when configured)
- Cloudflare R2 / S3-compatible storage (`django-storages`, `boto3`)
- Email via SMTP (Brevo relay configured in settings)
- Django allauth + dj-rest-auth (including Google social login)

## Repository Structure
```text
openeire-api/
  openeire_api/        # Project settings, URL root, cache/throttle config, custom admin
  userprofiles/        # Auth/profile endpoints, serializers, token utilities, profile model
  products/            # Catalog, downloads, licensing requests, internal worker endpoints
  checkout/            # Payment intent, Stripe webhook processing, order history, fulfillment
  blog/                # Blog post/comment APIs and sanitization
  home/                # Testimonials, newsletter signup, contact form
  templates/           # Email and admin templates
  .github/workflows/   # CI workflow
```

## Local Development Setup
1. Create and activate a virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a local `.env` file (see "Environment Variables").
4. Run migrations:
   ```bash
   python manage.py migrate
   ```
5. Start the API server:
   ```bash
   python manage.py runserver
   ```

## Environment Variables
Only variables observed in code are listed below. Values/secrets are `Configuration Required`.

Core/security:
- `DEBUG`
- `SECRET_KEY`
- `ENFORCE_STRONG_SECRET_KEY`
- `DJANGO_ADMIN_URL`
- `APP_ENV`
- `RENDER_ENVIRONMENT`

Hosts/CORS/CSRF/security:
- `RENDER_EXTERNAL_HOSTNAME`
- `SECURE_SSL_REDIRECT`
- `SESSION_COOKIE_SECURE`
- `CSRF_COOKIE_SECURE`
- `SESSION_COOKIE_SAMESITE`
- `CSRF_COOKIE_SAMESITE`
- `SECURE_HSTS_SECONDS`
- `SECURE_HSTS_INCLUDE_SUBDOMAINS`
- `SECURE_HSTS_PRELOAD`
- `SECURE_REFERRER_POLICY`
- `X_FRAME_OPTIONS`

JWT/auth cookie mode:
- `JWT_USE_HTTPONLY_COOKIES`
- `JWT_ACCESS_COOKIE_NAME`
- `JWT_REFRESH_COOKIE_NAME`
- `JWT_COOKIE_SECURE`
- `JWT_COOKIE_SAMESITE`
- `JWT_COOKIE_DOMAIN`
- `JWT_COOKIE_CSRF_PROTECTION`
- `JWT_CSRF_COOKIE_NAME`
- `JWT_CSRF_HEADER_NAME`
- `EMAIL_VERIFICATION_TOKEN_MINUTES`
- `PASSWORD_RESET_TOKEN_MINUTES`

Google social login:
- `GOOGLE_OAUTH_CLIENT_ID` or `GOOGLE_CLIENT_ID`
- `GOOGLE_OAUTH_SECRET` or `GOOGLE_CLIENT_SECRET`
- `GOOGLE_OAUTH_KEY` (optional)

Cache/throttling:
- `CACHE_REDIS_URL` or `REDIS_URL`
- `CACHE_KEY_PREFIX`
- `CACHE_REDIS_CONNECT_TIMEOUT_SECONDS`
- `CACHE_REDIS_SOCKET_TIMEOUT_SECONDS`
- `REQUIRE_SHARED_THROTTLE_CACHE`
- `THROTTLE_FAIL_OPEN`

Storage (R2/S3):
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET_NAME`
- `R2_ENDPOINT_URL`
- `R2_CUSTOM_DOMAIN`
- `R2_PRIVATE_BUCKET_NAME`
- `R2_PRIVATE_ACCESS_KEY_ID`
- `R2_PRIVATE_SECRET_ACCESS_KEY`

Email:
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `DEFAULT_FROM_EMAIL`
- `LICENSING_FROM_EMAIL`
- `LICENSOR_CONTACT_EMAIL`
- `LICENCE_ADMIN_NOTIFICATION_RECIPIENTS`

Stripe:
- `STRIPE_PUBLIC_KEY`
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_TIMEOUT_SECONDS`
- `STRIPE_MAX_NETWORK_RETRIES`
- `STRIPE_WEBHOOK_STALE_PROCESSING_SECONDS`
- `CHECKOUT_ALLOW_LEGACY_USERNAME_FALLBACK`

Prodigi:
- `PRODIGI_API_KEY`
- `PRODIGI_SANDBOX`
- `PRODIGI_CONNECT_TIMEOUT_SECONDS`
- `PRODIGI_READ_TIMEOUT_SECONDS`
- `PRODIGI_CALLBACK_BASE_URL` (required for tracking callbacks)
- `SITE_URL` (fallback only; used if storage returns a relative asset path instead of an absolute signed URL)

Licensing/AI worker:
- `LICENCE_DOWNLOAD_BASE_URL`
- `LICENCE_DELIVERY_TOKEN_DAYS`
- `LICENCE_SEND_INITIAL_DRAFT_EMAIL`
- `LICENCE_TERMS_VERSION`
- `LICENCE_MASTER_AGREEMENT`
- `AI_WORKER_SECRET`
- `AI_WORKER_IP_ALLOWLIST`
- `AI_WORKER_TRUSTED_PROXY_IPS`
- `AI_WORKER_MAX_BATCH`
- `AI_WORKER_MAX_BATCH_HARD`
- `AI_DRAFT_MAX_CHARS`

Blog sanitization:
- `BLOG_ALLOWED_IMAGE_HOSTS`

SQLite tuning (default DB engine in settings):
- `SQLITE_TIMEOUT_SECONDS`
- `SQLITE_SAVE_RETRY_ATTEMPTS`
- `SQLITE_SAVE_RETRY_DELAY_SECONDS`

## Running the Server
```bash
python manage.py runserver
```

Useful commands:
```bash
python manage.py check
python manage.py migrate
python manage.py test
python manage.py collectstatic --no-input
```

## Background Worker Overview
- Celery tasks are not defined in this repository (Coming soon).
- There is an internal AI-draft integration exposed as protected API endpoints:
  - `GET /api/internal/draft-queue/`
  - `POST /api/internal/draft-update/<pk>/`
- Access is controlled by a bearer secret (`AI_WORKER_SECRET`) and optional IP allowlist checks.

## API Overview
Primary route groups:
- ` /api/auth/` - authentication and user profile APIs
- ` /api/` - products, gallery, downloads, licensing
- ` /api/checkout/` - payment intents, webhooks, order history
- ` /api/blog/` - blog posts, likes, comments
- ` /api/home/` - testimonials, newsletter, contact

Detailed endpoint contracts are documented in [docs/api.md](docs/api.md).

## Deployment Reference
See:
- [docs/deployment.md](docs/deployment.md)
- [docs/operations.md](docs/operations.md)

Prodigi fulfillment note:
- Physical print orders prefer storage-generated URLs for `high_res_file`, which allows private Cloudflare R2 assets to be handed to Prodigi via short-lived signed URLs.
- `SITE_URL` remains configured as a fallback only for environments where storage returns a relative media path.
- Prodigi order creation sets a callback URL when `PRODIGI_CALLBACK_BASE_URL` is configured.
- Callback payloads are verified by fetching the referenced order back from Prodigi before shipment updates are trusted locally.
- When a callback includes shipment tracking details, the API stores the shipment metadata on the order and emails the customer once per unique tracking update.

## Maintainer
- Project/team owner: [Miss Bott](https://github.com/petra66orii)
- Primary backend maintainer: [Miss Bott](https://github.com/petra66orii)
