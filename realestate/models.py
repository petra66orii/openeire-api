from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from decimal import Decimal


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
        "additional_stills": "Additional edited stills - EUR 10 per image",
        "floor_plan": "Floor plan, 2D measured - EUR 75",
        "rush_delivery": "Rush same-day delivery, stills only - EUR 75",
        "extended_drone_video": "Extended drone video, up to 3 minutes - EUR 150",
        "additional_social_cuts": "Additional social media cuts - EUR 50",
        "travel_supplement": "Travel supplement beyond 40 km - EUR 0.50 per km",
    }

    PACKAGE_SUMMARIES = {
        PreferredPackage.ESSENTIAL: "Essential - EUR 175 - 10 edited interior/exterior photos",
        PreferredPackage.STARTER: "Starter - EUR 229 - 20 edited interior/exterior photos + 5-8 aerial drone photos",
        PreferredPackage.PRO: "Pro - EUR 399 - 25 edited interior/exterior photos + 5-8 aerial drone photos + 60-90s 4K aerial drone video + social media cuts",
        PreferredPackage.PREMIUM: "Premium - EUR 579 - 30 edited interior/exterior photos + 5-8 aerial drone photos + aerial video + social media cuts + 3D interactive virtual tour",
        PreferredPackage.CUSTOM: "Custom - POA",
        PreferredPackage.NOT_SURE: "Not sure yet",
    }

    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=50)
    client_type = models.CharField(max_length=32, choices=ClientType.choices)
    property_address = models.TextField()
    county = models.CharField(max_length=100)
    property_type = models.CharField(max_length=100)
    preferred_package = models.CharField(max_length=32, choices=PreferredPackage.choices)
    consent_to_contact = models.BooleanField(default=False)

    company_name = models.CharField(max_length=255, blank=True)
    eircode = models.CharField(max_length=20, blank=True)
    add_ons = models.JSONField(default=list, blank=True)
    preferred_date = models.DateField(null=True, blank=True)
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
    internal_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    proposed_shoot_date = models.DateField(null=True, blank=True)
    booking_agreement_received = models.BooleanField(default=False)
    deposit_payment_link = models.URLField(max_length=500, blank=True)
    stripe_deposit_session_id = models.CharField(max_length=255, blank=True)
    deposit_paid = models.BooleanField(default=False)
    deposit_paid_at = models.DateTimeField(null=True, blank=True)
    booking_agreement_link = models.URLField(blank=True)
    # Delivery provider is metadata only. When the Client Portal ships, use
    # provider="portal" and delivery_link="https://app.openeire.ie/projects/<token>";
    # the Delivery email can keep using the same delivery_link CTA.
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
        return self.PACKAGE_SUMMARIES.get(self.preferred_package, self.get_preferred_package_display())

    def get_add_on_labels(self):
        return [self.ADD_ON_LABELS.get(key, key) for key in (self.add_ons or [])]

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
    reference_url = models.URLField(blank=True)
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
    TEMPLATE_VERSION = "1.4"

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
        value = self.payments.filter(status=RealEstatePayment.Status.SUCCEEDED).aggregate(
            total=Sum("amount")
        )["total"]
        return value or Decimal("0.00")

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

    invoice = models.ForeignKey(
        RealEstateInvoice, on_delete=models.PROTECT, related_name="payments"
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    currency = models.CharField(max_length=3, default="EUR")
    method = models.CharField(max_length=32, choices=Method.choices)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
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

