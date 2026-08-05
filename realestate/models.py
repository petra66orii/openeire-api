from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from decimal import Decimal
import re
import uuid

from .package_catalogue import (
    ADDITIONAL_PHOTOGRAPH_COPY,
    PACKAGE_SUMMARIES,
    get_included_photograph_count,
    get_included_photographs_label,
    get_included_add_ons,
    get_package_summary,
)
from .turnaround import (
    get_package_turnaround_code,
    get_package_turnaround_detail,
    get_package_turnaround_label,
)


class RealEstateEnquiry(models.Model):
    class PaymentArrangement(models.TextChoices):
        DEPOSIT_THEN_BALANCE = "deposit_then_balance", "30% deposit then balance"
        FULL_UPFRONT = "full_upfront", "Full payment upfront"
        FULL_ON_SHOOT_DAY = "full_on_shoot_day", "Full payment on shoot day"
        CUSTOM = "custom", "Custom"

    class ExpectedPaymentMethod(models.TextChoices):
        STRIPE = "stripe", "Stripe"
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        OTHER = "other", "Other"
    class ClientType(models.TextChoices):
        ESTATE_AGENT = "estate_agent", "Estate Agent"
        DEVELOPER = "developer", "Developer"
        PRIVATE_SELLER = "private_seller", "Private Seller"
        LANDLORD = "landlord", "Landlord"
        OTHER = "other", "Other"

    class PreferredPackage(models.TextChoices):
        ESSENTIAL = "essential", "Essential"
        STARTER = "starter", "Starter"
        PRO = "pro", "Pro"
        PREMIUM = "premium", "Premium"
        CUSTOM = "custom", "Custom"
        NOT_SURE = "not_sure", "Not Sure"

    class PropertyType(models.TextChoices):
        HOUSE = "house", "House"
        APARTMENT = "apartment", "Apartment"
        NEW_BUILD = "new_build", "New build / development"
        SITE_LAND = "site_land", "Site / land"
        COMMERCIAL = "commercial", "Commercial property"
        AGRICULTURAL = "agricultural", "Agricultural property"
        OTHER = "other", "Other"

    class BedroomCount(models.TextChoices):
        STUDIO = "studio", "Studio"
        ONE = "1", "1"
        TWO = "2", "2"
        THREE = "3", "3"
        FOUR = "4", "4"
        FIVE = "5", "5"
        SIX_PLUS = "6_plus", "6+"
        NOT_APPLICABLE = "not_applicable", "Not applicable"

    class FloorCount(models.TextChoices):
        ONE = "1", "1"
        TWO = "2", "2"
        THREE = "3", "3"
        FOUR_PLUS = "4_plus", "4+"
        NOT_APPLICABLE = "not_applicable", "Not applicable"

    class YesNoNotSure(models.TextChoices):
        YES = "yes", "Yes"
        NO = "no", "No"
        NOT_SURE = "not_sure", "Not sure"

    class GroundsSize(models.TextChoices):
        NO_GROUNDS = "no_grounds", "No grounds"
        NORMAL_GARDEN = "normal_garden", "Normal garden"
        LARGE_GARDEN = "large_garden", "Large garden"
        UNDER_ONE_ACRE = "under_1_acre", "Under 1 acre"
        ONE_TO_FIVE_ACRES = "1_to_5_acres", "1-5 acres"
        OVER_FIVE_ACRES = "over_5_acres", "Over 5 acres"
        NOT_SURE = "not_sure", "Not sure"
        NOT_APPLICABLE = "not_applicable", "Not applicable"

    class FloorAreaUnit(models.TextChoices):
        SQUARE_METRES = "sqm", "m²"
        SQUARE_FEET = "sqft", "sq ft"

    class OccupancyStatus(models.TextChoices):
        VACANT = "vacant", "Vacant"
        OWNER_OCCUPIED = "owner_occupied", "Owner occupied"
        TENANT_OCCUPIED = "tenant_occupied", "Tenant occupied"
        NEW_BUILD_SITE = "new_build_site", "New build / site"
        OTHER = "other", "Other"

    class AccessProvider(models.TextChoices):
        ENQUIRER = "enquirer", "Enquirer"
        OWNER = "owner", "Property owner / vendor"
        TENANT = "tenant", "Tenant"
        AGENT = "agent_colleague", "Agent / colleague"
        OTHER = "other", "Other"

    class SchedulingPreference(models.TextChoices):
        REQUEST_DATE = "request_date", "Request a preferred date"
        FLEXIBLE = "flexible", "Flexible / please contact me"

    class PreferredTimeWindow(models.TextChoices):
        MORNING = "morning", "Morning"
        AFTERNOON = "afternoon", "Afternoon"
        FLEXIBLE = "flexible", "Flexible"

    class HowHeard(models.TextChoices):
        GOOGLE = "google", "Google"
        INSTAGRAM = "instagram", "Instagram"
        FACEBOOK = "facebook", "Facebook"
        LINKEDIN = "linkedin", "LinkedIn"
        REFERRAL = "referral", "Referral"
        ESTATE_AGENT_COLLEAGUE = "estate_agent_colleague", "Estate Agent Colleague"
        OPENEIRE_WEBSITE = "openeire_website", "OpenEire Website"
        OTHER = "other", "Other"
        NOT_SURE = "not_sure", "Not Sure"

    class Status(models.TextChoices):
        NEW = "new", "New"
        REVIEWING = "reviewing", "Reviewing"
        QUOTED = "quoted", "Quoted"
        BOOKED = "booked", "Booked"
        COMPLETED = "completed", "Completed"
        CLOSED = "closed", "Closed"
        SPAM = "spam", "Spam"

    class DeliveryProvider(models.TextChoices):
        MYAIRBRIDGE = "myairbridge", "MyAirBridge"
        GOOGLE_DRIVE = "google_drive", "Google Drive"
        DROPBOX = "dropbox", "Dropbox"
        ONEDRIVE = "onedrive", "OneDrive"
        PORTAL = "portal", "OpenEire Client Portal"
        OTHER = "other", "Other"

    ADD_ON_LABELS = {
        "additional_stills": "Additional edited photographs - EUR 10 per photograph",
        "floor_plan": "Measured 2D floor plan - EUR 75",
        "virtual_tour_3d": "Hosted 3D virtual tour - EUR 150",
        "rush_delivery": "Rush same-day delivery, still photography only - EUR 75",
        "extended_drone_video": "Extended drone video, up to 3 minutes - EUR 150",
        "additional_social_cuts": (
            "Additional social-media cuts, alternative formats or additional edits - EUR 50"
        ),
        "travel_supplement": "Travel supplement beyond 40 km - EUR 0.50 per km",
    }

    PACKAGE_SUMMARIES = PACKAGE_SUMMARIES
    ADDITIONAL_PHOTOGRAPH_COPY = ADDITIONAL_PHOTOGRAPH_COPY

    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=50)
    client_type = models.CharField(max_length=32, choices=ClientType.choices)
    property_address = models.TextField()
    county = models.CharField(max_length=100)
    property_type = models.CharField(max_length=100)
    preferred_package = models.CharField(max_length=32, choices=PreferredPackage.choices)
    consent_to_contact = models.BooleanField(default=False)
    form_schema_version = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Public enquiry payload schema. Blank indicates the legacy form; "
            "version 2 uses the structured shoot-scoping form."
        ),
    )

    company_name = models.CharField(max_length=255, blank=True)
    eircode = models.CharField(max_length=20, blank=True)
    no_eircode = models.BooleanField(default=False)
    location_details = models.TextField(blank=True)
    property_type_details = models.CharField(max_length=255, blank=True)
    bedroom_count = models.CharField(max_length=20, choices=BedroomCount.choices, blank=True)
    floor_count = models.CharField(max_length=20, choices=FloorCount.choices, blank=True)
    secondary_accommodation = models.CharField(
        max_length=12, choices=YesNoNotSure.choices, blank=True
    )
    secondary_accommodation_details = models.CharField(max_length=500, blank=True)
    outbuildings = models.CharField(
        max_length=12, choices=YesNoNotSure.choices, blank=True
    )
    outbuildings_details = models.CharField(max_length=500, blank=True)
    grounds_size = models.CharField(max_length=24, choices=GroundsSize.choices, blank=True)
    internal_floor_area = models.PositiveIntegerField(null=True, blank=True)
    internal_floor_area_unit = models.CharField(
        max_length=4, choices=FloorAreaUnit.choices, blank=True
    )
    property_features = models.TextField(blank=True)
    occupancy_status = models.CharField(
        max_length=20, choices=OccupancyStatus.choices, blank=True
    )
    access_provider = models.CharField(
        max_length=20, choices=AccessProvider.choices, blank=True
    )
    access_contact_name = models.CharField(max_length=255, blank=True)
    access_contact_phone = models.CharField(max_length=50, blank=True)
    readiness_acknowledged = models.BooleanField(default=False)
    add_ons = models.JSONField(default=list, blank=True)
    additional_stills_quantity = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(50)],
        help_text="Public requests are limited to 50 additional edited images.",
    )
    travel_supplement_amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text=(
            "Exact travel supplement already included in the quoted price. "
            "Required before issuing an agreement when the travel add-on is selected."
        ),
    )
    travel_details = models.TextField(
        blank=True,
        help_text=(
            "Explain the agreed travel basis, such as chargeable distance or destination. "
            "Required before issuing an agreement when the travel add-on is selected."
        ),
    )
    preferred_date = models.DateField(null=True, blank=True)
    scheduling_preference = models.CharField(
        max_length=20, choices=SchedulingPreference.choices, blank=True
    )
    alternative_date = models.DateField(null=True, blank=True)
    preferred_time_window = models.CharField(
        max_length=12, choices=PreferredTimeWindow.choices, blank=True
    )
    on_camera = models.CharField(
        max_length=12, choices=YesNoNotSure.choices, blank=True
    )
    on_camera_people = models.CharField(max_length=500, blank=True)
    audio_requirements = models.TextField(blank=True)
    how_heard = models.CharField(max_length=32, choices=HowHeard.choices, blank=True)
    message = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    quoted_price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    pricing_snapshot_version = models.PositiveSmallIntegerField(null=True, blank=True)
    price_input_is_gross = models.BooleanField(null=True, blank=True)
    vat_registered_at_quote = models.BooleanField(null=True, blank=True)
    quoted_vat_rate = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        null=True,
        blank=True,
    )
    quoted_subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    quoted_vat_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    quoted_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    quoted_deposit_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    quoted_balance_due = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    shoot_date = models.DateField(null=True, blank=True)
    shoot_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Agreed local start time for the property shoot.",
    )
    access_contact = models.CharField(
        max_length=255,
        blank=True,
        help_text="Name and contact details for the person providing access on site.",
    )
    access_notes = models.TextField(
        blank=True,
        help_text="Agreed access instructions, restrictions or arrival notes.",
    )
    internal_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    proposed_shoot_date = models.DateField(null=True, blank=True)
    booking_agreement_received = models.BooleanField(default=False)
    deposit_payment_link = models.URLField(max_length=500, blank=True)
    stripe_deposit_session_id = models.CharField(max_length=255, blank=True)
    stripe_deposit_creation_key = models.CharField(max_length=255, blank=True)
    deposit_paid = models.BooleanField(default=False)
    deposit_paid_at = models.DateTimeField(null=True, blank=True)
    booking_agreement_link = models.URLField(blank=True)
    # Legacy/fallback delivery metadata. Portal fragment links are generated
    # per recipient and must never be stored in this field.
    delivery_provider = models.CharField(
        max_length=20,
        choices=DeliveryProvider.choices,
        default=DeliveryProvider.MYAIRBRIDGE,
    )
    delivery_link = models.URLField(blank=True)
    review_link = models.URLField(blank=True)
    payment_arrangement = models.CharField(
        max_length=24, choices=PaymentArrangement.choices,
        default=PaymentArrangement.DEPOSIT_THEN_BALANCE,
    )
    payment_due_date = models.DateField(null=True, blank=True)
    expected_payment_method = models.CharField(
        max_length=20, choices=ExpectedPaymentMethod.choices, blank=True
    )
    custom_payment_terms = models.TextField(blank=True)
    custom_required_total = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Real estate enquiry"
        verbose_name_plural = "Real estate enquiries"

    def __str__(self):
        return f"[{self.county}] {self.get_preferred_package_display()} - {self.name}"

    def get_preferred_package_summary(self):
        persisted_scope = self._get_persisted_package_scope()
        if persisted_scope:
            return persisted_scope.get("package_name") or (
                "Agreed package scope - refer to the issued Booking Agreement"
            )
        if self._requires_historical_scope_review():
            return "Agreed package scope - verify the issued quotation or agreement"
        return get_package_summary(
            self.preferred_package,
            self.get_preferred_package_display(),
        )

    def get_included_photographs_label(self):
        persisted_scope = self._get_persisted_package_scope()
        if persisted_scope:
            label = persisted_scope.get("included_photographs_label")
            if label:
                return label
            package_name = str(persisted_scope.get("package_name") or "")
            match = re.search(r"(\d+)\s+(?:professionally\s+)?edited", package_name, re.I)
            if match:
                return (
                    f"{match.group(1)} professionally edited interior "
                    "and exterior photographs"
                )
            return "Included photographs - refer to the issued Booking Agreement"
        if self._requires_historical_scope_review():
            return "Included photographs - verify the issued quotation or agreement"
        return get_included_photographs_label(self.preferred_package)

    def get_included_photograph_count(self):
        persisted_scope = self._get_persisted_package_scope()
        if persisted_scope:
            count = persisted_scope.get("included_photograph_count")
            if isinstance(count, int):
                return count
            match = re.search(
                r"(\d+)\s+(?:professionally\s+)?edited",
                str(persisted_scope.get("package_name") or ""),
                re.I,
            )
            return int(match.group(1)) if match else None
        if self._requires_historical_scope_review():
            return None
        return get_included_photograph_count(self.preferred_package)

    def _get_persisted_package_scope(self):
        if not self.pk:
            return None
        snapshot = self.booking_agreement_snapshots.first()
        if not snapshot:
            return None
        return snapshot.context if isinstance(snapshot.context, dict) else {}

    def _requires_historical_scope_review(self):
        return self.status in {self.Status.BOOKED, self.Status.COMPLETED}

    def get_preferred_package_turnaround_code(self):
        return get_package_turnaround_code(self.preferred_package)

    def get_preferred_package_turnaround_label(self):
        return get_package_turnaround_label(self.preferred_package)

    def get_preferred_package_turnaround_detail(self):
        return get_package_turnaround_detail(self.preferred_package)

    def get_add_on_labels(self):
        labels = []
        included_add_ons = (
            frozenset()
            if self._get_persisted_package_scope() or self._requires_historical_scope_review()
            else get_included_add_ons(self.preferred_package)
        )
        for key in self.add_ons or []:
            if key in included_add_ons:
                continue
            label = self.ADD_ON_LABELS.get(key, key)
            if key == "additional_stills" and self.additional_stills_quantity:
                label = f"{label} x {self.additional_stills_quantity}"
            labels.append(label)
        return labels

    def get_add_ons_summary(self):
        labels = self.get_add_on_labels()
        return ", ".join(labels) if labels else "None"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = RealEstateEnquiry.objects.filter(pk=self.pk).values_list(
                "payment_arrangement", flat=True
            ).first()
            if previous and previous != self.payment_arrangement and self.invoices.exists():
                raise ValidationError(
                    "Payment arrangement cannot change after invoices have been created."
                )
        if self.payment_arrangement == self.PaymentArrangement.CUSTOM:
            if not str(self.custom_payment_terms or "").strip() or not self.custom_required_total:
                raise ValidationError(
                    "Custom payment arrangements require terms and a required total."
                )
        if (
            self.status == self.Status.BOOKED
            and self.payment_arrangement == self.PaymentArrangement.FULL_UPFRONT
        ):
            paid_full = self.pk and self.invoices.filter(
                invoice_type="full", status="paid"
            ).exists()
            if not paid_full:
                raise ValidationError(
                    "Full-upfront arrangements must be paid before booking confirmation."
                )
        if (
            self.payment_arrangement == self.PaymentArrangement.FULL_ON_SHOOT_DAY
            and not self.payment_due_date
            and self.shoot_date
        ):
            self.payment_due_date = self.shoot_date
        super().save(*args, **kwargs)


