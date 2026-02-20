from rest_framework import serializers
from .models import Photo, Video, ProductVariant, ProductReview
from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg

# 1. Helper Serializer for Variants (Nested inside Photo)
class ProductVariantSerializer(serializers.ModelSerializer):
    """
    Serializes the physical options (Size/Material/Price).
    Includes 'display' fields for human-readable labels in the UI.
    """
    material_display = serializers.CharField(source='get_material_display', read_only=True)
    size_display = serializers.CharField(source='get_size_display', read_only=True)
    product_type = serializers.CharField(default='physical', read_only=True)

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
            'product_type'
        )

# 2. List Serializers (For catalog pages)
class PhotoListSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='photo', read_only=True)
    starting_price = serializers.DecimalField(max_digits=6, decimal_places=2, read_only=True)

    class Meta:
        model = Photo
        fields = ('id', 'title', 'description', 'collection', 'preview_image', 'price_hd', 'price_4k', 'product_type', 'starting_price')


class VideoListSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='video', read_only=True)
    
    class Meta:
        model = Video
        fields = ('id', 'title', 'description', 'thumbnail_image', 'collection', 'price_hd', 'price_4k', 'product_type', 'video_file', 'duration', 'resolution', 'frame_rate')


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

    class Meta:
        model = ProductVariant
        fields = (
            'id', 'title', 'preview_image', 'price', 'product_type', 
            'material', 'material_display', 'size', 'size_display', 'photo_id'
        )


# 3. Detail Serializers (For single product pages)
class PhotoDetailSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='photo', read_only=True)
    
    # Existing Fields
    variants = ProductVariantSerializer(many=True, read_only=True)
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    
    related_products = serializers.SerializerMethodField()
    
    class Meta:
        model = Photo
        fields = (
            'id', 'title', 'description', 'collection', 'preview_image', 
            'high_res_file', 'price_hd', 'price_4k', 'tags', 'created_at',
            'product_type', 'variants', 'average_rating', 'review_count',
            'related_products'
        )

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
        qs = Photo.objects.filter(collection=obj.collection).exclude(id=obj.id).order_by('?')[:4]
        # Reuse the existing List Serializer so the format matches your Grid Cards
        return PhotoListSerializer(qs, many=True, context=self.context).data


class VideoDetailSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='video', read_only=True)
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    
    related_products = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = (
            'id', 'title', 'description', 'collection', 'thumbnail_image', 
            'video_file', 'price_hd', 'price_4k', 'tags', 'created_at',
            'product_type', 'average_rating', 'review_count',
            'duration', 'resolution', 'frame_rate',
            'related_products'
        )
    
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
        qs = Video.objects.filter(collection=obj.collection).exclude(id=obj.id).order_by('?')[:4]
        return VideoListSerializer(qs, many=True, context=self.context).data


class ProductDetailSerializer(serializers.ModelSerializer):
    """
    Serializer for a specific variant (e.g. A4 Canvas).
    """
    photo = PhotoDetailSerializer(read_only=True) 
    product_type = serializers.CharField(default='physical', read_only=True)
    title = serializers.CharField(source='photo.title', read_only=True)
    description = serializers.CharField(source='photo.description', read_only=True)
    variants = ProductVariantSerializer(source='photo.variants', many=True, read_only=True)
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = (
            'id', 'photo', 'title', 'description', 'material', 'size', 'price', 'sku', 
            'product_type', 'variants', 'average_rating', 'review_count'
        )

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