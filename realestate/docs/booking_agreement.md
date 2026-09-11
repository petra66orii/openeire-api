# {{ business_display_name }} Real Estate Media Booking Agreement v2

{% if is_preview %}**DRAFT / PREVIEW - not for customer issue**

{% endif %}| Agreement details | Information |
| --- | --- |
| Booking reference | **{{ booking_reference }}** |
| Issued on | **{{ issued_on }}** |
| Agreement version | **{{ agreement_template_version }}** |

This Booking Agreement forms part of the Agreement between {{ business_display_name }} and the Client for the provision of real estate media services for the specific property booking set out below. It should be read together with the {{ business_display_name }} Property Media Service Terms, which set out the core legal terms governing property photography, videography, drone operations, licensing, payment, liability, and cancellation.

Drone operations in Europe are governed under the EASA framework, including Regulation (EU) 2019/947, and are administered in Ireland by the Irish Aviation Authority.

---

## 1. Parties

This Booking Agreement is entered into between:

| {{ business_display_name }} details | Information |
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
| Shoot date | {{ shoot_date }} |
| Shoot time | {{ shoot_time }} |
| Access contact on site | {{ access_contact }} |
| Access notes / restrictions | {{ access_notes }} |
| Drone services included | Subject to the selected package, agreed add-ons, legal conditions, weather, safety, and operational restrictions on the day |
| Travel supplement applies | {{ travel_supplement_applies }} |
| Travel details | {{ travel_details }} |

The Client is responsible for identifying before or during the Shoot any specific features, views, boundaries, fencing, access points, rooms, land parcels, structures, selling features, required angles or locations, or other elements that are essential to the marketing brief. Where the Client or their representative is present at the Shoot, {{ business_display_name }} may rely on their instructions as to the required coverage.

---

## 3. Selected Package and Included Services

The Client books the following package:

| Package and payment details | Information |
| --- | --- |
| Package name | {{ package_name }} |
{% if travel_supplement_amount %}| Travel supplement included | {{ travel_supplement_amount }} |
{% endif %}| {% if vat_registered and not price_input_is_gross %}Quoted services subtotal excluding VAT{% else %}Quoted services total{% endif %} | {{ quote_total }} |
| VAT | {% if vat_registered %}{{ vat_total }}{% else %}Not applicable{% endif %} |
{% if has_adjustments %}| Approved fee reductions | {{ adjustment_total }} |
{% endif %}| Total fee payable | {{ total_required }} |
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

{% for deliverable in agreed_deliverables %}- {{ deliverable }}
{% empty %}- Scope not yet agreed (preview only).
{% endfor %}
- Any additional agreed add-ons listed below.
- Professionally edited property media suitable for marketing the property identified in this Booking Agreement.
- Any drone, video, social media, floor plan, virtual tour, or additional media outputs only where expressly included in the package or agreed in writing.
- {{ additional_photograph_copy }}

Additional Agreed Add-Ons:

- {{ add_ons_summary }}

Only the Deliverables expressly listed above are included in this Booking Agreement. RAW files, source files, unedited footage, and any services not expressly listed are excluded unless separately agreed in writing.

---

## 4. Fee and Payment Terms

4.1 {% if is_custom_payment %}Payment is due according to the approved custom payment schedule in Section 3.{% else %}{{ payment_clause_text }}{% endif %}

4.2 {% if is_custom_payment %}Booking confirmation is subject to acceptance of this Booking Agreement and the confirmation conditions in the approved custom schedule in Section 3.{% else %}{{ booking_confirmation_text }}{% endif %} Until confirmation, {{ business_display_name }} reserves the right to release the proposed booking slot to another client.

4.3 {{ business_display_name }} may withhold delivery of the Deliverables until 100% of the Total Fee and any other sums due have been paid in full.

4.4 No licence shall take effect until all sums due have been paid in full.

4.5 Any additional work, amendments, extra travel, waiting time, extended attendance, additional outputs, or post-booking scope changes requested by the Client may be charged separately at {{ business_display_name }}'s then-current rates. A request made after {{ business_display_name }} has left the Property for photographs or footage of an element that was not identified as a required Deliverable before or during the Shoot shall be treated as additional work. Where fulfilling that request requires a return visit, additional attendance and travel charges may apply, together with additional production or editing charges where applicable. This does not apply where {{ business_display_name }} failed to capture an element expressly agreed in writing as part of the original scope.