class RealEstateTimelineEvent(models.Model):
    class EventType(models.TextChoices):
        ENQUIRY_RECEIVED = "enquiry_received", "Enquiry received"
        QUOTE_SENT = "quote_sent", "Quote sent"
        BOOKING_AGREEMENT_SENT = "booking_agreement_sent", "Booking agreement sent"
        BOOKING_AGREEMENT_RECEIVED = "booking_agreement_received", "Booking agreement received"
        DEPOSIT_REQUEST_SENT = "deposit_request_sent", "Deposit request sent"
        DEPOSIT_PAID = "deposit_paid", "Deposit paid"
        CONFIRMATION_SENT = "confirmation_sent", "Confirmation sent"
        WEATHER_RESCHEDULE_SENT = "weather_reschedule_sent", "Weather reschedule sent"
        SHOOT_SCHEDULED = "shoot_scheduled", "Shoot scheduled"
        SHOOT_COMPLETED = "shoot_completed", "Shoot completed"
        DELIVERY_SENT = "delivery_sent", "Delivery sent"
        FOLLOW_UP_SENT = "follow_up_sent", "Follow-up sent"
        THANK_YOU_SENT = "thank_you_sent", "Thank-you sent"
        REVIEW_RECEIVED = "review_received", "Review received"
        STATUS_CHANGED = "status_changed", "Status changed"
        NOTE = "note", "Note"
        INVOICE_ISSUED = "invoice_issued", "Invoice issued"
        PAYMENT_RECORDED = "payment_recorded", "Payment recorded"
        INVOICE_PAID = "invoice_paid", "Invoice paid in full"
        DELIVERY_READY = "delivery_ready", "Delivery ready"
        DELIVERY_RELEASED = "delivery_released", "Delivery released"
        DELIVERY_OVERRIDE_GRANTED = "delivery_override_granted", "Delivery override granted"
        DELIVERY_OVERRIDE_REVOKED = "delivery_override_revoked", "Delivery override revoked"

    class EventStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    class ActorType(models.TextChoices):
        SYSTEM = "system", "System"
        ADMIN = "admin", "Admin"
        CLIENT = "client", "Client"

    enquiry = models.ForeignKey(
        RealEstateEnquiry,
        on_delete=models.CASCADE,
        related_name="timeline_events",
    )
    event_type = models.CharField(max_length=50, choices=EventType.choices)
    status = models.CharField(
        max_length=20,
        choices=EventStatus.choices,
        default=EventStatus.COMPLETED,
    )
    actor_type = models.CharField(
        max_length=20,
        choices=ActorType.choices,
        default=ActorType.SYSTEM,
    )

    title = models.CharField(max_length=255)
    notes = models.TextField(blank=True)

    email_template = models.CharField(max_length=100, blank=True)
    recipient_email = models.EmailField(blank=True)
    reference_url = models.URLField(max_length=2048, blank=True)
    stripe_session_id = models.CharField(max_length=255, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Real estate timeline event"
        verbose_name_plural = "Real estate timeline events"

    def __str__(self):
        return f"{self.get_event_type_display()} - {self.enquiry}"


class RealEstateDocumentSequence(models.Model):
    class Kind(models.TextChoices):
        INVOICE = "invoice", "Invoice"
        RECEIPT = "receipt", "Receipt"

    kind = models.CharField(max_length=12, choices=Kind.choices)
    year = models.PositiveSmallIntegerField()
    next_value = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("kind", "year"), name="uniq_re_doc_sequence")
        ]


