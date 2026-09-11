# Real Estate Email QA Checklist

Use this checklist before enabling or changing any real estate email flow in production.

## Rendering

- [ x ] Send each HTML template to Mailpit and confirm the layout renders as a centred 600px email with the branded dark header, green accent divider, white content card, and readable footer.
- [ x ] Open each email on mobile Mailpit/Gmail preview and confirm the content is readable without horizontal scrolling.
- [ x ] Confirm the hidden preheader appears correctly in inbox preview text and does not appear in the visible body.
- [ x ] Confirm the white transparent OpenEire Studios logo loads from an absolute public URL and remains readable on the dark header.
- [ x ] Confirm no broken image icon appears when the logo URL is missing.
- [x] Confirm the logo width looks balanced at roughly 180-220px on desktop and mobile email clients.
- [ ] Confirm the logo alt text reads "OpenEire Studios" if images are blocked.
- [x] Confirm the centred header tagline reads: `Aerial Photography • Property Media • Visual Licensing`.
- [x] Confirm the plain-text version is included and readable for each email.

## Accent And CTA Styling

- [x] Confirm OpenEire green (`#16A34A`) is the dominant accent colour for the divider, primary buttons, and highlighted states.
- [x] Confirm gold (`#FFC400`) is used only as a subtle secondary highlight and does not overpower the email.
- [x] Confirm the reusable green CTA button appears only when both label and URL are present.
- [x] Confirm no empty or broken CTA buttons render when a URL or label is missing.

## Links And Reply Handling

- [ ] Confirm `Reply-To` points to the intended studio/real estate inbox.
- [ ] Confirm quote emails use a mailto CTA when reply details are configured.
- [ ] Confirm booking agreement links open correctly before sending a booking/payment email.
- [ ] Confirm Arrangement-appropriate Stripe payment links open correctly before sending a booking/payment email.
- [ ] Confirm delivery links open correctly and only expose the intended client delivery files.
- [ ] Confirm review links open correctly after follow-up or thank-you emails.

## Flow-Specific Copy

- [ ] Quote emails describe scope and price only, and do not imply the booking is confirmed.
- [ ] Quote emails include "Ready to proceed?" and explain that the Booking Agreement and arrangement-appropriate payment instructions are issued after the client replies.
- [ ] Quote, booking-agreement and confirmation emails show the package-aware standard turnaround: next business day for Essential/Starter, two business days for Pro/Premium, or specifically agreed for Custom/Not sure.
- [ ] Enquiry, quote, booking-agreement and confirmation emails show the current catalogue's included edited-photograph allowance for new work, while issued historical scope remains unchanged.
- [ ] Customer-facing scope copy states that additional edited photographs may be agreed at EUR 10 per photograph.
- [ ] Rush delivery is described as still-photography only and never as rush processing for video, social cuts, virtual tours, floor plans or other Premium outputs.
- [ ] Deposit bookings require the signed Booking Agreement and cleared deposit before confirmation.
- [ ] Quote emails include a "Proceed with this quote" mailto button when a reply email is configured.
- [ ] Quote emails show a safe highlighted fallback message instead of a broken button when no reply email is configured.
- [ ] New quote price summary displays package total, zero VAT, total payable, arrangement-appropriate payment fields, and the supplier-not-VAT-registered notice.
- [ ] Historical quote price summaries retain their snapshotted VAT treatment and monetary values.
- [ ] Quote price summary never shows `€None`, `€.00`, or blank rows.
- [ ] Quote plain-text emails mirror the HTML quote summary and booking-confirmation conditions.
- [ ] Deposit emails use "Pay deposit" when a valid deposit link is present; full-upfront invoice emails use "Pay in full".
- [ ] Booking/payment emails show "Review Booking Agreement" as a secondary text link when an agreement link is present.
- [ ] Full-upfront bookings require the signed agreement and full cleared payment before confirmation.
- [ ] Full-on-shoot-day bookings can confirm after the signed agreement, while full payment remains due on the shoot date.
- [ ] Custom bookings follow their explicit approved terms; missing terms fail validation. Staff verify any custom pre-confirmation conditions before confirming.
- [ ] No deposit requirement or deposit payment CTA leaks into non-deposit bookings.
- [ ] Full-on-shoot-day agreement/quote emails show no advance payment CTA; an issued invoice may link to its valid hosted payment page. Custom invoice links describe the actual invoice and approved schedule.
- [ ] Render Booking Agreement v2 PDFs for all four arrangements: deposit and remaining balance for split payments, full amount for upfront/shoot-day, approved schedule for custom. Verify dates and amounts.
- [ ] Cash shoot-day agreements and quote payment instructions mention that a receipt will be issued; recorded cash payments retain receipt handling.
- [ ] Verify must-have coverage responsibility, chargeable new capture/return visits, and correction of genuine defects/written-scope failures with 24-hour notification.
- [ ] Existing issued agreement snapshots remain unchanged; explicitly generating a new version produces version 2.0.

