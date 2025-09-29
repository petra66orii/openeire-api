from rest_framework import generics
from rest_framework.permissions import AllowAny
from .models import BlogPost
from .serializers import BlogPostListSerializer, BlogPostDetailSerializer

class BlogPostListView(generics.ListAPIView):
    """
    API endpoint to list all published blog posts.
    """
    queryset = BlogPost.objects.filter(status=1).order_by('-created_at')
    serializer_class = BlogPostListSerializer
    permission_classes = [AllowAny]


class BlogPostDetailView(generics.RetrieveAPIView):
    """
    API endpoint to retrieve a single published blog post by its slug.
    """
    queryset = BlogPost.objects.filter(status=1)
    serializer_class = BlogPostDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = 'slug'