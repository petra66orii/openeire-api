from django.urls import path
from .views import GalleryListView

urlpatterns = [
    path('gallery/', GalleryListView.as_view(), name='gallery_list'),
]