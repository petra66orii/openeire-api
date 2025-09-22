from rest_framework import serializers
from .models import Photo, Video, Product, ProductReview
from django.contrib.contenttypes.models import ContentType

class PhotoListSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='photo', read_only=True)

    class Meta:
        model = Photo
        fields = ('id', 'title', 'preview_image', 'price_hd', 'product_type')


class VideoListSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='video', read_only=True)
    
    class Meta:
        model = Video
        fields = ('id', 'title', 'thumbnail_image', 'price_hd', 'product_type')


class ProductListSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='physical', read_only=True)
    title = serializers.CharField(source='photo.title', read_only=True)
    preview_image = serializers.ImageField(source='photo.preview_image', read_only=True)

    class Meta:
        model = Product
        fields = ('id', 'title', 'preview_image', 'price', 'product_type', 'material', 'size')


class PhotoDetailSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='photo', read_only=True)
    
    class Meta:
        model = Photo
        fields = (
            'id', 'title', 'description', 'collection', 'preview_image', 
            'high_res_file', 'price_hd', 'price_4k', 'tags', 'created_at',
            'product_type'
        )

class VideoDetailSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(default='video', read_only=True)

    class Meta:
        model = Video
        fields = (
            'id', 'title', 'description', 'collection', 'thumbnail_image', 
            'video_file', 'price_hd', 'price_4k', 'tags', 'created_at',
            'product_type'
        )

class ProductDetailSerializer(serializers.ModelSerializer):
    # We want to display full photo details within the physical product
    photo = PhotoDetailSerializer(read_only=True) 
    product_type = serializers.CharField(default='physical', read_only=True)

    class Meta:
        model = Product
        fields = (
            'id', 'photo', 'material', 'size', 'price', 'sku', 'product_type'
        )

class ProductReviewSerializer(serializers.ModelSerializer):
    """
    Serializer for creating a product review.
    """
    user = serializers.ReadOnlyField(source='user.username')

    class Meta:
        model = ProductReview
        fields = ['id', 'user', 'rating', 'comment', 'created_at']
        read_only_fields = ['user', 'created_at']

    def validate(self, data):
        """
        Check that the user has not already reviewed this product.
        """
        # The product object is passed in the context from the view
        product = self.context['product']
        user = self.context['request'].user
        
        content_type = ContentType.objects.get_for_model(product)

        if ProductReview.objects.filter(content_type=content_type, object_id=product.pk, user=user).exists():
            raise serializers.ValidationError("You have already reviewed this product.")
        
        return data