---

## 5. Cancellation and Rescheduling

5.1 If the Client cancels the booking more than 72 hours before the Shoot Date, {% if is_split_payment %}the deposit shall be retained by {{ business_display_name }} and no further fee shall be due{% else %}{{ business_display_name }} may retain any amount already paid against administration, scheduling, preparation, and other work already performed, and no further fee shall be due unless otherwise set out in the approved payment terms{% endif %}.

5.2 {{ cancellation_payment_text }}

5.3 If the Client cancels the booking less than 24 hours before the Shoot Date, or if {{ business_display_name }} attends the Property and cannot reasonably perform the Services due to lack of access, inaccurate instructions, an unready site, or other Client-side failure, 100% of the Total Fee shall be payable.

5.4 {{ business_display_name }} shall permit one reschedule without additional rescheduling charge where {{ business_display_name }} reasonably determines that weather conditions, safety concerns, or legal or operational restrictions make the Services unsuitable to proceed on the Shoot Date.

5.5 Any further reschedule, or any reschedule requested by the Client, may be charged at {{ business_display_name }}'s then-current rates and shall be subject to availability.

5.6 A material change to the Property, scope, date, time, or access arrangements may be treated by {{ business_display_name }} as a cancellation and rebooking.

---

## 6. Delivery and Editing

6.1 Standard turnaround for the selected package: **{{ turnaround_label }}**. {{ turnaround_detail }}

6.2 {{ turnaround_context }}

6.3 Deliverables shall be supplied in {{ business_display_name }}'s standard professional editing style.

6.4 No subjective or discretionary revision rounds are included unless expressly stated in the selected package. {{ business_display_name }} will correct an obvious technical defect or material failure to deliver the agreed written scope. Requests to substitute, reorder, add or newly capture material based on preferences or requirements introduced after delivery may be charged separately.

6.5 Any obvious technical defect or material deviation from the agreed written scope must be notified within 24 hours of delivery. {{ business_display_name }} will address such defects or deviations, including items expressly agreed in writing that {{ business_display_name }} failed to capture.

---

## 7. Client Acknowledgements

7.1 The Client acknowledges and agrees that:

- The licence granted for the Deliverables is limited to the marketing of the specific property identified in this Booking Agreement.
- Where the Client is a private property owner, the Client may permit one appointed estate agent/auctioneer acting on their behalf to use the Deliverables solely for marketing the same property listing, subject to these Terms.
- The licence is non-exclusive, non-transferable except as expressly permitted above, does not transfer ownership of any Deliverable to the Client, and may not be reused for any other property, development, marketing campaign, or instruction.
- The licence ends immediately when the Property is sold, let, withdrawn from the market, or the relevant marketing instruction otherwise ends.
- RAW files and unedited source material are excluded.
- Drone capture is subject to legal, regulatory, and safety conditions on the day.
- {{ business_display_name }} may rely on the Client's permissions, instructions, and warranties regarding access and authority to instruct the Services.

---

## 8. Incorporation of Service Terms

8.1 This Booking Agreement incorporates and is subject to the {{ business_display_name }} Property Media Service Terms, which together form the entire agreement between the parties for the relevant booking.

8.2 In the event of any inconsistency between this Booking Agreement and the {{ business_display_name }} Property Media Service Terms, this Booking Agreement shall prevail to the extent of that inconsistency for the specific booking details and commercial terms only.

---

## 9. Signatures and Acceptance

Issued for and on behalf of {{ business_display_name }}

| {{ business_display_name }} issuing details | Information |
| --- | --- |
| Name | {{ business_signatory_name }} |
| Title | Proprietor |
| Date | {{ issued_on }} |

Signed by or on behalf of the Client:

| Client acceptance details | Completion |
| --- | --- |
| Name | ______________________________ |
| Title | ______________________________ |
| Date | ______________________________ |
| Signature (if signing) | ______________________________ |

{{ acceptance_text }}
