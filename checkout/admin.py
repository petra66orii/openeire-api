from django.contrib import admin
from django.contrib import messages
from django.utils import timezone
from .models import Order, OrderItem, ProductShipping
from .emails import send_order_confirmation_email
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

    actions = ("retry_confirmation_emails",)

    # Make all fields read-only in the detail view
    readonly_fields = ('order_number', 'user_profile', 'date', 'delivery_cost',
                       'order_total', 'total_price', 'stripe_pid', 'first_name',
                       'email', 'phone_number', 'country', 'postcode', 'town',
                       'street_address1', 'street_address2', 'county',
                       'personal_terms_version', 'confirmation_email_status',
                       'confirmation_email_sent_at', 'confirmation_email_failed_at',
                       'confirmation_email_error')

    # Configure the list view
    list_display = (
        'order_number',
        'first_name',
        'email',
        'order_total',
        'date',
        'personal_terms_version',
        'confirmation_email_status',
        'confirmation_email_sent_at',
    )
    list_filter = ('date', 'confirmation_email_status')
    search_fields = ('order_number', 'email', 'first_name')
    ordering = ('-date',)

    @admin.action(description="Retry confirmation emails for selected orders")
    def retry_confirmation_emails(self, request, queryset):
        sent_count = 0
        failed_count = 0
        skipped_sent_count = 0

        for order in queryset:
            if order.confirmation_email_status == 'SENT':
                skipped_sent_count += 1
                continue
            try:
                send_order_confirmation_email(order, request=request)
                order.confirmation_email_status = 'SENT'
                order.confirmation_email_sent_at = timezone.now()
                order.confirmation_email_failed_at = None
                order.confirmation_email_error = ""
                sent_count += 1
            except Exception as exc:
                order.confirmation_email_status = 'FAILED'
                order.confirmation_email_failed_at = timezone.now()
                order.confirmation_email_error = f"{exc.__class__.__name__}: {exc}"
                failed_count += 1
            order.save(
                update_fields=[
                    'confirmation_email_status',
                    'confirmation_email_sent_at',
                    'confirmation_email_failed_at',
                    'confirmation_email_error',
                ]
            )

        if sent_count:
            self.message_user(
                request,
                f"Retried confirmation emails successfully for {sent_count} order(s).",
                level=messages.SUCCESS,
            )
        if failed_count:
            self.message_user(
                request,
                f"Confirmation email retry failed for {failed_count} order(s).",
                level=messages.ERROR,
            )
        if skipped_sent_count:
            self.message_user(
                request,
                f"Skipped {skipped_sent_count} order(s) already marked as sent.",
                level=messages.WARNING,
            )

class ProductShippingAdmin(admin.ModelAdmin):
    list_display = ('product', 'country', 'method', 'cost')
    list_filter = ('country', 'method', 'product__material')
    search_fields = ('product__sku_suffix', 'product__material')
    list_editable = ('cost',)
    ordering = ('product', 'country', 'cost')

custom_admin_site.register(Order, OrderAdmin)
custom_admin_site.register(ProductShipping, ProductShippingAdmin)
