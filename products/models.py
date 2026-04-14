import re
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from datetime import timedelta
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.files.storage import default_storage
from django.utils.html import strip_tags
from django.conf import settings

from .storage import PrivateAssetStorage

AI_DRAFT_MAX_CHARS = 8000
CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')

def sanitize_free_text(value, max_len):
    if value is None:
        return None
    text = strip_tags(str(value))
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = CONTROL_CHARS_RE.sub('', text).strip()
    if max_len and len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def normalize_email(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    return text

class GalleryAccess(models.Model):
    """
    Stores temporary access codes for the Digital Gallery.
    Codes are valid for 30 days.
    """
    email = models.EmailField()
    access_code = models.CharField(max_length=8, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if not self.access_code:
            # Generate a readable 8-char code (e.g., A1B2C3D4)
            self.access_code = uuid.uuid4().hex[:8].upper()
        if not self.expires_at:
            # Set expiration to 30 days from now
            self.expires_at = timezone.now() + timedelta(days=30)
        super().save(*args, **kwargs)

    @property
    def is_valid(self):
        return timezone.now() < self.expires_at

    def __str__(self):
        return f"{self.email} ({self.access_code})"
    
class Photo(models.Model):
    title = models.CharField(max_length=254)
    description = models.TextField()
    collection = models.CharField(max_length=100)
    
    # PUBLIC: Stays in the default public bucket
    preview_image = models.ImageField(upload_to="previews/photos/")
    
    # PRIVATE: Uploads exclusively to the Private Vault!
    high_res_file = models.FileField(
        upload_to="digital_products/photos/", 
        storage=PrivateAssetStorage()
    )
    
    price = models.DecimalField(max_digits=6, decimal_places=2)
    tags = models.CharField(max_length=254, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    is_printable = models.BooleanField(default=False)

    def __str__(self):
        return self.title


class Video(models.Model):
    title = models.CharField(max_length=254)
    description = models.TextField()
    collection = models.CharField(max_length=100)
    
    # PUBLIC: Stays in the default public bucket
    thumbnail_image = models.ImageField(upload_to="previews/videos/")
    
    # PRIVATE: Uploads exclusively to the Private Vault!
    video_file = models.FileField(
        upload_to="digital_products/videos/", 
        storage=PrivateAssetStorage(),
        blank=True,
    )
    video_file_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="Existing private R2 object key, e.g. digital_products/videos/my-video.mp4",
    )
    preview_video_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="Public preview clip object key, e.g. previews/videos/my-video-preview.mp4",
    )
    
    price = models.DecimalField(max_digits=6, decimal_places=2)
    duration = models.PositiveIntegerField(help_text="Duration in seconds", null=True, blank=True)
    resolution = models.CharField(max_length=50, help_text="e.g. 3840x2160 (4K)", null=True, blank=True)
    frame_rate = models.CharField(max_length=20, help_text="e.g. 24fps, 60fps", null=True, blank=True)
    tags = models.CharField(max_length=254, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def clean(self):
        super().clean()
        self.video_file_key = (self.video_file_key or "").strip()
        self.preview_video_key = (self.preview_video_key or "").strip()
        if not self.video_file and not self.video_file_key:
            raise ValidationError(
                {
                    "video_file": "Upload a video file or provide an existing R2 object key.",
                    "video_file_key": "Upload a video file or provide an existing R2 object key.",
                }
            )

    @property
    def video_asset_name(self):
        if self.video_file and self.video_file.name:
            try:
                if self.video_file.storage.exists(self.video_file.name):
                    return self.video_file.name
            except Exception:
                return self.video_file.name
        return self.video_file_key or ""

    @property
    def video_asset_filename(self):
        if not self.video_asset_name:
            return ""
        return Path(self.video_asset_name).name

    @property
    def preview_video_url(self):
        key = (self.preview_video_key or "").strip()
        if not key:
            return ""
        if key.startswith(("http://", "https://")):
            return key
        try:
            return default_storage.url(key)
        except Exception:
            media_url = getattr(settings, "MEDIA_URL", "/media/")
            return urljoin(f"{media_url.rstrip('/')}/", key.lstrip("/"))

    def open_video_asset(self, mode="rb"):
        if self.video_file and self.video_file.name:
            try:
                self.video_file.open(mode)
                return self.video_file
            except Exception:
                pass
        if self.video_file_key:
            return PrivateAssetStorage().open(self.video_file_key, mode)
        return None

    def __str__(self):
        return self.title


class VideoUploadSession(models.Model):
    MAX_UPLOAD_ID_LENGTH = 1024

    PURPOSE_MASTER = "master"
    PURPOSE_PREVIEW = "preview"
    PURPOSE_CHOICES = [
        (PURPOSE_MASTER, "Master"),
        (PURPOSE_PREVIEW, "Preview"),
    ]

    STATUS_INITIATED = "initiated"
    STATUS_COMPLETED = "completed"
    STATUS_ABORTED = "aborted"
    STATUS_FAILED = "failed"
    STATUS_COMPLETING = "completing"
    STATUS_ABORTING = "aborting"
    STATUS_CHOICES = [
        (STATUS_INITIATED, "Initiated"),
        (STATUS_COMPLETING, "Completing"),
        (STATUS_ABORTING, "Aborting"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_ABORTED, "Aborted"),
        (STATUS_FAILED, "Failed"),
    ]

    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="video_upload_sessions",
    )
    target_video = models.ForeignKey(
        Video,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="upload_sessions",
    )
    original_filename = models.CharField(max_length=255)
    object_key = models.CharField(max_length=500, unique=True)
    upload_id = models.CharField(max_length=MAX_UPLOAD_ID_LENGTH, unique=True)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_INITIATED,
    )
    file_size = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=100)
    part_size = models.PositiveBigIntegerField()
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    aborted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_by", "status"], name="vidupl_creator_status_idx"),
            models.Index(fields=["purpose", "status"], name="vidupl_purpose_status_idx"),
        ]

    def __str__(self):
        return f"{self.original_filename} ({self.purpose}, {self.status})"
    
