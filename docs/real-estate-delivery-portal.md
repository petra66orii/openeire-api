# Real-estate delivery portal operations

## Architecture and threat model

The portal is an additive replacement option for selected future real-estate
deliveries. Django owns delivery, recipient, finance, file and audit state.
Next.js owns the private client interface and three same-origin route handlers.
Media bytes travel directly between a staff browser or recipient browser and
the private R2 bucket; Django, Next.js and Render never proxy those bytes.

The emailed credential is:

`https://openeire.ie/delivery/<recipient-public-id>#<recipient-secret>`

The UUID path segment is public. The fragment is the credential and is not sent
in the initial HTTP request. Client JavaScript removes the fragment with
`history.replaceState`, exchanges it through a same-origin POST, and receives
an HttpOnly, Secure, SameSite=Strict cookie. Next stores only the signed Django
session in that cookie. Django rechecks current delivery status, recipient
revocation, availability, shoot completion, finance and active files on every
page-data and download request.

The principal threats are credential leakage through paths, referrers,
analytics or logs; stale sessions after revocation/refund; unauthorised upload
session reuse; public-bucket mistakes; and unsafe cleanup prefixes. Controls
include fragment credentials, dedicated HMAC keys, constant-time comparison,
12-hour bounded sessions, no-store/no-referrer/no-index headers, a private
bucket and UUID object keys, staff/upload ownership checks, `HeadObject`
verification, bounded file/part counts, endpoint rate limits, five-minute
downloads, and validated cleanup prefixes.

Never put recipient secrets, delivery sessions, signed URLs, complete
credential links, object keys, exact addresses, Eircodes, raw IPs or complete
user agents in logs, timeline notes, tickets or screenshots.

## Environment

Backend:

- `REAL_ESTATE_DELIVERY_PORTAL_ENABLED=False` — global kill switch. Keep false
  through the backend deployment and initial verification.
- `REAL_ESTATE_DELIVERY_TOKEN_KEY` — dedicated random secret, at least 32
  characters with high entropy.
- `REAL_ESTATE_DELIVERY_SESSION_KEY` — a different dedicated random secret.
- `REAL_ESTATE_DELIVERY_INTERNAL_SECRET` — shared only by Django and Next.
- `REAL_ESTATE_DELIVERY_ACCESS_DAYS=30`
- `REAL_ESTATE_DELIVERY_GRACE_DAYS=60`
- `REAL_ESTATE_DELIVERY_SESSION_SECONDS=43200`
- `REAL_ESTATE_DELIVERY_R2_PREFIX=real-estate-deliveries`
- `REAL_ESTATE_DELIVERY_MAX_FILE_SIZE=53687091200`
- `REAL_ESTATE_DELIVERY_MAX_FILES=100`
- `REAL_ESTATE_DELIVERY_ALLOWED_MIME_TYPES=application/zip,image/jpeg,image/webp,video/mp4,application/pdf`
- Existing private R2 variables: `R2_ENDPOINT_URL`,
  `R2_PRIVATE_BUCKET_NAME`, `R2_PRIVATE_ACCESS_KEY_ID` and
  `R2_PRIVATE_SECRET_ACCESS_KEY`.
- `FRONTEND_URL=https://openeire.ie`

Frontend server environment:

- `OPENEIRE_API_BASE_URL=https://api.openeire.ie/api/`
- `REAL_ESTATE_DELIVERY_INTERNAL_SECRET` — exactly the corresponding backend
  value. Do not prefix it with `NEXT_PUBLIC_`.

When the feature flag is enabled outside tests, Django refuses to boot with a
missing/weak delivery secret, non-independent secrets, or an invalid file
limit.

## Key management and rotation

Generate all three secrets independently in a password manager or managed
secret store. Do not derive them from Django `SECRET_KEY`.

The recipient secret is deterministic HMAC-SHA256 over an application
namespace, public UUID, random per-recipient salt and positive token version.
Only the salt/version are stored. Resend regenerates the same active link;
rotating a recipient increments its token version and invalidates its old link
and sessions.

Changing `REAL_ESTATE_DELIVERY_TOKEN_KEY` invalidates every active emailed
link. Changing `REAL_ESTATE_DELIVERY_SESSION_KEY` invalidates every current
cookie session. A planned token-key rotation therefore requires a recipient
communication and resend plan. Never silently rotate either key. The internal
secret can be rotated only with coordinated backend/frontend deployment.

## R2 CORS and lifecycle (manual)

Configure this manually on the private bucket; this task does not modify R2:

- Allowed origins: only the real Django admin origin(s), normally
  `https://api.openeire.ie`, plus an explicit non-production admin origin when
  testing. Do not use `*`.
- Allowed methods: `PUT`.
- Allowed headers: `Content-Type` and the S3/R2 signing headers required by the
  generated presigned request.
- Exposed headers: `ETag`.
- Keep the maximum age conservative.

The app expires access after 30 days and makes referenced media eligible for
deletion after the additional 60-day grace period. If an R2 lifecycle rule is
used as defence in depth, scope it only to the dedicated delivery prefix and
set it later than the application retention window. Do not let a bucket rule
delete active or extended deliveries. R2 is object storage, not a complete
backup strategy.

## Staff workflow

1. In the enquiry operations hub, change the enquiry to Completed. The admin
   records the shoot-completed timeline event once.
2. Create/open the one portal delivery linked from that hub. Use a generic
   title such as “Coastal property media”; never use the exact address.
3. Add recipients individually and assign commissioning-client, agent, vendor,
   payer or other roles. Payment does not grant recipient access.
