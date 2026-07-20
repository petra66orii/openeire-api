# {{ business_display_name }} Real Estate Media Booking Agreement

| Agreement details | Information |
| --- | --- |
| Booking reference | **{{ booking_reference }}** |
| Issued on | **{{ issued_on }}** |
| Agreement version | **{{ agreement_template_version }}** |

This Booking Agreement forms part of the Agreement between {{ business_display_name }} (OpenEire) and the Client for the provision of real estate media services for the specific property booking set out below. It should be read together with the OpenEire Property Media Service Terms, which set out the core legal terms governing property photography, videography, drone operations, licensing, payment, liability, and cancellation.

Drone operations in Europe are governed under the EASA framework, including Regulation (EU) 2019/947, and are administered in Ireland by the Irish Aviation Authority.

---

## 1. Parties

This Booking Agreement is entered into between:

| OpenEire details | Information |
| --- | --- |
| Business | **{{ business_display_name }}** |
| Address | {{ business_address }} |
| Email | {{ business_email }} |

and

| Client details | Information |
| --- | --- |
| Client name | {{ client_name }} |
| Agency / business name | {{ company_name }} |
| Contact name | {{ client_contact_name }} |
| Email | {{ email }} |
| Telephone | {{ phone }} |
| Registered / business address | {{ registered_business_address }} |

---

## 2. Property and Booking Details

| Property and booking details | Information |
| --- | --- |
| Property address | {{ property_address }} |
| Property type | {{ property_type }} |
| Listing type | {{ listing_type }} |
| Shoot date | {{ shoot_date }} |
| Shoot time | {{ shoot_time }} |
| Access contact on site | {{ access_contact }} |
| Access notes / restrictions | {{ access_notes }} |
| Drone services included | Subject to the selected package, agreed add-ons, legal conditions, weather, safety, and operational restrictions on the day |
| Travel supplement applies | If agreed in writing before the shoot |
| Travel details | {{ travel_details }} |

---

## 3. Selected Package and Included Services

The Client books the following package:

| Package and payment details | Information |
| --- | --- |
| Package name | {{ package_name }} |
| {% if vat_registered and not price_input_is_gross %}Package fee excluding VAT{% else %}Package total{% endif %} | {{ quote_total }} |
| VAT | {{ vat_total }} |
| Total fee payable | {{ total_required }} |
| Payment arrangement | {{ payment_arrangement_label }} |
| Payment due date | {{ payment_due_date }} |
| Expected payment method | {{ expected_payment_method }} |
{% if is_split_payment %}| Deposit required | {{ deposit_amount }} |
| Remaining balance | {{ balance_due }} |
{% elif is_custom_payment %}| Approved custom payment schedule | {{ custom_payment_terms }} |
{% else %}| Full payment due | {{ total_required }} |
{% endif %}

{% if not vat_registered %}*{{ vat_notice }}*

{% endif %}
Included Deliverables:

- The deliverables included in the selected package listed above.
- Any additional agreed add-ons listed below.
- Professionally edited property media suitable for marketing the property identified in this Booking Agreement.
- Any drone, video, social media, floor plan, virtual tour, or additional media outputs only where expressly included in the package or agreed in writing.

Additional Agreed Add-Ons:

- {{ add_ons_summary }}

Only the Deliverables expressly listed above are included in this Booking Agreement. RAW files, source files, unedited footage, and any services not expressly listed are excluded unless separately agreed in writing.

---

## 4. Fee and Payment Terms

4.1 {{ payment_clause_text }}

4.2 {{ booking_confirmation_text }} Until confirmation, OpenEire reserves the right to release the proposed booking slot to another client.

4.3 OpenEire may withhold delivery of the Deliverables until 100% of the Total Fee and any other sums due have been paid in full.

4.4 No licence shall take effect until all sums due have been paid in full.

4.5 Any additional work, amendments, extra travel, waiting time, extended attendance, additional outputs, or post-booking scope changes requested by the Client may be charged separately at OpenEire's then-current rates.

---

## 5. Cancellation and Rescheduling

5.1 If the Client cancels the booking more than 72 hours before the Shoot Date, {% if is_split_payment %}the deposit shall be retained by OpenEire and no further fee shall be due{% else %}OpenEire may retain any amount already paid against administration, scheduling, preparation, and other work already performed, and no further fee shall be due unless otherwise set out in the approved payment terms{% endif %}.

5.2 {{ cancellation_payment_text }}

5.3 If the Client cancels the booking less than 24 hours before the Shoot Date, or if OpenEire attends the Property and cannot reasonably perform the Services due to lack of access, inaccurate instructions, an unready site, or other Client-side failure, 100% of the Total Fee shall be payable.

5.4 OpenEire shall permit one reschedule without additional rescheduling charge where OpenEire reasonably determines that weather conditions, safety concerns, or legal or operational restrictions make the Services unsuitable to proceed on the Shoot Date.

5.5 Any further reschedule, or any reschedule requested by the Client, may be charged at OpenEire's then-current rates and shall be subject to availability.

5.6 A material change to the Property, scope, date, time, or access arrangements may be treated by OpenEire as a cancellation and rebooking.

---

## 6. Delivery and Editing

6.1 OpenEire shall use reasonable endeavours to deliver the Deliverables within 24 hours of the Shoot Date.

6.2 The Client acknowledges that delivery within 24 hours is conditional upon lawful and safe operating conditions, property access, weather, technical issues, and events outside OpenEire's reasonable control.

6.3 Deliverables shall be supplied in OpenEire's standard professional editing style.

6.4 No subjective revision rounds are included.

6.5 Any obvious technical defect or material deviation from the agreed scope must be notified within 24 hours of delivery, following which OpenEire may, at its discretion, correct the issue.

---

## 7. Client Acknowledgements

7.1 The Client acknowledges and agrees that:

- The licence granted for the Deliverables is limited to the marketing of the specific property identified in this Booking Agreement.
- Where the Client is a private property owner, the Client may permit one appointed estate agent/auctioneer acting on their behalf to use the Deliverables solely for marketing the same property listing, subject to these Terms.
- The licence is non-exclusive, non-transferable except as expressly permitted above, does not transfer ownership of any Deliverable to the Client, and may not be reused for any other property, development, marketing campaign, or instruction.
- The licence ends immediately when the Property is sold, let, withdrawn from the market, or the relevant marketing instruction otherwise ends.
- RAW files and unedited source material are excluded.
- Drone capture is subject to legal, regulatory, and safety conditions on the day.
- OpenEire may rely on the Client's permissions, instructions, and warranties regarding access and authority to instruct the Services.

---

## 8. Incorporation of Service Terms

8.1 This Booking Agreement incorporates and is subject to the OpenEire Property Media Service Terms, which together form the entire agreement between the parties for the relevant booking.

8.2 In the event of any inconsistency between this Booking Agreement and the OpenEire Property Media Service Terms, this Booking Agreement shall prevail to the extent of that inconsistency for the specific booking details and commercial terms only.

---

## 9. Signatures and Acceptance

Signed electronically for and on behalf of {{ business_display_name }}

| OpenEire signature details | Information |
| --- | --- |
| Name | {{ business_signatory_name }} |
| Title | {{ business_display_name }} |
| Date | {{ issued_on }} |

Signed by or on behalf of the Client:

| Client signature details | Handwritten completion |
| --- | --- |
| Name | ______________________________ |
| Title | ______________________________ |
| Date | ______________________________ |

{{ acceptance_text }}

{{ booking_confirmation_text }}
