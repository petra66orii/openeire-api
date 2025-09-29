from rest_framework import generics
from django.db.models import Q
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny 
from .models import Photo, Video, Product, ProductReview
from django.http import Http404
from django.contrib.contenttypes.models import ContentType
from .serializers import (
    PhotoListSerializer,
    VideoListSerializer,
    ProductListSerializer,
    PhotoDetailSerializer,
    VideoDetailSerializer,
    ProductDetailSerializer,
    ProductReviewSerializer
)
from rest_framework.permissions import IsAuthenticated

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
        product_type = self.request.query_params.get('type')
        collection = self.request.query_params.get('collection')
        search_term = self.request.query_params.get('search')

        # Start with the base querysets
        photos = Photo.objects.all()
        videos = Video.objects.all()
        products = Product.objects.all()

        if search_term:
            # Create a query that searches title, description, and tags
            photo_video_query = (
                Q(title__icontains=search_term) |
                Q(description__icontains=search_term) |
                Q(tags__icontains=search_term)
            )
            # Apply the filter to Photo and Video querysets
            photos = photos.filter(photo_video_query)
            videos = videos.filter(photo_video_query)
            # For physical products, search the related photo's details
            products = products.filter(
                Q(photo__title__icontains=search_term) |
                Q(photo__description__icontains=search_term) |
                Q(photo__tags__icontains=search_term)
            )

        # Apply collection filter if it exists
        if collection and collection != 'all':
            photos = photos.filter(collection=collection)
            videos = videos.filter(collection=collection)
            # Physical products are linked to photos, so we filter the photos
            products = products.filter(photo__collection=collection)

        # The rest of the logic remains the same
        if product_type == 'digital':
            for photo in photos:
                queryset.append({'item': photo, 'serializer': PhotoListSerializer(photo)})
            for video in videos:
                queryset.append({'item': video, 'serializer': VideoListSerializer(video)})
        elif product_type == 'physical':
            for product in products:
                queryset.append({'item': product, 'serializer': ProductListSerializer(product)})
        else: # All products
            for photo in photos:
                queryset.append({'item': photo, 'serializer': PhotoListSerializer(photo)})
            for video in videos:
                queryset.append({'item': video, 'serializer': VideoListSerializer(video)})
            for product in products:
                queryset.append({'item': product, 'serializer': ProductListSerializer(product)})
        
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

class ProductReviewListCreateView(generics.ListCreateAPIView): # <-- Changed to ListCreateAPIView
    """
    API endpoint to list all APPROVED reviews for a specific product (GET)
    and allow an authenticated user to create a review (POST).
    """
    serializer_class = ProductReviewSerializer

    def get_permissions(self):
        """
        Set permissions based on the request method.
        GET requests (list reviews) are public (AllowAny).
        POST requests (create review) require authentication (IsAuthenticated).
        """
        if self.request.method == 'POST':
            permission_classes = [IsAuthenticated]
        else:
            permission_classes = [AllowAny]
        return [permission() for permission in permission_classes]

    def get_queryset(self):
        """
        Returns only APPROVED reviews for the specified product.
        """
        product = self.get_product_from_kwargs() # Re-use logic for getting product
        
        content_type = ContentType.objects.get_for_model(product)
        return ProductReview.objects.filter(
            content_type=content_type,
            object_id=product.pk,
            approved=True
        ).order_by('-created_at')

    def get_serializer_context(self):
        """
        Pass the product object to the serializer for validation and creation.
        """
        return {'request': self.request, 'product': self.get_product_from_kwargs()}

    def perform_create(self, serializer):
        """
        Associate the review with the product from the URL and the authenticated user.
        """
        product = self.get_product_from_kwargs()
        serializer.save(user=self.request.user, product=product)

    def get_product_from_kwargs(self):
        """Helper method to get the product instance from URL kwargs."""
        product_type_str = self.kwargs.get('product_type')
        product_pk = self.kwargs.get('pk')
        
        model_map = {'photo': Photo, 'video': Video, 'product': Product}
        model_class = model_map.get(product_type_str)
        
        if not model_class:
            raise Http404("Invalid product type.")
        
        return generics.get_object_or_404(model_class.objects.all(), pk=product_pk)