## Deposit Checkout link lifetime

Deposit-request emails currently contain a raw Stripe Checkout URL. Each Session is
created with Stripe's maximum 24-hour lifetime, so the URL is not a durable payment
address. Before an admin email is sent, the stored Session is retrieved and reused
only while it is open, unpaid, correctly scoped, and at least 30 minutes from expiry.
Expired or invalid Sessions are replaced, and Stripe after-expiration recovery is
enabled for clients who open an older email.

The recommended durable design is a stable, signed OpenÉire URL in the email. That
endpoint should validate the signature and enquiry, then redirect to a current valid
Checkout Session (creating one when required). This keeps short-lived Stripe URLs out
of long-lived email content. The existing hosted-invoice workflow is another viable
option for deposits and already provides a durable Stripe-hosted payment page, but
adopting it would change deposit collection, invoice delivery, reminder, and webhook
semantics and should be planned as a separate payment-flow migration.
- [ ] Delivery emails include a "Download Media" CTA when a delivery link is present.
- [ ] Delivery emails include the agreed commercial marketing licence wording and do not imply ownership transfer.
- [ ] Delivery emails retain the MyAirBridge three-day link-expiry warning.
- [ ] Follow-up emails include a "Leave a Google Review" CTA only when a review link exists.
- [ ] Weather reschedule emails clearly state the proposed replacement date/time and next step.

## Final Send Checks

- [ ] Final Booking Agreement sends require a persisted reference, client name/email, property address, agreed shoot date, selected package, agreed enquiry price and explicit deliverables. Split payments also require a balance due date. Shoot-day payment dates must match the shoot date.
- [ ] In admin, enter **Agreed scope** (one deliverable per line) for Custom work or negotiated scope. This replaces the standard scope; customer messages and internal notes are never treated as approved deliverables. Historical bookings with no recoverable written scope require staff review.
- [ ] Legacy Custom placeholders such as “Included photographs as specifically agreed” block final reissue until actual deliverables are entered. They must not become approved scope merely by being copied into a newer snapshot.
- [ ] When Agreed scope replaces catalogue scope, agreement emails omit the catalogue photograph allowance. Check the attached PDF for the approved deliverables.
- [ ] Changing the selected package after an agreement was issued requires newly reconciled Agreed scope before reissue. An unchanged override from the previous package is not automatically reapproved; the original snapshot remains intact.
- [ ] Package names and agreed add-on labels are price-neutral. Monetary rows use the enquiry's quote snapshot (or issued invoice amounts where no quote exists), approved Custom total and active fee reductions; never today's catalogue price. Travel is already included, not added again.
- [ ] Non-VAT agreements say `VAT: Not applicable`; no VAT is added. Historic VAT snapshots retain their treatment.
- [ ] Client acceptance is method-neutral; the business issues the document without claiming an electronic signature. The client acceptance table includes an optional signature line.
- [ ] Existing immutable snapshots stay unchanged. Corrected agreements remain version 2.0; the existing new-issue action creates a separate timestamped snapshot.

### Repeatable agreement previews

After applying migration `0030_realestateenquiry_agreed_scope`, use:

```bash
python scripts/preview_booking_agreements.py
python scripts/preview_booking_agreements.py --mailpit
```

The script uses an isolated in-memory database and current backend catalogue prices
for standard sample quotes. It does not touch customer records. PDFs are written to
`tmp/booking-agreement-samples/`; the optional Mailpit run sends only to the local
SMTP capture with sample recipients and `LOCAL SAMPLE` subjects. Review all four
arrangements at http://localhost:8025, including HTML, text and PDF attachments.
An unsaved/incomplete preview is allowed but labelled `DRAFT / PREVIEW`; the real
admin send calls `for_customer=True` and fails closed on missing contractual data.

For each PDF, inspect the complete payment table, readable deliverable bullets,
unsplit short clauses, headings with their content, and the complete acceptance
section. Also check that no catalogue rate or electronic-signature claim remains.

Custom commercial terms still require staff review for consistency with the
standard cancellation, delivery and licence clauses; software does not interpret
free-text terms or establish that acceptance is legally sufficient.

- [ ] Check sender display name appears as OpenEire Studios / OpenÉire Studios as configured for the mailbox.
- [ ] Check required client/property/package variables are populated, with no raw template placeholders such as `{{ first_name }}`.
- [ ] Check optional fields are either populated or gracefully omitted.
- [ ] Check any attachments are intentional and safe to send.
- [ ] Check the email subject matches the client journey stage.
- [ ] Confirm the final email in Mailpit before sending.
- [ ] Confirm Gmail and mobile rendering during the final launch QA pass.
