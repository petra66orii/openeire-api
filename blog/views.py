from rest_framework import generics
from rest_framework.permissions import AllowAny, IsAuthenticated
from .models import BlogPost, Comment
from .serializers import BlogPostListSerializer, BlogPostDetailSerializer, CommentSerializer
from products.views import CustomPagination

class BlogPostListView(generics.ListAPIView):
    """
    API endpoint to list all published blog posts.
    """
    queryset = BlogPost.objects.filter(status=1).order_by('-created_at')
    serializer_class = BlogPostListSerializer
    permission_classes = [AllowAny]
    pagination_class = CustomPagination

class BlogPostDetailView(generics.RetrieveAPIView):
    """
    API endpoint to retrieve a single published blog post by its slug.
    """
    queryset = BlogPost.objects.filter(status=1)
    serializer_class = BlogPostDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = 'slug'


class CommentListCreateView(generics.ListCreateAPIView):
    """
    GET: List all approved comments for a specific blog post.
    POST: Create a new comment for a specific blog post (requires authentication).
    """
    serializer_class = CommentSerializer

    def get_permissions(self):
        if self.request.method == 'POST':
            # Only authenticated users can create comments
            return [IsAuthenticated()]
        # Anyone can view comments
        return [AllowAny()]

    def get_queryset(self):
        """
        Filter comments to only show approved ones for the post specified in the URL.
        """
        post_slug = self.kwargs['slug']
        return Comment.objects.filter(post__slug=post_slug, approved=True)

    def perform_create(self, serializer):
        """
        Associate the comment with the post from the URL and the logged-in user.
        """
        post = generics.get_object_or_404(BlogPost, slug=self.kwargs['slug'])
        serializer.save(user=self.request.user, post=post)