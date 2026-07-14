from django.conf import settings
from django.db import models


class RealEstateEnquiry(models.Model):
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

