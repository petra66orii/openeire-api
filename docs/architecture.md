# Architecture

## High-Level Structure
This backend is a modular Django project with app-level domain boundaries.

- Project package: `openeire_api`
- Domain apps: `userprofiles`, `products`, `checkout`, `blog`, `home`
- API stack: Django REST Framework class-based views
- Auth stack: SimpleJWT + dj-rest-auth/allauth (+ Google provider)

## Installed Apps and Responsibilities
Observed from `INSTALLED_APPS`:

- `userprofiles`
  - Registration, login, token refresh/logout, password/email/account flows
  - `UserProfile` model linked to Django `User`
  - One-off action tokens for email verification and password reset
- `products`
  - Catalog APIs (photos, videos, physical variants)
  - Review APIs and secure media download endpoints
  - Commercial license request pipeline and delivery-token download flow
  - Internal AI draft queue/update endpoints
- `checkout`
  - Stripe payment intent creation
  - Stripe webhook processing for orders and license delivery
  - Order history and physical fulfillment integration (Prodigi)
- `blog`
  - Blog listing/detail/like/comment APIs
  - HTML/plain-text sanitization pipeline
- `home`
  - Testimonials, newsletter signups, contact form endpoint

## Service Layer Pattern
A dedicated service module layer is used in selected domains:

- `products/licensing.py`
  - Generates and persists license documents
  - Issues one-time delivery tokens
  - Sends licensing emails
- `checkout/prodigi.py`
  - Builds and submits fulfillment payloads to Prodigi
  - Handles timeout/error parsing
- `products/personal_licence.py`, `products/pdf_generator.py`
  - License text/version helpers and PDF generation

Most other logic is view/serializer centric (standard DRF pattern).

## Async Processing Architecture
No Celery app/tasks were found in the repository (`Configuration Required` for Celery adoption).

Current async-like external processing pattern:
- Internal protected API endpoints are used by a separate worker process to pull and submit AI draft responses.
- Stripe webhook callbacks are synchronous HTTP handlers with idempotency tracking via `StripeWebhookEvent`.

## Integration Points
- Stripe: payment intents, webhooks, payment links (licensing offers)
- Prodigi: physical print fulfillment API
- Email SMTP: transactional emails (verification, reset, order/licensing updates)
- Cloudflare R2 (S3-compatible): media storage (public + private)
- Redis cache backend: shared throttling/cache when configured
- Google OAuth: allauth social login endpoint

## Request Lifecycle
1. Client calls API endpoint under `/api/...`.
2. Django URL routing dispatches to app-specific DRF view.
3. Permissions + throttling execute (`SharedScopedRateThrottle` for selected endpoints).
4. Serializer validation + domain logic run.
5. Data is persisted via Django ORM (or external provider calls are made).
6. Response payload is serialized and returned.

## Diagram: Core Request Path
```text
Client -> API -> Business Logic -> Database

Expanded:
Client
  -> Django URL Router
  -> DRF View / Permissions / Throttle
  -> Serializer + Domain Logic
  -> Django ORM
  -> Database
  -> Response
```

## Diagram: External Processing Path
```text
API/Webhook
  -> Domain Logic
  -> External Service (Stripe/Prodigi/SMTP)
  -> Persist status/audit events
```

## Diagram: Celery Pattern (Configuration Required)
```text
API -> Celery Queue -> Worker -> External Service
```

## Diagram: Internal Worker Path
```text
AI Worker (separate process)
  -> GET /api/internal/draft-queue/
  -> Generate draft externally
  -> POST /api/internal/draft-update/<id>/
  -> LicenseRequest updated in DB
```
