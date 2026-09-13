# Property Access & Shoot Authorisation

Implemented as a separate operational document, version **1.0**. The Booking
Agreement and all payment, invoice, deposit, Stripe, cash and delivery-confirmation
rules remain unchanged.

## Staff workflow

Open a persisted Real Estate Enquiry in Django admin. Its Operations Hub now has a
Property Access & Shoot Authorisation panel.

1. Select **Generate / Preview**. Confirm the property/parcel locations covered and
   the authorising person's email. This person can differ from the instructing
   client or payer. Generation persists a draft with frozen wording, booking data
   and a print-ready PDF; it does not send email or record acceptance.
2. Review the snapshot and copy the secure review link, download the PDF, or use
   **Send authorisation**. Sending has a review/confirmation page and attaches the
   PDF. Only successful sends record the sent timestamp and awaiting status.
3. Either acceptance route is valid:
   - **Electronic:** the recipient opens the token link, reviews the entire
     document, enters their role, disclosures and preferences, confirms authority
     and accuracy, and types their full name. A matching typed name is required.
   - **Handwritten:** the recipient may print, complete, sign and return the PDF.
     Staff choose **Record handwritten copy received**, faithfully transcribe the
     responses and record the receipt date. Keep the original signed document in
     existing private records. No second electronic acceptance is requested.
4. **Reissue** creates a new draft from the current enquiry, linked to its
   predecessor, and disables the old public link without rewriting old content.
   **Revoke link** disables public access without deleting the audit record.
5. Before departure, use **Record final on-site scope check**. Record location,
   person confirming (or explicitly record that nobody was available), capacity,
   completion status, outstanding capture and optional notes. The staff account
   and timestamp are automatic. These checks are append-only; record a new check
   to correct an earlier one. No second signature is required.

The Hub shows status, version, sent/accepted/received dates, signer, method, link
expiry, action links and recent final scope checks. It warns when a shoot is within
seven days and authorisation is outstanding. Financial confirmation is not
cancelled or altered.

## Client input and scope

Required selections distinguish no restrictions, no known hazards, and no specific
capture requirements from positive disclosures. A client choosing **No specific
requirements** can leave instructions blank or use **N/A**. All agreed package
deliverables and OpenÉire's responsibilities remain intact. Specific requirements
must contain meaningful text; contradictory choices and text are rejected instead
of silently losing instructions. Video directions are optional and are shown when
video/film appears in the recorded scope/add-ons; they never add video to a booking.

Flexible text supports acreage, agricultural areas, bog, forestry, water, fencing,
structures, boundaries and geographically distinct capture points. Entering any
request does not change a quote, invoice or approved scope. Material price changes
remain subject to the existing authorised adjustment workflow.

The frozen document includes authority/access, hazard disclosure, readiness,
professional judgement, on-site changes, new capture/return-visit charges with the
expressly-agreed-scope exception, justified operational discretion and neutral
acceptance wording. It contains no blanket accident/negligence waiver.

## Persistence, security and privacy

Migration `0031_propertyscopecheck_propertyauthorisation` adds two models:

- `PropertyAuthorisation`: enquiry and location, independent template version,
  256-bit random URL token, recipient, status and lifecycle timestamps, creator,
  predecessor link, issued snapshot/PDF, accepted snapshot/PDF, name, capacity,
  email, acceptance method, recorded timestamp, handwritten receipt date and staff
  recorder where applicable.
- `PropertyScopeCheck`: enquiry/location, confirming person/capacity, outcome,
  completion flag, outstanding capture, notes, staff recorder and timestamp.

Private business identity/address/contact/signatory data comes from the same
`get_business_identity(private_legal_document=True)` helper as the Booking
Agreement. Snapshot JSON includes the exact terms, booking details, answers,
acceptance metadata and rendered section labels. Issued and accepted PDFs are
stored in the database, never a public media directory. Neither later enquiry
edits nor template edits regenerate existing document content. Ordinary model
edits/deletes of protected content are rejected; lifecycle service functions are
the intended write boundary. As with other application audit records, privileged
direct database writes must be restricted operationally.

Public URLs are under `/api/real-estate/authorisation/<token>/` and require no
admin login. Links expire after 90 days. Invalid, revoked, superseded or expired
links return an unavailable page. Accepted records can be reviewed while their
link remains valid, but a second acceptance returns a conflict. Acceptance uses a
transaction, row lock and conditional database update. Staff actions check enquiry
change permission and bind every document lookup to that enquiry. POST actions
use Django CSRF protection; no mutation occurs on GET. Responses use private
no-store caching, no-referrer, no-index and restrictive content-security headers.
Client text is escaped in HTML/PDF. No IP address, geolocation, device fingerprint
or analytics is collected by this workflow. Staff actions are also recorded in
the existing Django admin log.

Email uses Django's configured backend and existing sender/reply-to settings.
Development Mailpit/localhost SMTP behaviour is unchanged. The message explains
that shot lists and printing are optional, links to the review page and PDF, and
attaches the print-ready PDF. An SMTP error does not record acceptance or a
successful send. As with ordinary SMTP, an ambiguous transport failure may need
staff review before retrying to avoid duplicate emails.

The email's PDF link opens a private download page (`?download=1`), where a
same-origin button downloads the stored PDF. This avoids starting an attachment
download inside a sandboxed email preview such as Mailpit. Both the download page
and PDF enforce the same token expiry and revocation rules as the review form.