class RealEstateBookingAgreementSnapshot(models.Model):
    TEMPLATE_VERSION = "1.8"

    enquiry = models.ForeignKey(
        RealEstateEnquiry,
        on_delete=models.PROTECT,
        related_name="booking_agreement_snapshots",
    )
    template_version = models.CharField(max_length=16, default=TEMPLATE_VERSION)
    payment_arrangement = models.CharField(max_length=24)
    total_required = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    deposit_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    balance_due = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    payment_due_date = models.DateField(null=True, blank=True)
    expected_payment_method = models.CharField(max_length=20, blank=True)
    custom_payment_terms = models.TextField(blank=True)
    context = models.JSONField(default=dict)
    rendered_markdown = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="realestate_booking_agreement_snapshots_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Real estate booking agreement snapshot"
        verbose_name_plural = "Real estate booking agreement snapshots"

    def __str__(self):
        return f"{self.enquiry} booking agreement v{self.template_version}"


class RealEstateInvoice(models.Model):
    class InvoiceType(models.TextChoices):
        DEPOSIT = "deposit", "Deposit"
        BALANCE = "balance", "Balance"
        FULL = "full", "Full"
        ADJUSTMENT = "adjustment", "Adjustment"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"
        VOID = "void", "Void"

    enquiry = models.ForeignKey(
        RealEstateEnquiry, on_delete=models.PROTECT, related_name="invoices"
    )
    invoice_type = models.CharField(max_length=20, choices=InvoiceType.choices)
    invoice_number = models.CharField(max_length=20, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    currency = models.CharField(max_length=3, default="EUR")
    description = models.CharField(max_length=255, blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    vat_rate = models.DecimalField(max_digits=6, decimal_places=5, default=0)
    vat_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    customer_name_snapshot = models.CharField(max_length=255)
    company_name_snapshot = models.CharField(max_length=255, blank=True)
    customer_email_snapshot = models.EmailField(blank=True)
    customer_phone_snapshot = models.CharField(max_length=50, blank=True)
    property_reference_snapshot = models.TextField()
    job_reference_snapshot = models.CharField(max_length=64)
    issued_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    stripe_invoice_id = models.CharField(max_length=255, blank=True)
    stripe_invoice_number = models.CharField(max_length=255, blank=True)
    stripe_hosted_invoice_url = models.URLField(max_length=500, blank=True)
    stripe_invoice_pdf_url = models.URLField(max_length=500, blank=True)
    stripe_invoice_status = models.CharField(max_length=32, blank=True)
    stripe_invoice_created_at = models.DateTimeField(null=True, blank=True)
    stripe_invoice_finalized_at = models.DateTimeField(null=True, blank=True)
    stripe_marked_paid_out_of_band_at = models.DateTimeField(null=True, blank=True)
    stripe_marked_paid_out_of_band_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="realestate_invoices_marked_paid_out_of_band",
    )
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True)
    stripe_checkout_url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("enquiry", "invoice_type"),
                condition=(
                    ~Q(status="void")
                    & Q(invoice_type__in=("deposit", "balance", "full"))
                ),
                name="uniq_active_re_invoice_type",
            ),
            models.UniqueConstraint(
                fields=("stripe_checkout_session_id",),
                condition=~Q(stripe_checkout_session_id=""),
                name="uniq_re_invoice_checkout_session",
            ),
        ]

    @property
    def amount_paid(self):
        total = Decimal("0.00")
        for payment in self.payments.filter(status=RealEstatePayment.Status.SUCCEEDED):
            if payment.reversal_status in {
                RealEstatePayment.ReversalStatus.REFUNDED,
                RealEstatePayment.ReversalStatus.DISPUTED,
                RealEstatePayment.ReversalStatus.CHARGEBACK,
            }:
                continue
            total += max(payment.amount - payment.reversed_amount, Decimal("0.00"))
        return total

    @property
    def amount_outstanding(self):
        return max(self.total - self.amount_paid, Decimal("0.00"))

    def save(self, *args, **kwargs):
        if self.pk:
            previous = RealEstateInvoice.objects.filter(pk=self.pk).values(
                "status", "currency", "subtotal", "vat_rate", "vat_amount", "total",
                "customer_name_snapshot", "company_name_snapshot",
                "customer_email_snapshot", "customer_phone_snapshot",
                "property_reference_snapshot", "job_reference_snapshot",
            ).first()
            if previous and previous["status"] != self.Status.DRAFT:
                immutable = tuple(key for key, value in previous.items() if key != "status" and value != getattr(self, key))
                if immutable:
                    raise ValidationError(f"Issued invoice fields are immutable: {', '.join(immutable)}")
        self.currency = str(self.currency or "EUR").upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.invoice_number


