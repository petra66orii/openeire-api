import logging

from django.contrib import admin
from django.contrib import messages
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.template.response import TemplateResponse
from django.utils import timezone
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.urls import path, reverse
from decimal import Decimal

from openeire_api.admin import custom_admin_site
from openeire_api.business_identity import get_business_identity

from .emails import build_realestate_email_context
from .emails import get_realestate_reply_to_email
from .emails import send_templated_email
from .documents import build_booking_agreement_filename
from .documents import generate_booking_agreement_pdf
from .models import RealEstateEnquiry
from .models import RealEstateTimelineEvent
from .models import RealEstateDeliveryOverride, RealEstateInvoice, RealEstatePayment
from .finance import can_release_realestate_delivery, create_realestate_balance_checkout_session, ensure_invoices_for_arrangement, record_realestate_payment, revoke_delivery_override
from .stripe_invoices import create_stripe_invoice, mark_stripe_invoice_paid_out_of_band, send_stripe_invoice
from .payments import calculate_realestate_deposit_amounts
from .payments import prepare_realestate_deposit_checkout_session
from .timeline import record_timeline_event
from .financial_documents import (
    build_invoice_filename, build_receipt_filename,
    generate_invoice_pdf, generate_cash_receipt_pdf,
)


logger = logging.getLogger(__name__)


class RealEstateTimelineEventInline(admin.TabularInline):
    model = RealEstateTimelineEvent
    extra = 0
    can_delete = False
    readonly_fields = (
        "created_at",
        "event_type",
        "status",
        "actor_type",
        "title",
        "email_template",
        "recipient_email",
        "reference_url",
        "stripe_session_id",
        "created_by",
        "notes",
    )
    fields = readonly_fields
    ordering = ("-created_at",)
    show_change_link = True


class RealEstateInvoiceInline(admin.TabularInline):
    model = RealEstateInvoice
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "invoice_link",
        "invoice_type",
        "status",
        "total",
        "amount_paid_inline",
        "amount_outstanding_inline",
        "stripe_status_inline",
        "stripe_links_inline",
    )
    readonly_fields = fields

    @admin.display(description="Invoice")
    def invoice_link(self, obj):
        if not obj or not obj.pk:
            return "—"
        url = reverse("customadmin:realestate_realestateinvoice_change", args=(obj.pk,))
        return format_html('<a href="{}">{}</a>', url, obj.invoice_number)

    @admin.display(description="Paid")
    def amount_paid_inline(self, obj):
        return obj.amount_paid if obj else Decimal("0.00")

    @admin.display(description="Outstanding")
    def amount_outstanding_inline(self, obj):
        return obj.amount_outstanding if obj else Decimal("0.00")

    @admin.display(description="Stripe")
    def stripe_status_inline(self, obj):
        return obj.stripe_invoice_status or ("Checkout" if obj.stripe_checkout_url else "Not created")

    @admin.display(description="Stripe links")
    def stripe_links_inline(self, obj):
        links = []
        if obj.stripe_hosted_invoice_url:
            links.append(format_html('<a href="{}" target="_blank" rel="noopener noreferrer">Hosted</a>', obj.stripe_hosted_invoice_url))
        if obj.stripe_invoice_pdf_url:
            links.append(format_html('<a href="{}" target="_blank" rel="noopener noreferrer">PDF</a>', obj.stripe_invoice_pdf_url))
        if obj.stripe_checkout_url:
            links.append(format_html('<a href="{}" target="_blank" rel="noopener noreferrer">Checkout</a>', obj.stripe_checkout_url))
        return mark_safe(" · ".join(str(link) for link in links)) if links else "—"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class RealEstateDeliveryOverrideInline(admin.TabularInline):
    model = RealEstateDeliveryOverride
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "state_inline",
        "reason",
        "created_by",
        "created_at",
        "revoked_by",
        "revoked_at",
        "revocation_reason",
    )
    readonly_fields = fields

    @admin.display(description="State")
    def state_inline(self, obj):
        return "Active" if obj and obj.is_active else "Revoked"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(RealEstateEnquiry, site=custom_admin_site)