## Multi-property limitation

The current repository has one address and flexible location/scope text per
enquiry; there is no structured child-property/parcel model to associate records
with. Multiple authorisations can therefore be created for one enquiry, each
explicitly limited to its named locations and authorising person. Staff must
verify coverage of every required location: the application cannot prove that an
unstructured list of properties is complete. Historical documents and final checks
remain attached to the original enquiry. This avoids introducing a parallel
property-management system.

## Files

- `realestate/authorisation_models.py`, imported by `realestate/models.py`
- `realestate/migrations/0031_propertyscopecheck_propertyauthorisation.py`
- `realestate/authorisation_forms.py`, `authorisation_terms.py`,
  `authorisations.py`, `authorisation_views.py`
- `realestate/admin.py`, `realestate/urls.py`
- `templates/realestate/authorisation.html`, `authorisation_document.html`
- `templates/admin/realestate/enquiry/authorisation_action.html`,
  `authorisation_hub.html`, existing `operations_hub.html`
- `templates/emails/real_estate/property_authorisation.html` and `.txt`
- `realestate/test_authorisations.py`, `requirements-test.txt`
- `scripts/preview_property_authorisations.py`
- This implementation report.

## Validation

29 new tests cover generation/prefill, residential and Custom land scope, private
identity, print fields, no-specific/N/A behaviour, positive instructions,
hazards/restrictions, optional/specific video, required/conditional fields,
immutable issued and accepted records, template changes, electronic/handwritten
acceptance, duplicate/revoked/expired/superseded links, CSRF, escaping, staff
permissions, enquiry binding, emails and failures, multi-location separation,
financial independence, Hub rendering/warnings and all final-check outcomes.

Final validation on 11 September 2026:

- Non-static Django suite: **627 tests, 626 passed, 1 existing skip**.
- Existing static-file tests: **2 passed** outside the Windows sandbox. The initial
  complete 629-test run encountered a sandbox temporary-directory access error in
  the manifest test; its isolated rerun passed without code changes.
- Combined Django coverage: **629 tests, 628 passed, 1 existing skip** across the
  split runs. The attempted final single full-suite run outside the sandbox was
  rejected by automatic approval review because its service reported a usage
  limit; it did not start.
- Existing JavaScript uploader suite: **18 passed**, run directly with Node after
  the subprocess-based `node --test` launcher encountered sandbox `spawn EPERM`.
- Django system checks, migration drift check and `git diff --check`: passed.
- Synthetic print-ready, electronically accepted and handwritten-received PDFs:
  generated successfully, four pages each; page layout visually inspected. The
  completion fields and accepted responses/metadata occupy separate coherent
  final pages. No live client email was sent.

Tests require `pip install -r requirements-test.txt` (PyMuPDF is test/preview-only).
Run the new tests with:

```powershell
.\.venv\Scripts\python.exe manage.py test realestate.test_authorisations --settings=openeire_api.settings_test
```

The preview script uses an isolated in-memory database and writes synthetic PDF
examples to `output/pdf/property-authorisation/`, with PNGs under `tmp/pdfs/`.

### Live Mailpit integration check

`scripts/test_property_authorisation_mailpit.py --serve` exercises the real Django
SMTP backend against the existing Mailpit service at `127.0.0.1:1025`, inspects the
captured MIME messages through `http://127.0.0.1:8025`, and submits forms over real
HTTP on loopback port 8766. It uses only synthetic records in a separate SQLite
database at `tmp/property-authorisation-mailpit.sqlite3`. It never uses the
customer database or production SMTP credentials.

Verified against the existing Mailpit instance:

- Electronic acceptance with no preferences and N/A instructions.
- Custom land requirements, restrictions, hazards and specific video instructions.
- Handwritten receipt through a real staff login and the Operations Hub action.
- A fourth sample remains awaiting manual acceptance when `--serve` is used.
- Captured email text/HTML, correct local review URL, PDF MIME attachment and
  handwritten fields; issued and accepted PDF downloads match persisted bytes.
- Missing CSRF rejection, required-field errors, successful acceptance, duplicate
  acceptance conflict and unchanged enquiry/financial fields.

The run writes scenario results and review links to
`tmp/property-authorisation-mailpit-results.json`. Without `--serve`, the HTTP
server stops after testing, so its email links are only usable during that run.
With `--serve`, stop the process after manual inspection. Reruns add clearly named
synthetic messages; they do not clear the existing Mailpit inbox.

## Rollout and business decisions

The additive migration was applied to the existing local workspace SQLite
database. Deployment still requires the normal production `manage.py migrate`
step and application release. No existing booking records need a data backfill.

No scan/photo upload was added: existing upload services are for customer media
delivery, and reusing them would risk exposing signed authorisations. Original
handwritten evidence must remain in existing private records. A future private
evidence-store integration needs an explicit storage/access/retention decision.

Petra should confirm the final business/legal wording, retention policy for these
records and original handwritten documents, and whether the initial 90-day link
lifetime and seven-day staff-warning window suit operations. No automatic deletion
policy, payment condition, unlimited liability waiver or change to commercial
terms was assumed. Custom video/drone detection relies on the existing written
scope/add-on text because the enquiry has no separate definitive inclusion flags;
staff should verify the preview against the Booking Agreement.