class RealEstatePayment(models.Model):
    class Method(models.TextChoices):
        STRIPE_DEPOSIT_CHECKOUT = "stripe_deposit_checkout", "Stripe deposit Checkout"
        STRIPE_BALANCE_CHECKOUT = "stripe_balance_checkout", "Stripe balance Checkout"
        STRIPE_INVOICE = "stripe_invoice", "Stripe invoice"
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
        VOID = "void", "Void"

    class ReversalStatus(models.TextChoices):
        NONE = "none", "None"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
        REFUNDED = "refunded", "Refunded"
        DISPUTED = "disputed", "Disputed"
        CHARGEBACK = "chargeback", "Chargeback"

    invoice = models.ForeignKey(
        RealEstateInvoice, on_delete=models.PROTECT, related_name="payments"
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    currency = models.CharField(max_length=3, default="EUR")
    method = models.CharField(max_length=32, choices=Method.choices)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    reversal_status = models.CharField(
        max_length=24,
        choices=ReversalStatus.choices,
        default=ReversalStatus.NONE,
    )
    reversed_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reference = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True)
    stripe_charge_id = models.CharField(max_length=255, blank=True)
    external_reference = models.CharField(max_length=255, blank=True)
    cash_receipt_number = models.CharField(max_length=20, blank=True)
    bank_lodgement_reference = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="realestate_payments_recorded"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="re_payment_amount_positive"),
            models.CheckConstraint(
                check=Q(reversed_amount__gte=0),
                name="re_payment_reversed_amount_nonnegative",
            ),
            models.UniqueConstraint(fields=("stripe_checkout_session_id",), condition=~Q(stripe_checkout_session_id=""), name="uniq_re_payment_checkout_session"),
            models.UniqueConstraint(fields=("stripe_payment_intent_id",), condition=~Q(stripe_payment_intent_id=""), name="uniq_re_payment_intent"),
            models.UniqueConstraint(fields=("cash_receipt_number",), condition=~Q(cash_receipt_number=""), name="uniq_re_cash_receipt"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = RealEstatePayment.objects.filter(pk=self.pk).first()
            if previous and previous.status == self.Status.SUCCEEDED:
                protected = ("invoice_id", "amount", "currency", "method", "status", "paid_at", "stripe_checkout_session_id", "stripe_payment_intent_id", "stripe_charge_id", "external_reference", "cash_receipt_number", "recorded_by_id")
                if any(getattr(previous, field) != getattr(self, field) for field in protected):
                    raise ValidationError("Successful payments cannot be edited; record a refund or void transaction.")
        self.currency = str(self.currency or "EUR").upper()
        if self.reversed_amount > self.amount:
            raise ValidationError("A payment reversal cannot exceed the payment amount.")
        if self.reversal_status == self.ReversalStatus.NONE and self.reversed_amount:
            raise ValidationError("A reversed amount requires a reversal status.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.Status.SUCCEEDED:
            raise ValidationError("Successful payments cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.get_method_display()} {self.amount} {self.currency}"


class RealEstateDeliveryOverride(models.Model):
    enquiry = models.ForeignKey(
        RealEstateEnquiry, on_delete=models.PROTECT, related_name="delivery_overrides"
    )
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="realestate_delivery_overrides_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="realestate_delivery_overrides_revoked"
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=("enquiry",), condition=Q(revoked_at__isnull=True), name="uniq_active_re_delivery_override")
        ]

    @property
    def is_active(self):
        return self.revoked_at is None


class RealEstateDelivery(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"
        ARCHIVED = "archived", "Archived"

    enquiry = models.OneToOneField(
        RealEstateEnquiry,
        on_delete=models.PROTECT,
        related_name="portal_delivery",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    public_title = models.CharField(
        max_length=160,
        help_text="Use a privacy-safe title; do not include an exact address or Eircode.",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    portal_enabled = models.BooleanField(
        default=False,
        help_text="Selectively enables portal delivery when the global feature flag is on.",
    )
    available_from = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="realestate_deliveries_revoked",
    )
    revocation_reason = models.TextField(blank=True)
    licence_summary = models.TextField(blank=True)
    download_instructions = models.TextField(blank=True)
    feature_version = models.PositiveSmallIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="realestate_deliveries_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        permissions = [
            ("activate_realestatedelivery", "Can activate real estate delivery"),
            ("revoke_realestatedelivery", "Can revoke real estate delivery"),
            ("archive_realestatedelivery", "Can archive real estate delivery"),
        ]

    def clean(self):
        super().clean()
        if self.status == self.Status.ACTIVE:
            if not self.available_from or not self.expires_at:
                raise ValidationError("Active deliveries require availability and expiry times.")
            if self.expires_at <= self.available_from:
                raise ValidationError("Delivery expiry must be after availability.")
        if self.status == self.Status.REVOKED and (
            not self.revoked_at or not str(self.revocation_reason or "").strip()
        ):
            raise ValidationError("Revoked deliveries require a timestamp and reason.")

    def __str__(self):
        return self.public_title


class RealEstateDeliveryRecipient(models.Model):
    class Role(models.TextChoices):
        COMMISSIONING_CLIENT = "commissioning_client", "Commissioning client"
        AGENT = "agent", "Agent"
        VENDOR = "vendor", "Vendor"
        PAYER = "payer", "Payer"
        OTHER = "other", "Other"

    delivery = models.ForeignKey(
        RealEstateDelivery,
        on_delete=models.CASCADE,
        related_name="recipients",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    email = models.EmailField()
    display_name = models.CharField(max_length=160)
    role = models.CharField(max_length=24, choices=Role.choices)
    token_salt = models.UUIDField(default=uuid.uuid4, editable=False)
    token_version = models.PositiveIntegerField(default=1)
    token_created_at = models.DateTimeField(auto_now_add=True)
    token_rotated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="realestate_delivery_recipients_revoked",
    )
    revocation_reason = models.TextField(blank=True)
    first_accessed_at = models.DateTimeField(null=True, blank=True)
    last_accessed_at = models.DateTimeField(null=True, blank=True)
    first_download_url_issued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display_name", "email")
        constraints = [
            models.UniqueConstraint(
                fields=("delivery", "email"),
                condition=Q(revoked_at__isnull=True),
                name="uniq_active_re_delivery_recipient_email",
            ),
            models.CheckConstraint(
                check=Q(token_version__gt=0),
                name="re_delivery_recipient_token_version_positive",
            ),
        ]
        permissions = [
            ("rotate_realestatedeliveryrecipient", "Can rotate recipient delivery link"),
            ("revoke_realestatedeliveryrecipient", "Can revoke delivery recipient"),
        ]

    def clean(self):
        super().clean()
        self.email = str(self.email or "").strip().lower()
        if self.revoked_at and not str(self.revocation_reason or "").strip():
            raise ValidationError("Revoked recipients require a reason.")

    def save(self, *args, **kwargs):
        self.email = str(self.email or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.display_name} ({self.get_role_display()})"


class RealEstateDeliverable(models.Model):
    class Category(models.TextChoices):
        PHOTOGRAPHS = "photographs", "Photographs"
        MAIN_VIDEO = "main_video", "Main video"
        SOCIAL_VIDEO = "social_video", "Social video"
        FLOOR_PLAN = "floor_plan", "Floor plan"
        ARCHIVE = "archive", "ZIP / archive"
        OTHER = "other", "Other"

    delivery = models.ForeignKey(
        RealEstateDelivery,
        on_delete=models.CASCADE,
        related_name="deliverables",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    category = models.CharField(max_length=24, choices=Category.choices)
    display_name = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255)
    object_key = models.CharField(max_length=500, unique=True)
    file_size = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=100)
    checksum_algorithm = models.CharField(max_length=32, blank=True)
    checksum_value = models.CharField(max_length=255, blank=True)
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=False)
    available_at = models.DateTimeField(null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="realestate_deliverables_uploaded",
    )
    replaces = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="replacements",
    )
    replaced_at = models.DateTimeField(null=True, blank=True)
    deletion_eligible_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "created_at")
        constraints = [
            models.CheckConstraint(check=Q(file_size__gt=0), name="re_deliverable_size_positive"),
            models.CheckConstraint(check=Q(version__gt=0), name="re_deliverable_version_positive"),
        ]

    def __str__(self):
        return self.display_name


