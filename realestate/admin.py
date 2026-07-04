from django.contrib import admin
from django.contrib import messages

from openeire_api.admin import custom_admin_site

from .emails import build_realestate_email_context
from .emails import get_realestate_reply_to_email
from .emails import send_templated_email
from .models import RealEstateEnquiry


@admin.register(RealEstateEnquiry, site=custom_admin_site)
class RealEstateEnquiryAdmin(admin.ModelAdmin):
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
    readonly_fields = ("created_at", "updated_at")
    actions = (
        "send_quote_email",
        "send_booking_agreement_deposit_email",
        "send_confirmation_email",
        "send_delivery_email",
        "send_follow_up_email",
        "send_weather_reschedule_email",
        "send_thank_you_email",
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
            "Notes",
            {
                "fields": ("internal_notes",)
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def _isoformat_date(self, value):
        return value.isoformat() if value else ""

    def _get_reply_to(self):
        reply_to_email = get_realestate_reply_to_email()
        return [reply_to_email] if reply_to_email else []

    def _build_base_context(self, enquiry):
        confirmed_or_preferred_date = enquiry.shoot_date or enquiry.preferred_date
        context = {
            "quote_total": enquiry.quoted_price,
            "shoot_date": self._isoformat_date(confirmed_or_preferred_date),
        }
        if enquiry.shoot_date:
            context["new_date"] = self._isoformat_date(enquiry.shoot_date)
        return context

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
                send_templated_email(
                    subject=subject,
                    to=[email],
                    template_base=template_base,
                    context=build_realestate_email_context(enquiry, **context),
                    reply_to=self._get_reply_to(),
                )
                sent_count += 1
            except Exception as exc:
                failed_count += 1
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
            subject="Your property media quote - OpenEire Studios",
            template_base="quote",
            description="Quote email",
        )

    @admin.action(description="Send booking agreement/deposit email")
    def send_booking_agreement_deposit_email(self, request, queryset):
        self._send_email_action(
            request,
            queryset,
            subject="Booking agreement and deposit details - OpenEire Studios",
            template_base="booking_agreement_deposit",
            description="Booking agreement/deposit email",
            warning_messages=lambda enquiry, context: [
                "deposit CTA omitted because no deposit payment link is stored."
                if not context.get("deposit_payment_link")
                else "",
                "booking agreement text link omitted because no booking agreement link is stored."
                if not context.get("booking_agreement_link")
                else "",
            ],
        )

    @admin.action(description="Send confirmation email")
    def send_confirmation_email(self, request, queryset):
        self._send_email_action(
            request,
            queryset,
            subject="Property shoot confirmed - OpenEire Studios",
            template_base="confirmation",
            description="Confirmation email",
        )

    @admin.action(description="Send delivery email")
    def send_delivery_email(self, request, queryset):
        self._send_email_action(
            request,
            queryset,
            subject="Your property media is ready - OpenEire Studios",
            template_base="delivery",
            description="Delivery email",
            warning_messages=lambda enquiry, context: [
                "delivery CTA omitted because no delivery link is stored."
                if not context.get("delivery_link")
                else ""
            ],
        )

    @admin.action(description="Send follow-up email")
    def send_follow_up_email(self, request, queryset):
        self._send_email_action(
            request,
            queryset,
            subject="A quick follow-up - OpenEire Studios",
            template_base="follow_up",
            description="Follow-up email",
            warning_messages=lambda enquiry, context: [
                "review CTA omitted because no review link is stored."
                if not context.get("review_link")
                else ""
            ],
        )

    @admin.action(description="Send weather reschedule email")
    def send_weather_reschedule_email(self, request, queryset):
        self._send_email_action(
            request,
            queryset,
            subject="Weather update for your property shoot - OpenEire Studios",
            template_base="weather_reschedule",
            description="Weather reschedule email",
            required_context=lambda enquiry, context: [
                ("a revised shoot date", context.get("new_date"))
            ],
        )

    @admin.action(description="Send thank-you email")
    def send_thank_you_email(self, request, queryset):
        self._send_email_action(
            request,
            queryset,
            subject="Thank you from OpenEire Studios",
            template_base="thank_you",
            description="Thank-you email",
            warning_messages=lambda enquiry, context: [
                "review CTA omitted because no review link is stored."
                if not context.get("review_link")
                else ""
            ],
        )