class LicenseRequest(models.Model):
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('SUBMITTED', 'Submitted'),
        ('NEEDS_INFO', 'Needs Info'),
        ('APPROVED', 'Approved'),
        ('PAYMENT_PENDING', 'Payment Pending'),
        ('PAID', 'Paid'),
        ('DELIVERED', 'Delivered'),
        ('EXPIRED', 'Expired'),
        ('REVOKED', 'Revoked'),
        ('REJECTED', 'Rejected'),
    ]
    
    PROJECT_TYPE_CHOICES = [
        ('REAL_ESTATE', 'Real Estate / Property'),
        ('CORPORATE', 'Corporate / B2B'),
        ('EDITORIAL', 'Editorial / Documentary'),
        ('COMMERCIAL', 'Commercial / Advertising'),
        ('OTHER', 'Other'),
    ]
    
    DURATION_CHOICES = [
        ('1_MONTH', '1 Month'),
        ('3_MONTHS', '3 Months'),
        ('6_MONTHS', '6 Months'),
        ('1_YEAR', '1 Year'),
        ('2_YEARS', '2 Years'),
        ('5_YEARS', '5 Years'),
        ('PERPETUAL', 'Perpetual / Lifetime'),
        ('OTHER', 'Other'),
    ]

    TERRITORY_CHOICES = [
        ('IRELAND', 'Ireland Only'),
        ('EU', 'EU / UK'),
        ('US_NA', 'US / North America'),
        ('SOUTH_AMERICA', 'South America'),
        ('ASIA', 'Asia'),
        ('AFRICA', 'Africa'),
        ('OCEANIA', 'Oceania'),
        ('WORLDWIDE', 'Worldwide'),
    ]

    MEDIA_CHOICES = [
        ('WEB_SOCIAL', 'Web & Organic Social Only'),
        ('PAID_DIGITAL', 'Paid Digital Ads'),
        ('PRINT_BROCHURE', 'Print & Brochure'),
        ('BROADCAST', 'TV / Broadcast / Cinema'),
        ('ALL_MEDIA', 'All Media'),
    ]

    EXCLUSIVITY_CHOICES = [
        ('NON_EXCLUSIVE', 'Non-Exclusive'),
        ('CATEGORY', 'Category Exclusive'),
        ('FULL', 'Fully Exclusive'),
    ]

    # 👇 Generic Foreign Key to link to either Photo or Video
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        limit_choices_to={'model__in': ('photo', 'video')},
        db_index=True
    )
    object_id = models.PositiveBigIntegerField(db_index=True)
    asset = GenericForeignKey('content_type', 'object_id')

    # Client Details
    client_name = models.CharField(max_length=255)
    company = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField()
    
    # Request Details
    project_type = models.CharField(max_length=50, choices=PROJECT_TYPE_CHOICES)
    duration = models.CharField(max_length=50, choices=DURATION_CHOICES)
    message = models.TextField(blank=True, null=True, max_length=2000)
    territory = models.CharField(max_length=20, choices=TERRITORY_CHOICES, default='IRELAND', null=True, blank=True)
    permitted_media = models.CharField(max_length=20, choices=MEDIA_CHOICES, default='WEB_SOCIAL', null=True, blank=True)
    exclusivity = models.CharField(max_length=20, choices=EXCLUSIVITY_CHOICES, default='NON_EXCLUSIVE', null=True, blank=True)
    reach_caps = models.CharField(max_length=255, default='NONE', null=True, blank=True)
    
    # Tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SUBMITTED')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)

    ai_draft_response = models.TextField(
        blank=True,
        null=True,
        max_length=AI_DRAFT_MAX_CHARS,
        help_text="AI generated draft response"
    )
    quoted_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    stripe_payment_link = models.URLField(blank=True, null=True)
    stripe_payment_link_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)

    _status_change_actor = None
    _status_change_note = ""
    _status_change_metadata = None

    def set_status_change_context(self, *, actor=None, note="", metadata=None):
        self._status_change_actor = actor
        self._status_change_note = note or ""
        self._status_change_metadata = metadata or {}

    def transition_to(self, to_status, *, actor=None, note="", metadata=None):
        if self.status == to_status:
            return False
        self.set_status_change_context(actor=actor, note=note, metadata=metadata)
        self.status = to_status
        self.save(update_fields=['status', 'updated_at'])
        return True

    def add_audit_note(self, note, *, actor=None, metadata=None):
        if not note:
            return
        LicenseRequestAuditLog.objects.create(
            license_request=self,
            from_status=self.status,
            to_status=self.status,
            changed_by=actor,
            note=note,
            metadata=metadata or {},
        )

    def save(self, *args, **kwargs):
        old_status = None
        if self.pk:
            old_status = (
                LicenseRequest.objects.filter(pk=self.pk)
                .values_list('status', flat=True)
                .first()
            )
        if self.email is not None:
            self.email = normalize_email(self.email)
        if self.message is not None:
            self.message = sanitize_free_text(self.message, 2000)
        if self.reach_caps is not None:
            self.reach_caps = sanitize_free_text(self.reach_caps, 255)
        try:
            super().save(*args, **kwargs)
            if old_status != self.status:
                LicenseRequestAuditLog.objects.create(
                    license_request=self,
                    from_status=old_status,
                    to_status=self.status,
                    changed_by=self._status_change_actor,
                    note=self._status_change_note or "",
                    metadata=self._status_change_metadata or {},
                )
        finally:
            # Always clear per-save transition context to avoid leaking it
            # into a later, unrelated status change.
            self._status_change_actor = None
            self._status_change_note = ""
            self._status_change_metadata = None

    def __str__(self):
        return f"Request by {self.client_name} for {self.asset}"
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "License Request"
        verbose_name_plural = "License Requests"
        indexes = [
            models.Index(fields=['content_type', 'object_id'], name='license_asset_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                Lower('email'),
                F('content_type'),
                F('object_id'),
                condition=~Q(status__in=['REJECTED', 'REVOKED', 'EXPIRED']),
                name='uniq_license_request_active_ci',
            ),
        ]


