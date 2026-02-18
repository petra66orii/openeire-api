import uuid
from django.db import models
from django.db.models import Sum
from django.conf import settings

from userprofiles.models import UserProfile
from products.models import PrintTemplate
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django_countries.fields import CountryField

class Order(models.Model):

    SHIPPING_METHOD_CHOICES = [
        ('budget', 'Budget'),
        ('standard', 'Standard'),
        ('express', 'Express'),
    ]

    order_number = models.CharField(max_length=32, null=False, editable=False)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    
    # --- Make these fields optional ---
    first_name = models.CharField(max_length=150, null=True, blank=True)
    email = models.EmailField(max_length=254, null=False, blank=False) # Keep email required
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    street_address1 = models.CharField(max_length=255, null=True, blank=True)
    street_address2 = models.CharField(max_length=255, null=True, blank=True)
    town = models.CharField(max_length=100, null=True, blank=True)
    county = models.CharField(max_length=100, null=True, blank=True)
    postcode = models.CharField(max_length=20, null=True, blank=True)
    country = CountryField(null=True, blank=True)    
    date = models.DateTimeField(auto_now_add=True)
    shipping_method = models.CharField(
        max_length=20, 
        choices=SHIPPING_METHOD_CHOICES, 
        default='budget',
        help_text="The shipping speed selected by the customer"
    )
    delivery_cost = models.DecimalField(max_digits=6, decimal_places=2, null=False, default=0)
    order_total = models.DecimalField(max_digits=10, decimal_places=2, null=False, default=0)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, null=False, default=0)
    stripe_pid = models.CharField(max_length=254, null=False, blank=False, default='')

    def _generate_order_number(self):
        """
        Generate a random, unique order number using UUID.
        """
        return uuid.uuid4().hex.upper()

    def save(self, *args, **kwargs):
        """
        Override the original save method to set the order number
        if it hasn't been set already.
        """
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.order_number


class OrderItem(models.Model):
    order = models.ForeignKey(Order, null=False, blank=False, on_delete=models.CASCADE, related_name='items')
    quantity = models.IntegerField(null=False, blank=False, default=1)
    item_total = models.DecimalField(max_digits=10, decimal_places=2, null=False, blank=False)

    # Generic ForeignKey to link to a Photo, Video, or Product
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    product = GenericForeignKey('content_type', 'object_id')
    
    # Store details at time of purchase
    details = models.JSONField(null=True, blank=True) # e.g., {'quality': '4k'} or {'size': 'A4'}

    def __str__(self):
        return f"Item for order {self.order.order_number}"

class ProductShipping(models.Model):
    """
    Stores the EXACT shipping cost for a specific product to a specific country.
    This replaces generic 'Tiers'.
    """
    product = models.ForeignKey(PrintTemplate, on_delete=models.CASCADE, related_name='shipping_costs')
    
    COUNTRY_CHOICES = [
        ('IE', 'Ireland'),
        ('US', 'United States'),
    ]
    country = models.CharField(max_length=2, choices=COUNTRY_CHOICES)
    
    METHOD_CHOICES = [
        ('budget', 'Budget'),
        ('standard', 'Standard'),
        ('express', 'Express'),
    ]
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    
    # THE SHIPPING COST (The "Shipping" column in your PDF)
    cost = models.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        # Ensures we only have one price per product+country+method
        unique_together = ('product', 'country', 'method')

    def __str__(self):
        return f"Ship {self.product} to {self.country} ({self.method}): €{self.cost}"