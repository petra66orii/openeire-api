from django.contrib import admin
from .models import Order, OrderItem, ProductShipping
from openeire_api.admin import custom_admin_site

class OrderItemInline(admin.TabularInline):
    """
    Allows viewing of OrderItems from within the Order admin page.
    """
    model = OrderItem
    # Orders should be immutable records, so we make the items read-only
    readonly_fields = ('product', 'quantity', 'item_total')
    can_delete = False
    extra = 0 # Don't show extra empty forms

#@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Order model.
    """
    inlines = (OrderItemInline,)

    # Make all fields read-only in the detail view
    readonly_fields = ('order_number', 'user_profile', 'date', 'delivery_cost',
                       'order_total', 'total_price', 'stripe_pid', 'first_name',
                       'email', 'phone_number', 'country', 'postcode', 'town',
                       'street_address1', 'street_address2', 'county')

    # Configure the list view
    list_display = ('order_number', 'first_name', 'email', 'order_total', 'date')
    list_filter = ('date',)
    search_fields = ('order_number', 'email', 'first_name')
    ordering = ('-date',)

class ProductShippingAdmin(admin.ModelAdmin):
    list_display = ('product', 'country', 'method', 'cost')
    list_filter = ('country', 'method', 'product__material')
    search_fields = ('product__sku_suffix', 'product__material')
    list_editable = ('cost',)
    ordering = ('product', 'country', 'cost')

custom_admin_site.register(Order, OrderAdmin)
custom_admin_site.register(ProductShipping, ProductShippingAdmin)