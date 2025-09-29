from rest_framework import serializers
from .models import BlogPost

class BlogPostListSerializer(serializers.ModelSerializer):
    """
    Serializer for the list view of blog posts (condensed).
    """
    author = serializers.ReadOnlyField(source='author.username')

    class Meta:
        model = BlogPost
        fields = ['id', 'title', 'slug', 'author', 'featured_image', 'excerpt', 'created_at']


class BlogPostDetailSerializer(serializers.ModelSerializer):
    """
    Serializer for the detail view of a single blog post (full content).
    """
    author = serializers.ReadOnlyField(source='author.username')

    class Meta:
        model = BlogPost
        fields = ['id', 'title', 'slug', 'author', 'featured_image', 'content', 'created_at', 'updated_at']