class StripeWebhookEvent(models.Model):
    STATUS_CHOICES = [
        ('PROCESSING', 'Processing'),
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
    ]

    stripe_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=255)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-received_at']
        verbose_name = "Stripe Webhook Event"
        verbose_name_plural = "Stripe Webhook Events"

    def __str__(self):
        return f"{self.event_type} ({self.stripe_event_id})"


class LicenceOffer(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('SUPERSEDED', 'Superseded'),
        ('PAID', 'Paid'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    license_request = models.ForeignKey(
        LicenseRequest,
        on_delete=models.CASCADE,
        related_name='offers',
    )
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    scope_snapshot = models.JSONField(default=dict, blank=True)
    quoted_price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='EUR')
    terms_version = models.CharField(max_length=50)
    master_agreement_version = models.CharField(max_length=100, blank=True, null=True)
    stripe_product_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_price_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_payment_link_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    stripe_payment_link_url = models.URLField(blank=True, null=True)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    superseded_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['license_request', 'version'],
                name='uniq_licence_offer_version',
            ),
        ]

    def __str__(self):
        return f"Offer v{self.version} for LicenseRequest {self.license_request_id}"


class LicenseRequestAuditLog(models.Model):
    license_request = models.ForeignKey(
        LicenseRequest,
        on_delete=models.CASCADE,
        related_name='audit_logs',
    )
    from_status = models.CharField(max_length=20, blank=True, null=True)
    to_status = models.CharField(max_length=20, blank=True, null=True)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True)
    note = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']
        verbose_name = "License Request Audit Log"
        verbose_name_plural = "License Request Audit Logs"

    def __str__(self):
        return (
            f"LicenseRequest {self.license_request_id}: "
            f"{self.from_status or '-'} -> {self.to_status or '-'}"
        )


