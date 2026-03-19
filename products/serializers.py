import re
from rest_framework import serializers
from .models import (
    Photo,
    Video,
    ProductVariant,
    ProductReview,
    LicenseRequest,
    AI_DRAFT_MAX_CHARS,
)
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.db.models import Avg, Min


REACH_CAP_PATTERNS = [
    re.compile(r"(?im)\breach(?:\s*cap)?\s*[:=-]\s*([^\n\r]{1,120})"),
    re.compile(r"(?im)\breach(?:\s*cap)?\s+(?:of\s+)?([0-9][0-9,\.\s]*(?:k|m|million|billion)?)\b"),
]

PERSONAL_CHECKOUT_FLOW = "PERSONAL_CHECKOUT"
COMMERCIAL_REQUEST_FLOW = "COMMERCIAL_REQUEST"
PHYSICAL_PRINT_CHECKOUT_FLOW = "PHYSICAL_PRINT_CHECKOUT"


def extract_reach_caps_from_message(message):
    if not message:
        return None
    text = str(message)
    for pattern in REACH_CAP_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = " ".join(match.group(1).strip().split())
        value = value.strip(" .;,")
        if not value:
            continue
        normalized = value.lower()
        if normalized in {"none", "n/a", "na", "no cap", "unlimited"}:
            return "NONE"
        return value
    return None

# 1. Helper Serializer for Variants (Nested inside Photo)
class ProductVariantSerializer(serializers.ModelSerializer):
    """
    Serializes the physical options (Size/Material/Price).
    Includes 'display' fields for human-readable labels in the UI.
    """
    material_display = serializers.CharField(source='get_material_display', read_only=True)
    size_display = serializers.CharField(source='get_size_display', read_only=True)
    product_type = serializers.CharField(default='physical', read_only=True)
    purchase_flows = serializers.SerializerMethodField()
    default_purchase_flow = serializers.CharField(default=PHYSICAL_PRINT_CHECKOUT_FLOW, read_only=True)

    class Meta:
        model = ProductVariant
        fields = (
            'id', 
            'material', 
            'material_display', 
            'size', 
            'size_display', 
            'price', 
            'sku',
            'product_type',
            'purchase_flows',
            'default_purchase_flow',
        )

    def get_purchase_flows(self, obj):
        return [PHYSICAL_PRINT_CHECKOUT_FLOW]

# 2. List Serializers (For catalog pages)
class PhotoListSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='photo', read_only=True)
    starting_price = serializers.DecimalField(max_digits=6, decimal_places=2, read_only=True)
    purchase_flows = serializers.SerializerMethodField()
    default_purchase_flow = serializers.CharField(default=PERSONAL_CHECKOUT_FLOW, read_only=True)

    class Meta:
        model = Photo
        fields = (
            'id',
            'title',
            'description',
            'collection',
            'preview_image',
            'price',
            'product_type',
            'starting_price',
            'purchase_flows',
            'default_purchase_flow',
        )

    def get_purchase_flows(self, obj):
        return [PERSONAL_CHECKOUT_FLOW, COMMERCIAL_REQUEST_FLOW]


class PhysicalPhotoListSerializer(PhotoListSerializer):
    product_type = serializers.CharField(default='physical', read_only=True)
    default_purchase_flow = serializers.CharField(default=PHYSICAL_PRINT_CHECKOUT_FLOW, read_only=True)

    class Meta(PhotoListSerializer.Meta):
        fields = (
            'id',
            'title',
            'description',
            'collection',
            'preview_image',
            'starting_price',
            'product_type',
            'purchase_flows',
            'default_purchase_flow',
        )

    def get_purchase_flows(self, obj):
        return [PHYSICAL_PRINT_CHECKOUT_FLOW]


class PhysicalPhotoEmbeddedSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='physical', read_only=True)
    purchase_flows = serializers.SerializerMethodField()
    default_purchase_flow = serializers.CharField(default=PHYSICAL_PRINT_CHECKOUT_FLOW, read_only=True)

    class Meta:
        model = Photo
        fields = (
            'id',
            'title',
            'description',
            'collection',
            'preview_image',
            'tags',
            'created_at',
            'product_type',
            'purchase_flows',
            'default_purchase_flow',
        )

    def get_purchase_flows(self, obj):
        return [PHYSICAL_PRINT_CHECKOUT_FLOW]


class VideoListSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='video', read_only=True)
    purchase_flows = serializers.SerializerMethodField()
    default_purchase_flow = serializers.CharField(default=PERSONAL_CHECKOUT_FLOW, read_only=True)
    
    class Meta:
        model = Video
        fields = (
            'id',
            'title',
            'description',
            'thumbnail_image',
            'collection',
            'price',
            'product_type',
            'duration',
            'resolution',
            'frame_rate',
            'purchase_flows',
            'default_purchase_flow',
        )

    def get_purchase_flows(self, obj):
        return [PERSONAL_CHECKOUT_FLOW, COMMERCIAL_REQUEST_FLOW]

