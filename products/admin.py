import json
from decimal import Decimal, ROUND_HALF_UP
import stripe
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.utils.safestring import mark_safe

from checkout.models import ProductShipping
from .models import Photo, Video, ProductVariant, ProductReview, PrintTemplate, LicenseRequest
from django.utils.html import format_html
from django.urls import reverse
from openeire_api.admin import custom_admin_site

stripe.api_key = settings.STRIPE_SECRET_KEY
stripe.max_network_retries = getattr(settings, 'STRIPE_MAX_NETWORK_RETRIES', 2)
STRIPE_TIMEOUT_SECONDS = getattr(settings, 'STRIPE_TIMEOUT_SECONDS', 10)

def get_price_autofill_script():
    """Generates the JS needed to auto-fill prices, SKUs, and filter Size dropdowns."""
    data_map = {}
    valid_sizes = {} # 👇 New dictionary for the dependent dropdown
    
    try:
        for template in PrintTemplate.objects.all():
            mat = template.material
            sz = template.size
            
            # 1. Map for prices and SKUs
            data_map[f"{mat}-{sz}"] = {
                'price': f"{template.retail_price:.2f}",
                'prodigi_sku': template.prodigi_sku or '',
                'sku_suffix': template.sku_suffix or ''
            }
            
            # 2. Map for Size filtering
            if mat not in valid_sizes:
                valid_sizes[mat] = []
            valid_sizes[mat].append(sz)
            
    except Exception:
        pass 

    js_script = f"""
    <script>
        (function() {{
            const dataMap = {json.dumps(data_map)};
            const validSizes = {json.dumps(valid_sizes)};

            // 👇 Helper function to hide/disable invalid sizes
            function filterSizeDropdown(matInput, szInput) {{
                if (!matInput || !szInput) return;
                
                let selectedMat = matInput.value;
                let allowedSizes = validSizes[selectedMat] || [];

                // Loop through all options in the Size dropdown
                Array.from(szInput.options).forEach(option => {{
                    if (option.value === '') return; // Keep the blank '---------' option
                    
                    if (allowedSizes.includes(option.value)) {{
                        option.hidden = false;
                        option.disabled = false;
                    }} else {{
                        option.hidden = true;
                        option.disabled = true;
                    }}
                }});

                // If the currently selected size is now hidden, reset the fields
                if (szInput.value && !allowedSizes.includes(szInput.value)) {{
                    szInput.value = '';
                    
                    let container = szInput.closest('tr') || szInput.closest('fieldset') || document;
                    let prInput = container.querySelector('input[name$="price"]');
                    let prodigiSkuInput = container.querySelector('input[name$="prodigi_sku"]');
                    let internalSkuInput = container.querySelector('input[name$="sku"]:not([name$="prodigi_sku"])');
                    
                    if (prInput) prInput.value = '';
                    if (prodigiSkuInput) prodigiSkuInput.value = '';
                    if (internalSkuInput) internalSkuInput.value = '';
                }}
            }}

            // 👇 Run once on page load to filter any existing rows
            document.addEventListener('DOMContentLoaded', function() {{
                let matInputs = document.querySelectorAll('select[name$="material"]');
                matInputs.forEach(matInput => {{
                    let container = matInput.closest('tr') || matInput.closest('fieldset') || document;
                    let szInput = container.querySelector('select[name$="size"]');
                    filterSizeDropdown(matInput, szInput);
                }});
            }});

            // 👇 Existing Change Listener
            document.addEventListener('change', function(e) {{
                if (!e.target || !e.target.name) return;

                let isMaterial = e.target.name.endsWith('material');
                let isSize = e.target.name.endsWith('size');
                let isPhoto = e.target.id === 'id_photo';

                if (isMaterial || isSize || isPhoto) {{
                    let container = e.target.closest('tr') || e.target.closest('fieldset') || document;
                    
                    let matInput = container.querySelector('select[name$="material"]');
                    let szInput = container.querySelector('select[name$="size"]');
                    let prInput = container.querySelector('input[name$="price"]');
                    let prodigiSkuInput = container.querySelector('input[name$="prodigi_sku"]');
                    let internalSkuInput = container.querySelector('input[name$="sku"]:not([name$="prodigi_sku"])');
                    
                    // 👇 If Material changed, filter the Size dropdown immediately
                    if (isMaterial) {{
                        filterSizeDropdown(matInput, szInput);
                    }}

                    if (matInput && szInput && matInput.value && szInput.value) {{
                        let key = matInput.value + '-' + szInput.value;
                        
                        if (dataMap[key]) {{
                            if (prInput) prInput.value = dataMap[key].price;
                            if (prodigiSkuInput) prodigiSkuInput.value = dataMap[key].prodigi_sku;
                            
                            let photoId = '';
                            let photoDropdown = document.getElementById('id_photo');
                            
                            if (photoDropdown && photoDropdown.value) {{
                                photoId = photoDropdown.value; 
                            }} else {{
                                let urlMatch = window.location.pathname.match(/\\/photo\\/(\\d+)\\//);
                                if (urlMatch) photoId = urlMatch[1];
                            }}

                            if (internalSkuInput && photoId && dataMap[key].sku_suffix) {{
                                internalSkuInput.value = 'PHOTO-' + photoId + '-' + dataMap[key].sku_suffix;
                            }}
                        }}
                    }}
                }}
            }});
        }})();
    </script>
    """
    return mark_safe(js_script)


