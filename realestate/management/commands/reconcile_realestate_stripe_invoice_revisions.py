import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from realestate.models import RealEstateInvoice, RealEstatePayment
from realestate.stripe_invoice_revisions import (
    StripeInvoiceRevisionError,
    apply_revision_snapshot,
    inspect_stored_invoice_revision,
    stripe_reference_id,
    stripe_value,
    was_manually_voided,
)


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Safely inspect or apply Stripe Invoice revisions to a real-estate invoice."

    def add_arguments(self, parser):
        parser.add_argument("--invoice-number", required=True)
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        invoice_number = str(options["invoice_number"] or "").strip()
        try:
            invoice = (
                RealEstateInvoice.objects.select_for_update()
                .select_related("enquiry")
                .get(invoice_number=invoice_number)
            )
        except RealEstateInvoice.DoesNotExist as exc:
            raise CommandError(f"Local invoice {invoice_number} was not found.") from exc

        stored_id = str(invoice.stripe_invoice_id or "").strip()
        stored_status = str(invoice.stripe_invoice_status or "").strip() or "unknown"
        try:
            chain = inspect_stored_invoice_revision(invoice)
        except StripeInvoiceRevisionError as exc:
            raise CommandError(str(exc)) from exc
        current = chain[-1]
        current_id = stripe_reference_id(current)
        current_status = str(stripe_value(current, "status", "") or "").strip() or "unknown"

        self.stdout.write(f"local_invoice_status={invoice.status}")
        self.stdout.write(f"stored_stripe_invoice_status={stored_status}")
        self.stdout.write(f"stored_stripe_invoice_id={stored_id or 'missing'}")
        self.stdout.write(f"current_stripe_invoice_id={current_id}")
        self.stdout.write(f"current_stripe_invoice_status={current_status}")
        self.stdout.write("revision_chain=" + " -> ".join(stripe_reference_id(item) for item in chain))

        successful_total = sum(
            invoice.payments.filter(status=RealEstatePayment.Status.SUCCEEDED)
            .values_list("amount", flat=True),
            start=invoice.total * 0,
        )
        if current_status == "paid" and successful_total != invoice.total:
            raise CommandError(
                "Stripe reports the current revision paid but the local successful "
                "payment total does not match; reconcile payment separately."
            )
        if invoice.status == RealEstateInvoice.Status.PAID and current_status != "paid":
            raise CommandError(
                "The local invoice is paid but the current Stripe revision is not paid."
            )
        if invoice.status == RealEstateInvoice.Status.VOID and was_manually_voided(invoice):
            raise CommandError("A manually voided local invoice will not be reopened.")
        if current_status == "open" and successful_total:
            raise CommandError(
                "The current Stripe revision is open but local successful payments exist."
            )

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("DRY RUN: no local records were changed."))
            return

        changed, revision_changed, restored = apply_revision_snapshot(invoice, current)
        logger.info(
            "Applied real estate Stripe invoice revision reconciliation. "
            "invoice_id=%s enquiry_id=%s old_stripe_invoice_id=%s "
            "new_stripe_invoice_id=%s changed=%s restored=%s",
            invoice.pk,
            invoice.enquiry_id,
            stored_id,
            current_id,
            changed,
            restored,
        )
        if revision_changed:
            self.stdout.write(self.style.SUCCESS("Applied the verified Stripe invoice revision."))
        elif changed:
            self.stdout.write(self.style.SUCCESS("Refreshed the current Stripe invoice fields."))
        else:
            self.stdout.write("No changes were required; the local invoice is already current.")
