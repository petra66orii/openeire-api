# API Reference

## Conventions
- Base prefix: `/api/`
- Auth scheme: JWT Bearer (`Authorization: Bearer <access_token>`) by default.
- Some deployments may also enable HttpOnly JWT cookie mode (`Configuration Required` via env).
- Unless overridden per-view, DRF default permission is authenticated. Most public endpoints explicitly set `AllowAny`.
- Pagination:
  - Gallery/blog liked/blog list use DRF pagination where configured.

No DRF routers were detected; endpoints are path-based class views.

---

## Authentication and User (`/api/auth/`)

### `POST /api/auth/register/`
- Purpose: Create user account (inactive until email verification).
- Auth: Public.
- Request:
  - `username` (string, required)
  - `email` (email, required, case-insensitive unique)
  - `password` (string, required)
  - `first_name` (string, optional)
  - `last_name` (string, optional)
- Response: Created user fields (excluding password), `201`.

### `POST /api/auth/verify-email/confirm/`
- Purpose: Activate account using action token.
- Auth: Public.
- Request:
  - `token` (string, required)
- Response:
  - Success: `{ "message": "Email successfully verified!" }`
  - Error: `{ "error": "Invalid activation link." }`

### `POST /api/auth/resend-verification/`
- Purpose: Re-send verification email.
- Auth: Public.
- Request:
  - `email` (email, required)
- Response: Generic success message (`200`) regardless of account discoverability.

### `POST /api/auth/login/`
- Purpose: Obtain JWT access/refresh tokens.
- Auth: Public.
- Request:
  - `username` (string; serializer accepts username or email-form identifier)
  - `password` (string)
- Response:
  - `access`, `refresh` tokens (`200`)
  - If cookie mode enabled, cookies are set as side-effect.

### `POST /api/auth/token/refresh/`
- Purpose: Refresh JWT access token.
- Auth: Public endpoint semantics; token validation enforced by serializer.
- Request:
  - `refresh` (string) or refresh cookie when cookie mode is enabled.
- Response:
  - New `access` and possibly rotated `refresh`.

### `POST /api/auth/logout/`
- Purpose: Clear auth cookies.
- Auth: Public (`AllowAny`).
- Request:
  - Empty body.
  - In cookie mode with auth cookies present, CSRF header/cookie match is required.
- Response:
  - `{ "message": "Logged out." }`

### `POST /api/auth/password/reset/`
- Purpose: Send password reset email.
- Auth: Public.
- Request:
  - `email` (email, required)
- Response:
  - Generic `{ "message": "Password reset link sent." }` (`200`).

### `POST /api/auth/password/reset/confirm/`
- Purpose: Complete password reset with token.
- Auth: Public.
- Request:
  - `token` (string)
  - `password` (string)
  - `confirm_password` (string)
- Response:
  - Success `{ "message": "Password reset successful." }`
  - Failure `{ "error": "Invalid or expired token." }`

### `PUT/PATCH /api/auth/password/change/`
- Purpose: Change password for authenticated user.
- Auth: Authenticated.
- Request:
  - `old_password` (string)
  - `new_password` (string)
- Response:
  - `{ "message": "Password updated successfully" }`

### `PUT/PATCH /api/auth/email/change/`
- Purpose: Change account email and force re-verification.
- Auth: Authenticated.
- Request:
  - `new_email` (email)
  - `current_password` (string)
- Response:
  - Success message; account is set inactive pending verification.

### `GET/PUT/PATCH /api/auth/profile/`
- Purpose: Read/update current profile.
- Auth: Authenticated.
- Request update fields:
  - `username`, `first_name`, `last_name`, `email`
  - `default_phone_number`, `default_street_address1`, `default_street_address2`
  - `default_town`, `default_county`, `default_postcode`, `country`
- Response: Profile + user identity fields.

