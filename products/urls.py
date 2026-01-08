from django.urls import path
from .views import (
    GalleryListView,
    PhotoDetailView,
    VideoDetailView,
    ProductDetailView,
    ProductReviewListCreateView
)

urlpatterns = [
    path('gallery/', GalleryListView.as_view(), name='gallery_list'),
    path('photos/<int:pk>/', PhotoDetailView.as_view(), name='photo_detail'),
    path('videos/<int:pk>/', VideoDetailView.as_view(), name='video_detail'),
    path('products/<int:pk>/', PhotoDetailView.as_view(), name='physical_product_page'),
    path('variants/<int:pk>/', ProductDetailView.as_view(), name='variant_detail'),
    path('<str:product_type>/<int:pk>/reviews/', ProductReviewListCreateView.as_view(), name='review_list_create'),
]