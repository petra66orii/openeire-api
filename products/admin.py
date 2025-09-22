from django.contrib import admin
from .models import Photo, Video, Product, ProductReview
from django.utils.html import format_html

admin.site.register(Photo)
admin.site.register(Video)
admin.site.register(Product)

# Register ProductReview with custom Admin options
@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    list_display = ('product_link', 'user', 'rating', 'comment_snippet', 'approved', 'created_at')
    list_filter = ('approved', 'rating', 'created_at')
    search_fields = ('comment', 'user__username', 'product__title')
    actions = ['mark_as_approved', 'mark_as_unapproved']

    # Custom method to display product title and link to its admin page
    def product_link(self, obj):
        if obj.product:
            # Get the admin URL for the specific product type
            content_type = obj.content_type
            product_admin_url = admin.site.reverse_admin_url(
                f'{content_type.app_label}_{content_type.model}_change',
                args=[obj.object_id]
            )
            return format_html('<a href="{}">{}</a>', product_admin_url, obj.product.title)
        return "N/A"
    product_link.short_description = 'Product'

    # Custom method to display a snippet of the comment
    def comment_snippet(self, obj):
        return obj.comment[:75] + '...' if len(obj.comment) > 75 else obj.comment
    comment_snippet.short_description = 'Comment'

    # Custom action: Mark selected reviews as approved
    @admin.action(description='Mark selected reviews as approved')
    def mark_as_approved(self, request, queryset):
        queryset.update(approved=True)
        self.message_user(request, f"{queryset.count()} reviews successfully marked as approved.")

    # Custom action: Mark selected reviews as unapproved
    @admin.action(description='Mark selected reviews as unapproved')
    def mark_as_unapproved(self, request, queryset):
        queryset.update(approved=False)
        self.message_user(request, f"{queryset.count()} reviews successfully marked as unapproved.")