class RealEstateDeliveryUploadSession(models.Model):
    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"
        COMPLETING = "completing", "Completing"
        COMPLETED = "completed", "Completed"
        ABORTING = "aborting", "Aborting"
        ABORTED = "aborted", "Aborted"
        FAILED = "failed", "Failed"

    delivery = models.ForeignKey(
        RealEstateDelivery,
        on_delete=models.CASCADE,
        related_name="upload_sessions",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="realestate_delivery_upload_sessions",
    )
    original_filename = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255)
    category = models.CharField(max_length=24, choices=RealEstateDeliverable.Category.choices)
    replaces = models.ForeignKey(
        RealEstateDeliverable,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="replacement_upload_sessions",
    )
    object_key = models.CharField(max_length=500, unique=True)
    upload_id = models.CharField(max_length=1024, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INITIATED)
    expected_size = models.PositiveBigIntegerField()
    expected_mime_type = models.CharField(max_length=100)
    part_size = models.PositiveBigIntegerField()
    sort_order = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    aborted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("created_by", "status"),
                name="redelupl_creator_status_idx",
            ),
            models.Index(
                fields=("delivery", "status"),
                name="redelupl_delivery_status_idx",
            ),
        ]


class RealEstateDeliveryEmailAttempt(models.Model):
    class Kind(models.TextChoices):
        INITIAL = "initial", "Initial"
        RESEND = "resend", "Resend"
        EXPIRY_REMINDER = "expiry_reminder", "Expiry reminder"
        EXTENSION = "extension", "Extension"
        REPLACEMENT = "replacement", "Replacement"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    recipient = models.ForeignKey(
        RealEstateDeliveryRecipient,
        on_delete=models.PROTECT,
        related_name="email_attempts",
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    idempotency_key = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    provider_message_id = models.CharField(max_length=255, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=64, blank=True)
    failure_message = models.CharField(max_length=255, blank=True)
    content_version = models.CharField(max_length=32, default="1")

    class Meta:
        ordering = ("-attempted_at",)


class RealEstateDeliveryAccessEvent(models.Model):
    class EventType(models.TextChoices):
        SESSION_ACCESSED = "session_accessed", "Page/session accessed"
        DOWNLOAD_URL_ISSUED = "download_url_issued", "Download URL issued"
        ACCESS_DENIED = "access_denied", "Access denied"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"
        UPLOAD_COMPLETED = "upload_completed", "Upload completed"
        FILE_REPLACED = "file_replaced", "File replaced"
        CLEANUP_SUCCEEDED = "cleanup_succeeded", "Cleanup succeeded"
        CLEANUP_FAILED = "cleanup_failed", "Cleanup failed"

    delivery = models.ForeignKey(
        RealEstateDelivery,
        on_delete=models.PROTECT,
        related_name="access_events",
    )
    recipient = models.ForeignKey(
        RealEstateDeliveryRecipient,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="access_events",
    )
    deliverable = models.ForeignKey(
        RealEstateDeliverable,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="access_events",
    )
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

