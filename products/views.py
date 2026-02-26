import os
from django.shortcuts import get_object_or_404
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.db import transaction
from django.db.models import Q, Exists, OuterRef, Min
from django.db.models.functions import Coalesce
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.conf import settings
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from .models import Photo, Video, ProductVariant, ProductReview, GalleryAccess, LicenseRequest
from checkout.models import OrderItem
from .serializers import (
    PhotoListSerializer,
    VideoListSerializer,
    PhotoDetailSerializer,
    VideoDetailSerializer,
    ProductDetailSerializer,
    ProductReviewSerializer,
    LicenseRequestSerializer,
    AIDraftUpdateSerializer,
)
from .permissions import IsDigitalGalleryAuthorized, IsAIWorkerAuthorized


class RequestGalleryAccessView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

        access_record = GalleryAccess.objects.create(email=email)

        # Ensure EMAIL_HOST_USER is set in settings.py
        send_mail(
            subject="OpenEire Studios - Private Gallery Access",
            message=f"Hello,\n\nHere is your access code for the Digital Stock Gallery:\n\n{access_record.access_code}\n\nValid for 30 days.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
        return Response({"message": "Code sent"}, status=status.HTTP_200_OK)

class VerifyGalleryAccessView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        code = request.data.get('access_code', '').upper().strip()
        try:
            access_record = GalleryAccess.objects.get(access_code=code)
            if access_record.is_valid:
                return Response({
                    "message": "Access granted", 
                    "expires_at": access_record.expires_at,
                    "valid": True
                })
            else:
                return Response({"error": "Code expired"}, status=status.HTTP_403_FORBIDDEN)
        except GalleryAccess.DoesNotExist:
            return Response({"error": "Invalid code"}, status=status.HTTP_404_NOT_FOUND)

class CustomPagination(PageNumberPagination):
    page_size = 10 # Number of items per page
    page_size_query_param = 'page_size'
    max_page_size = 100

class GalleryListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    # pagination_class = CustomPagination # Uncomment if you have this imported

    def get_queryset(self):
        queryset = []
        
        # 1. GET PARAMS
        product_type = self.request.query_params.get('type')
        collection = self.request.query_params.get('collection')
        search_term = self.request.query_params.get('search')
        sort_key = self.request.query_params.get('sort')

        # Default to physical if no type provided
        if not product_type or product_type == 'all':
            product_type = 'physical'

        # Digital Gate Check
        if product_type == 'digital':
            checker = IsDigitalGalleryAuthorized()
            if not checker.has_permission(self.request, self):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied(checker.message)

        # 2. INITIALIZE BASE QUERYSETS
        photos = Photo.objects.filter(is_active=True)
        videos = Video.objects.filter(is_active=True)

        # 3. APPLY COLLECTION FILTER (Case Insensitive)
        # Using __iexact fixes the "thailand" vs "Thailand" issue
        if collection and collection != 'all':
            photos = photos.filter(collection__iexact=collection)
            videos = videos.filter(collection__iexact=collection)

        # 4. APPLY SEARCH FILTER
        if search_term:
            query = (
                Q(title__icontains=search_term) |
                Q(description__icontains=search_term) |
                Q(tags__icontains=search_term)
            )
            photos = photos.filter(query)
            videos = videos.filter(query)

        # 5. SPLIT LOGIC BASED ON TYPE
        if product_type == 'digital':
            # --- DIGITAL LOGIC ---
            
            # Annotate prices for sorting
            photos = photos.annotate(display_price=Coalesce('price_hd', 'price_4k'))
            videos = videos.annotate(display_price=Coalesce('price_hd', 'price_4k'))

            # Apply Sorting
            if sort_key == 'price_asc':
                photos = photos.order_by('display_price')
                videos = videos.order_by('display_price')
            elif sort_key == 'price_desc':
                photos = photos.order_by('-display_price')
                videos = videos.order_by('-display_price')
            else: # date_desc
                photos = photos.order_by('-created_at')
                videos = videos.order_by('-created_at')

            # Serialize
            for photo in photos:
                queryset.append({'item': photo, 'serializer': PhotoListSerializer(photo)})
            for video in videos:
                queryset.append({'item': video, 'serializer': VideoListSerializer(video)})

        elif product_type == 'physical':
            # --- PHYSICAL LOGIC ---
            
            # Only get photos that have Physical Variants
            has_variants = ProductVariant.objects.filter(photo=OuterRef('pk'))
            
            # We filter the 'photos' queryset which has ALREADY been filtered by collection/search above
            physical_photos = photos.annotate(
                has_physical=Exists(has_variants),
                starting_price=Min('variants__price')
            ).filter(has_physical=True)

            # Apply Sorting
            if sort_key == 'price_asc':
                physical_photos = physical_photos.order_by('starting_price')
            elif sort_key == 'price_desc':
                physical_photos = physical_photos.order_by('-starting_price')
            else:
                physical_photos = physical_photos.order_by('-created_at')

            # Serialize
            for photo in physical_photos:
                queryset.append({'item': photo, 'serializer': PhotoListSerializer(photo)})

        return queryset

    def list(self, request, *args, **kwargs):
        queryset_dict = self.get_queryset()
        
        # Extract the data from the dictionary wrapper
        data = [item['serializer'].data for item in queryset_dict]
        
        # Manual Pagination since we built a custom list
        page = self.paginate_queryset(data)
        if page is not None:
            return self.get_paginated_response(page)
            
        return Response(data)
    

class DigitalPhotoDetailView(generics.RetrieveAPIView):
    queryset = Photo.objects.filter(is_active=True)
    serializer_class = PhotoDetailSerializer
    permission_classes = [IsDigitalGalleryAuthorized]

class PhysicalPhotoDetailView(generics.RetrieveAPIView):
    queryset = Photo.objects.filter(is_active=True)
    serializer_class = PhotoDetailSerializer
    permission_classes = [AllowAny]

class VideoDetailView(generics.RetrieveAPIView):
    queryset = Video.objects.filter(is_active=True)
    serializer_class = VideoDetailSerializer
    permission_classes = [IsDigitalGalleryAuthorized]

class LicenseRequestCreateView(generics.CreateAPIView):
    queryset = LicenseRequest.objects.all()
    serializer_class = LicenseRequestSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'license_request'

class AILicenseDraftQueueView(APIView):
    """Secure endpoint for the local AI worker to fetch pending requests."""
    authentication_classes = [] 
    permission_classes = [IsAIWorkerAuthorized]

    def get(self, request):
        # Find requests that don't have an AI draft yet
        base_qs = LicenseRequest.objects.filter(
            Q(ai_draft_response__isnull=True) | Q(ai_draft_response__exact=""),
            status__in=['NEW', 'REVIEWED']
        ).order_by('created_at')

        default_limit = getattr(settings, 'AI_WORKER_MAX_BATCH', 25)
        hard_max = getattr(settings, 'AI_WORKER_MAX_BATCH_HARD', 100)
        try:
            limit = int(request.query_params.get('limit', default_limit))
        except (TypeError, ValueError):
            limit = default_limit
        limit = max(1, min(limit, hard_max))

        pending = base_qs[:limit]
        data = [{
            "id": req.id,
            "client_name": req.client_name,
            "company": req.company or "N/A",
            "project_type": req.get_project_type_display(),
            "duration": req.get_duration_display(),
            "message": req.message or "No additional details provided.",
            "asset_name": str(req.asset)
        } for req in pending]
        return Response(data, status=status.HTTP_200_OK)

class AILicenseDraftUpdateView(APIView):
    """Secure endpoint for the local AI worker to post the finished draft."""
    authentication_classes = [] 
    permission_classes = [IsAIWorkerAuthorized]

    def post(self, request, pk):
        serializer = AIDraftUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        draft_text = serializer.validated_data["draft_text"]
        allowed_statuses = ['NEW', 'REVIEWED']

        with transaction.atomic():
            updated = LicenseRequest.objects.filter(
                pk=pk,
                status__in=allowed_statuses
            ).filter(
                Q(ai_draft_response__isnull=True) | Q(ai_draft_response__exact="")
            ).update(ai_draft_response=draft_text)

        if updated == 1:
            return Response({"status": "Draft successfully saved"}, status=status.HTTP_200_OK)

        req_obj = LicenseRequest.objects.filter(pk=pk).only('status', 'ai_draft_response').first()
        if not req_obj:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        if req_obj.status not in allowed_statuses:
            return Response(
                {"error": "Draft updates are not allowed for this status."},
                status=status.HTTP_409_CONFLICT
            )
        return Response(
            {"error": "Draft already exists for this request."},
            status=status.HTTP_409_CONFLICT
        )

class ProductDetailView(generics.RetrieveAPIView):
    queryset = ProductVariant.objects.filter(photo__is_active=True)
    serializer_class = ProductDetailSerializer
    permission_classes = [AllowAny]

class ProductReviewListCreateView(generics.ListCreateAPIView):
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
        
        if product_type_str == 'photo':
            queryset = Photo.objects.filter(is_active=True)
        elif product_type_str == 'video':
            queryset = Video.objects.filter(is_active=True)
        elif product_type_str == 'product':
            queryset = ProductVariant.objects.filter(photo__is_active=True)
        else:
            raise Http404("Invalid product type.")
        
        return generics.get_object_or_404(queryset, pk=product_pk)
    
class ShoppingBagRecommendationsView(APIView):
    """
    Returns 4 random photos to display as recommendations 
    on the Shopping Bag / Cart page.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        photos = Photo.objects.filter(is_active=True).annotate(
            starting_price=Min('variants__price')
        ).order_by('?')[:4]
        
        serializer = PhotoListSerializer(photos, many=True)
        return Response(serializer.data)

class ProtectedDownloadView(APIView):
    """
    Securely serves the high-res file ONLY if the user has purchased it.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, product_type, product_id):
        user = request.user
        # 1. Map string (URL) to actual Model Class
        model_map = {
            'photo': Photo,
            'video': Video
        }
        model_class = model_map.get(product_type)
        if not model_class:
            raise Http404("Invalid product type")

        # 2. Determine ContentType for the GenericForeignKey lookup 
        try:
            content_type = ContentType.objects.get_for_model(model_class)
        except:
             raise Http404("Content Type not found")

        # 3. VERIFY PURCHASE
        has_purchased = OrderItem.objects.filter(
            order__user_profile__user=user, # Link Order -> Profile -> User
            content_type=content_type,      # Match the type (Photo vs Video)
            object_id=product_id            # Match the specific ID
        ).exists()

        if not has_purchased:
            # Allow Admin/Staff to bypass (useful for testing)
            if not user.is_staff:
                raise PermissionDenied("You have not purchased this item.")

        # 4. Fetch the Product to get the file path
        product = get_object_or_404(model_class, id=product_id)
        
        # Logic to get the correct file field
        file_handle = None
        if product_type == 'photo':
            file_handle = product.high_res_file
        elif product_type == 'video':

            file_handle = product.video_file

        if not file_handle:
            raise Http404("File not attached to product")

        file_path = file_handle.path

        if not os.path.exists(file_path):
            raise Http404("File on disk not found")
        # 5. Serve the file as an attachment
        response = FileResponse(open(file_path, 'rb'))
        
        # Set filename so the browser saves it nicely (e.g., "sunset.jpg")
        filename = os.path.basename(file_path)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
