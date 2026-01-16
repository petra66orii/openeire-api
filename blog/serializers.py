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
    Includes 'related_posts' based on shared tags.
    """
    author = serializers.ReadOnlyField(source='author.username')
    tags = TagListSerializerField()
    likes_count = serializers.IntegerField(source='number_of_likes', read_only=True)
    has_liked = serializers.SerializerMethodField()
    related_posts = serializers.SerializerMethodField()

    class Meta:
        model = BlogPost
        fields = [
            'id', 'title', 'slug', 'author', 'featured_image', 'content', 
            'created_at', 'updated_at', 'tags', 'likes_count', 'has_liked',
            'related_posts' 
        ]

    def get_has_liked(self, obj):
        user = self.context.get('request').user
        if user.is_authenticated:
            return obj.likes.filter(id=user.id).exists()
        return False

    def get_related_posts(self, obj):
        # 1. Ask taggit for similar objects (based on shared tags)
        # This returns a list of similar BlogPost objects, excluding the current one automatically.
        related = obj.tags.similar_objects()
        
        # 2. Take the top 3 matches
        top_3 = related[:3]
        
        # 3. Manually serialize the essential data
        # We do this manually to avoid circular import issues or unnecessary overhead
        return [{
            'title': post.title,
            'slug': post.slug,
            'featured_image': post.featured_image.url if post.featured_image else None,
            'created_at': post.created_at
        } for post in top_3]

class CommentSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.username')

    class Meta:
        model = Comment
        fields = ['id', 'user', 'content', 'created_at']
        read_only_fields = ['id', 'user', 'created_at']