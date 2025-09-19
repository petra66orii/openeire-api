from django.urls import path
from .views import GalleryListView, PhotoDetailView, VideoDetailView, ProductDetailView

urlpatterns = [
    path('gallery/', GalleryListView.as_view(), name='gallery_list'),
    path('photos/<int:pk>/', PhotoDetailView.as_view(), name='photo_detail'),
    path('videos/<int:pk>/', VideoDetailView.as_view(), name='video_detail'),
    path('products/<int:pk>/', ProductDetailView.as_view(), name='product_detail')
]