### `DELETE /api/auth/delete/`
- Purpose: Delete authenticated account.
- Auth: Authenticated.
- Request:
  - `password` (string, required in request body)
- Response:
  - `204` with `{ "message": "Account deleted successfully." }`

### `GET /api/auth/countries/`
- Purpose: Return countries for UI dropdowns.
- Auth: Public.
- Response:
  - Array of `{ "code": "...", "name": "..." }`.

### `POST /api/auth/google/`
- Purpose: Google social login via allauth/dj-rest-auth.
- Auth: Public.
- Request/response shape: Provider flow dependent (`Configuration Required` frontend integration details).

---

## Products, Gallery, Licensing (`/api/`)

### `GET /api/licence/personal-use/`
- Purpose: Return personal-use license full text.
- Auth: Public.
- Response:
  - Plain text body with terms version header `X-Personal-Terms-Version`.

### `POST /api/gallery-request/`
- Purpose: Issue gallery access code to email.
- Auth: Public.
- Throttle scope: `gallery_access_request`.
- Request:
  - `email`
- Response:
  - `{ "message": "Code sent" }`

### `POST /api/gallery-verify/`
- Purpose: Validate gallery access code.
- Auth: Public.
- Throttle scope: `gallery_access_verify`.
- Request:
  - `access_code`
- Response:
  - Success: `{ "message": "Access granted", "expires_at": "...", "valid": true }`
  - Failure: `403` with error.

### `GET /api/gallery/`
- Purpose: List gallery products with filtering.
- Auth:
  - Public for physical view.
  - Digital view requires `X-Gallery-Access-Token` header.
- Query params:
  - `type` (`physical`, `digital`, `all`; defaults to physical path)
  - `collection`
  - `search`
  - `sort` (`price_asc`, `price_desc`, default date descending)
  - `page`, `page_size` (when paginated)
- Response:
  - Mixed list of serialized photo/video/physical photo list payloads.

### `GET /api/photos/<pk>/`
- Purpose: Digital photo detail.
- Auth: Requires digital gallery access header token.
- Response:
  - Photo detail including pricing, related items, variants, review stats.

### `GET /api/videos/<pk>/`
- Purpose: Video detail.
- Auth: Requires digital gallery access header token.
- Response:
  - Video detail including pricing, metadata, related items, review stats.

### `GET /api/products/<pk>/`
- Purpose: Physical photo detail page payload.
- Auth: Public.

### `GET /api/variants/<pk>/`
- Purpose: Physical product variant detail.
- Auth: Public.

### `GET/POST /api/<product_type>/<pk>/reviews/`
- Purpose:
  - `GET`: list approved reviews for product.
  - `POST`: create review for authenticated user (one review/user/product).
- Auth:
  - `GET` public.
  - `POST` authenticated.
- Request (`POST`):
  - `rating` (1-5)
  - `comment` (optional)
- Response:
  - Review objects with `user`, `rating`, `comment`, `created_at`, `admin_reply`.

### `GET /api/products/recommendations/`
- Purpose: Return up to 4 active photo recommendations.
- Auth: Public.

### `GET /api/products/download/<product_type>/<product_id>/`
- Purpose: Secure purchased digital asset download.
- Auth: Authenticated.
- Rules:
  - Requires prior purchase in order history, unless staff user.
  - Optional `?preview=1` returns metadata instead of file stream.
- Response:
  - File attachment stream, or preview metadata JSON.

### `POST /api/license-requests/`
- Purpose: Create commercial license request(s) for photo/video assets.
- Auth: Public.
- Throttle scope: `license_request`.
- Request (single):
  - `asset_type` (`photo` or `video`)
  - `asset_id` (int)
  - `client_name`, `company`, `email`
  - `project_type`, `duration`, optional `territory`, `permitted_media`, `exclusivity`, `reach_caps`, `message`
- Request (batch):
  - `asset_ids` array + same shared request fields
- Response:
  - `201` created object(s) or mixed `207` with partial errors.

