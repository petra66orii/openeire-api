from .models import RealEstateEnquiry
from openeire_api.business_identity import get_business_identity


def booking_payment_copy(enquiry):
    """Shared agreement and email wording for the approved payment arrangement."""
    arrangement = enquiry.payment_arrangement
    custom_terms = str(getattr(enquiry, "custom_payment_terms", "") or "").strip()
    if arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM and not custom_terms:
        raise ValueError("Custom booking agreements require approved custom payment terms.")

    brand = get_business_identity().display_name
    acceptance_text = (
        f"By signing or otherwise formally accepting this Booking Agreement through {brand}'s "
        "approved booking process, the Client confirms that they have read, understood and agreed "
        f"to this Booking Agreement and the {brand} Property Media Service Terms."
    )
    cancellation_payment_text = (
        "If the Client cancels the booking between 24 and 72 hours before the Shoot Date, 50% of the "
        "Total Fee shall be payable by the Client, less any amount already paid."
    )
    if arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
        booking_confirmation_text = (
            "The booking is confirmed once the Booking Agreement has been accepted or signed "
            f"and {brand} has received the booking deposit in cleared funds."
        )
        payment_clause_text = (
            "The booking deposit forms part of the Total Fee. The remaining balance is due on the "
            "agreed payment due date. Final high-resolution media and usage rights remain withheld "
            "until all sums due have been paid in full."
        )
        acceptance_text += " The deposit and remaining balance are payable as set out in Section 4."
        cancellation_payment_text = cancellation_payment_text.replace("amount already paid", "deposit already paid")
    elif arrangement == RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT:
        booking_confirmation_text = (
            "The booking is confirmed once the Booking Agreement has been accepted or signed "
            f"and {brand} has received full payment in cleared funds."
        )
        payment_clause_text = (
            "The Total Fee is payable in full before booking confirmation. Final high-resolution "
            "media and usage rights remain withheld until all sums due have been paid in full."
        )
        acceptance_text += " Full payment is required before booking confirmation."
    elif arrangement == RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY:
        booking_confirmation_text = (
            "Where payment is due in full on the Shoot Date, the booking may be confirmed once "
            "the Booking Agreement has been accepted or signed, before payment is made."
        )
        payment_clause_text = (
            "The Total Fee is due on the Shoot Date. The fee is payable for services performed and is not "
            "contingent on the property being sold, let, or otherwise completed. Final high-resolution media "
            "and usage rights remain withheld until full payment has been received."
        )
        if getattr(enquiry, "expected_payment_method", "") == RealEstateEnquiry.ExpectedPaymentMethod.CASH:
            payment_clause_text += " Where cash is the expected payment method, a receipt will be issued."
        acceptance_text += " Full payment remains due on the Shoot Date."
    elif arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM:
        booking_confirmation_text = custom_terms
        payment_clause_text = custom_terms
        acceptance_text += " Payment is due according to the agreed custom schedule."
    else:
        raise ValueError("Select a valid payment arrangement before preparing a Booking Agreement.")

    return {
        "custom_payment_terms": custom_terms,
        "booking_confirmation_text": booking_confirmation_text,
        "payment_clause_text": payment_clause_text,
        "acceptance_text": acceptance_text,
        "cancellation_payment_text": cancellation_payment_text,
    }


def invoice_payment_label(enquiry, invoice=None):
    if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT:
        return "Pay in full"
    if (
        enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE
        and invoice and invoice.invoice_type == "deposit"
    ):
        return "Pay deposit"
    return "Open Invoice"
