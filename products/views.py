from rest_framework import generics
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny 
from .models import Photo, Video, Product
from .serializers import PhotoListSerializer, VideoListSerializer, ProductListSerializer, PhotoDetailSerializer, VideoDetailSerializer, ProductDetailSerializer

class CustomPagination(PageNumberPagination):
    page_size = 10 # Number of items per page
    page_size_query_param = 'page_size'
    max_page_size = 100

class GalleryListView(generics.ListAPIView):
    """
    API endpoint to list all photos, videos, and physical products.
    Supports filtering by 'type' (digital, physical) and pagination.
    """
    permission_classes = [AllowAny]
    pagination_class = CustomPagination

    def get_queryset(self):
        queryset = []
        product_type = self.request.query_params.get('type') # 'digital' or 'physical'

        if product_type == 'digital':
            photos = Photo.objects.all()
            videos = Video.objects.all()
            # For digital, we combine photos and videos
            for photo in photos:
                queryset.append({'item': photo, 'serializer': PhotoListSerializer(photo)})
            for video in videos:
                queryset.append({'item': video, 'serializer': VideoListSerializer(video)})
        elif product_type == 'physical':
            # For physical, we list all 'Product' instances (prints)
            products = Product.objects.all()
            for product in products:
                queryset.append({'item': product, 'serializer': ProductListSerializer(product)})
        else:
            # If no type or 'all' is requested, list everything (for a general gallery)
            photos = Photo.objects.all()
            videos = Video.objects.all()
            products = Product.objects.all()
            
            for photo in photos:
                queryset.append({'item': photo, 'serializer': PhotoListSerializer(photo)})
            for video in videos:
                queryset.append({'item': video, 'serializer': VideoListSerializer(video)})
            for product in products:
                queryset.append({'item': product, 'serializer': ProductListSerializer(product)})
        
        # We'll need to sort this combined list if we want a consistent order
        # For now, it will be the order in which they were appended.
        return queryset

    def list(self, request, *args, **kwargs):
        # get_queryset returns a list of dictionaries, each containing an item and its serializer
        queryset_with_serializers = self.get_queryset()

        # Extract just the serialized data
        data = [item['serializer'].data for item in queryset_with_serializers]
        
        # Now, we manually apply pagination since our queryset is a combined list
        page = self.paginate_queryset(data)
        if page is not None:
            return self.get_paginated_response(page)

        return Response(data)
    

class PhotoDetailView(generics.RetrieveAPIView):
    queryset = Photo.objects.all()
    serializer_class = PhotoDetailSerializer
    permission_classes = [AllowAny]

class VideoDetailView(generics.RetrieveAPIView):
    queryset = Video.objects.all()
    serializer_class = VideoDetailSerializer
    permission_classes = [AllowAny]

class ProductDetailView(generics.RetrieveAPIView):
    queryset = Product.objects.all()
    serializer_class = ProductDetailSerializer
    permission_classes = [AllowAny]