# --- CUSTOM FORMS ---
class PhotoAdminForm(forms.ModelForm):
    class Meta:
        model = Photo
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Inject script into the 'title' field help text so it loads on the Photo page
        if 'title' in self.fields:
            existing_help = self.fields['title'].help_text or ''
            self.fields['title'].help_text = existing_help + get_price_autofill_script()


class ProductVariantAdminForm(forms.ModelForm):
    class Meta:
        model = ProductVariant
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Inject script into the 'price' field so it loads on standalone Variant page
        if 'price' in self.fields:
            existing_help = self.fields['price'].help_text or ''
            self.fields['price'].help_text = existing_help + get_price_autofill_script()


class LicenseRequestAdminForm(forms.ModelForm):
    class Meta:
        model = LicenseRequest
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        if not self.instance or not self.instance.pk:
            return cleaned_data

        quoted_price_changed = 'quoted_price' in self.changed_data
        link_present = bool(self.instance.stripe_payment_link)
        link_still_present = bool(cleaned_data.get('stripe_payment_link'))

        if quoted_price_changed and link_present and link_still_present:
            raise forms.ValidationError(
                "Quoted price changed but a Stripe payment link already exists. "
                "Clear the Stripe link field to regenerate it."
            )
        return cleaned_data
# 1. Create an Inline for Variants
class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1  # Show 1 empty row by default
    fields = ('material', 'size', 'price', 'sku')

