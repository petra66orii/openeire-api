# Returning-client booking portal operations

## Scope

The booking portal is an additive, private enquiry channel for explicitly
verified returning real-estate clients. It creates ordinary
`RealEstateEnquiry` records. Existing quotation, agreement, finance, shoot,
timeline and delivery workflows remain authoritative.

Do not use normalized email or phone values as authentication, uniqueness or
automatic merge keys. They exist only to warn staff about possible duplicates.
Historical enquiries are not automatically linked or backfilled.

## Private-link architecture

Links use:

`https://openeire.ie/book/<credential-public-uuid>#<credential-secret>`

The fragment is a deterministic HMAC over a booking-specific namespace,
credential UUID, random salt and token version. Only the salt and version are
stored. Next.js removes the fragment immediately and exchanges it for a
booking-specific HttpOnly cookie. Django checks feature state, client state,
credential expiry/revocation and token version on exchange, session reads and
submissions.

Never place fragments, complete links, sessions, request bodies, contact data,
raw IP addresses or complete user agents in logs, timelines, analytics,
tickets or screenshots. Booking access events contain fixed event/result codes
and internal relationships only.

## Configuration

Backend:

- `REAL_ESTATE_BOOKING_PORTAL_ENABLED=False`
- `REAL_ESTATE_BOOKING_EMAIL_ENABLED=False`
- `REAL_ESTATE_BOOKING_TOKEN_KEY`
- `REAL_ESTATE_BOOKING_SESSION_KEY`
- `REAL_ESTATE_BOOKING_INTERNAL_SECRET`
- `REAL_ESTATE_BOOKING_CREDENTIAL_DAYS=90`
- `REAL_ESTATE_BOOKING_SESSION_SECONDS=43200`
- `FRONTEND_URL=https://openeire.ie`
- `FRONTEND_ORIGIN=https://openeire.ie`

Frontend server:

- `REAL_ESTATE_BOOKING_PORTAL_ENABLED=false`
- `REAL_ESTATE_BOOKING_INTERNAL_SECRET` matching Django
- `OPENEIRE_API_BASE_URL=https://api.openeire.ie/api/`
- `NEXT_PUBLIC_SITE_URL=https://openeire.ie`

All booking secrets must be independently generated, high entropy and at least
32 characters. They must differ from Django `SECRET_KEY`, from one another and
from every delivery-portal key. Never prefix them with `NEXT_PUBLIC_`.

Deploy migrations and both applications with both feature flags disabled.
Enable only after fictional acceptance. Roll back by disabling the feature in
both applications; retain used additive tables and audit records.

The frontend flag is read by dynamic server routes and the dynamic booking
page. Changing it requires the frontend service to be restarted/redeployed so
all running instances receive the same runtime environment; it does not expose
the flag through `NEXT_PUBLIC_*`.

## Staff workflow

1. Open a reviewed real-estate enquiry and run **Create verified returning
   client from reviewed enquiry**. Review stable details and possible duplicate
   warnings. Do not merge automatically.
2. Open the client and run **Generate or show active private booking access**.
   An active unexpired link is preserved; an expired active credential is
   explicitly renewed and rotated.
3. Open the credential and use **Copy link** for the approved WhatsApp or email
   distribution workflow.
4. Resending an active link does not rotate it. Rotation is a separately
   confirmed action and invalidates existing links and sessions.
5. Revocation requires a reason. Archive or deactivate the client to fail all
   their booking access closed.
6. Returning enquiries show `Returning client`, link to the client and retain
   copied enquiry-time contact snapshots. Review any contact-update request in
   the enquiry admin; the portal never edits the client directly.

Credential generation, rotation, revocation and identity resolution use
explicit Django permissions. Grant them only to staff responsible for client
identity and private access.

## Email and click tracking

The booking email contains the identical private URL in its text and HTML
parts, including visible copy-and-paste fallback text. The SMTP transport does
not provide reliable per-message click-tracking control.

Private booking email must therefore use a provider account/template with
click tracking disabled and verified from a received staging message. A
tracking redirect can strip or mishandle fragments. If no-tracking delivery
cannot be guaranteed, the approved MVP distribution path is copying the link
from Django admin into WhatsApp or another verified untracked channel. Copy
fallback is a usability measure, not a security workaround for tracking.
`REAL_ESTATE_BOOKING_EMAIL_ENABLED` must remain false until that staging check
is complete; the service and admin action both fail closed while it is false.

## Fictional acceptance

Use a non-production database, `example.test` addresses and fictional property
details. Do not inspect or link a production client.

1. Apply migrations with both flags off and run system checks.
2. Create a fictional confirmed enquiry and explicitly create its client.
3. Verify duplicate warnings do not merge a second fictional matching client.
4. Generate a credential and confirm its 90-day expiry.
5. Copy the link into a mobile browser. Confirm the fragment disappears and a
   12-hour `Secure`, `HttpOnly`, `SameSite=Strict` cookie is issued.
6. Confirm the page shows only masked contact details and no history.
7. Submit two fictional properties and confirm two ordinary enquiries.
8. Retry one submission UUID and confirm no duplicate enquiry, timeline or
   email is created.
   In a PostgreSQL staging database, send two requests concurrently from
   separate connections with the same UUID. Confirm one 201 response, one safe
   idempotent 200 response, one enquiry-received event, and one pair of email
   attempts. SQLite is not a substitute for this lock/visibility exercise.
9. Rotate, then verify the old link and existing browser session fail.
10. Revoke and archive/inactivate the client; verify generic unavailable
    responses.
11. Render/capture the email and verify HTML/text URLs match and have not been
    rewritten.
12. Confirm public enquiry, agreements, payments, delivery portal and
    MyAirBridge fallback remain unchanged.

Only after this acceptance and explicit approval should a real client be
manually linked for a controlled pilot.