class LicenceDocument(models.Model):
    DOC_TYPE_CHOICES = [
        ('SCHEDULE', 'Appendix A - Licence Schedule'),
        ('CERTIFICATE', 'Appendix B - Licence Certificate'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    license_request = models.ForeignKey(
        LicenseRequest,
        on_delete=models.CASCADE,
        related_name='licence_documents'
    )
    doc_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES)
    file = models.FileField(
        upload_to="licences/documents/",
        storage=PrivateAssetStorage()
    )
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Licence Document"
        verbose_name_plural = "Licence Documents"

    def __str__(self):
        return f"{self.get_doc_type_display()} for {self.license_request_id}"


class LicenceDeliveryToken(models.Model):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    license_request = models.ForeignKey(
        LicenseRequest,
        on_delete=models.CASCADE,
        related_name='delivery_tokens'
    )
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Licence Delivery Token"
        verbose_name_plural = "Licence Delivery Tokens"

    @property
    def is_valid(self):
        now = timezone.now()
        return self.used_at is None and now < self.expires_at

    def __str__(self):
        return f"Token for LicenseRequest {self.license_request_id}"


class PersonalDownloadToken(models.Model):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order_item = models.ForeignKey(
        'checkout.OrderItem',
        on_delete=models.CASCADE,
        related_name='personal_download_tokens',
    )
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Personal Download Token"
        verbose_name_plural = "Personal Download Tokens"

    @property
    def is_valid(self):
        now = timezone.now()
        return self.used_at is None and now < self.expires_at

    def __str__(self):
        return f"Token for OrderItem {self.order_item_id}"

class ProductVariant(models.Model):
    """
    Specific physical versions of a Photo (e.g., A4 Canvas, Framed Print).
    Each variant has its own price and Prodigi SKU.
    """
    
    # Prodigi Material Codes (Expanded for realism)
    MATERIAL_CHOICES = [
        ('eco_canvas', 'Eco Canvas'),
        ('lustre_photo_paper', 'Lustre Photo Paper'),
        ('enhanced_matte_art_paper', 'Enhanced Matte Art Paper'),
        ('hahnemuhle_photo_rag', 'Hahnemuhle Photo Rag'),
    ]

    SIZE_CHOICES = [
        # Eco Canvas 
        ('12x18', '12x18" (30x45cm)'),
        ('16x24', '16x24" (40x60cm)'),
        ('20x30', '20x30" (50x75cm)'),

        # Lustre
        ('24x36', '24x36" (60x90cm)'),
        ('26x38', '26x38" (66x96cm)'),
        ('13x60', '13x60" (33x152cm)'),
        ('72x24', '72x24" (183x61cm)'),
        ('27x41', '27x41" (70x105cm)'),

        # Matte
        ('18x30', '18x30" (46x76cm)'),
        ('42x56', '42x56" (107x142cm)'),
        ('60x48', '60x48" (152x122cm)'),

        # Hahnemuhle
        ('16x32', '16x32" (40x80cm)'),
        ('24x40', '24x40" (60x100cm)'),
        ('28x40', '28x40" (70x100cm)'),
        
        # Note: Duplicate sizes across materials (like 20x30) are handled by the unique_together constraint
    ]

    photo = models.ForeignKey(Photo, on_delete=models.CASCADE, related_name="variants")
    material = models.CharField(max_length=30, choices=MATERIAL_CHOICES)
    size = models.CharField(max_length=20, choices=SIZE_CHOICES)
    price = models.DecimalField(max_digits=6, decimal_places=2)
    sku = models.CharField(max_length=254, null=True, blank=True, help_text="Internal SKU (e.g. PHOTO-1-CAN-A4)")
    prodigi_sku = models.CharField(max_length=50, blank=True, null=True, help_text="Prodigi SKU (e.g. GLOBAL-CAN-A4)")

    class Meta:
        unique_together = ('photo', 'material', 'size')
        ordering = ['material', 'size']

    def __str__(self):
        return f'{self.get_material_display()} - {self.get_size_display()} ({self.photo.title})'


class PrintTemplate(models.Model):
    MATERIAL_CHOICES = [
        ('eco_canvas', 'Eco Canvas'),
        ('lustre_photo_paper', 'Lustre Photo Paper'),
        ('enhanced_matte_art_paper', 'Enhanced Matte Art Paper'),
        ('hahnemuhle_photo_rag', 'Hahnemuhle Photo Rag'),
    ]

    # CORRECTED SIZES FROM PDF
    SIZE_CHOICES = [
        # Eco Canvas 
        ('12x18', '12x18" (30x45cm)'),
        ('16x24', '16x24" (40x60cm)'),
        ('20x30', '20x30" (50x75cm)'),

        # Lustre
        ('24x36', '24x36" (60x90cm)'),
        ('26x38', '26x38" (66x96cm)'),
        ('13x60', '13x60" (33x152cm)'),
        ('72x24', '72x24" (183x61cm)'),
        ('27x41', '27x41" (70x105cm)'),

        # Matte
        ('18x30', '18x30" (46x76cm)'),
        ('42x56', '42x56" (107x142cm)'),
        ('60x48', '60x48" (152x122cm)'),

        # Hahnemuhle
        ('16x32', '16x32" (40x80cm)'),
        ('24x40', '24x40" (60x100cm)'),
        ('28x40', '28x40" (70x100cm)'),
        
        # Note: Duplicate sizes across materials (like 20x30) are handled by the unique_together constraint
    ]

    material = models.CharField(max_length=50, choices=MATERIAL_CHOICES)
    size = models.CharField(max_length=20, choices=SIZE_CHOICES)
    
    # PRODUCTION COST ONLY (The "Item" column in your PDF)
    production_cost = models.DecimalField(max_digits=6, decimal_places=2, help_text="Cost to produce (Item Price)")
    prodigi_sku = models.CharField(max_length=50, blank=True, null=True, help_text="Prodigi Product Code")
    sku_suffix = models.CharField(max_length=50)

    class Meta:
        unique_together = ('material', 'size')

    def __str__(self):
        return f"{self.get_material_display()} - {self.size}"

    @property
    def retail_price(self):
        # Example logic: Production Cost * 2.5 Profit Margin
        # You can make the multiplier a setting or a field later
        return self.production_cost * Decimal('2.5')

class ProductReview(models.Model):
    """
    Model for a single product review. 
    Can be linked to a Photo (the design) or Video via GenericFK.
    """
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    product = GenericForeignKey('content_type', 'object_id')

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    rating = models.IntegerField(choices=RATING_CHOICES)
    comment = models.TextField(blank=True, null=True)
    approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    admin_reply = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = ('content_type', 'object_id', 'user')
        ordering = ['-created_at']

    def __str__(self):
        return f'Review by {self.user.username}'
    

@receiver(post_save, sender=Photo)
def generate_variants_for_photo(sender, instance, created, **kwargs):
    """
    Automatically create ProductVariants for a new printable Photo based on
    PrintTemplates.
    """
    if created and instance.is_printable:
        templates = PrintTemplate.objects.all()
        
        variants_to_create = []
        for t in templates:
            # Generate a unique SKU: "PHOTO-{ID}-{SUFFIX}"
            # e.g. "PHOTO-25-CAN-A4"
            sku = f"PHOTO-{instance.id}-{t.sku_suffix}"

            variants_to_create.append(
                ProductVariant(
                    photo=instance,
                    material=t.material,
                    size=t.size,
                    price=t.retail_price,
                    sku=sku,
                    prodigi_sku=t.prodigi_sku,
                )
            )
        
        if variants_to_create:
            ProductVariant.objects.bulk_create(variants_to_create)
