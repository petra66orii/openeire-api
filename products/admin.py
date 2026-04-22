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
from .licensing import (
    get_active_offer,
    get_current_offer,
    get_latest_offer,
    get_licensing_from_email,
    build_offer_expires_at,
    send_licence_negotiation_email,
    send_licence_quote_email,
)
from .models import (
    Photo,
    Video,
    VideoUploadSession,
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


class VideoAdminForm(forms.ModelForm):
    class Meta:
        model = Video
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        video_file = cleaned_data.get('video_file')
        video_file_key = (cleaned_data.get('video_file_key') or '').strip()
        preview_video_key = (cleaned_data.get('preview_video_key') or '').strip()
        cleaned_data['video_file_key'] = video_file_key
        cleaned_data['preview_video_key'] = preview_video_key
        if not video_file and not video_file_key:
            raise forms.ValidationError(
                "Upload a video file or provide an existing R2 object key."
            )
        return cleaned_data


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
        if self.instance.client_confirmed_at and scope_changed:
            raise forms.ValidationError(
                "Scope and pricing are frozen once client confirmation has been recorded. "
                "Use the 'Reset Client Confirmation' admin action before editing scope or price."
            )
        return cleaned_data
# 1. Create an Inline for Variants
class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1  # Show 1 empty row by default
    fields = ('material', 'size', 'price', 'sku', 'prodigi_sku')

# @admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    form = PhotoAdminForm
    
    list_display = ('title', 'collection', 'is_printable', 'price', 'created_at')
    list_filter = ('collection', 'is_printable')
    search_fields = ('title', 'tags', 'description')
    
    inlines = [ProductVariantInline]
    actions = ['regenerate_variants']

    @admin.action(description="Generate missing variants from Templates")
    def regenerate_variants(self, request, queryset):
        templates = PrintTemplate.objects.all()
        count = 0
        repaired = 0
        skipped = 0
        variants_to_backfill = []
        for photo in queryset:
            if not photo.is_printable:
                skipped += 1
                continue
            for t in templates:
                # Calculate retail price dynamically here as well
                obj, created = ProductVariant.objects.get_or_create(
                    photo=photo,
                    material=t.material,
                    size=t.size,
                    defaults={
                        'price': t.retail_price, # Ensure this uses the *retail* price, not base price!
                        'sku': f"PHOTO-{photo.id}-{t.sku_suffix}",
                        'prodigi_sku': t.prodigi_sku,
                    }
                )
                if created:
                    count += 1
                elif not obj.prodigi_sku and t.prodigi_sku:
                    obj.prodigi_sku = t.prodigi_sku
                    variants_to_backfill.append(obj)
                    repaired += 1
        if variants_to_backfill:
            ProductVariant.objects.bulk_update(
                variants_to_backfill,
                ['prodigi_sku'],
                batch_size=500,
            )
        message = f"Created {count} new variants."
        if repaired:
            message += f" Repaired {repaired} variant SKU(s)."
        if skipped:
            message += f" Skipped {skipped} non-printable photo(s)."
        self.message_user(request, message)

# @admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    form = VideoAdminForm
    list_display = ('title', 'collection', 'resolution', 'frame_rate', 'price')
    list_filter = ('collection', 'resolution')
    search_fields = ('title', 'description', 'tags')
    
    # Organizes the detail view nicely
    fieldsets = (
        ('General Info', {
            'fields': ('title', 'description', 'collection', 'tags')
        }),
        ('Media', {
            'fields': ('thumbnail_image', 'preview_video_key', 'video_file', 'video_file_key'),
            'description': 'Use preview_video_key for the public watermarked clip. Use either a normal upload or an existing private R2 object key for the master video file.',
        }),
        ('Technical Specs', {
            'fields': ('duration', 'resolution', 'frame_rate')
        }),
        ('Pricing', {
            'fields': ('price',)
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
        'expires_at',
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
    SCOPE_FIELDS = (
        'project_type',
        'permitted_media',
        'territory',
        'duration',
        'reach_caps',
        'exclusivity',
        'message',
        'quoted_price',
    )

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
        'negotiation_sent_at',
        'client_confirmed_at',
        'agreed_scope_snapshot',
        'payment_email_sent_at',
        'stripe_payment_link_id',
        'stripe_checkout_session_id',
        'stripe_payment_intent_id',
        'last_negotiation_email_body',
        'last_payment_email_body',
        'licence_documents_links',
    )
    inlines = [LicenceOfferInline, LicenseRequestAuditLogInline]
    actions = [
        'generate_negotiation_draft',
        'send_negotiation_email',
        'mark_client_confirmed',
        'reset_client_confirmation',
        'generate_payment_offer',
        'regenerate_payment_offer',
        'generate_payment_email_draft',
        'send_payment_email',
        'mark_needs_info',
        'approve_requests',
        'reject_requests',
    ]
    
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
                'negotiation_sent_at',
                'client_confirmed_at',
                'agreed_scope_snapshot',
                'stripe_payment_link',
                'stripe_payment_link_id',
                'payment_email_sent_at',
                'stripe_checkout_session_id',
                'stripe_payment_intent_id',
                'paid_at',
                'delivered_at',
                'ai_draft_response',
                'ai_payment_draft_response',
                'last_negotiation_email_body',
                'last_payment_email_body',
                'licence_documents_links',
                'internal_note',
            ),
            'description': (
                'Use explicit admin actions to request drafts, send negotiation emails, '
                'mark client confirmation, generate payment offers, and send payment emails.'
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
            if obj.client_confirmed_at:
                readonly.extend(self.SCOPE_FIELDS)
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
                        'negotiation_sent_at',
                        'client_confirmed_at',
                        'agreed_scope_snapshot',
                        'stripe_payment_link',
                        'stripe_payment_link_id',
                        'payment_email_sent_at',
                        'stripe_checkout_session_id',
                        'stripe_payment_intent_id',
                        'paid_at',
                        'delivered_at',
                        'ai_draft_response',
                        'ai_payment_draft_response',
                        'last_negotiation_email_body',
                        'last_payment_email_body',
                        'internal_note',
                    ),
                    'description': (
                        'Use explicit admin actions to request drafts, send negotiation emails, '
                        'mark client confirmation, generate payment offers, and send payment emails.'
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
            "quoted_price": str(obj.quoted_price) if obj.quoted_price is not None else None,
            "message": obj.message,
            "asset": str(obj.asset) if obj.asset else None,
            "asset_label": str(obj.asset) if obj.asset else None,
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
                from_email=get_licensing_from_email(),
                recipient_list=[obj.email],
                fail_silently=False,
            )
        except Exception:
            # Do not block admin status updates if email transport fails.
            pass

    def _is_locked_request(self, obj):
        return obj.status in {'PAID', 'DELIVERED', 'EXPIRED', 'REVOKED'}

    def _has_current_active_offer(self, obj):
        return get_current_offer(obj) is not None

    def _ensure_agreed_scope_snapshot(self, obj):
        if obj.agreed_scope_snapshot:
            return obj.agreed_scope_snapshot, False
        snapshot = self._build_scope_snapshot(obj)
        obj.agreed_scope_snapshot = snapshot
        obj.save(update_fields=['agreed_scope_snapshot', 'updated_at'])
        return snapshot, True

    def _clear_payment_state(self, obj, *, clear_payment_draft=True):
        obj.stripe_payment_link = None
        obj.stripe_payment_link_id = None
        obj.payment_email_sent_at = None
        obj.last_payment_email_body = ""
        update_fields = [
            'stripe_payment_link',
            'stripe_payment_link_id',
            'payment_email_sent_at',
            'last_payment_email_body',
            'updated_at',
        ]
        if clear_payment_draft:
            obj.ai_payment_draft_response = None
            update_fields.append('ai_payment_draft_response')
        return update_fields

    def _supersede_active_offers(self, obj):
        active_offers = list(
            LicenceOffer.objects.filter(license_request=obj, status='ACTIVE')
            .order_by('-version')
        )
        if not active_offers:
            return 0
        superseded_at = timezone.now()
        for offer in active_offers:
            offer.status = "SUPERSEDED"
            offer.superseded_at = superseded_at
        LicenceOffer.objects.bulk_update(active_offers, ['status', 'superseded_at'])
        return len(active_offers)

    def _issue_new_offer(self, request, obj):
        if obj.quoted_price is None or obj.quoted_price <= 0:
            raise ValueError("Quoted price must be greater than zero.")
        if not obj.client_confirmed_at:
            raise ValueError("Client confirmation must be recorded before creating a payment offer.")

        amount_in_cents = int(
            (obj.quoted_price * Decimal('100')).quantize(
                Decimal('1'),
                rounding=ROUND_HALF_UP
            )
        )

        if amount_in_cents > 99_999_999:
            raise ValueError("Quoted price exceeds Stripe limit for EUR (max EUR 999,999.99).")

        scope_snapshot, snapshot_backfilled = self._ensure_agreed_scope_snapshot(obj)
        next_version = (
            (LicenceOffer.objects.filter(license_request=obj).aggregate(max_v=Max("version"))["max_v"] or 0)
            + 1
        )
        expires_at = build_offer_expires_at()
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
                "offer_expires_at": expires_at.isoformat(),
            },
            idempotency_key=f"{idempotency_base}-link"
        )

        self._supersede_active_offers(obj)

        offer = LicenceOffer.objects.create(
            license_request=obj,
            version=next_version,
            status='ACTIVE',
            scope_snapshot=scope_snapshot,
            quoted_price=obj.quoted_price,
            currency="EUR",
            terms_version=getattr(settings, "LICENCE_TERMS_VERSION", "RM-1.0"),
            master_agreement_version=getattr(settings, "LICENCE_MASTER_AGREEMENT", ""),
            stripe_product_id=stripe_product.id,
            stripe_price_id=stripe_price.id,
            stripe_payment_link_id=payment_link.id,
            stripe_payment_link_url=payment_link.url,
            created_by=request.user if request.user.is_authenticated else None,
            expires_at=expires_at,
        )
        obj.stripe_payment_link = payment_link.url
        obj.stripe_payment_link_id = payment_link.id
        obj.ai_payment_draft_response = None
        obj.payment_email_sent_at = None
        obj.last_payment_email_body = ""
        if snapshot_backfilled:
            obj.agreed_scope_snapshot = scope_snapshot
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

    @admin.action(description="Generate Negotiation Draft")
    def generate_negotiation_draft(self, request, queryset):
        updated = 0
        for obj in queryset:
            if self._is_locked_request(obj) or obj.status == 'PAYMENT_PENDING':
                messages.warning(
                    request,
                    f"Request {obj.id} is already in or beyond payment fulfilment and cannot request a negotiation draft.",
                )
                continue
            if obj.client_confirmed_at:
                messages.warning(
                    request,
                    f"Request {obj.id} already has client confirmation recorded. Reset confirmation before drafting a revised negotiation email.",
                )
                continue
            if get_active_offer(obj):
                messages.warning(
                    request,
                    f"Request {obj.id} already has an active payment offer. Generate a new payment email draft instead.",
                )
                continue
            obj.ai_draft_response = None
            obj.save(update_fields=['ai_draft_response', 'updated_at'])
            obj.add_audit_note(
                "Negotiation draft requested.",
                actor=request.user if request.user.is_authenticated else None,
                metadata={"draft_mode": "negotiation"},
            )
            updated += 1
        self.message_user(request, f"{updated} request(s) queued for negotiation drafting.")

    @admin.action(description="Send Negotiation Email")
    def send_negotiation_email(self, request, queryset):
        sent_count = 0
        for obj in queryset:
            if self._is_locked_request(obj) or obj.status == 'PAYMENT_PENDING':
                messages.warning(
                    request,
                    f"Request {obj.id} is already in or beyond payment fulfilment and cannot send a negotiation email.",
                )
                continue
            if obj.client_confirmed_at:
                messages.warning(
                    request,
                    f"Request {obj.id} already has client confirmation recorded. Use Reset Client Confirmation before sending a revised negotiation email.",
                )
                continue
            if get_active_offer(obj):
                messages.warning(
                    request,
                    f"Request {obj.id} already has an active payment offer. Send a payment email instead.",
                )
                continue
            if not (obj.ai_draft_response or "").strip():
                messages.warning(request, f"Request {obj.id} does not have a negotiation draft to send.")
                continue
            try:
                body = send_licence_negotiation_email(obj)
            except Exception as exc:
                messages.error(request, f"Failed to send negotiation email for request {obj.id}: {exc}")
                continue

            now = timezone.now()
            obj.negotiation_sent_at = now
            obj.client_confirmed_at = None
            obj.agreed_scope_snapshot = None
            obj.payment_email_sent_at = None
            obj.ai_payment_draft_response = None
            obj.last_negotiation_email_body = body
            obj.last_payment_email_body = ""
            obj.stripe_payment_link = None
            obj.stripe_payment_link_id = None
            obj.save(
                update_fields=[
                    'negotiation_sent_at',
                    'client_confirmed_at',
                    'agreed_scope_snapshot',
                    'payment_email_sent_at',
                    'ai_payment_draft_response',
                    'last_negotiation_email_body',
                    'last_payment_email_body',
                    'stripe_payment_link',
                    'stripe_payment_link_id',
                    'updated_at',
                ]
            )
            if obj.status != 'AWAITING_CLIENT_CONFIRMATION':
                obj.transition_to(
                    'AWAITING_CLIENT_CONFIRMATION',
                    actor=request.user if request.user.is_authenticated else None,
                    note="Negotiation email sent to client.",
                    metadata={"action": "negotiation_email_sent", "email_type": "negotiation"},
                )
            else:
                obj.add_audit_note(
                    "Negotiation email sent to client.",
                    actor=request.user if request.user.is_authenticated else None,
                    metadata={"action": "negotiation_email_sent", "email_type": "negotiation"},
                )
            sent_count += 1
        self.message_user(request, f"{sent_count} negotiation email(s) sent.")

    @admin.action(description="Mark Client Confirmed")
    def mark_client_confirmed(self, request, queryset):
        changed = 0
        for obj in queryset:
            if self._is_locked_request(obj):
                messages.warning(request, f"Request {obj.id} is locked and cannot be marked confirmed.")
                continue
            if obj.status != 'AWAITING_CLIENT_CONFIRMATION':
                messages.warning(
                    request,
                    f"Request {obj.id} must be in Awaiting Client Confirmation before client confirmation can be recorded.",
                )
                continue
            if not obj.negotiation_sent_at:
                messages.warning(
                    request,
                    f"Request {obj.id} has not sent a negotiation email yet.",
                )
                continue
            if obj.client_confirmed_at:
                messages.info(request, f"Request {obj.id} is already marked confirmed.")
                continue
            obj.client_confirmed_at = timezone.now()
            if not obj.agreed_scope_snapshot:
                obj.agreed_scope_snapshot = self._build_scope_snapshot(obj)
            obj.save(update_fields=['client_confirmed_at', 'agreed_scope_snapshot', 'updated_at'])
            obj.add_audit_note(
                "Client marked confirmed by admin and agreed scope frozen.",
                actor=request.user if request.user.is_authenticated else None,
                metadata={"action": "client_confirmed", "scope_frozen": True},
            )
            changed += 1
        self.message_user(request, f"{changed} request(s) marked as client confirmed.")

    @admin.action(description="Reset Client Confirmation")
    def reset_client_confirmation(self, request, queryset):
        reset_count = 0
        for obj in queryset:
            if self._is_locked_request(obj):
                messages.warning(request, f"Request {obj.id} is locked and cannot reset client confirmation.")
                continue
            if not obj.client_confirmed_at and not obj.agreed_scope_snapshot:
                messages.info(request, f"Request {obj.id} does not have frozen client confirmation state to reset.")
                continue

            superseded_count = self._supersede_active_offers(obj)
            obj.client_confirmed_at = None
            obj.agreed_scope_snapshot = None
            obj.payment_email_sent_at = None
            obj.ai_payment_draft_response = None
            obj.last_payment_email_body = ""
            obj.stripe_payment_link = None
            obj.stripe_payment_link_id = None
            obj.save(
                update_fields=[
                    'client_confirmed_at',
                    'agreed_scope_snapshot',
                    'payment_email_sent_at',
                    'ai_payment_draft_response',
                    'last_payment_email_body',
                    'stripe_payment_link',
                    'stripe_payment_link_id',
                    'updated_at',
                ]
            )
            if obj.status != 'APPROVED':
                obj.transition_to(
                    'APPROVED',
                    actor=request.user if request.user.is_authenticated else None,
                    note="Client confirmation reset; request returned to pre-confirmation review.",
                    metadata={
                        "action": "reset_client_confirmation",
                        "superseded_offers": superseded_count,
                    },
                )
            else:
                obj.add_audit_note(
                    "Client confirmation reset; request returned to pre-confirmation review.",
                    actor=request.user if request.user.is_authenticated else None,
                    metadata={
                        "action": "reset_client_confirmation",
                        "superseded_offers": superseded_count,
                    },
                )
            reset_count += 1
        self.message_user(request, f"{reset_count} request(s) reset to pre-confirmation review.")

    @admin.action(description="Generate Payment Offer")
    def generate_payment_offer(self, request, queryset):
        created = 0
        for obj in queryset:
            if self._is_locked_request(obj):
                messages.warning(request, f"Request {obj.id} is locked and cannot generate a payment offer.")
                continue
            if not obj.client_confirmed_at:
                messages.warning(
                    request,
                    f"Request {obj.id} cannot generate a payment offer until client confirmation is recorded.",
                )
                continue
            if obj.quoted_price is None or obj.quoted_price <= 0:
                messages.warning(request, f"Request {obj.id} needs a positive quoted price before generating a payment offer.")
                continue
            current_offer = get_current_offer(obj)
            if current_offer:
                messages.info(request, f"Request {obj.id} already has an active payment offer for the current scope.")
                continue
            latest_offer = get_latest_offer(obj)
            if latest_offer:
                messages.warning(
                    request,
                    f"Request {obj.id} already has offer history. Use Regenerate Payment Offer to issue a fresh version.",
                )
                continue
            try:
                offer = self._issue_new_offer(request, obj)
                obj.save(
                    update_fields=[
                        'stripe_payment_link',
                        'stripe_payment_link_id',
                        'ai_payment_draft_response',
                        'payment_email_sent_at',
                        'last_payment_email_body',
                        'agreed_scope_snapshot',
                        'updated_at',
                    ]
                )
                obj.add_audit_note(
                    f"Payment offer v{offer.version} created.",
                    actor=request.user if request.user.is_authenticated else None,
                    metadata={
                        "action": "payment_offer_created",
                        "offer_version": offer.version,
                        "payment_link_id": offer.stripe_payment_link_id,
                        "expires_at": offer.expires_at.isoformat() if offer.expires_at else None,
                    },
                )
                created += 1
            except stripe.error.StripeError as exc:
                messages.error(request, f"Failed to issue Stripe offer for request {obj.id}: {exc.user_message or str(exc)}")
            except Exception as exc:
                messages.error(request, f"Failed to issue Stripe offer for request {obj.id}: {exc}")
        self.message_user(request, f"{created} payment offer(s) created.")

    @admin.action(description="Regenerate Payment Offer")
    def regenerate_payment_offer(self, request, queryset):
        regenerated = 0
        for obj in queryset:
            if self._is_locked_request(obj):
                messages.warning(request, f"Request {obj.id} is locked and cannot regenerate a payment offer.")
                continue
            if not obj.client_confirmed_at:
                messages.warning(
                    request,
                    f"Request {obj.id} cannot regenerate a payment offer until client confirmation is recorded.",
                )
                continue
            if obj.quoted_price is None or obj.quoted_price <= 0:
                messages.warning(request, f"Request {obj.id} needs a positive quoted price before regenerating a payment offer.")
                continue
            previous_offer = get_latest_offer(obj)
            if not previous_offer:
                messages.warning(
                    request,
                    f"Request {obj.id} does not have an existing offer to regenerate. Use Generate Payment Offer first.",
                )
                continue
            try:
                offer = self._issue_new_offer(request, obj)
                obj.save(
                    update_fields=[
                        'stripe_payment_link',
                        'stripe_payment_link_id',
                        'ai_payment_draft_response',
                        'payment_email_sent_at',
                        'last_payment_email_body',
                        'agreed_scope_snapshot',
                        'updated_at',
                    ]
                )
                obj.add_audit_note(
                    f"Payment offer regenerated as v{offer.version}.",
                    actor=request.user if request.user.is_authenticated else None,
                    metadata={
                        "action": "payment_offer_regenerated",
                        "offer_version": offer.version,
                        "previous_offer_version": previous_offer.version,
                        "payment_link_id": offer.stripe_payment_link_id,
                        "expires_at": offer.expires_at.isoformat() if offer.expires_at else None,
                    },
                )
                regenerated += 1
            except stripe.error.StripeError as exc:
                messages.error(request, f"Failed to regenerate Stripe offer for request {obj.id}: {exc.user_message or str(exc)}")
            except Exception as exc:
                messages.error(request, f"Failed to regenerate Stripe offer for request {obj.id}: {exc}")
        self.message_user(request, f"{regenerated} payment offer(s) regenerated.")

    @admin.action(description="Generate Payment Email Draft")
    def generate_payment_email_draft(self, request, queryset):
        updated = 0
        for obj in queryset:
            if self._is_locked_request(obj):
                messages.warning(request, f"Request {obj.id} is locked and cannot request a payment email draft.")
                continue
            if not obj.client_confirmed_at and obj.status != 'PAYMENT_PENDING':
                messages.warning(request, f"Request {obj.id} must be client-confirmed before generating a payment email draft.")
                continue
            offer = get_current_offer(obj)
            if not offer or not offer.stripe_payment_link_url:
                latest_offer = get_latest_offer(obj)
                if latest_offer and latest_offer.is_expired:
                    messages.warning(
                        request,
                        f"Request {obj.id} has an expired payment offer. Regenerate the offer before requesting a payment email draft.",
                    )
                else:
                    messages.warning(request, f"Request {obj.id} does not have a valid current payment offer yet.")
                continue
            obj.ai_payment_draft_response = None
            obj.save(update_fields=['ai_payment_draft_response', 'updated_at'])
            obj.add_audit_note(
                "Payment email draft requested.",
                actor=request.user if request.user.is_authenticated else None,
                metadata={
                    "draft_mode": "payment_link",
                    "offer_version": offer.version if offer else None,
                    "expires_at": offer.expires_at.isoformat() if offer and offer.expires_at else None,
                },
            )
            updated += 1
        self.message_user(request, f"{updated} request(s) queued for payment email drafting.")

    @admin.action(description="Send Payment Email")
    def send_payment_email(self, request, queryset):
        sent_count = 0
        for obj in queryset:
            if self._is_locked_request(obj):
                messages.warning(request, f"Request {obj.id} is locked and cannot send a payment email.")
                continue
            if not obj.client_confirmed_at and obj.status != 'PAYMENT_PENDING':
                messages.warning(request, f"Request {obj.id} must be client-confirmed before sending a payment email.")
                continue
            offer = get_current_offer(obj)
            if not offer or not offer.stripe_payment_link_url:
                latest_offer = get_latest_offer(obj)
                if latest_offer and latest_offer.is_expired:
                    messages.warning(
                        request,
                        f"Request {obj.id} has an expired payment offer. Regenerate the offer before sending a payment email.",
                    )
                else:
                    messages.warning(request, f"Request {obj.id} does not have a valid current payment offer yet.")
                continue
            if not (obj.ai_payment_draft_response or "").strip():
                messages.warning(request, f"Request {obj.id} does not have a payment email draft to send.")
                continue
            try:
                body = send_licence_quote_email(obj)
            except Exception as exc:
                messages.error(request, f"Failed to send payment email for request {obj.id}: {exc}")
                continue

            obj.payment_email_sent_at = timezone.now()
            obj.last_payment_email_body = body
            obj.save(update_fields=['payment_email_sent_at', 'last_payment_email_body', 'updated_at'])
            if obj.status != 'PAYMENT_PENDING':
                obj.transition_to(
                    'PAYMENT_PENDING',
                    actor=request.user if request.user.is_authenticated else None,
                    note=f"Payment email sent for offer v{offer.version if offer else 'legacy'}.",
                    metadata={
                        "action": "payment_email_sent",
                        "offer_version": offer.version if offer else None,
                    },
                )
            else:
                obj.add_audit_note(
                    "Payment email sent to client.",
                    actor=request.user if request.user.is_authenticated else None,
                    metadata={
                        "action": "payment_email_sent",
                        "offer_version": offer.version if offer else None,
                    },
                )
            sent_count += 1
        self.message_user(request, f"{sent_count} payment email(s) sent.")

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
        'expires_at',
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
        'expires_at',
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


class VideoUploadSessionAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename",
        "purpose",
        "status",
        "target_video",
        "created_by",
        "created_at",
        "completed_at",
    )
    list_filter = ("purpose", "status", "created_at")
    search_fields = ("original_filename", "object_key", "upload_id", "target_video__title")
    readonly_fields = (
        "created_by",
        "target_video",
        "original_filename",
        "object_key",
        "upload_id",
        "purpose",
        "status",
        "file_size",
        "content_type",
        "part_size",
        "error_message",
        "created_at",
        "updated_at",
        "completed_at",
        "aborted_at",
    )


custom_admin_site.register(VideoUploadSession, VideoUploadSessionAdmin)