# @admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    form = PhotoAdminForm
    
    list_display = ('title', 'collection', 'price_hd', 'price_4k', 'created_at')
    list_filter = ('collection',)
    search_fields = ('title', 'tags', 'description')
    
    inlines = [ProductVariantInline]
    actions = ['regenerate_variants']

    @admin.action(description="Generate missing variants from Templates")
    def regenerate_variants(self, request, queryset):
        templates = PrintTemplate.objects.filter(is_active=True)
        count = 0
        for photo in queryset:
            for t in templates:
                # Calculate retail price dynamically here as well
                obj, created = ProductVariant.objects.get_or_create(
                    photo=photo,
                    material=t.material,
                    size=t.size,
                    defaults={
                        'price': t.retail_price, # Ensure this uses the *retail* price, not base price!
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

class LicenseRequestAdmin(admin.ModelAdmin):
    form = LicenseRequestAdminForm
    list_display = ('client_name', 'email', 'get_asset_link', 'project_type', 'status', 'created_at')
    list_filter = ('status', 'project_type', 'created_at')
    search_fields = ('client_name', 'email', 'company')
    readonly_fields = ('asset_link', 'content_type', 'object_id', 'created_at', 'updated_at')
    
    fieldsets = (
        ('Client Info', {
            'fields': ('client_name', 'company', 'email')
        }),
        ('Request Details', {
            'fields': ('asset_link', 'project_type', 'duration', 'message')
        }),
        ('Admin / Fulfillment', {
            'fields': ('status', 'quoted_price', 'stripe_payment_link', 'ai_draft_response'),
            'description': 'Enter a quoted price and click Save to generate a Stripe payment link.'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('content_type')

    def get_asset_link(self, obj):
        asset = obj.asset
        if not asset:
            return "-"
        url = reverse(
            f"{custom_admin_site.name}:{asset._meta.app_label}_{asset._meta.model_name}_change",
            args=[asset.pk],
        )
        return format_html('<a href="{}">{}</a>', url, asset)
    get_asset_link.short_description = 'Requested Asset'
    get_asset_link.admin_order_field = 'object_id'

    def asset_link(self, obj):
        return self.get_asset_link(obj)
    asset_link.short_description = 'Requested Asset'

    def save_model(self, request, obj, form, change):
        # If the client entered a price, but there is no Stripe link yet
        if obj.pk is None:
            super().save_model(request, obj, form, change)
            return

        if obj.quoted_price and not obj.stripe_payment_link:
            try:
                if obj.quoted_price <= 0:
                    messages.error(request, "Quoted price must be greater than zero.")
                    super().save_model(request, obj, form, change)
                    return

                # 1. Create a custom Product in Stripe for this specific license
                product_name = f"Commercial License: {obj.asset} ({obj.get_project_type_display()})"
                amount_in_cents = int(
                    (obj.quoted_price * Decimal('100')).quantize(
                        Decimal('1'),
                        rounding=ROUND_HALF_UP
                    )
                )
                idempotency_base = f"license-request-{obj.pk}-quote-{amount_in_cents}"
                request_opts = {"timeout": STRIPE_TIMEOUT_SECONDS}

                stripe_product = stripe.Product.create(
                    name=product_name,
                    idempotency_key=f"{idempotency_base}-product",
                    **request_opts
                )

                # 2. Create a Price for that Product (Stripe uses cents, so multiply by 100)
                stripe_price = stripe.Price.create(
                    product=stripe_product.id,
                    unit_amount=amount_in_cents,
                    currency="eur",
                    idempotency_key=f"{idempotency_base}-price",
                    **request_opts
                )

                # 3. Generate the reusable Payment Link (Restricted to ONE payment)
                payment_link = stripe.PaymentLink.create(
                    line_items=[{"price": stripe_price.id, "quantity": 1}],
                    restrictions={
                        "completed_sessions": {
                            "limit": 1, # The link automatically expires after 1 successful payment!
                        },
                    },
                    idempotency_key=f"{idempotency_base}-link",
                    **request_opts
                )

                # 4. Save the link to the object
                obj.stripe_payment_link = payment_link.url
                
                # Automatically move the status to QUOTED
                if obj.status in ['NEW', 'REVIEWED']:
                    obj.status = 'QUOTED'
                    
                messages.success(
                    request,
                    f"Stripe Payment Link successfully generated for EUR {obj.quoted_price}."
                )

            except stripe.error.StripeError as e:
                messages.error(request, f"Failed to generate Stripe link: {e.user_message or str(e)}")
            except Exception as e:
                messages.error(request, f"Failed to generate Stripe link: {e}")
                
        # If the client wants to regenerate the link for a new price, 
        # they just delete the old link, change the price, and click Save again!
        elif 'quoted_price' in form.changed_data and obj.stripe_payment_link:
            messages.error(
                request,
                "Quoted price changed but a Stripe link already exists. "
                "Clear the Stripe Link field to regenerate it."
            )
            return

        # Finally, save the object to the database
        super().save_model(request, obj, form, change)

class ProductShippingInline(admin.TabularInline):
    """
    Allows editing shipping costs directly inside the Product page.
    """
    model = ProductShipping
    extra = 0  # Removes empty extra rows to keep the UI clean
    fields = ('country', 'method', 'cost')
    ordering = ('country', 'cost')

# @admin.register(PrintTemplate)
class PrintTemplateAdmin(admin.ModelAdmin):
    # Display the production cost and the calculated retail price
    list_display = ('material', 'size', 'production_cost', 'get_retail_price', 'sku_suffix')
    
    # Filters to easily find specific groups of products
    list_filter = ('material',)
    
    # Allow quick editing of production costs from the list view
    list_editable = ('production_cost',)
    
    # Search by material or SKU
    search_fields = ('material', 'sku_suffix')
    
    # Add the shipping costs table inside the product edit page
    inlines = [ProductShippingInline]

    # Helper to display the calculated property in the admin list
    def get_retail_price(self, obj):
        return f"€{obj.retail_price:.2f}"
    get_retail_price.short_description = 'Retail Price (Est.)'

# @admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    form = ProductVariantAdminForm

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
custom_admin_site.register(LicenseRequest, LicenseRequestAdmin)

