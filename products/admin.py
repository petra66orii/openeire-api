from django.contrib import admin
from .models import Photo, Video, ProductVariant, ProductReview, PrintTemplate
from django.utils.html import format_html
from django.urls import reverse
from openeire_api.admin import custom_admin_site

# 1. Create an Inline for Variants
class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1  # Show 1 empty row by default
    fields = ('material', 'size', 'price', 'sku')

# @admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ('title', 'collection', 'price_hd', 'price_4k', 'created_at')
    list_filter = ('collection',)
    search_fields = ('title', 'tags', 'description')
    
    # 👇 Connect the Inline here
    inlines = [ProductVariantInline]
    actions = ['regenerate_variants']

    @admin.action(description="Generate missing variants from Templates")
    def regenerate_variants(self, request, queryset):
        templates = PrintTemplate.objects.filter(is_active=True)
        count = 0
        for photo in queryset:
            for t in templates:
                # Check if it exists to avoid duplicates
                obj, created = ProductVariant.objects.get_or_create(
                    photo=photo,
                    material=t.material,
                    size=t.size,
                    defaults={
                        'price': t.base_price,
                        'sku': f"PHOTO-{photo.id}-{t.sku_suffix}"
                    }
                )
                if created:
                    count += 1
        self.message_user(request, f"Created {count} new variants.")

# @admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    list_display = ('title', 'collection', 'resolution', 'frame_rate', 'price_hd')
    list_filter = ('collection', 'resolution')
    search_fields = ('title', 'description', 'tags')
    
    # Organizes the detail view nicely
    fieldsets = (
        ('General Info', {
            'fields': ('title', 'description', 'collection', 'tags')
        }),
        ('Media', {
            'fields': ('thumbnail_image', 'video_file')
        }),
        ('Technical Specs', {
            'fields': ('duration', 'resolution', 'frame_rate')
        }),
        ('Pricing', {
            'fields': ('price_hd', 'price_4k')
        }),
    )

# @admin.register(PrintTemplate)
class PrintTemplateAdmin(admin.ModelAdmin):
    list_display = ('material', 'size', 'base_price', 'sku_suffix', 'is_active')
    list_filter = ('material', 'is_active')
    list_editable = ('base_price', 'is_active')

# @admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    # Updated to show parent Photo and SKU
    list_display = ('photo', 'material', 'size', 'price', 'sku')
    list_filter = ('material', 'size')
    search_fields = ('photo__title', 'sku')
    ordering = ('photo', 'material', 'size')

# @admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
# 1. Put 'id' first. This gives you a clear number to click to edit THE REVIEW.
    list_display = ('id', 'user', 'product_link', 'rating', 'short_comment', 'approved', 'created_at')
    
    # 2. Explicitly tell Django: "Clicking the ID opens the review edit page"
    list_display_links = ('id', 'short_comment')
    
    list_filter = ('approved', 'rating')
    search_fields = ('comment', 'user__username')
    
    # 3. FORCE the 'admin_reply' field to appear in the edit form
    fields = ('user', 'rating', 'comment', 'approved', 'admin_reply')
    
    # 4. Make these read-only so you don't accidentally change history
    readonly_fields = ('user', 'rating', 'comment')
    actions = ['mark_as_approved', 'mark_as_unapproved']

    # Helper to keep the list view clean
    def short_comment(self, obj):
        return obj.comment[:50] + "..." if len(obj.comment) > 50 else obj.comment
    short_comment.short_description = "Comment"

    # This method creates the clickable link to the product
    def product_link(self, obj):
        if obj.product:
            content_type = obj.content_type
            url = reverse(
                f'admin:{content_type.app_label}_{content_type.model}_change',
                args=[obj.object_id]
            )
            # Safe check to display title if it exists
            title = getattr(obj.product, 'title', str(obj.product))
            return format_html('<a href="{}">{}</a>', url, title)
        return "N/A"
    product_link.short_description = 'ProductVariant'

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

custom_admin_site.register(Photo, PhotoAdmin)
custom_admin_site.register(Video, VideoAdmin)
custom_admin_site.register(ProductVariant, ProductVariantAdmin)
custom_admin_site.register(ProductReview, ProductReviewAdmin)
custom_admin_site.register(PrintTemplate, PrintTemplateAdmin)