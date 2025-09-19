from rest_framework import serializers
from .models import Photo, Video, Product

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