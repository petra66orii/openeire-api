from django.urls import path
from .views import BlogPostListView, BlogPostDetailView, CommentListCreateView, BlogPostLikeView, LikedPostsView

urlpatterns = [
    path('', BlogPostListView.as_view(), name='blog_post_list'),
    path('liked/', LikedPostsView.as_view(), name='liked_posts'),
    path('<slug:slug>/', BlogPostDetailView.as_view(), name='blog_post_detail'),
    path('<slug:slug>/like/', BlogPostLikeView.as_view(), name='blog_like'),
    path('<slug:slug>/comments/', CommentListCreateView.as_view(), name='comment_list_create'),
]