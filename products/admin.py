import json
import time
from decimal import Decimal, ROUND_HALF_UP
import stripe
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.mail import send_mail
from django.db import OperationalError, transaction
from django.db.models import Max
from django.db.transaction import TransactionManagementError
from django.utils import timezone
from django.utils.safestring import mark_safe

from checkout.models import ProductShipping
from .licensing import send_licence_quote_email
from .models import (
    Photo,
    Video,
    ProductVariant,
    ProductReview,
    PrintTemplate,
    LicenseRequest,
    StripeWebhookEvent,
    LicenceOffer,
    LicenseRequestAuditLog,
    LicenceDocument,
    LicenceDeliveryToken,
)
from django.utils.html import format_html
from django.urls import reverse
from openeire_api.admin import custom_admin_site

stripe.api_key = settings.STRIPE_SECRET_KEY
stripe.max_network_retries = getattr(settings, 'STRIPE_MAX_NETWORK_RETRIES', 2)
STRIPE_TIMEOUT_SECONDS = getattr(settings, 'STRIPE_TIMEOUT_SECONDS', 10)
stripe.default_http_client = stripe.RequestsClient(timeout=STRIPE_TIMEOUT_SECONDS)

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
    internal_note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Optional internal note recorded in the audit log.",
    )

    class Meta:
        model = LicenseRequest
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        if not self.instance or not self.instance.pk:
            return cleaned_data

        immutable_statuses = {'PAID', 'DELIVERED', 'EXPIRED', 'REVOKED'}
        scope_fields = {
            'project_type',
            'permitted_media',
            'territory',
            'duration',
            'reach_caps',
            'exclusivity',
            'message',
            'quoted_price',
        }
        scope_changed = bool(scope_fields.intersection(set(self.changed_data)))

        if self.instance.status in immutable_statuses and scope_changed:
            raise forms.ValidationError(
                "Scope and pricing are immutable after payment/delivery/expiry/revocation. "
                "Create a new licence request or offer version instead."
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


class LicenceOfferInline(admin.TabularInline):
    model = LicenceOffer
    extra = 0
    can_delete = False
    fields = (
        'version',
        'status',
        'quoted_price',
        'currency',
        'stripe_payment_link_id',
        'stripe_checkout_session_id',
        'paid_at',
        'created_at',
    )
    readonly_fields = fields
    ordering = ('-version',)


class LicenseRequestAuditLogInline(admin.TabularInline):
    model = LicenseRequestAuditLog
    extra = 0
    can_delete = False
    fields = ('changed_at', 'from_status', 'to_status', 'changed_by', 'note')
    readonly_fields = fields
    ordering = ('-changed_at',)

class LicenseRequestAdmin(admin.ModelAdmin):
    form = LicenseRequestAdminForm
    list_display = ('client_name', 'email', 'get_asset_link', 'project_type', 'status', 'created_at')
    list_filter = ('status', 'project_type', 'created_at')
    search_fields = ('client_name', 'email', 'company')
    readonly_fields = (
        'asset_link',
        'created_at',
        'updated_at',
        'paid_at',
        'delivered_at',
        'stripe_payment_link_id',
        'stripe_checkout_session_id',
        'stripe_payment_intent_id',
        'licence_documents_links',
    )
    inlines = [LicenceOfferInline, LicenseRequestAuditLogInline]
    actions = ['mark_needs_info', 'approve_requests', 'reject_requests']
    
    fieldsets = (
        ('Client Info', {
            'fields': ('client_name', 'company', 'email')
        }),
        ('Licence Scope (Rights-Managed)', {
            'fields': ('asset_link', 'project_type', 'permitted_media', 'territory', 'duration', 'reach_caps', 'exclusivity', 'message')
        }),
        ('Admin / Fulfillment', {
            'fields': (
                'status',
                'quoted_price',
                'stripe_payment_link',
                'stripe_payment_link_id',
                'stripe_checkout_session_id',
                'stripe_payment_intent_id',
                'paid_at',
                'delivered_at',
                'ai_draft_response',
                'licence_documents_links',
                'internal_note',
            ),
            'description': (
                'Set quote/scope and save to issue a versioned Licence Offer + Stripe Payment Link. '
                'Any post-approval scope change creates a new offer version.'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('content_type')

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj:
            readonly.extend(['content_type', 'object_id'])
        return tuple(readonly)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return (
                ('Client Info', {
                    'fields': ('client_name', 'company', 'email')
                }),
                ('Licence Scope (Rights-Managed)', {
                    'fields': ('content_type', 'object_id', 'project_type', 'permitted_media', 'territory', 'duration', 'reach_caps', 'exclusivity', 'message')
                }),
                ('Admin / Fulfillment', {
                    'fields': (
                        'status',
                        'quoted_price',
                        'stripe_payment_link',
                        'stripe_payment_link_id',
                        'stripe_checkout_session_id',
                        'stripe_payment_intent_id',
                        'paid_at',
                        'delivered_at',
                        'ai_draft_response',
                        'internal_note',
                    ),
                    'description': (
                        'Set quote/scope and save to issue a versioned Licence Offer + Stripe Payment Link. '
                        'Any post-approval scope change creates a new offer version.'
                    )
                }),
                ('Timestamps', {
                    'fields': ('created_at', 'updated_at'),
                    'classes': ('collapse',)
                }),
            )
        return super().get_fieldsets(request, obj)

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

    def licence_documents_links(self, obj):
        docs = obj.licence_documents.order_by("doc_type", "-created_at")
        if not docs:
            return "-"
        links = []
        for doc in docs:
            if not doc.file:
                continue
            links.append(
                f'<a href="{doc.file.url}" target="_blank">{doc.get_doc_type_display()}</a>'
            )
        return mark_safe("<br>".join(links) if links else "-")
    licence_documents_links.short_description = "Generated Documents"

    def _build_scope_snapshot(self, obj):
        return {
            "project_type": obj.project_type,
            "project_type_display": obj.get_project_type_display(),
            "permitted_media": obj.permitted_media,
            "permitted_media_display": (
                obj.get_permitted_media_display() if obj.permitted_media else None
            ),
            "territory": obj.territory,
            "territory_display": obj.get_territory_display() if obj.territory else None,
            "duration": obj.duration,
            "duration_display": obj.get_duration_display() if obj.duration else None,
            "exclusivity": obj.exclusivity,
            "exclusivity_display": (
                obj.get_exclusivity_display() if obj.exclusivity else None
            ),
            "reach_caps": obj.reach_caps,
            "message": obj.message,
            "asset": str(obj.asset) if obj.asset else None,
            "asset_id": obj.object_id,
            "asset_type": obj.content_type.model if obj.content_type_id else None,
            "client_name": obj.client_name,
            "company": obj.company,
            "email": obj.email,
            "terms_version": getattr(settings, "LICENCE_TERMS_VERSION", "RM-1.0"),
            "master_agreement_version": getattr(settings, "LICENCE_MASTER_AGREEMENT", ""),
        }

    def _notify_status(self, obj, status_label):
        try:
            send_mail(
                subject=f"Licence request update: {obj.asset}",
                message=(
                    f"Hi {obj.client_name},\n\n"
                    f"Your licence request status is now: {status_label}.\n\n"
                    "If you have questions, reply to this email.\n\n"
                    "Kind regards,\n"
                    "OpenEire Studios\n"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[obj.email],
                fail_silently=False,
            )
        except Exception:
            # Do not block admin status updates if email transport fails.
            pass

    def _issue_new_offer(self, request, obj):
        if obj.quoted_price is None or obj.quoted_price <= 0:
            raise ValueError("Quoted price must be greater than zero.")

        amount_in_cents = int(
            (obj.quoted_price * Decimal('100')).quantize(
                Decimal('1'),
                rounding=ROUND_HALF_UP
            )
        )

        if amount_in_cents > 99_999_999:
            raise ValueError("Quoted price exceeds Stripe limit for EUR (max EUR 999,999.99).")

        existing_active = (
            LicenceOffer.objects
            .filter(license_request=obj, status='ACTIVE')
            .order_by('-version')
            .first()
        )
        next_version = (
            (LicenceOffer.objects.filter(license_request=obj).aggregate(max_v=Max("version"))["max_v"] or 0)
            + 1
        )
        idempotency_base = f"license-request-{obj.pk}-offer-v{next_version}-{amount_in_cents}"
        product_name = f"Commercial License Offer v{next_version}: {obj.asset} ({obj.get_project_type_display()})"

        stripe_product = stripe.Product.create(
            name=product_name,
            idempotency_key=f"{idempotency_base}-product"
        )
        stripe_price = stripe.Price.create(
            product=stripe_product.id,
            unit_amount=amount_in_cents,
            currency="eur",
            idempotency_key=f"{idempotency_base}-price"
        )
        payment_link = stripe.PaymentLink.create(
            line_items=[{"price": stripe_price.id, "quantity": 1}],
            restrictions={"completed_sessions": {"limit": 1}},
            metadata={
                "license_request_id": str(obj.pk),
                "offer_version": str(next_version),
            },
            idempotency_key=f"{idempotency_base}-link"
        )

        if existing_active:
            existing_active.status = "SUPERSEDED"
            existing_active.superseded_at = timezone.now()
            existing_active.save(update_fields=["status", "superseded_at"])

        offer = LicenceOffer.objects.create(
            license_request=obj,
            version=next_version,
            status='ACTIVE',
            scope_snapshot=self._build_scope_snapshot(obj),
            quoted_price=obj.quoted_price,
            currency="EUR",
            terms_version=getattr(settings, "LICENCE_TERMS_VERSION", "RM-1.0"),
            master_agreement_version=getattr(settings, "LICENCE_MASTER_AGREEMENT", ""),
            stripe_product_id=stripe_product.id,
            stripe_price_id=stripe_price.id,
            stripe_payment_link_id=payment_link.id,
            stripe_payment_link_url=payment_link.url,
            created_by=request.user if request.user.is_authenticated else None,
        )
        obj.stripe_payment_link = payment_link.url
        obj.stripe_payment_link_id = payment_link.id
        return offer

    def _save_model_with_retry(self, request, obj, form, change):
        attempts = int(getattr(settings, 'SQLITE_SAVE_RETRY_ATTEMPTS', 6))
        base_delay = float(getattr(settings, 'SQLITE_SAVE_RETRY_DELAY_SECONDS', 0.3))
        for attempt in range(1, attempts + 1):
            sid = transaction.savepoint()
            try:
                super().save_model(request, obj, form, change)
                transaction.savepoint_commit(sid)
                return True
            except OperationalError as exc:
                transaction.savepoint_rollback(sid)
                # Clear rollback state so retries can proceed inside admin's outer atomic block.
                transaction.set_rollback(False)
                message = str(exc).lower()
                if 'database is locked' not in message:
                    raise
                if attempt == attempts:
                    messages.error(
                        request,
                        "Database is busy (SQLite lock). Please try saving this request again in a few seconds."
                    )
                    return False
                time.sleep(base_delay * attempt)
            except TransactionManagementError as exc:
                # If a prior DB error marked the outer transaction as broken, clear and retry.
                transaction.savepoint_rollback(sid)
                transaction.set_rollback(False)
                if attempt == attempts:
                    messages.error(
                        request,
                        "Database transaction was interrupted. Please save again."
                    )
                    return False
                time.sleep(base_delay * attempt)
        return False

    def save_model(self, request, obj, form, change):
        note = (form.cleaned_data.get("internal_note") or "").strip()

        # Preserve actor/note only when status changes; non-status notes are
        # written explicitly below via add_audit_note.
        if 'status' in form.changed_data:
            obj.set_status_change_context(
                actor=request.user if request.user.is_authenticated else None,
                note=note,
            )

        if not self._save_model_with_retry(request, obj, form, change):
            return

        if note and 'status' not in form.changed_data:
            obj.add_audit_note(
                note,
                actor=request.user if request.user.is_authenticated else None,
            )

        scope_fields = {
            'project_type',
            'permitted_media',
            'territory',
            'duration',
            'reach_caps',
            'exclusivity',
            'message',
            'quoted_price',
        }
        scope_changed = bool(scope_fields.intersection(set(form.changed_data)))
        has_active_offer = LicenceOffer.objects.filter(
            license_request=obj,
            status='ACTIVE',
        ).exists()
        should_issue_offer = bool(obj.quoted_price) and (
            not has_active_offer or scope_changed
        )

        if should_issue_offer:
            try:
                offer = self._issue_new_offer(request, obj)
                obj.set_status_change_context(
                    actor=request.user if request.user.is_authenticated else None,
                    note=f"Issued licence offer v{offer.version}.",
                    metadata={"offer_version": offer.version},
                )
                if obj.status in {'DRAFT', 'SUBMITTED', 'NEEDS_INFO'}:
                    obj.status = 'APPROVED'
                    obj.save(update_fields=['status', 'updated_at'])
                if obj.status != 'PAYMENT_PENDING':
                    obj.transition_to(
                        'PAYMENT_PENDING',
                        actor=request.user if request.user.is_authenticated else None,
                        note=f"Awaiting payment for offer v{offer.version}.",
                        metadata={"offer_version": offer.version},
                    )
                obj.save(update_fields=['stripe_payment_link', 'stripe_payment_link_id', 'updated_at'])
                send_licence_quote_email(obj)
                messages.success(
                    request,
                    f"Offer v{offer.version} issued and quote email sent to {obj.email}."
                )
            except stripe.error.StripeError as e:
                messages.error(request, f"Failed to issue Stripe offer: {e.user_message or str(e)}")
            except Exception as e:
                messages.error(request, f"Failed to issue Stripe offer: {e}")

    @admin.action(description="Request More Info")
    def mark_needs_info(self, request, queryset):
        changed = 0
        for obj in queryset:
            did_change = obj.transition_to(
                'NEEDS_INFO',
                actor=request.user if request.user.is_authenticated else None,
                note="Admin requested additional scope information.",
            )
            if did_change:
                changed += 1
                self._notify_status(obj, "Needs Info")
        self.message_user(request, f"{changed} request(s) moved to Needs Info.")

    @admin.action(description="Approve Request")
    def approve_requests(self, request, queryset):
        changed = 0
        for obj in queryset:
            did_change = obj.transition_to(
                'APPROVED',
                actor=request.user if request.user.is_authenticated else None,
                note="Admin approved request.",
            )
            if did_change:
                changed += 1
                self._notify_status(obj, "Approved")
        self.message_user(request, f"{changed} request(s) approved.")

    @admin.action(description="Reject Request")
    def reject_requests(self, request, queryset):
        changed = 0
        for obj in queryset:
            did_change = obj.transition_to(
                'REJECTED',
                actor=request.user if request.user.is_authenticated else None,
                note="Admin rejected request.",
            )
            if did_change:
                changed += 1
                self._notify_status(obj, "Rejected")
        self.message_user(request, f"{changed} request(s) rejected.")

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


class StripeWebhookEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'stripe_event_id', 'status', 'received_at', 'processed_at')
    list_filter = ('status', 'event_type', 'received_at')
    search_fields = ('stripe_event_id',)
    readonly_fields = ('stripe_event_id', 'event_type', 'received_at', 'processed_at', 'status', 'error_message')
    ordering = ('-received_at',)


class LicenceDocumentAdmin(admin.ModelAdmin):
    list_display = ('license_request', 'doc_type', 'created_at', 'file_link')
    list_filter = ('doc_type', 'created_at')
    search_fields = ('license_request__id',)
    readonly_fields = ('license_request', 'doc_type', 'file', 'sha256', 'created_at')

    def file_link(self, obj):
        if not obj.file:
            return "-"
        return format_html('<a href="{}" target="_blank">Download</a>', obj.file.url)
    file_link.short_description = 'File'


class LicenceDeliveryTokenAdmin(admin.ModelAdmin):
    list_display = ('license_request', 'token', 'expires_at', 'used_at', 'created_at')
    list_filter = ('expires_at', 'used_at')
    search_fields = ('license_request__id', 'token')
    readonly_fields = ('token', 'license_request', 'expires_at', 'used_at', 'created_at')


class LicenceOfferAdmin(admin.ModelAdmin):
    list_display = (
        'license_request',
        'version',
        'status',
        'quoted_price',
        'currency',
        'stripe_payment_link_id',
        'paid_at',
        'created_at',
    )
    list_filter = ('status', 'created_at', 'paid_at')
    search_fields = ('license_request__id', 'stripe_payment_link_id', 'stripe_checkout_session_id')
    readonly_fields = (
        'license_request',
        'version',
        'status',
        'scope_snapshot',
        'quoted_price',
        'currency',
        'terms_version',
        'master_agreement_version',
        'stripe_product_id',
        'stripe_price_id',
        'stripe_payment_link_id',
        'stripe_payment_link_url',
        'stripe_checkout_session_id',
        'stripe_payment_intent_id',
        'created_by',
        'created_at',
        'paid_at',
        'superseded_at',
    )


class LicenseRequestAuditLogAdmin(admin.ModelAdmin):
    list_display = ('license_request', 'changed_at', 'from_status', 'to_status', 'changed_by')
    list_filter = ('to_status', 'changed_at')
    search_fields = ('license_request__id', 'note')
    readonly_fields = ('license_request', 'from_status', 'to_status', 'changed_by', 'note', 'metadata', 'changed_at')


custom_admin_site.register(StripeWebhookEvent, StripeWebhookEventAdmin)
custom_admin_site.register(LicenceOffer, LicenceOfferAdmin)
custom_admin_site.register(LicenseRequestAuditLog, LicenseRequestAuditLogAdmin)
custom_admin_site.register(LicenceDocument, LicenceDocumentAdmin)
custom_admin_site.register(LicenceDeliveryToken, LicenceDeliveryTokenAdmin)

