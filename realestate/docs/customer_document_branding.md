# Customer-document branding implementation

Completed 13 September 2026. This is a presentation pass: legal template versions,
wording, validation, snapshots, acceptance and financial workflows are unchanged.

## Shared components and visual system

`openeire_api/pdf_branding.py` provides an opt-in `OpenEirePDFTheme`, standard page
margins, header/footer decoration, title/body/metadata styles, green section
markers, information tables, financial emphasis, paired metadata cards, notices,
neutral capture-preference cards and handwritten completion fields.

The palette follows `templates/emails/base_email.html`: charcoal `#1A1A1A`, green
`#16A34A`, a small yellow `#FFC400` accent, warm white `#FAFAF9`, borders `#E5E7EB`
and muted text `#5F6673`. Pale green `#EDF7F0` supplies restrained table/card fills.
Body text is near-black on white. PDFs use built-in Helvetica, with selectable
text and page numbers; no new font dependency or rasterised document pages.

The canonical `static/emails/openeire-studios-logo.png` is loaded locally for PDF
headers. Transparent padding is trimmed in memory for layout; the repository
asset is unchanged. Missing or unreadable assets fall back to the business name.
PDF rendering never downloads a logo. Business identity comes from the existing
identity helpers; authorisation identity uses its frozen snapshot. Financial
documents continue to use public identity and omit the private legal signatory.

The project-level `static/` directory previously was not included in Django's
static-file discovery. `STATICFILES_DIRS` now includes it, allowing the existing
email logo to be served and included by `collectstatic`. That directory contains
the canonical email logo; application static assets remain discoverable.

## Email changes

The authorisation HTML now extends `emails/base_email.html`, inheriting the logo,
charcoal header/footer, tagline, responsive table layout, typography and green
rounded CTA. A reusable `emails/includes/details_card.html` contains the location
card. The send function supplies public identity, the existing email-logo helper,
CTA URL and CTA label. The shared base and other email templates are unchanged.

The friendly explanation, No specific requirements reassurance, PDF link,
attachment and equally acceptable handwritten route remain. The plain-text
template and email backend selection are unchanged.

## PDF presentation changes

**Booking Agreement:** retains the complete rendered Markdown and v2.0 snapshot
path. It opts into the common header/footer, left-aligned title, numbered heading
markers and softly tinted table headers. Existing grouping of compact tables and
the final acceptance block remains. All agreement paragraphs and table text are
checked against extracted PDF text in a regression test.

**Invoice:** keeps its original row values and calculation functions. Metadata and
customer/job details sit side by side when reasonably short, with full-width
fallback for long locations. Description and package scope occupy full-width
paragraphs. Total, Paid and Outstanding have stronger type and pale-green emphasis;
Paid status is highlighted. VAT and release notices remain intact. The realistic
outstanding and paid samples each fit on one page.

**Property Access & Shoot Authorisation:** retains v1.0 and the existing frozen
content/acceptance paths. Booking details use an information table; restrictions
and hazards remain readable text. Both no-specific and specific capture/video
responses receive the same neutral card treatment. The final page groups neutral
acceptance wording with either the handwritten fields or accepted responses and
signer details. Sample documents remain four pages, without an isolated acceptance
paragraph on an otherwise empty page.

**Cash receipt:** receives the same restrained theme through the existing shared
financial `_pdf()` path. Receipt number, payer, amounts and remaining balance are
unchanged. The sample remains one page.

**Markdown renderer:** accepts an optional `theme` argument. Default callers retain
their existing styles. Product licences and unrelated PDFs do not opt into the
theme. Branded tables support splitting long rows; default table behaviour remains
unchanged. No commercial wording or document-template files were edited.

## Snapshot and deployment behaviour

- No migration or data backfill is introduced by this branding pass.
- Existing stored authorisation PDF bytes are returned unchanged. No bulk
  regeneration occurs. Newly generated issued/accepted PDFs use the new theme.
- Booking Agreement snapshots store Markdown/context, not PDF bytes; rendering a
  snapshot uses the new presentation while preserving its frozen wording/data.
- Invoice/receipt calculations, numbering, status persistence and payment logic
  are unchanged; only presentation of their existing rows changes.
- Deploy application code/templates and run the normal `collectstatic` build step.
  Keep the canonical PNG in the application checkout for server-side PDF use.
- Existing email logo settings remain supported. When an email points at the
  frontend/site domain, that domain must serve its configured logo URL, or set
  `REALESTATE_EMAIL_LOGO_URL` to the deployed public asset URL. Local Mailpit
  verification served the logo from the loopback test server and confirmed HTTP
  200 with `image/png`.
