from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.shortcuts import get_object_or_404
from .models import BlogPost, Comment
from .serializers import BlogPostListSerializer, BlogPostDetailSerializer, CommentSerializer
from products.views import CustomPagination

class BlogPostListView(generics.ListAPIView):
    """
    API endpoint to list all published blog posts.
    Supports filtering by tag via query param: /api/blog/?tag=travel
    """
    serializer_class = BlogPostListSerializer
    permission_classes = [AllowAny]
    pagination_class = CustomPagination

    def get_queryset(self):
        # Start with all published posts
        queryset = BlogPost.objects.filter(status=1).order_by('-created_at')
        
        # Check if a tag was provided in the URL
        tag_slug = self.request.query_params.get('tag')
        if tag_slug:
            # Filter by the tag slug (slug is usually the tag name in lowercase)
            queryset = queryset.filter(tags__slug__in=[tag_slug])
            
        return queryset

class BlogPostDetailView(generics.RetrieveAPIView):
    queryset = BlogPost.objects.filter(status=1)
    serializer_class = BlogPostDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = 'slug'

class BlogPostLikeView(APIView):
    """
    POST: Toggle like status for a specific blog post.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, slug):
        post = get_object_or_404(BlogPost, slug=slug)
        user = request.user

        if post.likes.filter(id=user.id).exists():
            # Already liked? Remove it (Unlike)
            post.likes.remove(user)
            liked = False
        else:
            # Not liked? Add it (Like)
            post.likes.add(user)
            liked = True
        
        return Response({
            'liked': liked, 
            'likes_count': post.number_of_likes()
        }, status=status.HTTP_200_OK)

class CommentListCreateView(generics.ListCreateAPIView):
    serializer_class = CommentSerializer

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated()]
        return [AllowAny()]

    def get_queryset(self):
        post_slug = self.kwargs['slug']
        return Comment.objects.filter(post__slug=post_slug, approved=True)

    def perform_create(self, serializer):
        post = get_object_or_404(BlogPost, slug=self.kwargs['slug'])
        serializer.save(user=self.request.user, post=post)