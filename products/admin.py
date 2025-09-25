from django.contrib import admin
from .models import Photo, Video, Product, ProductReview
from django.utils.html import format_html
from django.urls import reverse

@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ('title', 'collection', 'price_hd', 'price_4k', 'created_at')
    list_filter = ('collection',)
    search_fields = ('title', 'tags', 'description')

@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ('title', 'collection', 'price_hd', 'price_4k', 'created_at')
    list_filter = ('collection',)
    search_fields = ('title', 'tags', 'description')

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'material', 'size', 'price')
    list_filter = ('material', 'size')
    search_fields = ('photo__title',)

# Register ProductReview with custom Admin options
@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    # We add 'comment_snippet' to the list of displayed fields
    list_display = ('product_link', 'user', 'rating', 'comment_snippet', 'approved', 'created_at')
    list_filter = ('approved', 'rating')
    search_fields = ('comment', 'user__username')
    actions = ['mark_as_approved', 'mark_as_unapproved']

    # This method creates the clickable link to the product
    def product_link(self, obj):
        if obj.product:
            content_type = obj.content_type
            url = reverse(
                f'admin:{content_type.app_label}_{content_type.model}_change',
                args=[obj.object_id]
            )
            return format_html('<a href="{}">{}</a>', url, obj.product.title)
        return "N/A"
    product_link.short_description = 'Product'

    # This method creates a short preview of the comment
    def comment_snippet(self, obj):
        if obj.comment:
            return obj.comment[:75] + '...' if len(obj.comment) > 75 else obj.comment
        return "No comment"
    comment_snippet.short_description = 'Comment'

    @admin.action(description='Mark selected reviews as approved')
    def mark_as_approved(self, request, queryset):
        queryset.update(approved=True)

    @admin.action(description='Mark selected reviews as unapproved')
    def mark_as_unapproved(self, request, queryset):
        queryset.update(approved=False)