- No production packages were added. Existing test/preview requirements provide
  PyMuPDF for extraction and visual inspection.

## Files changed for this pass

1. `openeire_api/pdf_branding.py` — shared PDF theme and components.
2. `openeire_api/pdf_markdown.py` — optional theme injection.
3. `openeire_api/settings.py` — discover the canonical static logo.
4. `realestate/documents.py` — Booking Agreement theme integration.
5. `realestate/financial_documents.py` — invoice/receipt presentation.
6. `realestate/authorisations.py` — PDF theme and branded email context.
7. `templates/emails/real_estate/property_authorisation.html` — shared email base.
8. `templates/emails/includes/details_card.html` — reusable location/details card.
9. `realestate/test_document_branding.py` — ten targeted regression tests.
10. `scripts/preview_customer_document_branding.py` — synthetic PDF/email previews.
11. `scripts/test_property_authorisation_mailpit.py` — selectable local port and
    local static asset serving for real SMTP/HTTP testing.
12. This report. Earlier workflow implementation files are retained separately;
    the pre-existing `.gitignore` edit was not changed by this pass.

## Samples and visual verification

Generated nine synthetic examples; all 31 PDF pages were rendered and visually
inspected for layout, table splitting, readable text, header/footer placement and
pagination. Extraction also checked for missing replacement glyphs and text
outside the page. Euro signs and OpenÉire's accented name remain selectable.

| Sample | Pages |
| --- | ---: |
| [Booking Agreement — deposit then balance](../../output/pdf/branding/booking-deposit_then_balance.pdf) | 6 |
| [Booking Agreement — full payment on shoot day](../../output/pdf/branding/booking-full_on_shoot_day.pdf) | 6 |
| [Invoice — outstanding](../../output/pdf/branding/invoice-outstanding.pdf) | 1 |
| [Invoice — paid](../../output/pdf/branding/invoice-paid.pdf) | 1 |
| [Cash receipt](../../output/pdf/branding/cash-receipt.pdf) | 1 |
| [Authorisation — print-ready](../../output/pdf/branding/authorisation-print-ready.pdf) | 4 |
| [Authorisation — electronic acceptance](../../output/pdf/branding/authorisation-electronic.pdf) | 4 |
| [Authorisation — specific instructions](../../output/pdf/branding/authorisation-specific.pdf) | 4 |
| [Authorisation — no specific requirements](../../output/pdf/branding/authorisation-no-specific.pdf) | 4 |

Samples and extracted text are local review artifacts under `output/pdf/branding/`
(ignored by Git). Regenerate with:

```powershell
.\.venv\Scripts\python.exe scripts/preview_customer_document_branding.py
```

## Test results

- Final full Django run: **640 tests, 639 passed, 1 existing skip** in 69.260s.
  This includes the production static-manifest test and the new logo-discovery
  test. The full suite ran outside the Windows sandbox because its existing
  temporary-directory restrictions prevent the static-manifest test there.
- Final focused branding/authorisation run: **40 passed**.
- Existing JavaScript multipart-upload suite: **18 passed**.
- Django system checks, migration drift checks and `git diff --check`: passed.
- Ten new tests cover opt-in styling, preserved legal text/snapshots, unchanged
  invoice values, receipt generation, private-identity policy, logo fallback,
  logo collection, immutable stored authorisation PDFs, shared email branding,
  all existing real-estate email-template rendering, and long descriptions.
  (Some tests exercise several related assertions.)

The existing Mailpit container was restarted after it had stopped. Real SMTP/HTTP
checks then passed for electronic acceptance with no preferences, Custom land
instructions/video/hazards, and staff recording of handwritten receipt. Checks
include email MIME/PDF attachment, review URL, required fields, CSRF, duplicate
acceptance and unchanged enquiry fields. A sample remains awaiting manual review.

Eight customer-email previews were sent to Mailpit: enquiry reply, confirmation,
Booking Agreement, deposit request, invoice issued, cash receipt, delivery and
authorisation. Look for **LOCAL BRANDING PREVIEW** subjects at
`http://127.0.0.1:8025`. The authorisation preview has a working local review URL;
financial links in generic previews deliberately use `example.invalid`.

No browser was connected for rendered desktop/mobile email inspection. Captured
HTML, shared responsive markup, logo HTTP availability, CTA URLs and real form
behaviour were verified; cross-email-client visual rendering remains a manual
review step. No customer data or production email service was used.

The email download link opens a private download page before downloading the
stored PDF, avoiding sandboxed-email download restrictions. The local Mailpit
preview server handles concurrent browser connections. GitHub CI installs
`requirements-test.txt` to include PDF extraction dependencies.
