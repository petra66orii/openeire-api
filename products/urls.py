from django.urls import path
from .views import (
    GalleryListView,
    DigitalPhotoDetailView,
    PhysicalPhotoDetailView,
    VideoDetailView,
    ProductDetailView,
    ProductReviewListCreateView,
    RequestGalleryAccessView,
    VerifyGalleryAccessView
)

urlpatterns = [
    path('gallery-request/', RequestGalleryAccessView.as_view(), name='gallery_request'),
    path('gallery-verify/', VerifyGalleryAccessView.as_view(), name='gallery_verify'),
    path('gallery/', GalleryListView.as_view(), name='gallery_list'),
    path('photos/<int:pk>/', DigitalPhotoDetailView.as_view(), name='photo_detail'),
    path('videos/<int:pk>/', VideoDetailView.as_view(), name='video_detail'),
    path('products/<int:pk>/', PhysicalPhotoDetailView.as_view(), name='physical_product_page'),
    path('variants/<int:pk>/', ProductDetailView.as_view(), name='variant_detail'),
    path('<str:product_type>/<int:pk>/reviews/', ProductReviewListCreateView.as_view(), name='review_list_create'),
]