class LicenseRequestSerializer(serializers.ModelSerializer):
    asset_type = serializers.CharField(write_only=True)
    asset_id = serializers.IntegerField(min_value=1, write_only=True)
    message = serializers.CharField(max_length=2000, allow_blank=True, allow_null=True, required=False)

    class Meta:
        model = LicenseRequest
        fields = [
            'id', 'client_name', 'company', 'email', 'project_type', 
            'duration', 'territory', 'permitted_media', 'exclusivity', 'reach_caps', 'message', 'asset_type', 'asset_id', 'status', 'created_at'
        ]
        # 👇 Prevents users from injecting status, prices, or AI drafts via POST
        read_only_fields = ['id', 'status', 'created_at']

    def validate(self, data):
        asset_type = data.pop('asset_type')
        asset_id = data.pop('asset_id')
        asset_type = asset_type.strip().lower()
        if asset_type not in {'photo', 'video'}:
            raise serializers.ValidationError({"asset_type": "Invalid asset type."})
        
        try:
            content_type = ContentType.objects.get(app_label='products', model=asset_type)
            model_class = content_type.model_class()
            
            # 👇 IDOR Prevention: Ensure the specific media asset actually exists
            if not model_class.objects.filter(id=asset_id, is_active=True).exists():
                raise serializers.ValidationError({"asset_id": "The requested media asset does not exist."})
                
            # Attach the resolved content type and ID to the validated data
            data['content_type'] = content_type
            data['object_id'] = asset_id

            email = (data.get('email') or '').strip().lower()
            if email:
                data['email'] = email
            if email:
                existing_qs = LicenseRequest.objects.filter(
                    content_type=content_type,
                    object_id=asset_id,
                    email=email,
                )
                if existing_qs.exclude(status__in=['REJECTED', 'REVOKED', 'EXPIRED']).exists():
                    raise serializers.ValidationError(
                        {"email": "A request for this asset already exists for this email."}
                    )
                rejected_count = existing_qs.filter(status='REJECTED').count()
                if rejected_count >= 3:
                    raise serializers.ValidationError(
                        {"email": "This email has reached the maximum number of rejected requests for this asset."}
                    )

            reach_caps = (data.get('reach_caps') or '').strip()
            if not reach_caps or reach_caps.upper() in {'NONE', 'N/A', 'NA'}:
                inferred_reach_caps = extract_reach_caps_from_message(data.get('message'))
                if inferred_reach_caps:
                    data['reach_caps'] = inferred_reach_caps
                else:
                    data['reach_caps'] = 'NONE'
            
        except ContentType.DoesNotExist:
            raise serializers.ValidationError({"asset_type": "Invalid asset type."})

        return data

    def create(self, validated_data):
        try:
            return super().create(validated_data)
        except IntegrityError:
            raise serializers.ValidationError(
                {"email": "A request for this asset already exists for this email."}
            )


class AIDraftUpdateSerializer(serializers.Serializer):
    draft_text = serializers.CharField(
        max_length=AI_DRAFT_MAX_CHARS,
        allow_blank=False,
        trim_whitespace=True
    )

    def validate_draft_text(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("draft_text cannot be empty.")
        return value

class ProductListSerializer(serializers.ModelSerializer):
    """
    Used if you want to list specific physical variants directly.
    """
    product_type = serializers.CharField(default='physical', read_only=True)
    # Fetch title/image from the parent Photo
    title = serializers.CharField(source='photo.title', read_only=True)
    preview_image = serializers.ImageField(source='photo.preview_image', read_only=True)

    photo_id = serializers.IntegerField(source='photo.id', read_only=True) 
    material_display = serializers.CharField(source='get_material_display', read_only=True)
    size_display = serializers.CharField(source='get_size_display', read_only=True)
    purchase_flows = serializers.SerializerMethodField()
    default_purchase_flow = serializers.CharField(default=PHYSICAL_PRINT_CHECKOUT_FLOW, read_only=True)

    class Meta:
        model = ProductVariant
        fields = (
            'id', 'title', 'preview_image', 'price', 'product_type', 
            'material', 'material_display', 'size', 'size_display', 'photo_id',
            'purchase_flows', 'default_purchase_flow',
        )

    def get_purchase_flows(self, obj):
        return [PHYSICAL_PRINT_CHECKOUT_FLOW]


# 3. Detail Serializers (For single product pages)
class PhotoDetailSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='photo', read_only=True)
    purchase_flows = serializers.SerializerMethodField()
    default_purchase_flow = serializers.CharField(default=PERSONAL_CHECKOUT_FLOW, read_only=True)
    
    # Existing Fields
    variants = ProductVariantSerializer(many=True, read_only=True)
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    
    related_products = serializers.SerializerMethodField()
    
    class Meta:
        model = Photo
        fields = (
            'id', 'title', 'description', 'collection', 'preview_image', 
            'price', 'tags', 'created_at',
            'product_type', 'variants', 'average_rating', 'review_count',
            'related_products', 'purchase_flows', 'default_purchase_flow',
        )

    def get_purchase_flows(self, obj):
        return [PERSONAL_CHECKOUT_FLOW, COMMERCIAL_REQUEST_FLOW]

    def get_average_rating(self, obj):
        reviews = ProductReview.objects.filter(
            content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.pk,
            approved=True
        )
        avg = reviews.aggregate(Avg('rating'))['rating__avg']
        return round(avg, 2) if avg is not None else 0
    
    def get_review_count(self, obj):
        return ProductReview.objects.filter(
            content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.pk,
            approved=True
        ).count()

    def get_related_products(self, obj):
        # Filter by collection, exclude current photo, randomize order, limit to 4
        qs = Photo.objects.filter(
            collection=obj.collection,
            is_active=True
        ).exclude(id=obj.id).order_by('?')[:4]
        # Reuse the existing List Serializer so the format matches your Grid Cards
        return PhotoListSerializer(qs, many=True, context=self.context).data


class VideoDetailSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='video', read_only=True)
    purchase_flows = serializers.SerializerMethodField()
    default_purchase_flow = serializers.CharField(default=PERSONAL_CHECKOUT_FLOW, read_only=True)
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    
    related_products = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = (
            'id', 'title', 'description', 'collection', 'thumbnail_image', 
            'price', 'tags', 'created_at',
            'product_type', 'average_rating', 'review_count',
            'duration', 'resolution', 'frame_rate',
            'related_products', 'purchase_flows', 'default_purchase_flow',
        )

    def get_purchase_flows(self, obj):
        return [PERSONAL_CHECKOUT_FLOW, COMMERCIAL_REQUEST_FLOW]
    
    def get_average_rating(self, obj):
        reviews = ProductReview.objects.filter(
            content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.pk,
            approved=True
        )
        avg = reviews.aggregate(Avg('rating'))['rating__avg']
        return round(avg, 2) if avg is not None else 0
    
    def get_review_count(self, obj):
        return ProductReview.objects.filter(
            content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.pk,
            approved=True
        ).count()
        
    def get_related_products(self, obj):
        qs = Video.objects.filter(
            collection=obj.collection,
            is_active=True
        ).exclude(id=obj.id).order_by('?')[:4]
        return VideoListSerializer(qs, many=True, context=self.context).data


class ProductDetailSerializer(serializers.ModelSerializer):
    """
    Serializer for a specific variant (e.g. A4 Canvas).
    """
    photo = PhysicalPhotoEmbeddedSerializer(read_only=True)
    product_type = serializers.CharField(default='physical', read_only=True)
    title = serializers.CharField(source='photo.title', read_only=True)
    description = serializers.CharField(source='photo.description', read_only=True)
    variants = ProductVariantSerializer(source='photo.variants', many=True, read_only=True)
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    purchase_flows = serializers.SerializerMethodField()
    default_purchase_flow = serializers.CharField(default=PHYSICAL_PRINT_CHECKOUT_FLOW, read_only=True)

    class Meta:
        model = ProductVariant
        fields = (
            'id', 'photo', 'title', 'description', 'material', 'size', 'price', 'sku', 
            'product_type', 'variants', 'average_rating', 'review_count',
            'purchase_flows', 'default_purchase_flow',
        )

    def get_purchase_flows(self, obj):
        return [PHYSICAL_PRINT_CHECKOUT_FLOW]

    def get_average_rating(self, obj):
        reviews = ProductReview.objects.filter(
            content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.pk,
            approved=True
        )
        avg = reviews.aggregate(Avg('rating'))['rating__avg']
        return round(avg, 2) if avg is not None else 0
    
    def get_review_count(self, obj):
        return ProductReview.objects.filter(
            content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.pk,
            approved=True
        ).count()


class PhysicalPhotoDetailSerializer(PhotoDetailSerializer):
    """
    Physical print page payload for Photo records.
    Keeps the base shape but exposes a physical purchase contract.
    """
    product_type = serializers.CharField(default='physical', read_only=True)
    default_purchase_flow = serializers.CharField(default=PHYSICAL_PRINT_CHECKOUT_FLOW, read_only=True)

    class Meta(PhotoDetailSerializer.Meta):
        fields = (
            'id', 'title', 'description', 'collection', 'preview_image',
            'tags', 'created_at', 'product_type', 'variants', 'average_rating',
            'review_count', 'related_products', 'purchase_flows',
            'default_purchase_flow',
        )

    def get_purchase_flows(self, obj):
        return [PHYSICAL_PRINT_CHECKOUT_FLOW]

    def get_related_products(self, obj):
        qs = Photo.objects.filter(
            collection=obj.collection,
            is_active=True,
            variants__isnull=False,
        ).exclude(id=obj.id).annotate(
            starting_price=Min('variants__price')
        ).distinct().order_by('?')[:4]
        return PhysicalPhotoListSerializer(qs, many=True, context=self.context).data

class ProductReviewSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.username')

    class Meta:
        model = ProductReview
        fields = ['id', 'user', 'rating', 'comment', 'created_at', 'admin_reply']
        read_only_fields = ['user', 'created_at', 'admin_reply']

    def validate(self, data):
        product = self.context['product']
        user = self.context['request'].user
        
        content_type = ContentType.objects.get_for_model(product)

        if ProductReview.objects.filter(content_type=content_type, object_id=product.pk, user=user).exists():
            raise serializers.ValidationError("You have already reviewed this product.")
        
        return data
