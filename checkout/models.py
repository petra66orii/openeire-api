import uuid

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django_countries.fields import CountryField
from django.db import models
from django.db.models import Q

from products.models import PrintTemplate
from userprofiles.models import UserProfile


class CheckoutAttempt(models.Model):
    checkout_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    payment_intent_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
    )
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checkout_attempts",
    )
    request_fingerprint = models.CharField(max_length=64)
    cart_snapshot = models.JSONField(default=list)
    pricing_snapshot = models.JSONField(default=list)
    shipping_details_snapshot = models.JSONField(default=dict, blank=True)
    shipping_method = models.CharField(max_length=20, default="budget")
    customer_email = models.EmailField(max_length=254)
    save_info = models.BooleanField(default=False)
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    free_shipping_applied = models.BooleanField(default=False)
    discount_code = models.CharField(max_length=50, blank=True, default="")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount_label = models.CharField(max_length=100, blank=True, default="")
    expected_amount_cents = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="eur")
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_version = models.CharField(max_length=80, blank=True, default="")
    privacy_accepted_at = models.DateTimeField(null=True, blank=True)
    privacy_version = models.CharField(max_length=80, blank=True, default="")
    personal_use_accepted_at = models.DateTimeField(null=True, blank=True)
    personal_terms_version = models.CharField(max_length=80, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.checkout_key)


class Order(models.Model):
    CONFIRMATION_EMAIL_STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("SENDING", "Sending"),
        ("SENT", "Sent"),
        ("FAILED", "Failed"),
    ]

    SHIPPING_METHOD_CHOICES = [
        ("budget", "Budget"),
        ("standard", "Standard"),
        ("express", "Express"),
    ]

    order_number = models.CharField(max_length=32, null=False, editable=False)
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    checkout_attempt = models.OneToOneField(
        CheckoutAttempt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order",
    )

    first_name = models.CharField(max_length=150, null=True, blank=True)
    email = models.EmailField(max_length=254, null=False, blank=False)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    street_address1 = models.CharField(max_length=255, null=True, blank=True)
    street_address2 = models.CharField(max_length=255, null=True, blank=True)
    town = models.CharField(max_length=100, null=True, blank=True)
    county = models.CharField(max_length=100, null=True, blank=True)
    postcode = models.CharField(max_length=20, null=True, blank=True)
    country = CountryField(null=True, blank=True)
    date = models.DateTimeField(auto_now_add=True)
    personal_terms_version = models.CharField(
        max_length=80,
        null=True,
        blank=True,
        help_text="Personal terms version captured for consumer digital downloads.",
    )
    shipping_method = models.CharField(
        max_length=20,
        choices=SHIPPING_METHOD_CHOICES,
        default="budget",
        help_text="The shipping speed selected by the customer",
    )
    delivery_cost = models.DecimalField(max_digits=6, decimal_places=2, null=False, default=0)
    order_total = models.DecimalField(max_digits=10, decimal_places=2, null=False, default=0)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, null=False, default=0)
    discount_code = models.CharField(max_length=50, blank=True, default="")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=False, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, null=False, default=0)
    discount_label = models.CharField(max_length=100, blank=True, default="")
    stripe_pid = models.CharField(max_length=254, null=False, blank=False, default="")
    prodigi_order_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    prodigi_status = models.CharField(max_length=64, null=True, blank=True)
    prodigi_submission_started_at = models.DateTimeField(null=True, blank=True)
    fulfilment_hold_reason = models.CharField(max_length=100, blank=True, default="")
    prodigi_shipments = models.JSONField(default=list, blank=True)
    prodigi_last_callback_at = models.DateTimeField(null=True, blank=True)
    prodigi_last_polled_at = models.DateTimeField(null=True, blank=True)
    tracking_email_sent_at = models.DateTimeField(null=True, blank=True)
    tracking_email_signature = models.CharField(max_length=64, null=True, blank=True)
    confirmation_email_status = models.CharField(
        max_length=20,
        choices=CONFIRMATION_EMAIL_STATUS_CHOICES,
        default="PENDING",
    )
    confirmation_email_sent_at = models.DateTimeField(null=True, blank=True)
    confirmation_email_failed_at = models.DateTimeField(null=True, blank=True)
    confirmation_email_error = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("stripe_pid",),
                condition=~Q(stripe_pid=""),
                name="uniq_order_nonempty_stripe_pid",
            )
        ]

    def _generate_order_number(self):
        return uuid.uuid4().hex.upper()

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.order_number


class OrderItem(models.Model):
    order = models.ForeignKey(Order, null=False, blank=False, on_delete=models.CASCADE, related_name="items")
    quantity = models.IntegerField(null=False, blank=False, default=1)
    item_total = models.DecimalField(max_digits=10, decimal_places=2, null=False, blank=False)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    product = GenericForeignKey("content_type", "object_id")

    details = models.JSONField(null=True, blank=True)

    def __str__(self):
        return f"Item for order {self.order.order_number}"


class ProductShipping(models.Model):
    product = models.ForeignKey(PrintTemplate, on_delete=models.CASCADE, related_name="shipping_costs")

    COUNTRY_CHOICES = [
        ("IE", "Ireland"),
        ("US", "United States"),
    ]
    country = models.CharField(max_length=2, choices=COUNTRY_CHOICES)

    METHOD_CHOICES = [
        ("budget", "Budget"),
        ("standard", "Standard"),
        ("express", "Express"),
    ]
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    cost = models.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        unique_together = ("product", "country", "method")

    def __str__(self):
        return f"Ship {self.product} to {self.country} ({self.method}): EUR {self.cost}"


class DiscountRedemption(models.Model):
    email = models.EmailField(max_length=254)
    normalized_email = models.EmailField(max_length=254)
    code = models.CharField(max_length=50)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="discount_redemption")
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("normalized_email", "code"),
                name="uniq_discount_redemption_email_code",
            )
        ]

    def save(self, *args, **kwargs):
        self.email = str(self.email or "").strip()
        self.normalized_email = str(self.normalized_email or self.email or "").strip().lower()
        self.code = str(self.code or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} redeemed by {self.normalized_email}"