class RealEstateEnquiryAdmin(admin.ModelAdmin):
    change_form_template = "admin/realestate/enquiry/change_form.html"
    EMAIL_TIMELINE_EVENTS = {
        "quote": (
            RealEstateTimelineEvent.EventType.QUOTE_SENT,
            "Quote email sent",
            "",
        ),
        "booking_agreement": (
            RealEstateTimelineEvent.EventType.BOOKING_AGREEMENT_SENT,
            "Booking agreement email sent",
            "booking_agreement_link",
        ),
        "deposit_request": (
            RealEstateTimelineEvent.EventType.DEPOSIT_REQUEST_SENT,
            "Deposit request email sent",
            "deposit_payment_link",
        ),
        "confirmation": (
            RealEstateTimelineEvent.EventType.CONFIRMATION_SENT,
            "Confirmation email sent",
            "",
        ),
        "invoice_issued": (
            RealEstateTimelineEvent.EventType.INVOICE_ISSUED,
            "Invoice email sent",
            "",
        ),
        "payment_reminder": (
            RealEstateTimelineEvent.EventType.NOTE,
            "Payment reminder email sent",
            "",
        ),
        "cash_receipt": (
            RealEstateTimelineEvent.EventType.PAYMENT_RECORDED,
            "Cash receipt email sent",
            "",
        ),
        "payment_received": (
            RealEstateTimelineEvent.EventType.PAYMENT_RECORDED,
            "Payment received email sent",
            "",
        ),
        "overdue_payment": (
            RealEstateTimelineEvent.EventType.NOTE,
            "Overdue payment email sent",
            "",
        ),
        "weather_reschedule": (
            RealEstateTimelineEvent.EventType.WEATHER_RESCHEDULE_SENT,
            "Weather reschedule email sent",
            "",
        ),
        "delivery": (
            RealEstateTimelineEvent.EventType.DELIVERY_SENT,
            "Delivery email sent",
            "delivery_link",
        ),
        "follow_up": (
            RealEstateTimelineEvent.EventType.FOLLOW_UP_SENT,
            "Follow-up email sent",
            "review_link",
        ),
        "thank_you": (
            RealEstateTimelineEvent.EventType.THANK_YOU_SENT,
            "Thank-you email sent",
            "review_link",
        ),
    }

    booking_field_help_texts = {
        "booking_agreement_link": (
            "Optional future e-signature/external signing URL. The Booking "
            "Agreement PDF is attached automatically when sending the Booking "
            "Agreement email."
        ),
        "delivery_provider": (
            "Where the finished media package is hosted. Until the OpenEire "
            "Client Portal is available, MyAirBridge is the recommended provider."
        ),
        "delivery_link": (
            'Secure download URL used for the "Download Files" button in the '
            "Delivery email."
        ),
        "review_link": (
            "Review URL shown as the Follow-up/Thank-you email CTA, usually "
            "the Google review link."
        ),
    }
    list_display = (
        "created_at",
        "name",
        "company_name",
        "county",
        "preferred_package",
        "preferred_date",
        "status",
        "quoted_price",
        "shoot_date",
        "booking_agreement_received",
        "deposit_paid",
    )
    list_filter = (
        "status",
        "preferred_package",
        "county",
        "client_type",
        "how_heard",
        "created_at",
    )
    search_fields = (
        "name",
        "email",
        "phone",
        "company_name",
        "property_address",
        "eircode",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "stripe_deposit_session_id",
        "deposit_paid_at",
        "deposit_paid",
        "pricing_snapshot_version",
        "price_input_is_gross",
        "vat_registered_at_quote",
        "quoted_vat_rate",
        "quoted_subtotal",
        "quoted_vat_amount",
        "quoted_total",
        "quoted_deposit_amount",
        "quoted_balance_due",
        "financial_summary",
    )
    actions = (
        "send_quote_email",
        "send_booking_agreement_email",
        "send_deposit_request_email",
        "issue_arrangement_invoices",
        "create_balance_checkout",
        "send_confirmation_email",
        "send_delivery_email",
        "send_follow_up_email",
        "send_weather_reschedule_email",
        "send_thank_you_email",
    )
    inlines = (
        RealEstateInvoiceInline,
        RealEstateDeliveryOverrideInline,
        RealEstateTimelineEventInline,
    )
    fieldsets = (
        (
            "Contact",
            {
                "fields": (
                    "name",
                    "email",
                    "phone",
                    "company_name",
                    "client_type",
                    "how_heard",
                    "consent_to_contact",
                )
            },
        ),
        (
            "Property",
            {
                "fields": (
                    "property_address",
                    "county",
                    "eircode",
                    "property_type",
                )
            },
        ),
        (
            "Package & Add-ons",
            {
                "fields": (
                    "preferred_package",
                    "add_ons",
                    "preferred_date",
                    "message",
                )
            },
        ),
        (
            "Pipeline",
            {
                "fields": (
                    "status",
                    "quoted_price",
                    "shoot_date",
                )
            },
        ),
        (
            "Financial workflow",
            {
                "fields": (
                    "payment_arrangement", "payment_due_date", "expected_payment_method",
                    "custom_payment_terms", "custom_required_total", "financial_summary",
                ),
                "description": "Choose the arrangement before issuing invoices. It is locked afterwards.",
            },
        ),
        (
            "Booking & Delivery Links",
            {
                "fields": (
                    "proposed_shoot_date",
                    "booking_agreement_received",
                    "delivery_provider",
                    "delivery_link",
                    "review_link",
                    "booking_agreement_link",
                )
            },
        ),
        (
            "Compatibility payment fields",
            {
                "classes": ("collapse",),
                "fields": (
                    "deposit_payment_link",
                    "stripe_deposit_session_id",
                    "deposit_paid",
                    "deposit_paid_at",
                )
            },
        ),
        (
            "Pricing snapshot",
            {
                "classes": ("collapse",),
                "fields": (
                    "pricing_snapshot_version",
                    "price_input_is_gross",
                    "vat_registered_at_quote",
                    "quoted_vat_rate",
                    "quoted_subtotal",
                    "quoted_vat_amount",
                    "quoted_total",
                    "quoted_deposit_amount",
                    "quoted_balance_due",
                ),
                "description": (
                    "Immutable monetary values used for quote documents, deposits, "
                    "balances, and Stripe validation. Existing quotes retain their "
                    "original VAT treatment."
                ),
            },
        ),
        (
            "Notes",
            {
                "fields": ("internal_notes",)
            },
        ),
        (
            "Timestamps",
            {
                "classes": ("collapse",),
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def _isoformat_date(self, value):
        return value.isoformat() if value else ""

    @admin.display(description="Financial summary")
    def financial_summary(self, enquiry):
        if not enquiry or not enquiry.pk:
            return "Save the enquiry to create financial records."
        invoices = list(enquiry.invoices.all())
        total_paid = sum((invoice.amount_paid for invoice in invoices), Decimal("0"))
        required = (
            enquiry.custom_required_total
            if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM
            else enquiry.quoted_total
        ) or Decimal("0")
        outstanding = max(required - total_paid, Decimal("0"))
        rows = []
        for invoice in invoices:
            url = reverse("customadmin:realestate_realestateinvoice_change", args=(invoice.pk,))
            stripe_link = (
                f' <a href="{invoice.stripe_hosted_invoice_url}" target="_blank" rel="noopener noreferrer">Stripe invoice</a>'
                if invoice.stripe_hosted_invoice_url else ""
            )
            rows.append(
                f'<li><a href="{url}">{invoice.invoice_number}</a>: EUR {invoice.total}; '
                f'paid EUR {invoice.amount_paid}; outstanding EUR {invoice.amount_outstanding}; '
                f'Stripe {invoice.stripe_invoice_status or "not created"}{stripe_link}</li>'
            )
        guidance = {
            RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE: "Deposit and balance invoices; deposit is required before confirmation.",
            RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT: "One full invoice; payment is required before confirmation.",
            RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY: "One full invoice due on the shoot day; booking may be confirmed while unpaid.",
            RealEstateEnquiry.PaymentArrangement.CUSTOM: "Explicit custom terms and invoice amounts are required.",
        }.get(enquiry.payment_arrangement, "")
        return format_html(
            '<div style="padding:12px;border:1px solid #ccc"><strong>Total:</strong> EUR {} &nbsp; '
            '<strong>Paid:</strong> EUR {} &nbsp; <strong>Outstanding:</strong> EUR {}<br>'
            '<strong>Agreement:</strong> {} &nbsp; <strong>Booking:</strong> {} &nbsp; '
            '<strong>Delivery:</strong> {} &nbsp; <strong>Override:</strong> {}<br>'
            '<strong>Workflow:</strong> {}<ul>{}</ul></div>',
            required, total_paid, outstanding,
            "Received" if enquiry.booking_agreement_received else "Pending",
            enquiry.get_status_display(),
            "Ready" if can_release_realestate_delivery(enquiry) else "Locked",
            "Active" if enquiry.delivery_overrides.filter(revoked_at__isnull=True).exists() else "None",
            guidance, mark_safe("".join(rows)),
        )

    def _money(self, value):
        return f"€{Decimal(value or 0):.2f}"

    def _admin_change_url(self, model_name, obj):
        return reverse(f"customadmin:realestate_{model_name}_change", args=(obj.pk,))

    def _ops_url(self, enquiry, action, invoice=None, payment=None, override=None):
        url = reverse(
            "customadmin:realestate_realestateenquiry_ops_action",
            args=(enquiry.pk, action),
        )
        query = []
        if invoice:
            query.append(f"invoice={invoice.pk}")
        if payment:
            query.append(f"payment={payment.pk}")
        if override:
            query.append(f"override={override.pk}")
        return f"{url}?{'&'.join(query)}" if query else url

    def _nonvoid_invoices(self, enquiry):
        return list(
            enquiry.invoices.exclude(status=RealEstateInvoice.Status.VOID)
            .prefetch_related("payments")
            .order_by("created_at")
        )

    def _invoice_for_next_payment(self, invoices):
        priority = {
            RealEstateInvoice.InvoiceType.DEPOSIT: 1,
            RealEstateInvoice.InvoiceType.FULL: 2,
            RealEstateInvoice.InvoiceType.BALANCE: 3,
            RealEstateInvoice.InvoiceType.ADJUSTMENT: 4,
        }
        outstanding = [invoice for invoice in invoices if invoice.amount_outstanding > 0]
        return sorted(outstanding, key=lambda invoice: priority.get(invoice.invoice_type, 99))[0] if outstanding else None

    def _stripe_invoice_candidate(self, invoices):
        return self._invoice_for_next_payment(invoices) or next(
            (
                invoice
                for invoice in invoices
                if invoice.stripe_invoice_id and not invoice.stripe_marked_paid_out_of_band_at
            ),
            None,
        ) or (invoices[0] if invoices else None)

    def _delivery_lock_reason(self, enquiry, invoices, ready):
        if ready:
            return "Payment complete or active override"
        if not invoices:
            return "required invoice has not been issued"
        unpaid = [invoice.invoice_number for invoice in invoices if invoice.amount_outstanding > 0]
        if unpaid:
            return "full payment required"
        return "required final invoice missing"

    def _recommended_next_step(self, enquiry, invoices, payment_invoice, ready, active_override):
        if not invoices:
            if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
                return "Send deposit invoice", "send-deposit-request"
            return "Issue invoice", "issue-invoices"
        if payment_invoice:
            if (
                enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY
                and enquiry.expected_payment_method == RealEstateEnquiry.ExpectedPaymentMethod.CASH
            ):
                return "Record full cash payment", "record-cash-payment"
            if enquiry.expected_payment_method == RealEstateEnquiry.ExpectedPaymentMethod.BANK_TRANSFER:
                return "Record bank transfer", "record-bank-payment"
            return "Open/send Stripe invoice", "create-send-stripe-invoice"
        local_paid_stripe_open = next(
            (
                invoice for invoice in invoices
                if invoice.amount_outstanding == 0
                and invoice.stripe_invoice_id
                and not invoice.stripe_marked_paid_out_of_band_at
                and invoice.stripe_invoice_status not in {"paid", "void"}
            ),
            None,
        )
        if local_paid_stripe_open:
            return "Mark Stripe invoice paid out of band", "mark-stripe-paid-out-of-band"
        if ready and not enquiry.delivery_link:
            return "Add delivery link", "edit-delivery-link"
        if ready and enquiry.delivery_link:
            return "Send delivery email", "send-delivery-email"
        if active_override:
            return "Review delivery override", "view-timeline"
        return "Review financial records", "view-invoices"

    def _build_operations_hub(self, request, enquiry):
        if not enquiry or not enquiry.pk:
            return None

        invoices = self._nonvoid_invoices(enquiry)
        payments = list(
            RealEstatePayment.objects.filter(invoice__enquiry=enquiry)
            .select_related("invoice", "recorded_by")
            .order_by("-paid_at", "-created_at")
        )
        active_override = enquiry.delivery_overrides.filter(revoked_at__isnull=True).first()
        ready = can_release_realestate_delivery(enquiry)
        total_paid = sum((invoice.amount_paid for invoice in invoices), Decimal("0.00"))
        required_total = (
            enquiry.custom_required_total
            if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM
            else (enquiry.quoted_total or enquiry.quoted_price)
        ) or Decimal("0.00")
        outstanding = max(required_total - total_paid, Decimal("0.00"))
        deposit_invoice = next((invoice for invoice in invoices if invoice.invoice_type == RealEstateInvoice.InvoiceType.DEPOSIT), None)
        balance_invoice = next((invoice for invoice in invoices if invoice.invoice_type == RealEstateInvoice.InvoiceType.BALANCE), None)
        payment_invoice = self._invoice_for_next_payment(invoices)
        stripe_invoice = self._stripe_invoice_candidate(invoices)
        latest_cash_receipt = next((payment for payment in payments if payment.cash_receipt_number), None)
        recommended_label, recommended_action = self._recommended_next_step(
            enquiry, invoices, payment_invoice, ready, active_override
        )

        can_change = self.has_change_permission(request, enquiry)
        can_view_invoice = request.user.has_perm("realestate.view_realestateinvoice")
        can_view_payment = request.user.has_perm("realestate.view_realestatepayment")
        can_view_timeline = request.user.has_perm("realestate.view_realestatetimelineevent")

        buttons = []

        def add_button(label, action=None, *, href=None, style="secondary", enabled=True, method="GET", invoice=None, payment=None, override=None):
            if not enabled:
                return
            buttons.append({
                "label": label,
                "href": href or self._ops_url(enquiry, action, invoice=invoice, payment=payment, override=override),
                "style": style,
                "method": method,
            })

        add_button(
            "Issue invoice",
            "issue-invoices",
            style="primary" if recommended_action == "issue-invoices" else "secondary",
            enabled=can_change and not invoices,
        )
        add_button(
            "Send deposit invoice",
            "send-deposit-request",
            style="primary" if recommended_action == "send-deposit-request" else "secondary",
            enabled=can_change and enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE and not enquiry.deposit_paid,
        )
        add_button(
            "Create/send Stripe invoice",
            "create-send-stripe-invoice",
            invoice=stripe_invoice,
            style="primary" if recommended_action == "create-send-stripe-invoice" else "secondary",
            enabled=can_change and bool(stripe_invoice) and stripe_invoice.amount_outstanding > 0,
        )
        add_button(
            "Open Stripe hosted invoice",
            href=stripe_invoice.stripe_hosted_invoice_url if stripe_invoice else "",
            enabled=bool(stripe_invoice and stripe_invoice.stripe_hosted_invoice_url),
        )
        add_button(
            "Download Stripe PDF",
            href=stripe_invoice.stripe_invoice_pdf_url if stripe_invoice else "",
            enabled=bool(stripe_invoice and stripe_invoice.stripe_invoice_pdf_url),
        )
        add_button("Download local invoice PDF", "download-local-invoice", invoice=stripe_invoice, enabled=bool(stripe_invoice))
        add_button(
            "Record cash payment",
            "record-cash-payment",
            invoice=payment_invoice,
            style="primary" if recommended_action == "record-cash-payment" else "secondary",
            enabled=can_change and bool(payment_invoice),
        )
        add_button(
            "Record bank transfer",
            "record-bank-payment",
            invoice=payment_invoice,
            style="primary" if recommended_action == "record-bank-payment" else "secondary",
            enabled=can_change and bool(payment_invoice),
        )
        add_button("Download cash receipt", "download-cash-receipt", payment=latest_cash_receipt, enabled=bool(latest_cash_receipt))
        add_button("Email cash receipt", "send-cash-receipt-email", payment=latest_cash_receipt, enabled=can_change and bool(latest_cash_receipt))
        add_button("Email invoice issued", "send-invoice-issued-email", invoice=stripe_invoice, enabled=can_change and bool(stripe_invoice))
        add_button("Email payment reminder", "send-payment-reminder-email", invoice=stripe_invoice, enabled=can_change and bool(stripe_invoice and stripe_invoice.amount_outstanding > 0))
        add_button("Email payment received", "send-payment-received-email", invoice=stripe_invoice, enabled=can_change and bool(stripe_invoice and stripe_invoice.amount_outstanding == 0))
        add_button("Email overdue payment", "send-overdue-payment-email", invoice=stripe_invoice, enabled=can_change and bool(stripe_invoice and stripe_invoice.amount_outstanding > 0))
        add_button(
            "Mark Stripe invoice paid out of band",
            "mark-stripe-paid-out-of-band",
            invoice=stripe_invoice,
            style="primary" if recommended_action == "mark-stripe-paid-out-of-band" else "secondary",
            enabled=can_change and bool(stripe_invoice and stripe_invoice.stripe_invoice_id and stripe_invoice.amount_outstanding == 0 and not stripe_invoice.stripe_marked_paid_out_of_band_at),
        )
        add_button("Send payment reminder", "send-stripe-reminder", invoice=stripe_invoice, enabled=can_change and bool(stripe_invoice and stripe_invoice.stripe_invoice_id and stripe_invoice.amount_outstanding > 0))
        add_button("Add/edit delivery link", href="#id_delivery_link", style="primary" if recommended_action == "edit-delivery-link" else "secondary", enabled=can_change)
        add_button("Send confirmation email", "send-confirmation-email", enabled=can_change)
        add_button("Send delivery email", "send-delivery-email", style="primary" if recommended_action == "send-delivery-email" else "secondary", enabled=can_change and ready and bool(enquiry.delivery_link))
        add_button("Grant delivery override", "grant-delivery-override", style="danger", enabled=can_change and not ready and not active_override)
        add_button("Revoke delivery override", "revoke-delivery-override", override=active_override, style="danger", enabled=can_change and bool(active_override))
        add_button("View all related invoices", href=reverse("customadmin:realestate_realestateinvoice_changelist") + f"?enquiry__id__exact={enquiry.pk}", enabled=can_view_invoice)
        add_button("View all related payments", href=reverse("customadmin:realestate_realestatepayment_changelist") + f"?invoice__enquiry__id__exact={enquiry.pk}", enabled=can_view_payment)
        add_button("View timeline", href=reverse("customadmin:realestate_realestatetimelineevent_changelist") + f"?enquiry__id__exact={enquiry.pk}", enabled=can_view_timeline)

        invoice_rows = []
        for invoice in invoices:
            invoice_rows.append({
                "number": invoice.invoice_number,
                "url": self._admin_change_url("realestateinvoice", invoice),
                "type": invoice.get_invoice_type_display(),
                "status": invoice.get_status_display(),
                "total": self._money(invoice.total),
                "paid": self._money(invoice.amount_paid),
                "outstanding": self._money(invoice.amount_outstanding),
                "stripe_status": invoice.stripe_invoice_status or ("Checkout" if invoice.stripe_checkout_url else "Not created"),
                "hosted_url": invoice.stripe_hosted_invoice_url,
                "pdf_url": invoice.stripe_invoice_pdf_url,
            })

        payment_rows = []
        for payment in payments:
            payment_rows.append({
                "invoice_number": payment.invoice.invoice_number,
                "invoice_url": self._admin_change_url("realestateinvoice", payment.invoice),
                "method": payment.get_method_display(),
                "status": payment.get_status_display(),
                "amount": self._money(payment.amount),
                "paid_at": payment.paid_at,
                "receipt_number": payment.cash_receipt_number or "—",
                "reference": payment.external_reference or payment.bank_lodgement_reference or "—",
            })

        override_rows = []
        for override in enquiry.delivery_overrides.select_related("created_by", "revoked_by").order_by("-created_at"):
            override_rows.append({
                "state": "Active" if override.is_active else "Revoked",
                "reason": override.reason,
                "created_by": override.created_by,
                "created_at": override.created_at,
                "revoked_at": override.revoked_at,
            })

        return {
            "booking": {
                "status": enquiry.get_status_display(),
                "shoot": enquiry.shoot_date or enquiry.preferred_date or "To be confirmed",
                "package": enquiry.get_preferred_package_display(),
                "arrangement": enquiry.get_payment_arrangement_display(),
                "expected_method": enquiry.get_expected_payment_method_display() or "Not set",
                "agreement": "Received" if enquiry.booking_agreement_received else "Pending",
            },
            "financial": {
                "total": self._money(required_total),
                "deposit_required": self._money(deposit_invoice.total if deposit_invoice else getattr(enquiry, "quoted_deposit_amount", None)) if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE else "Not applicable",
                "deposit_paid": "Yes" if enquiry.deposit_paid else "No",
                "balance_due": self._money(balance_invoice.amount_outstanding if balance_invoice else getattr(enquiry, "quoted_balance_due", None)) if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE else "Not applicable",
                "paid": self._money(total_paid),
                "outstanding": self._money(outstanding),
                "invoice_numbers": ", ".join(invoice.invoice_number for invoice in invoices) or "None",
                "stripe_status": stripe_invoice.stripe_invoice_status if stripe_invoice and stripe_invoice.stripe_invoice_status else ("Open" if stripe_invoice and stripe_invoice.stripe_invoice_id else "Not created"),
                "payment_status": "Paid" if outstanding == 0 and invoices else ("Part paid" if total_paid else "Unpaid"),
            },
            "delivery": {
                "state": "Ready" if ready and not enquiry.delivery_link else ("Released" if ready and enquiry.delivery_link else "Locked"),
                "reason": self._delivery_lock_reason(enquiry, invoices, ready),
                "provider": enquiry.get_delivery_provider_display(),
                "link_status": "Stored" if enquiry.delivery_link else "Missing",
                "override": "Active" if active_override else "None",
            },
            "recommended": {"label": recommended_label, "action": recommended_action},
            "buttons": buttons,
            "invoices": invoice_rows,
            "payments": payment_rows,
            "overrides": override_rows,
        }

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/ops/<slug:action>/",
                self.admin_site.admin_view(self.operations_action_view),
                name="realestate_realestateenquiry_ops_action",
            ),
        ]
        return custom_urls + urls

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        if object_id:
            enquiry = self.get_object(request, object_id)
            if enquiry:
                extra_context["operations_hub"] = self._build_operations_hub(request, enquiry)
        return super().changeform_view(request, object_id, form_url, extra_context)

    def _require_change_permission(self, request, enquiry):
        if not self.has_change_permission(request, enquiry):
            raise PermissionDenied("You do not have permission to change this enquiry.")

    def _get_ops_invoice(self, enquiry, request):
        invoice_id = request.GET.get("invoice") or request.POST.get("invoice")
        if invoice_id:
            return enquiry.invoices.exclude(
                status=RealEstateInvoice.Status.VOID,
            ).get(pk=invoice_id)
        invoices = self._nonvoid_invoices(enquiry)
        return self._stripe_invoice_candidate(invoices)

    def _confirm_action(
        self, request, enquiry, *, action, title, message, invoice=None,
        danger=False, template_base="", recipient="", amount="", due_date="",
    ):
        return TemplateResponse(request, "admin/realestate/confirm_action.html", {
            **self.admin_site.each_context(request),
            "title": title,
            "message": message,
            "enquiry": enquiry,
            "action": action,
            "invoice": invoice,
            "danger": danger,
            "template_base": template_base,
            "arrangement": enquiry.get_payment_arrangement_display(),
            "recipient": recipient,
            "amount": amount,
            "due_date": due_date,
        })

    def _redirect_to_enquiry(self, enquiry):
        return HttpResponseRedirect(
            reverse("customadmin:realestate_realestateenquiry_change", args=(enquiry.pk,))
        )

    def _record_ops_payment(self, request, enquiry, *, method):
        self._require_change_permission(request, enquiry)
        invoice = self._get_ops_invoice(enquiry, request)
        if not invoice or invoice.amount_outstanding <= 0:
            raise ValidationError("No outstanding invoice is available for payment.")
        initial = {
            "amount": invoice.amount_outstanding,
            "method": method,
            "received_at": timezone.now(),
        }
        form = ManualPaymentForm(
            request.POST if request.method == "POST" else None,
            initial=initial,
        )
        if request.method == "POST" and form.is_valid():
            payment, _ = record_realestate_payment(
                invoice=invoice,
                amount=form.cleaned_data["amount"],
                method=method,
                paid_at=form.cleaned_data["received_at"],
                recorded_by=request.user,
                external_reference=form.cleaned_data["payer_reference"],
                bank_lodgement_reference=form.cleaned_data["bank_lodgement_reference"],
                notes=form.cleaned_data["notes"],
            )
            self.message_user(request, f"Payment recorded for {invoice.invoice_number}.", level=messages.SUCCESS)
            return self._redirect_to_enquiry(enquiry)
        return TemplateResponse(request, "admin/realestate/manual_payment.html", {
            **self.admin_site.each_context(request),
            "form": form,
            "invoice": invoice,
            "title": f"Record {RealEstatePayment.Method(method).label.lower()} for {invoice.invoice_number}",
            "return_url": reverse("customadmin:realestate_realestateenquiry_change", args=(enquiry.pk,)),
        })

    def _confirmation_blocker(self, enquiry):
        if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT:
            full_invoice = enquiry.invoices.filter(
                invoice_type=RealEstateInvoice.InvoiceType.FULL,
                status=RealEstateInvoice.Status.PAID,
            ).first()
            if not full_invoice:
                return "Full-upfront bookings cannot be confirmed until the full invoice is paid."
        if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
            if not enquiry.deposit_paid:
                return "Deposit bookings cannot be confirmed until the deposit is paid."
        return ""

    def _send_single_realestate_email(
        self, request, enquiry, *, template_base, subject, description, invoice=None,
        payment=None,
    ):
        email = str(getattr(enquiry, "email", "") or "").strip()
        if not email:
            raise ValidationError("No client email is available.")
        context = self._build_base_context(enquiry)
        context.update(build_realestate_email_context(enquiry))
        if invoice:
            context.update({
                "invoice_number": invoice.invoice_number,
                "invoice_type": invoice.get_invoice_type_display(),
                "stripe_hosted_invoice_url": invoice.stripe_hosted_invoice_url,
                "outstanding_amount": invoice.amount_outstanding,
            })
        if payment:
            context.update({
                "cash_receipt_number": payment.cash_receipt_number,
                "payment_amount": payment.amount,
                "payment_received_at": payment.paid_at,
            })
        if invoice or payment:
            context = build_realestate_email_context(enquiry, **context)
        try:
            send_templated_email(
                subject=subject,
                to=[email],
                template_base=template_base,
                context=context,
                reply_to=self._get_reply_to(),
            )
        except Exception as exc:
            self._record_email_timeline_event(
                enquiry,
                request,
                template_base=template_base,
                email=email,
                context=context,
                status=RealEstateTimelineEvent.EventStatus.FAILED,
                notes=f"{exc.__class__.__name__}: {exc}",
            )
            raise
        self._record_email_timeline_event(
            enquiry,
            request,
            template_base=template_base,
            email=email,
            context=context,
            status=RealEstateTimelineEvent.EventStatus.SENT,
        )
        self.message_user(request, f"{description} sent.", level=messages.SUCCESS)

    def _confirm_email_action(self, request, enquiry, *, action, title, template_base, invoice=None):
        context = build_realestate_email_context(enquiry)
        amount = context.get("outstanding_amount") or context.get("total_required")
        due_date = context.get("payment_due_date")
        if invoice:
            amount = self._money(invoice.amount_outstanding)
            if invoice.due_at:
                due_date = self._date_key(invoice.due_at.date())
        return self._confirm_action(
            request,
            enquiry,
            action=action,
            invoice=invoice,
            title=title,
            message="Confirm before sending this customer email.",
            template_base=template_base,
            recipient=enquiry.email,
            amount=amount,
            due_date=due_date,
        )

    def operations_action_view(self, request, object_id, action):
        enquiry = self.get_object(request, object_id)
        if not enquiry:
            raise RealEstateEnquiry.DoesNotExist()

        try:
            self._require_change_permission(request, enquiry)

            if action == "download-local-invoice":
                invoice = self._get_ops_invoice(enquiry, request)
                if not invoice:
                    raise ValidationError("No invoice is available.")
                response = HttpResponse(generate_invoice_pdf(invoice), content_type="application/pdf")
                response["Content-Disposition"] = f'attachment; filename="{build_invoice_filename(invoice)}"'
                return response

            if action == "download-cash-receipt":
                payment_id = request.GET.get("payment") or request.POST.get("payment")
                payment = RealEstatePayment.objects.select_related("invoice").get(
                    pk=payment_id,
                    invoice__enquiry=enquiry,
                )
                if not payment.cash_receipt_number:
                    raise ValidationError("The selected payment has no cash receipt.")
                response = HttpResponse(generate_cash_receipt_pdf(payment), content_type="application/pdf")
                response["Content-Disposition"] = f'attachment; filename="{build_receipt_filename(payment)}"'
                return response

            if action == "record-cash-payment":
                return self._record_ops_payment(request, enquiry, method=RealEstatePayment.Method.CASH)

            if action == "record-bank-payment":
                return self._record_ops_payment(request, enquiry, method=RealEstatePayment.Method.BANK_TRANSFER)

            if action == "issue-invoices":
                if request.method != "POST":
                    return self._confirm_action(
                        request, enquiry, action=action,
                        title="Issue invoice(s)",
                        message="This creates issued local invoice records for the selected payment arrangement.",
                    )
                created_or_existing = ensure_invoices_for_arrangement(enquiry)
                self.message_user(request, f"Prepared {len(created_or_existing)} invoice(s).", level=messages.SUCCESS)
                return self._redirect_to_enquiry(enquiry)

            if action == "send-deposit-request":
                if enquiry.payment_arrangement != RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
                    raise ValidationError("Deposit actions are unavailable for this payment arrangement.")
                if request.method != "POST":
                    return self._confirm_action(
                        request, enquiry, action=action,
                        title="Send deposit invoice",
                        message="This creates/reuses the deposit Checkout link and sends the deposit request email.",
                    )
                self.send_deposit_request_email(request, RealEstateEnquiry.objects.filter(pk=enquiry.pk))
                return self._redirect_to_enquiry(enquiry)

            invoice = self._get_ops_invoice(enquiry, request)

            if action == "create-send-stripe-invoice":
                if not invoice:
                    raise ValidationError("Issue a local invoice first.")
                if request.method != "POST":
                    return self._confirm_action(
                        request, enquiry, action=action, invoice=invoice,
                        title="Create/send Stripe invoice",
                        message=f"This creates or reuses the Stripe invoice for {invoice.invoice_number} and sends it to the client.",
                    )
                create_stripe_invoice(invoice, send=True)
                self.message_user(request, f"Stripe invoice prepared for {invoice.invoice_number}.", level=messages.SUCCESS)
                return self._redirect_to_enquiry(enquiry)

            if action == "send-invoice-issued-email":
                if not invoice:
                    raise ValidationError("No invoice is available.")
                if request.method != "POST":
                    return self._confirm_email_action(
                        request, enquiry, action=action, invoice=invoice,
                        title="Send invoice issued email", template_base="invoice_issued",
                    )
                self._send_single_realestate_email(
                    request, enquiry, template_base="invoice_issued",
                    subject=f"Property media invoice - {get_business_identity().display_name}",
                    description="Invoice email", invoice=invoice,
                )
                return self._redirect_to_enquiry(enquiry)

            if action == "send-payment-reminder-email":
                if not invoice or invoice.amount_outstanding <= 0:
                    raise ValidationError("No outstanding invoice is available.")
                if request.method != "POST":
                    return self._confirm_email_action(
                        request, enquiry, action=action, invoice=invoice,
                        title="Send payment reminder email", template_base="payment_reminder",
                    )
                self._send_single_realestate_email(
                    request, enquiry, template_base="payment_reminder",
                    subject=f"Payment reminder - {get_business_identity().display_name}",
                    description="Payment reminder email", invoice=invoice,
                )
                return self._redirect_to_enquiry(enquiry)

            if action == "send-overdue-payment-email":
                if not invoice or invoice.amount_outstanding <= 0:
                    raise ValidationError("No outstanding invoice is available.")
                if request.method != "POST":
                    return self._confirm_email_action(
                        request, enquiry, action=action, invoice=invoice,
                        title="Send overdue payment email", template_base="overdue_payment",
                    )
                self._send_single_realestate_email(
                    request, enquiry, template_base="overdue_payment",
                    subject=f"Overdue payment after shoot - {get_business_identity().display_name}",
                    description="Overdue payment email", invoice=invoice,
                )
                return self._redirect_to_enquiry(enquiry)

            if action == "send-payment-received-email":
                if not invoice or invoice.amount_outstanding != 0:
                    raise ValidationError("Payment received emails require a fully paid invoice.")
                if request.method != "POST":
                    return self._confirm_email_action(
                        request, enquiry, action=action, invoice=invoice,
                        title="Send payment received email", template_base="payment_received",
                    )
                self._send_single_realestate_email(
                    request, enquiry, template_base="payment_received",
                    subject=f"Payment received - {get_business_identity().display_name}",
                    description="Payment received email", invoice=invoice,
                )
                return self._redirect_to_enquiry(enquiry)

            if action == "send-cash-receipt-email":
                payment_id = request.GET.get("payment") or request.POST.get("payment")
                payment = RealEstatePayment.objects.select_related("invoice", "invoice__enquiry").get(
                    pk=payment_id,
                    invoice__enquiry=enquiry,
                    cash_receipt_number__gt="",
                )
                if request.method != "POST":
                    return self._confirm_email_action(
                        request, enquiry, action=action, invoice=payment.invoice,
                        title="Send cash receipt email", template_base="cash_receipt",
                    )
                self._send_single_realestate_email(
                    request, enquiry, template_base="cash_receipt",
                    subject=f"Cash payment receipt - {get_business_identity().display_name}",
                    description="Cash receipt email", invoice=payment.invoice,
                    payment=payment,
                )
                return self._redirect_to_enquiry(enquiry)

            if action == "send-stripe-reminder":
                if not invoice or not invoice.stripe_invoice_id:
                    raise ValidationError("No Stripe invoice is available for a reminder.")
                if request.method != "POST":
                    return self._confirm_action(
                        request, enquiry, action=action, invoice=invoice,
                        title="Send Stripe invoice reminder",
                        message=f"This sends a Stripe invoice reminder for {invoice.invoice_number}.",
                    )
                send_stripe_invoice(invoice)
                self.message_user(request, f"Stripe reminder sent for {invoice.invoice_number}.", level=messages.SUCCESS)
                return self._redirect_to_enquiry(enquiry)

            if action == "mark-stripe-paid-out-of-band":
                if not invoice:
                    raise ValidationError("No invoice is available.")
                if request.method != "POST":
                    return self._confirm_action(
                        request, enquiry, action=action, invoice=invoice, danger=True,
                        title="Mark Stripe invoice paid out of band",
                        message="This tells Stripe that the invoice was settled outside Stripe. No Stripe charge is created.",
                    )
                mark_stripe_invoice_paid_out_of_band(invoice, user=request.user)
                self.message_user(request, f"Stripe invoice marked paid out of band for {invoice.invoice_number}.", level=messages.SUCCESS)
                return self._redirect_to_enquiry(enquiry)

            if action == "send-confirmation-email":
                blocker = self._confirmation_blocker(enquiry)
                if blocker:
                    raise ValidationError(blocker)
                if request.method != "POST":
                    return self._confirm_email_action(
                        request, enquiry, action=action,
                        title="Send confirmation email", template_base="confirmation",
                    )
                self.send_confirmation_email(request, RealEstateEnquiry.objects.filter(pk=enquiry.pk))
                return self._redirect_to_enquiry(enquiry)

            if action == "send-delivery-email":
                if not can_release_realestate_delivery(enquiry):
                    raise ValidationError("Delivery is locked until payment is complete or an override is active.")
                if not enquiry.delivery_link:
                    raise ValidationError("Add a delivery link before sending the delivery email.")
                if request.method != "POST":
                    return self._confirm_action(
                        request, enquiry, action=action,
                        title="Send delivery email",
                        message="This sends the delivery email and records the release in the timeline.",
                    )
                self.send_delivery_email(request, RealEstateEnquiry.objects.filter(pk=enquiry.pk))
                return self._redirect_to_enquiry(enquiry)

            if action == "grant-delivery-override":
                form = DeliveryOverrideReasonForm(request.POST if request.method == "POST" else None)
                if request.method == "POST" and form.is_valid():
                    from .finance import grant_delivery_override
                    grant_delivery_override(enquiry, user=request.user, reason=form.cleaned_data["reason"])
                    self.message_user(request, "Delivery override granted.", level=messages.SUCCESS)
                    return self._redirect_to_enquiry(enquiry)
                return TemplateResponse(request, "admin/realestate/override_reason.html", {
                    **self.admin_site.each_context(request),
                    "form": form,
                    "title": "Grant delivery override",
                    "enquiry": enquiry,
                    "action": action,
                    "danger": True,
                })

            if action == "revoke-delivery-override":
                override_id = request.GET.get("override") or request.POST.get("override")
                override = enquiry.delivery_overrides.get(pk=override_id, revoked_at__isnull=True)
                form = RevokeOverrideForm(request.POST if request.method == "POST" else None)
                if request.method == "POST" and form.is_valid():
                    revoke_delivery_override(override, user=request.user, reason=form.cleaned_data["reason"])
                    self.message_user(request, "Delivery override revoked.", level=messages.SUCCESS)
                    return self._redirect_to_enquiry(enquiry)
                return TemplateResponse(request, "admin/realestate/override_reason.html", {
                    **self.admin_site.each_context(request),
                    "form": form,
                    "title": "Revoke delivery override",
                    "enquiry": enquiry,
                    "action": action,
                    "override": override,
                    "danger": True,
                })

            raise ValidationError("Unknown operation.")
        except PermissionDenied:
            raise
        except Exception as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return self._redirect_to_enquiry(enquiry)

    def _get_reply_to(self):
        reply_to_email = get_realestate_reply_to_email()
        return [reply_to_email] if reply_to_email else []

    def _date_key(self, value):
        if value is None:
            return ""
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if formfield and db_field.name in self.booking_field_help_texts:
            formfield.help_text = self.booking_field_help_texts[db_field.name]
        return formfield

    def _build_base_context(self, enquiry):
        confirmed_or_preferred_date = enquiry.shoot_date or enquiry.preferred_date
        context = {
            "quote_total": enquiry.quoted_price,
            "shoot_date": self._isoformat_date(confirmed_or_preferred_date),
            "deposit_payment_link": enquiry.deposit_payment_link,
            "booking_agreement_link": enquiry.booking_agreement_link,
            "delivery_link": enquiry.delivery_link,
            "review_link": enquiry.review_link,
        }
        if enquiry.quoted_price is not None:
            try:
                context.update(calculate_realestate_deposit_amounts(enquiry))
            except ValueError:
                pass
        return context

    def _record_email_timeline_event(
        self,
        enquiry,
        request,
        *,
        template_base,
        email,
        context,
        status,
        notes="",
    ):
        event_config = self.EMAIL_TIMELINE_EVENTS.get(template_base)
        if not event_config:
            return
        event_type, title, reference_context_key = event_config
        try:
            record_timeline_event(
                enquiry,
                event_type,
                status=status,
                actor_type=RealEstateTimelineEvent.ActorType.ADMIN,
                title=title,
                notes=notes,
                email_template=template_base,
                recipient_email=email,
                reference_url=context.get(reference_context_key, "")
                if reference_context_key
                else "",
                stripe_session_id=getattr(enquiry, "stripe_deposit_session_id", "")
                if template_base == "deposit_request"
                else "",
                created_by=getattr(request, "user", None),
            )
        except Exception:
            logger.exception(
                "Failed to record real estate email timeline event. "
                "enquiry_id=%s template=%s status=%s",
                enquiry.pk,
                template_base,
                status,
            )
            if status == RealEstateTimelineEvent.EventStatus.SENT:
                warning = (
                    f"{enquiry}: the email was sent, but its timeline event could not "
                    "be recorded. Check server logs."
                )
            else:
                warning = (
                    f"{enquiry}: the email failed, and its failure timeline event could "
                    "not be recorded. Check server logs."
                )
            self.message_user(request, warning, level=messages.WARNING)
            return False
        return True

    def _send_email_action(
        self,
        request,
        queryset,
        *,
        subject,
        template_base,
        description,
        extra_context=None,
        warning_messages=None,
        required_context=None,
        attachment_builder=None,
    ):
        sent_count = 0
        failed_count = 0
        skipped_count = 0
        warnings = []

        for enquiry in queryset:
            email = str(getattr(enquiry, "email", "") or "").strip()
            if not email:
                skipped_count += 1
                warnings.append(
                    f"{enquiry}: skipped because no client email is available."
                )
                continue

            context = self._build_base_context(enquiry)
            if extra_context:
                context.update(extra_context(enquiry))

            missing_requirements = []
            if required_context:
                missing_requirements = [
                    label
                    for label, value in required_context(enquiry, context)
                    if not value
                ]
            if missing_requirements:
                skipped_count += 1
                warnings.append(
                    f"{enquiry}: skipped because {', '.join(missing_requirements)} is missing."
                )
                continue

            if warning_messages:
                enquiry_warnings = [
                    message
                    for message in warning_messages(enquiry, context)
                    if message
                ]
                for message in enquiry_warnings:
                    warnings.append(f"{enquiry}: {message}")

            try:
                attachments = []
                if attachment_builder:
                    attachments = attachment_builder(enquiry, context)
                send_templated_email(
                    subject=subject,
                    to=[email],
                    template_base=template_base,
                    context=build_realestate_email_context(enquiry, **context),
                    reply_to=self._get_reply_to(),
                    attachments=attachments,
                )
                self._record_email_timeline_event(
                    enquiry,
                    request,
                    template_base=template_base,
                    email=email,
                    context=context,
                    status=RealEstateTimelineEvent.EventStatus.SENT,
                )
                sent_count += 1
            except Exception as exc:
                failed_count += 1
                self._record_email_timeline_event(
                    enquiry,
                    request,
                    template_base=template_base,
                    email=email,
                    context=context,
                    status=RealEstateTimelineEvent.EventStatus.FAILED,
                    notes=f"{exc.__class__.__name__}: {exc}",
                )
                warnings.append(
                    f"{enquiry}: {description.lower()} failed ({exc.__class__.__name__}: {exc})."
                )

        if sent_count:
            self.message_user(
                request,
                f"{description} sent for {sent_count} enquiry(s).",
                level=messages.SUCCESS,
            )
        if skipped_count:
            self.message_user(
                request,
                f"Skipped {skipped_count} enquiry(s) because required data was missing.",
                level=messages.WARNING,
            )
        if failed_count:
            self.message_user(
                request,
                f"{description} failed for {failed_count} enquiry(s).",
                level=messages.ERROR,
            )
        if warnings:
            preview = "; ".join(warnings[:3])
            if len(warnings) > 3:
                preview = f"{preview}; plus {len(warnings) - 3} more."
            self.message_user(request, preview, level=messages.WARNING)

    @admin.action(description="Send quote email")
    def send_quote_email(self, request, queryset):
        self._send_email_action(
            request,
            queryset,
            subject=f"Your property media quote - {get_business_identity().display_name}",
            template_base="quote",
            description="Quote email",
        )

    @admin.action(description="Send booking agreement email")
    def send_booking_agreement_email(self, request, queryset):
        self._send_email_action(
            request,
            queryset,
            subject=f"Booking Agreement for your property media booking - {get_business_identity().display_name}",
            template_base="booking_agreement",
            description="Booking agreement email",
            attachment_builder=lambda enquiry, context: [
                (
                    build_booking_agreement_filename(enquiry),
                    generate_booking_agreement_pdf(
                        enquiry,
                        create_new_version=True,
                        created_by=request.user,
                    ),
                    "application/pdf",
                )
            ],
        )


    @admin.action(description="Send deposit request email")
    def send_deposit_request_email(self, request, queryset):
        sent_count = 0
        failed_count = 0
        skipped_count = 0
        paid_count = 0
        warnings = []

        for enquiry in queryset:
            if enquiry.payment_arrangement != RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
                skipped_count += 1
                warnings.append(
                    f"{enquiry}: deposit actions are unavailable for the selected payment arrangement."
                )
                continue
            email = str(getattr(enquiry, "email", "") or "").strip()
            if not email:
                skipped_count += 1
                warnings.append(f"{enquiry}: skipped because no client email is available.")
                continue
            if not getattr(enquiry, "booking_agreement_received", False):
                skipped_count += 1
                warnings.append(
                    f"{enquiry}: skipped because signed booking agreement received is missing."
                )
                continue
            if enquiry.quoted_price is None:
                skipped_count += 1
                warnings.append(f"{enquiry}: skipped because quoted price is missing.")
                continue

            context = self._build_base_context(enquiry)
            try:
                checkout = prepare_realestate_deposit_checkout_session(enquiry)
            except Exception as exc:
                failed_count += 1
                warnings.append(
                    f"{enquiry}: deposit checkout preparation failed "
                    f"({exc.__class__.__name__}: {exc})."
                )
                continue
            if checkout.payment_already_exists:
                paid_count += 1
                warnings.append(
                    f"{enquiry}: the deposit payment is already recorded; "
                    "no payment request email was sent."
                )
                continue
            context["deposit_payment_link"] = checkout.checkout_url

            try:
                send_templated_email(
                    subject=f"Booking deposit request - {get_business_identity().display_name}",
                    to=[email],
                    template_base="deposit_request",
                    context=build_realestate_email_context(enquiry, **context),
                    reply_to=self._get_reply_to(),
                )
                self._record_email_timeline_event(
                    enquiry,
                    request,
                    template_base="deposit_request",
                    email=email,
                    context=context,
                    status=RealEstateTimelineEvent.EventStatus.SENT,
                )
                sent_count += 1
            except Exception as exc:
                failed_count += 1
                self._record_email_timeline_event(
                    enquiry,
                    request,
                    template_base="deposit_request",
                    email=email,
                    context=context,
                    status=RealEstateTimelineEvent.EventStatus.FAILED,
                    notes=f"{exc.__class__.__name__}: {exc}",
                )
                warnings.append(
                    f"{enquiry}: deposit request email failed ({exc.__class__.__name__}: {exc})."
                )

        if sent_count:
            self.message_user(
                request,
                f"Deposit request email sent for {sent_count} enquiry(s).",
                level=messages.SUCCESS,
            )
        if skipped_count:
            self.message_user(
                request,
                f"Skipped {skipped_count} enquiry(s) because required data was missing.",
                level=messages.WARNING,
            )
        if paid_count:
            self.message_user(
                request,
                f"Payment already exists for {paid_count} enquiry(s); no deposit request email was sent.",
                level=messages.INFO,
            )
        if failed_count:
            self.message_user(
                request,
                f"Deposit request email failed for {failed_count} enquiry(s).",
                level=messages.ERROR,
            )
        if warnings:
            preview = "; ".join(warnings[:3])
            if len(warnings) > 3:
                preview = f"{preview}; plus {len(warnings) - 3} more."
            self.message_user(request, preview, level=messages.WARNING)

    @admin.action(description="Send confirmation email")
    def send_confirmation_email(self, request, queryset):
        eligible = []
        blocked = []
        for enquiry in queryset:
            blocker = self._confirmation_blocker(enquiry)
            if blocker:
                blocked.append(f"{enquiry}: {blocker}")
            else:
                eligible.append(enquiry.pk)
        for warning in blocked[:3]:
            self.message_user(request, warning, level=messages.ERROR)
        if len(blocked) > 3:
            self.message_user(request, f"Plus {len(blocked) - 3} more blocked confirmation email(s).", level=messages.ERROR)
        queryset = queryset.filter(pk__in=eligible)
        if not eligible:
            return
        self._send_email_action(
            request,
            queryset,
            subject=f"Property shoot confirmed - {get_business_identity().display_name}",
            template_base="confirmation",
            description="Confirmation email",
        )

    @admin.action(description="Issue invoices for payment arrangement")
    def issue_arrangement_invoices(self, request, queryset):
        count = 0
        for enquiry in queryset:
            try:
                count += len(ensure_invoices_for_arrangement(enquiry))
            except Exception as exc:
                self.message_user(request, f"{enquiry}: {exc}", level=messages.ERROR)
        if count:
            self.message_user(request, f"Prepared {count} invoice(s).", level=messages.SUCCESS)

    @admin.action(description="Send delivery email")
    def send_delivery_email(self, request, queryset):
        eligible = []
        blocked = 0
        for enquiry in queryset:
            if can_release_realestate_delivery(enquiry):
                eligible.append(enquiry.pk)
            else:
                blocked += 1
        if blocked:
            self.message_user(
                request,
                f"Blocked delivery for {blocked} enquiry(s): payment is not complete and no active override exists.",
                level=messages.ERROR,
            )
        queryset = queryset.filter(pk__in=eligible)
        if not eligible:
            return
        self._send_email_action(
            request,
            queryset,
            subject=f"Your property media is ready - {get_business_identity().display_name}",
            template_base="delivery",
            description="Delivery email",
            warning_messages=lambda enquiry, context: [
                (
                    "Delivery email sent, but no delivery CTA was included "
                    "because no delivery link is stored."
                )
                if not context.get("delivery_link")
                else ""
            ],
        )

    @admin.action(description="Create balance payment Checkout")
    def create_balance_checkout(self, request, queryset):
        created = 0
        for enquiry in queryset:
            try:
                create_realestate_balance_checkout_session(enquiry)
            except Exception as exc:
                self.message_user(request, f"{enquiry}: {exc}", level=messages.ERROR)
            else:
                created += 1
        if created:
            self.message_user(request, f"Created balance Checkout for {created} enquiry(s).", level=messages.SUCCESS)

    @admin.action(description="Send follow-up email")
    def send_follow_up_email(self, request, queryset):
        self._send_email_action(
            request,
            queryset,
            subject=f"A quick follow-up - {get_business_identity().display_name}",
            template_base="follow_up",
            description="Follow-up email",
            warning_messages=lambda enquiry, context: [
                "Review CTA omitted because no review link is stored."
                if not context.get("review_link")
                else ""
            ],
        )

    @admin.action(description="Send weather reschedule email")
    def send_weather_reschedule_email(self, request, queryset):
        self._send_email_action(
            request,
            queryset,
            subject=f"Weather update for your property shoot - {get_business_identity().display_name}",
            template_base="weather_reschedule",
            description="Weather reschedule email",
            extra_context=lambda enquiry: {
                "shoot_date": self._isoformat_date(
                    enquiry.shoot_date or enquiry.preferred_date
                ),
                "new_date": self._isoformat_date(enquiry.proposed_shoot_date),
            },
            required_context=lambda enquiry, context: [
                ("a confirmed shoot date", context.get("shoot_date")),
                ("a proposed new shoot date", context.get("new_date")),
            ],
        )

    @admin.action(description="Send thank-you email")
    def send_thank_you_email(self, request, queryset):
        self._send_email_action(
            request,
            queryset,
            subject=f"Thank you from {get_business_identity().display_name}",
            template_base="thank_you",
            description="Thank-you email",
            warning_messages=lambda enquiry, context: [
                "Review CTA omitted because no review link is stored."
                if not context.get("review_link")
                else ""
            ],
        )

    def save_model(self, request, obj, form, change):
        previous = None
        if change and obj.pk:
            previous = RealEstateEnquiry.objects.filter(pk=obj.pk).first()

        super().save_model(request, obj, form, change)

        if previous is None:
            return

        if (
            not previous.booking_agreement_received
            and obj.booking_agreement_received
        ):
            record_timeline_event(
                obj,
                RealEstateTimelineEvent.EventType.BOOKING_AGREEMENT_RECEIVED,
                status=RealEstateTimelineEvent.EventStatus.COMPLETED,
                actor_type=RealEstateTimelineEvent.ActorType.ADMIN,
                title="Booking agreement marked as received",
                created_by=getattr(request, "user", None),
            )

        shoot_date_key = self._date_key(obj.shoot_date)
        previous_shoot_date_key = self._date_key(previous.shoot_date)
        if shoot_date_key and shoot_date_key != previous_shoot_date_key:
            record_timeline_event(
                obj,
                RealEstateTimelineEvent.EventType.SHOOT_SCHEDULED,
                status=RealEstateTimelineEvent.EventStatus.COMPLETED,
                actor_type=RealEstateTimelineEvent.ActorType.ADMIN,
                title="Shoot scheduled",
                notes=f"Shoot date: {shoot_date_key}",
                created_by=getattr(request, "user", None),
            )


@admin.register(RealEstateTimelineEvent, site=custom_admin_site)
class RealEstateTimelineEventAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "enquiry",
        "event_type",
        "status",
        "actor_type",
        "title",
        "created_by",
    )
    list_filter = (
        "enquiry",
        "event_type",
        "status",
        "actor_type",
        "created_at",
    )
    search_fields = (
        "title",
        "notes",
        "recipient_email",
        "enquiry__name",
        "enquiry__email",
        "enquiry__property_address",
    )
    readonly_fields = (
        "created_at",
    )


