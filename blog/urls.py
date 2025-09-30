from django.urls import path
from .views import BlogPostListView, BlogPostDetailView, CommentListCreateView

urlpatterns = [
    path('posts/', BlogPostListView.as_view(), name='blog_post_list'),
    path('posts/<slug:slug>/', BlogPostDetailView.as_view(), name='blog_post_detail'),
    path('posts/<slug:slug>/comments/', CommentListCreateView.as_view(), name='comment_list_create'),
]