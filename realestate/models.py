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

    ADD_ON_LABELS = {
        "additional_stills": "Additional edited stills - EUR 10+VAT per image",
        "floor_plan": "Floor plan, 2D measured - EUR 75+VAT",
        "rush_delivery": "Rush same-day delivery, stills only - EUR 75+VAT",
        "extended_drone_video": "Extended drone video, up to 3 minutes - EUR 150+VAT",
        "additional_social_cuts": "Additional social media cuts - EUR 50+VAT",
        "travel_supplement": "Travel supplement beyond 40 km - EUR 0.50+VAT per km",
    }

    PACKAGE_SUMMARIES = {
        PreferredPackage.ESSENTIAL: "EUR 175+VAT - 10 edited interior/exterior photos",
        PreferredPackage.STARTER: "EUR 229+VAT - 20 edited interior/exterior photos + 5-8 aerial drone photos",
        PreferredPackage.PRO: "EUR 399+VAT - 25 edited interior/exterior photos + 5-8 aerial drone photos + 60-90s 4K aerial drone video + social media cuts",
        PreferredPackage.PREMIUM: "EUR 579+VAT - 30 edited interior/exterior photos + 5-8 aerial drone photos + aerial video + social media cuts + 3D interactive virtual tour",
        PreferredPackage.CUSTOM: "POA",
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
    shoot_date = models.DateField(null=True, blank=True)
    internal_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    proposed_shoot_date = models.DateField(null=True, blank=True)
    booking_agreement_received = models.BooleanField(default=False)
    deposit_payment_link = models.URLField(blank=True)
    booking_agreement_link = models.URLField(blank=True)
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