class ManualPaymentForm(forms.Form):
    amount = forms.DecimalField(min_value=0.01, decimal_places=2, max_digits=10)
    received_at = forms.DateTimeField(initial=timezone.now)
    method = forms.ChoiceField(choices=(
        (RealEstatePayment.Method.CASH, "Cash"),
        (RealEstatePayment.Method.BANK_TRANSFER, "Bank transfer"),
        (RealEstatePayment.Method.OTHER, "Other"),
    ))
    payer_reference = forms.CharField(max_length=255)
    bank_lodgement_reference = forms.CharField(max_length=255, required=False)
    notes = forms.CharField(widget=forms.Textarea)


@admin.register(RealEstateInvoice, site=custom_admin_site)
class RealEstateInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "enquiry", "invoice_type", "status", "total", "issued_at", "paid_at")
    list_filter = ("invoice_type", "status", "currency", "enquiry")
    search_fields = ("invoice_number", "customer_name_snapshot", "property_reference_snapshot")
    actions = (
        "record_manual_payment_action", "download_invoice_pdf",
        "create_stripe_invoice_action", "create_and_send_stripe_invoice_action",
        "send_stripe_reminder_action", "mark_stripe_paid_out_of_band_action",
    )

    @admin.display(description="Amount paid")
    def amount_paid_display(self, obj):
        return obj.amount_paid if obj else Decimal("0")

    @admin.display(description="Amount outstanding")
    def amount_outstanding_display(self, obj):
        return obj.amount_outstanding if obj else Decimal("0")

    @admin.display(description="Stripe hosted invoice")
    def stripe_hosted_link(self, obj):
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">Open invoice</a>', obj.stripe_hosted_invoice_url) if obj and obj.stripe_hosted_invoice_url else "—"

    @admin.display(description="Stripe PDF")
    def stripe_pdf_link(self, obj):
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">Download Stripe PDF</a>', obj.stripe_invoice_pdf_url) if obj and obj.stripe_invoice_pdf_url else "—"

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status != RealEstateInvoice.Status.DRAFT:
            return tuple(field.name for field in obj._meta.fields) + (
                "amount_paid_display", "amount_outstanding_display", "stripe_hosted_link", "stripe_pdf_link",
            )
        return (
            "invoice_number", "created_at", "updated_at", "paid_at",
            "amount_paid_display", "amount_outstanding_display", "stripe_hosted_link", "stripe_pdf_link",
        )

    def has_delete_permission(self, request, obj=None):
        return not obj or obj.status == RealEstateInvoice.Status.DRAFT

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.action(description="Create Stripe invoice")
    def create_stripe_invoice_action(self, request, queryset):
        for invoice in queryset:
            try:
                create_stripe_invoice(invoice)
            except Exception as exc:
                self.message_user(request, f"{invoice}: {exc}", level=messages.ERROR)

    @admin.action(description="Create and send Stripe invoice")
    def create_and_send_stripe_invoice_action(self, request, queryset):
        for invoice in queryset:
            try:
                create_stripe_invoice(invoice, send=True)
            except Exception as exc:
                self.message_user(request, f"{invoice}: {exc}", level=messages.ERROR)

    @admin.action(description="Mark Stripe invoice paid out of band")
    def mark_stripe_paid_out_of_band_action(self, request, queryset):
        if "confirm_out_of_band" not in request.POST:
            return TemplateResponse(request, "admin/realestate/mark_out_of_band.html", {
                **self.admin_site.each_context(request), "invoices": queryset,
                "title": "Confirm Stripe out-of-band settlement",
            })
        for invoice in queryset:
            try:
                mark_stripe_invoice_paid_out_of_band(invoice, user=request.user)
            except Exception as exc:
                self.message_user(request, f"{invoice}: {exc}", level=messages.ERROR)

    @admin.action(description="Send Stripe invoice reminder")
    def send_stripe_reminder_action(self, request, queryset):
        for invoice in queryset:
            try:
                send_stripe_invoice(invoice)
            except Exception as exc:
                self.message_user(request, f"{invoice}: {exc}", level=messages.ERROR)

    @admin.action(description="Record cash/bank/other payment")
    def record_manual_payment_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one invoice.", level=messages.ERROR)
            return
        invoice = queryset.get()
        form = ManualPaymentForm(request.POST if "confirm_manual_payment" in request.POST else None)
        if form.is_valid():
            try:
                payment, _ = record_realestate_payment(
                    invoice=invoice,
                    amount=form.cleaned_data["amount"],
                    method=form.cleaned_data["method"],
                    paid_at=form.cleaned_data["received_at"],
                    recorded_by=request.user,
                    external_reference=form.cleaned_data["payer_reference"],
                    bank_lodgement_reference=form.cleaned_data["bank_lodgement_reference"],
                    notes=form.cleaned_data["notes"],
                )
            except Exception as exc:
                form.add_error(None, str(exc))
            else:
                self.message_user(request, f"Payment recorded for {invoice.invoice_number}.", level=messages.SUCCESS)
                return None
        return TemplateResponse(request, "admin/realestate/manual_payment.html", {
            **self.admin_site.each_context(request), "form": form, "invoice": invoice,
            "title": f"Record payment for {invoice.invoice_number}",
        })

    @admin.action(description="Download invoice PDF")
    def download_invoice_pdf(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one invoice.", level=messages.ERROR)
            return None
        invoice = queryset.get()
        response = HttpResponse(generate_invoice_pdf(invoice), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{build_invoice_filename(invoice)}"'
        return response


@admin.register(RealEstatePayment, site=custom_admin_site)
class RealEstatePaymentAdmin(admin.ModelAdmin):
    list_display = ("enquiry_display", "invoice", "amount", "currency", "method", "status", "paid_at", "remaining_balance", "out_of_band_display", "cash_receipt_number", "recorded_by")
    list_filter = ("method", "status", "currency", "invoice__enquiry")
    search_fields = ("invoice__invoice_number", "external_reference", "cash_receipt_number", "stripe_checkout_session_id")
    actions = ("download_cash_receipt",)

    @admin.display(description="Enquiry")
    def enquiry_display(self, obj):
        return obj.invoice.enquiry

    @admin.display(description="Remaining balance")
    def remaining_balance(self, obj):
        return obj.invoice.amount_outstanding

    @admin.display(boolean=True, description="Stripe out of band")
    def out_of_band_display(self, obj):
        return bool(obj.invoice.stripe_marked_paid_out_of_band_at)

    @admin.action(description="Download cash receipt PDF")
    def download_cash_receipt(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one cash payment.", level=messages.ERROR)
            return None
        payment = queryset.select_related("invoice").get()
        if not payment.cash_receipt_number:
            self.message_user(request, "The selected payment has no cash receipt.", level=messages.ERROR)
            return None
        response = HttpResponse(generate_cash_receipt_pdf(payment), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{build_receipt_filename(payment)}"'
        return response

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == RealEstatePayment.Status.SUCCEEDED:
            return tuple(field.name for field in obj._meta.fields)
        return ("created_at", "updated_at", "cash_receipt_number")

    def has_delete_permission(self, request, obj=None):
        return not obj or obj.status != RealEstatePayment.Status.SUCCEEDED

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


class RevokeOverrideForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea)


class DeliveryOverrideReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea)


@admin.register(RealEstateDeliveryOverride, site=custom_admin_site)
class RealEstateDeliveryOverrideAdmin(admin.ModelAdmin):
    list_display = ("enquiry", "created_by", "created_at", "revoked_at")
    readonly_fields = ("created_by", "created_at", "revoked_by", "revoked_at", "revocation_reason")
    actions = ("revoke_override_action",)

    def save_model(self, request, obj, form, change):
        if change:
            raise forms.ValidationError("Delivery overrides cannot be edited; revoke the override instead.")
        if not str(obj.reason or "").strip():
            raise forms.ValidationError("An override reason is required.")
        obj.created_by = request.user
        super().save_model(request, obj, form, change)
        record_timeline_event(
            obj.enquiry, RealEstateTimelineEvent.EventType.DELIVERY_OVERRIDE_GRANTED,
            actor_type=RealEstateTimelineEvent.ActorType.ADMIN,
            title="Delivery override granted", notes=obj.reason, created_by=request.user,
        )

    @admin.action(description="Revoke selected delivery override")
    def revoke_override_action(self, request, queryset):
        form = RevokeOverrideForm(request.POST if "confirm_override_revocation" in request.POST else None)
        if form.is_valid():
            for override in queryset:
                revoke_delivery_override(override, user=request.user, reason=form.cleaned_data["reason"])
            self.message_user(request, f"Revoked {queryset.count()} override(s).", level=messages.SUCCESS)
            return None
        return TemplateResponse(request, "admin/realestate/revoke_override.html", {
            **self.admin_site.each_context(request), "form": form, "overrides": queryset,
            "title": "Revoke delivery override",
        })
