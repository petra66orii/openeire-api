from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

import stripe
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from realestate.finance import ensure_standard_realestate_invoices, record_realestate_payment
from realestate.models import RealEstateEnquiry, RealEstatePayment


class Command(BaseCommand):
    help = "Report ambiguous legacy deposits and optionally reconcile them against Stripe."

    def add_arguments(self, parser):
        parser.add_argument("--verify-stripe", action="store_true")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        if options["apply"] and not options["verify_stripe"]:
            raise CommandError("--apply requires --verify-stripe")
        ambiguous = RealEstateEnquiry.objects.filter(deposit_paid=True).filter(
            deposit_paid_at__isnull=True
        )
        if not ambiguous.exists():
            self.stdout.write("No ambiguous legacy real-estate deposits found.")
            return
        stripe.api_key = settings.STRIPE_SECRET_KEY
        for enquiry in ambiguous.iterator():
            self.stdout.write(
                f"RE-{enquiry.pk}: MANUAL REVIEW required; paid flag has no paid timestamp; "
                f"session={enquiry.stripe_deposit_session_id or 'missing'}"
            )
            if not options["verify_stripe"] or not enquiry.stripe_deposit_session_id:
                continue
            session = stripe.checkout.Session.retrieve(enquiry.stripe_deposit_session_id)
            expected = int(Decimal(enquiry.quoted_deposit_amount) * 100)
            if session.get("payment_status") != "paid" or session.get("currency") != "eur" or int(session.get("amount_total") or 0) != expected:
                self.stdout.write(self.style.WARNING(f"RE-{enquiry.pk}: Stripe state did not validate."))
                continue
            self.stdout.write(self.style.SUCCESS(f"RE-{enquiry.pk}: Stripe confirms paid session."))
            if not options["apply"]:
                continue
            intent_id = str(session.get("payment_intent") or "")
            if not intent_id:
                self.stdout.write(self.style.ERROR(f"RE-{enquiry.pk}: missing PaymentIntent; not applied."))
                continue
            intent = stripe.PaymentIntent.retrieve(intent_id)
            paid_at = datetime.fromtimestamp(int(intent.get("created")), tz=dt_timezone.utc)
            invoice, _ = ensure_standard_realestate_invoices(enquiry)
            record_realestate_payment(
                invoice=invoice,
                amount=enquiry.quoted_deposit_amount,
                method=RealEstatePayment.Method.STRIPE_DEPOSIT_CHECKOUT,
                paid_at=paid_at,
                stripe_checkout_session_id=enquiry.stripe_deposit_session_id,
                stripe_payment_intent_id=intent_id,
                notes="Reconciled explicitly against Stripe by management command.",
            )
            self.stdout.write(self.style.SUCCESS(f"RE-{enquiry.pk}: payment ledger record created."))
