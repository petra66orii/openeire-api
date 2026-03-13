# Database Model Reference

## Overview
The project uses Django ORM models across five domain apps. In `settings.py`, the default configured engine is SQLite. CI includes PostgreSQL service configuration, but runtime DB switching is `Configuration Required` because `DATABASE_URL` is not consumed in current settings.

## Core Entities

### `auth.User` (Django built-in)
- Used for platform authentication identity.
- Related to:
  - `UserProfile` (one-to-one)
  - Blog authoring, likes, comments
  - Product reviews
  - License offer/audit attribution

### `userprofiles.UserProfile`
- Purpose: Persist user profile + default delivery fields.
- Key fields:
  - `user` (OneToOne to `auth.User`)
  - `default_phone_number`, `default_street_address1/2`, `default_town`, `default_county`, `default_postcode`, `default_country`
  - `can_access_gallery` (bool)

### `products.Photo`
- Purpose: Digital photo asset and physical parent for variants.
- Key fields:
  - `title`, `description`, `collection`, `tags`, `is_active`
  - `preview_image` (public media)
  - `high_res_file` (private asset storage)
  - `price_hd`, `price_4k`
  - `created_at`
- Related to:
  - `ProductVariant` (one-to-many)
  - Generic references from reviews/license requests/order items

### `products.Video`
- Purpose: Digital video asset.
- Key fields:
  - `title`, `description`, `collection`, `tags`, `is_active`
  - `thumbnail_image` (public)
  - `video_file` (private)
  - `price_hd`, `price_4k`
  - `duration`, `resolution`, `frame_rate`
  - `created_at`

### `products.ProductVariant`
- Purpose: Physical print SKU for a photo/material/size combination.
- Key fields:
  - `photo` (FK to `Photo`)
  - `material`, `size`, `price`, `sku`, `prodigi_sku`
- Constraints:
  - Unique together: `(photo, material, size)`

### `products.PrintTemplate`
- Purpose: Canonical material/size template and production cost basis.
- Key fields:
  - `material`, `size`, `production_cost`, `prodigi_sku`, `sku_suffix`
- Constraints:
  - Unique together: `(material, size)`

### `checkout.ProductShipping`
- Purpose: Shipping price matrix by print template + destination + method.
- Key fields:
  - `product` (FK to `PrintTemplate`)
  - `country` (`IE`/`US`)
  - `method` (`budget`/`standard`/`express`)
  - `cost`
- Constraints:
  - Unique together: `(product, country, method)`

### `checkout.Order`
- Purpose: Customer order record from Stripe checkout webhook.
- Key fields:
  - `order_number` (generated UUID-like string)
  - `user_profile` (nullable FK)
  - Shipping/contact fields (`first_name`, `email`, address fields)
  - `shipping_method`, `delivery_cost`, `order_total`, `total_price`
  - `stripe_pid`
  - `personal_terms_version`
  - `date`

### `checkout.OrderItem`
- Purpose: Line items in an order.
- Key fields:
  - `order` (FK to `Order`)
  - `quantity`, `item_total`
  - Generic relation: `content_type`, `object_id`, `product`
  - `details` (JSON options snapshot)

### `products.ProductReview`
- Purpose: User review against photo/video/physical variant via GenericFK.
- Key fields:
  - Generic relation: `content_type`, `object_id`, `product`
  - `user`, `rating`, `comment`, `approved`, `admin_reply`, `created_at`
- Constraints:
  - Unique together: `(content_type, object_id, user)`

### `products.GalleryAccess`
- Purpose: Temporary gallery access tokens.
- Key fields:
  - `email`, `access_code` (unique), `created_at`, `expires_at`

### `products.LicenseRequest`
- Purpose: Rights-managed commercial licensing request.
- Key fields:
  - Generic relation to asset: `content_type`, `object_id`, `asset`
  - Client/request scope fields (`client_name`, `company`, `email`, `project_type`, `duration`, etc.)
  - `reach_caps`, `message`
  - Lifecycle/status fields (`status`, `created_at`, `updated_at`, `paid_at`, `delivered_at`)
  - Payment linkage (`stripe_checkout_session_id`, `stripe_payment_intent_id`, `stripe_payment_link`, `stripe_payment_link_id`)
  - `ai_draft_response`, `quoted_price`
- Indexes/constraints:
  - Index on `(content_type, object_id)` (`license_asset_idx`)
  - Conditional unique constraint for active requests per email+asset (case-insensitive on email)

### `products.LicenceOffer`
- Purpose: Versioned offer snapshots per license request.
- Key fields:
  - UUID primary key
  - `license_request` FK
  - `version`, `status`, `scope_snapshot`, `quoted_price`, `currency`
  - Stripe linkage fields (`stripe_product_id`, `stripe_price_id`, `stripe_payment_link_id`, etc.)
  - `terms_version`, `master_agreement_version`
  - `created_by`, `created_at`, `paid_at`, `superseded_at`
- Constraints:
  - Unique `(license_request, version)`
  - Unique `stripe_payment_link_id`

### `products.LicenseRequestAuditLog`
- Purpose: Append-only audit history for status transitions and notes.
- Key fields:
  - `license_request` FK
  - `from_status`, `to_status`
  - `changed_by` (nullable user FK)
  - `note`, `metadata`, `changed_at`

### `products.LicenceDocument`
- Purpose: Generated licensing PDFs (schedule/certificate).
- Key fields:
  - UUID primary key
  - `license_request` FK
  - `doc_type`, `file` (private storage), `sha256`, `created_at`

### `products.LicenceDeliveryToken`
- Purpose: One-time, expiring delivery token for licensed asset download.
- Key fields:
  - `token` (UUID unique)
  - `license_request` FK
  - `expires_at`, `used_at`, `created_at`

### `products.StripeWebhookEvent`
- Purpose: Idempotency and processing status log for Stripe webhook events.
- Key fields:
  - `stripe_event_id` (unique), `event_type`
  - `received_at`, `processed_at`
  - `status` (`PROCESSING`, `SUCCESS`, `FAILED`)
  - `error_message`

### `blog.BlogPost`
- Purpose: Blog post content.
- Key fields:
  - `title`, `slug` (unique), `author` FK
  - `featured_image`, `content`, `excerpt`
  - `status` (draft/published), timestamps
  - `tags` (Taggit manager), `likes` (M2M to users)
- Behavior:
  - Sanitizes `content` and `excerpt` on save.

### `blog.Comment`
- Purpose: Blog comments tied to posts/users.
- Key fields:
  - `post` FK, `user` FK, `content`, `approved`, `created_at`
- Behavior:
  - Sanitizes `content` on save.

### `home.Testimonial`
- Purpose: Display testimonials.
- Fields:
  - `name`, `text`, `rating`

### `home.NewsletterSubscriber`
- Purpose: Newsletter signup storage.
- Fields:
  - `email` (unique), `created_at`

## Schema Overview (Simplified)
```text
auth.User 1---1 UserProfile
auth.User 1---* ProductReview
auth.User 1---* BlogPost
auth.User *---* BlogPost (likes)
auth.User 1---* Comment

Photo 1---* ProductVariant
PrintTemplate 1---* ProductShipping

UserProfile 1---* Order
Order 1---* OrderItem
OrderItem *---1 (GenericFK -> Photo | Video | ProductVariant)

LicenseRequest *---1 (GenericFK -> Photo | Video)
LicenseRequest 1---* LicenceOffer
LicenseRequest 1---* LicenseRequestAuditLog
LicenseRequest 1---* LicenceDocument
LicenseRequest 1---* LicenceDeliveryToken

BlogPost 1---* Comment
```
