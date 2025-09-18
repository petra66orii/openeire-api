from django.db import models

# Create your models here.
class Photo(models.Model):
    """Model for digital photo products"""
    title = models.CharField(max_length=254)
    description = models.TextField()
    preview_image = models.ImageField(upload_to="previews/photos/")
    high_res_file = models.FileField(upload_to="digital_products/photos/")
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
    thumbnail_image = models.ImageField(upload_to="previews/videos/")
    video_file = models.FileField(upload_to="digital_products/videos/")
    price_hd = models.DecimalField(max_digits=6, decimal_places=2)
    price_4k = models.DecimalField(max_digits=6, decimal_places=2)
    tags = models.CharField(max_length=254, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class Product(models.Model):
    """Model for physical products like framed prints or canvases"""
    MATERIAL_CHOICES = [('canvas', 'Canvas'), ('framed', 'Framed Print')]
    SIZE_CHOICES = [('A4', 'A4'), ('A3', 'A3'), ('A2', 'A2')]

    photo = models.ForeignKey(Photo, on_delete=models.CASCADE, related_name="prints")
    material = models.CharField(max_length=10, choices=MATERIAL_CHOICES)
    size = models.CharField(max_length=5, choices=SIZE_CHOICES)
    price = models.DecimalField(max_digits=6, decimal_places=2)
    sku = models.CharField(max_length=254, null=True, blank=True)

    def __str__(self):
        return f'{self.get_material_display()} of "{self.photo.title}" ({self.size})'