from rest_framework import generics
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny 
from .models import Photo, Video, Product
from .serializers import PhotoListSerializer, VideoListSerializer, ProductListSerializer, PhotoDetailSerializer, VideoDetailSerializer, ProductDetailSerializer, ProductReviewSerializer
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
        collection = self.request.query_params.get('collection') # <-- Get the collection filter

        # Start with the base querysets
        photos = Photo.objects.all()
        videos = Video.objects.all()
        products = Product.objects.all()

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

class ProductReviewCreateView(generics.CreateAPIView):
    """
    API endpoint to allow a user to create a review for a product.
    """
    serializer_class = ProductReviewSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_context(self):
        """
        Pass the product object to the serializer.
        """
        # Get product_type and pk from the URL
        product_type_str = self.kwargs.get('product_type')
        product_pk = self.kwargs.get('pk')
        
        # Determine the model class based on the product_type string
        if product_type_str == 'photo':
            model_class = Photo
        elif product_type_str == 'video':
            model_class = Video
        elif product_type_str == 'product':
            model_class = Product
        else:
            # Handle invalid type if necessary
            return None 
        
        # Get the product instance
        product = generics.get_object_or_404(model_class.objects.all(), pk=product_pk)
        
        return {'request': self.request, 'product': product}

    def perform_create(self, serializer):
        """
        Associate the review with the product and the authenticated user.
        """
        product = self.get_serializer_context()['product']
        serializer.save(user=self.request.user, product=product)