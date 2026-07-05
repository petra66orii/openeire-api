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
- [ ] Confirm booking agreement links open correctly before sending a booking/deposit email.
- [ ] Confirm Stripe/deposit payment links open correctly before sending a booking/deposit email.
- [ ] Confirm delivery links open correctly and only expose the intended client delivery files.
- [ ] Confirm review links open correctly after follow-up or thank-you emails.

## Flow-Specific Copy

- [ ] Quote emails describe scope and price only, and do not imply the booking is confirmed.
- [ ] Quote emails include "Ready to proceed?" and explain that the Booking Agreement and booking deposit request are issued after the client replies.
- [ ] Quote emails clearly state the booking is confirmed only after both the Booking Agreement is signed and the booking deposit has cleared.
- [ ] Quote emails include a "Proceed with this quote" mailto button when a reply email is configured.
- [ ] Quote emails show a safe highlighted fallback message instead of a broken button when no reply email is configured.
- [ ] Quote price summary displays only supplied rows: quote total ex VAT, VAT, total incl. VAT, deposit required, and balance on delivery.
- [ ] Quote price summary never shows `€None`, `€.00`, or blank rows.
- [ ] Quote plain-text emails mirror the HTML quote summary and booking-confirmation conditions.
- [ ] Booking/deposit emails use "Pay Secure Deposit" as the primary CTA when a deposit link is present.
- [ ] Booking/deposit emails show "Review Booking Agreement" as a secondary text link when an agreement link is present.
- [ ] Booking/deposit emails state the booking is not confirmed until the agreement is signed and the deposit is received in cleared funds.
- [ ] Confirmation emails are sent only after the signed agreement and cleared deposit are received.
- [ ] Delivery emails include a "Download Media" CTA when a delivery link is present.
- [ ] Delivery emails include the agreed commercial marketing licence wording and do not imply ownership transfer.
- [ ] Follow-up emails include a "Leave a Google Review" CTA only when a review link exists.
- [ ] Weather reschedule emails clearly state the proposed replacement date/time and next step.

## Final Send Checks

- [ ] Check sender display name appears as OpenEire Studios / OpenÉire Studios as configured for the mailbox.
- [ ] Check required client/property/package variables are populated, with no raw template placeholders such as `{{ first_name }}`.
- [ ] Check optional fields are either populated or gracefully omitted.
- [ ] Check any attachments are intentional and safe to send.
- [ ] Check the email subject matches the client journey stage.
- [ ] Confirm the final email in Mailpit before sending.
- [ ] Confirm Gmail and mobile rendering during the final launch QA pass.
