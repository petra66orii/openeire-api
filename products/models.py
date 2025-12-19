from django.db import models
from django.contrib.auth.models import User
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

class Photo(models.Model):
    """Model for the main design/photo asset."""
    title = models.CharField(max_length=254)
    description = models.TextField()
    collection = models.CharField(max_length=100, default='General')
    
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
    collection = models.CharField(max_length=100, default='General')
    thumbnail_image = models.ImageField(upload_to="previews/videos/")
    video_file = models.FileField(upload_to="digital_products/videos/")
    price_hd = models.DecimalField(max_digits=6, decimal_places=2)
    price_4k = models.DecimalField(max_digits=6, decimal_places=2)
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
        ('matte', 'Fine Art Print (Matte)'),
        ('gloss', 'Fine Art Print (Gloss)'),
        ('canvas', 'Premium Canvas'),
        ('framed', 'Framed Fine Art Print'),
    ]

    # Prodigi/Standard Sizes
    SIZE_CHOICES = [
        ('A4', 'A4 (210x297mm)'),
        ('A3', 'A3 (297x420mm)'),
        ('A2', 'A2 (420x594mm)'),
        ('12x16', '12x16"'),
        ('16x20', '16x20"'),
        ('18x24', '18x24"'),
        ('24x36', '24x36"'),
    ]

    # Link back to the parent Photo
    # related_name='variants' allows us to say: photo.variants.all()
    photo = models.ForeignKey(Photo, on_delete=models.CASCADE, related_name="variants")
    
    material = models.CharField(max_length=20, choices=MATERIAL_CHOICES)
    size = models.CharField(max_length=20, choices=SIZE_CHOICES)
    price = models.DecimalField(max_digits=6, decimal_places=2)
    
    # Critical for Prodigi Automation
    sku = models.CharField(max_length=254, null=True, blank=True, help_text="Prodigi SKU (e.g. GLOBAL-CAN-12x16)")

    class Meta:
        # Prevent creating the exact same variant twice for one photo
        unique_together = ('photo', 'material', 'size')
        ordering = ['material', 'size']

    def __str__(self):
        return f'{self.get_material_display()} - {self.get_size_display()} ({self.photo.title})'

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

    class Meta:
        unique_together = ('content_type', 'object_id', 'user')
        ordering = ['-created_at']

    def __str__(self):
        return f'Review by {self.user.username}'