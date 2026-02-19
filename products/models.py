import uuid
from decimal import Decimal
from django.utils import timezone
from datetime import timedelta
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

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
    """Model for the main design/photo asset."""
    title = models.CharField(max_length=254)
    description = models.TextField()
    collection = models.CharField(max_length=100)
    
    # Images
    preview_image = models.ImageField(upload_to="previews/photos/")
    high_res_file = models.FileField(upload_to="digital_products/photos/")
    
    # Digital Pricing (Legacy/Download options)
    price_hd = models.DecimalField(max_digits=6, decimal_places=2)
    price_4k = models.DecimalField(max_digits=6, decimal_places=2)
    
    tags = models.CharField(max_length=254, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class Video(models.Model):
    """Model for digital video products"""
    title = models.CharField(max_length=254)
    description = models.TextField()
    collection = models.CharField(max_length=100)
    thumbnail_image = models.ImageField(upload_to="previews/videos/")
    video_file = models.FileField(upload_to="digital_products/videos/")
    price_hd = models.DecimalField(max_digits=6, decimal_places=2)
    price_4k = models.DecimalField(max_digits=6, decimal_places=2)
    duration = models.PositiveIntegerField(help_text="Duration in seconds", null=True, blank=True)
    resolution = models.CharField(
        max_length=50, 
        help_text="e.g. 3840x2160 (4K)", 
        null=True, 
        blank=True
    )
    frame_rate = models.CharField(
        max_length=20, 
        help_text="e.g. 24fps, 60fps", 
        null=True, 
        blank=True
    )
    tags = models.CharField(max_length=254, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

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
    Automatically create ProductVariants for a new Photo based on active PrintTemplates.
    """
    if created:
        templates = PrintTemplate.objects.filter(is_active=True)
        
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
                    price=t.base_price,
                    sku=sku
                )
            )
        
        if variants_to_create:
            ProductVariant.objects.bulk_create(variants_to_create)