### `GET /api/license/download/<uuid:token>/`
- Purpose: One-time expiring licensed asset download link.
- Auth: Public by token.
- Response:
  - File attachment if token valid and unused.
  - `404` when invalid/expired/used.

### `GET /api/internal/draft-queue/`
- Purpose: Internal AI worker pulls pending license requests.
- Auth: Internal bearer secret + optional IP allowlist.
- Query params:
  - `limit` (bounded by `AI_WORKER_MAX_BATCH_HARD`)
- Response:
  - Array of pending request summaries.

### `POST /api/internal/draft-update/<pk>/`
- Purpose: Internal AI worker submits draft response text.
- Auth: Internal bearer secret + optional IP allowlist.
- Request:
  - `draft_text` (string, max `AI_DRAFT_MAX_CHARS`)
- Response:
  - Success/failure status JSON with `200/404/409`.

---

## Checkout (`/api/checkout/`)

### `POST /api/checkout/create-payment-intent/`
- Purpose: Validate cart and create Stripe PaymentIntent.
- Auth:
  - Public for physical-only carts.
  - Digital items require authenticated user.
- Request:
  - `cart` (required array)
    - item fields include `product_id`, `product_type` (`photo`, `video`, `physical`), `quantity`, optional `options`
  - `shipping_details` (object, required for physical items)
  - `shipping_method` (`budget` default, `standard`, `express`)
  - `save_info` (optional boolean-like)
- Response:
  - `{ "clientSecret", "shippingCost", "totalPrice" }`
  - Validation errors for invalid cart/address/options.

### `POST /api/checkout/wh/`
- Purpose: Stripe webhook receiver.
- Auth: Public (signature-verified).
- Supported events:
  - `payment_intent.succeeded`
  - `checkout.session.completed`
- Behavior:
  - Idempotency tracked in `StripeWebhookEvent`
  - Creates orders from cart metadata
  - Sends confirmation emails
  - Triggers Prodigi for physical items
  - Processes licensing payment completion and delivery
- Response:
  - `200` (including for safely ignored/replayed events), `400` on invalid signature.

### `GET /api/checkout/order-history/`
- Purpose: List authenticated user's past orders.
- Auth: Authenticated.
- Response:
  - Order list with totals, shipping, terms version, and item-level product payload + download URLs for digital items.

---

## Blog (`/api/blog/`)

### `GET /api/blog/`
- Purpose: List published posts.
- Auth: Public.
- Query params:
  - `tag` (slug)
  - pagination params
- Response:
  - Paginated blog list serializer output.

### `GET /api/blog/liked/`
- Purpose: List posts liked by current user.
- Auth: Authenticated.

### `GET /api/blog/<slug>/`
- Purpose: Retrieve published blog detail by slug.
- Auth: Public.
- Response:
  - Includes sanitized `content`, likes metadata, and related posts.

### `POST /api/blog/<slug>/like/`
- Purpose: Toggle like/unlike for authenticated user.
- Auth: Authenticated.
- Response:
  - `{ "liked": <bool>, "likes_count": <int> }`

### `GET/POST /api/blog/<slug>/comments/`
- Purpose:
  - `GET`: list approved comments
  - `POST`: create comment (pending approval by default model behavior)
- Auth:
  - `GET` public
  - `POST` authenticated
- Request (`POST`):
  - `content` (string)
- Response:
  - Comment serializer payload.

---

## Home (`/api/home/`)

### `GET /api/home/testimonials/`
- Purpose: List testimonials.
- Auth: Public.

### `POST /api/home/newsletter-signup/`
- Purpose: Add newsletter subscriber.
- Auth: Public.
- Request:
  - `email`

### `POST /api/home/contact/`
- Purpose: Submit contact form and send email to configured recipient.
- Auth: Public.
- Request:
  - `name`, `email`, `subject`, `message`
- Response:
  - Success message or generic email failure error.
