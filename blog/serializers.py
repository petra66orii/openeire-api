from rest_framework import serializers
from .models import BlogPost, Comment
from taggit.serializers import (TagListSerializerField, TaggitSerializer)

class BlogPostListSerializer(TaggitSerializer, serializers.ModelSerializer):
    """
    Serializer for the list view (condensed).
    Includes tags and total like count.
    """
    author = serializers.ReadOnlyField(source='author.username')
    tags = TagListSerializerField()
    likes_count = serializers.IntegerField(source='number_of_likes', read_only=True)

    class Meta:
        model = BlogPost
        fields = ['id', 'title', 'slug', 'author', 'featured_image', 'excerpt', 'created_at', 'tags', 'likes_count']


class BlogPostDetailSerializer(TaggitSerializer, serializers.ModelSerializer):
    """
    Serializer for the full post view.
    Includes 'has_liked' to tell the frontend if the current user liked it.
    """
    author = serializers.ReadOnlyField(source='author.username')
    tags = TagListSerializerField()
    likes_count = serializers.IntegerField(source='number_of_likes', read_only=True)
    has_liked = serializers.SerializerMethodField()

    class Meta:
        model = BlogPost
        fields = [
            'id', 'title', 'slug', 'author', 'featured_image', 'content', 
            'created_at', 'updated_at', 'tags', 'likes_count', 'has_liked'
        ]

    def get_has_liked(self, obj):
        # We access the request object via 'context'
        user = self.context.get('request').user
        if user.is_authenticated:
            return obj.likes.filter(id=user.id).exists()
        return False

class CommentSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.username')

    class Meta:
        model = Comment
        fields = ['id', 'user', 'content', 'created_at']
        read_only_fields = ['id', 'user', 'created_at']