4. Review the existing finance panel. Resolve missing final/full invoices,
   partial payments, refunds/disputes or use the existing reasoned override.
5. Open **Upload media**. Choose a client-safe display name and category. The
   browser uploads parts directly to private R2. Completion creates an active
   deliverable only after object size/type verification.
6. To replace a file through the API, start a new upload with `replaces_id`.
   The new UUID object/version becomes active only after verification and the
   previous version becomes inactive; it is not overwritten.
7. Set availability, expiry, licence summary and download instructions.
8. Use **Activate delivery** and confirm. Activation requires the global flag,
   selected-delivery flag, completed shoot, recipient, verified file and
   settled finance/override.
9. On each recipient, confirm the initial email. Resend regenerates the same
   active link. Rotation is a separate confirmed action.
10. Extend by editing `expires_at`, then send the extension email. Revoke a
    recipient or whole delivery with a required reason. Archive stops access
    without deleting media.

Until the rollout is accepted, the existing `delivery_provider` and
`delivery_link` fields remain the MyAirBridge workflow. Historical records and
templates are unchanged. Use the existing MyAirBridge action as fallback.

The MVP does not automate ZIP creation. Prepare ZIP files locally, scan/check
them, and upload them manually.

## Finance and reversals

Release uses the existing enquiry-level invoice policy for deposit/balance,
full upfront, full on shoot day, custom terms, cash/bank/manual settlement,
Stripe Checkout, Stripe invoices/revisions and active staff overrides.

Stripe `charge.refunded`, `charge.dispute.created`,
`charge.dispute.closed` and `charge.dispute.funds_reinstated` events update
additive reversal metadata without rewriting the successful historical payment.
Refunds, open disputes and lost disputes reduce or remove settled value, so the
next portal request relocks. If a reversal cannot be matched to a local
real-estate payment, the webhook emits an operations warning and staff must
reconcile the payment record manually. Never invent a payment or delete the
historical successful row.

## Email and audit

Portal HTML and text templates send exactly one message per recipient. Email
attempts use local idempotency keys and failed attempts remain retryable with a
new explicit operation. The SMTP transport does not expose a provider message
ID, so that field stays blank; “sent” means the configured SMTP transport
accepted the message, not that the recipient opened or received it.

Disable provider click tracking for the portal templates/account. A generic
tracking redirect can strip or mishandle fragments. Never replace the fragment
link with a tracked redirect.

Only material delivery actions enter the enquiry timeline, using internal IDs
and masked email where relevant. High-volume session/download-URL activity
uses the privacy-safe access-event table. “Download URL issued” does not mean
the R2 download completed.

## Expiry and cleanup

Dry-run (default):

`python manage.py maintain_realestate_deliveries`

Execute only after reviewing the dry run:

`python manage.py maintain_realestate_deliveries --execute`

The command marks elapsed active deliveries expired, aborts stale multipart
uploads and deletes only retention-eligible objects whose keys pass the fixed
delivery-prefix validation. Expiry never immediately deletes media. Extending
`expires_at` postpones eligibility. Do not schedule or execute this command in
production until operations approves the output and backup/retention policy.

## Fictional staging test

1. Use a non-production database, private R2-compatible bucket, email capture
   backend and Stripe test mode. Use only `example.test` recipients and a
   fictional property title.
2. Configure the three staging-only secrets and CORS staging admin origin.
3. Keep the global flag off, apply migrations, deploy backend, and run
   `manage.py check`.
4. Deploy frontend and verify `/delivery/<random UUID>` has no analytics,
   marketing UI, identifying metadata, canonical/JSON-LD, cache or referrer.
5. Enable the staging flag. Create a completed fictional enquiry with each
   payment arrangement; verify unpaid/partial/missing-final cases are locked.
6. Upload JPEG, WebP, MP4, PDF and ZIP fixtures. Confirm R2 receives bytes
   directly, `ETag` is exposed, incorrect size/type fails, retry/abort works,
   and no object key appears in the client DTO.
7. Activate with two recipients. Confirm each gets a distinct fragment link,
   resend is stable, rotation invalidates only one, and revocation beats an
   existing cookie.
8. Download and inspect the 303 to a five-minute R2 URL. Revoke/refund before a
   second download and confirm it is denied.
9. Exercise expiry/extension/replacement and the cleanup dry-run. Do not use
   `--execute` against production.
10. Verify MyAirBridge records/actions and public enquiry pages still work.

## Deployment, rollback and troubleshooting

Deploy backend and additive migrations first with the global flag false. Add
secrets and R2 CORS, deploy frontend, run staging acceptance, then enable the
backend flag and opt in only selected new delivery records. Roll back by
turning the global flag off; keep additive tables/migrations in place and use
MyAirBridge. Do not reverse migrations after real delivery/audit data exists.

Common failures:

- “Secure delivery configuration unavailable”: missing/weak/mismatched server
  secret.
- Generic unavailable: invalid/rotated link, feature disabled, revoked/expired
  delivery, or inactive recipient.
- Payment locked: inspect all issued final/full invoices, successful payments,
  reversal metadata and overrides.
- Upload missing `ETag`: expose `ETag` in private-bucket CORS.
- Upload verification failed: compare expected size/MIME with R2 `HeadObject`;
  do not manually activate the object.
- Download generation failed: verify private R2 credentials/bucket and current
  policy; do not proxy the file through the app.

Deferred full-client-portal features are OTP, accounts/passwords, comments,
approvals, transcoding, agency dashboards and automatic ZIP generation.
The admin preview uses a separate ten-minute signed credential. It can render
active client-safe files but cannot generate download URLs and does not grant or
modify recipient access.
