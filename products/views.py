import logging
import random
from smtplib import SMTPException
from django.shortcuts import get_object_or_404
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.db import transaction
from django.db.models import Q, Exists, OuterRef, Min, Case, When, IntegerField, Prefetch
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.urls import reverse
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from openeire_api.throttling import SharedScopedRateThrottle
from .models import (
    Photo,
    Video,
    ProductVariant,
    ProductReview,
    GalleryAccess,
    LicenseRequest,
    LicenceOffer,
    LicenceDeliveryToken,
    PersonalDownloadToken,
    PersonalLicenceToken,
    normalize_email,
)
from .licensing import (
    get_current_offer,
    send_licence_admin_notification_email,
)
from .file_access import asset_file_exists, get_asset_file_name, open_asset_file
from .utils import generate_r2_presigned_url
from .personal_licence import (
    build_personal_licence_download_url,
    build_personal_licence_filename,
    generate_personal_licence_pdf,
    get_personal_licence_url,
    get_personal_licence_summary,
    get_personal_licence_text,
    get_personal_terms_version,
)
from openeire_api.mail_utils import get_default_from_email
from checkout.models import OrderItem
from .serializers import (
    PhotoListSerializer,
    PhysicalPhotoListSerializer,
    VideoListSerializer,
    PhotoDetailSerializer,
    PhysicalPhotoDetailSerializer,
    VideoDetailSerializer,
    ProductDetailSerializer,
    ProductReviewSerializer,
    LicenseRequestSerializer,
    AIDraftUpdateSerializer,
)
from .permissions import IsDigitalGalleryAuthorized, IsAIWorkerAuthorized

logger = logging.getLogger(__name__)


def _to_iso8601_utc(value):
    if not value:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _agreed_snapshot_for_queue(req, offer=None):
    if offer and offer.scope_snapshot:
        return offer.scope_snapshot
    return req.agreed_scope_snapshot or {}


def _build_agreed_scope_summary(snapshot, payload, currency="EUR"):
    project_type = snapshot.get("project_type_display") or payload["project_type"]
    currency = (currency or "EUR").strip() or "EUR"
    scope_parts = [
        project_type,
        f"media: {snapshot.get('permitted_media_display') or payload['permitted_media']}",
        f"territory: {snapshot.get('territory_display') or payload['territory']}",
        f"duration: {snapshot.get('duration_display') or payload['duration']}",
        f"exclusivity: {snapshot.get('exclusivity_display') or payload['exclusivity']}",
    ]
    reach_caps = snapshot.get('reach_caps') or payload["reach_caps"]
    if reach_caps and str(reach_caps).strip().lower() != "none":
        scope_parts.append(f"reach caps: {reach_caps}")
    quoted_price = snapshot.get('quoted_price') or payload["quoted_price"]
    if quoted_price:
        scope_parts.append(f"fee: {currency} {quoted_price}")
    return "; ".join(scope_parts)


def _redirect_to_private_asset_if_supported(asset, file_key, filename):
    if not file_key or not asset_file_exists(asset):
        return None
    presigned_url = generate_r2_presigned_url(
        file_key,
        download_filename=filename,
    )
    if not presigned_url:
        return None
    return HttpResponseRedirect(presigned_url)


class RequestGalleryAccessView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [SharedScopedRateThrottle]
    throttle_scope = 'gallery_access_request'

    def post(self, request):
        email = normalize_email(request.data.get('email'))
        if not email:
            return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                access_record = GalleryAccess.objects.create(email=email)

                send_mail(
                    subject="OpenÉire Studios - Private Gallery Access",
                    message=f"Hello,\n\nHere is your access code for the Digital Stock Gallery:\n\n{access_record.access_code}\n\nValid for 30 days.",
                    from_email=get_default_from_email(),
                    recipient_list=[email],
                    fail_silently=False,
                )
        except (SMTPException, OSError):
            logger.exception("Failed to send gallery access email")
            return Response(
                {"error": "Unable to send access code right now. Please try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"message": "Code sent"}, status=status.HTTP_200_OK)

class VerifyGalleryAccessView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SharedScopedRateThrottle]
    throttle_scope = 'gallery_access_verify'

    def post(self, request):
        code = request.data.get('access_code', '').upper().strip()
        user_email = normalize_email(getattr(request.user, "email", ""))
        if not user_email:
            return Response(
                {"error": "Your account must have a verified email address to unlock the digital gallery."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            access_record = GalleryAccess.objects.get(access_code=code)
            if not access_record.is_valid:
                return Response({"error": "Invalid or expired code"}, status=status.HTTP_403_FORBIDDEN)
            if normalize_email(access_record.email) != user_email:
                return Response(
                    {"error": "This access code belongs to a different email address than your signed-in account."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            access_record.grant_to_user(request.user)
            return Response({
                "message": "Access granted",
                "expires_at": access_record.expires_at,
                "valid": True
            })
        except GalleryAccess.DoesNotExist:
            return Response({"error": "Invalid or expired code"}, status=status.HTTP_403_FORBIDDEN)

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
            
            # Apply Sorting
            if sort_key == 'price_asc':
                photos = photos.order_by('price')
                videos = videos.order_by('price')
            elif sort_key == 'price_desc':
                photos = photos.order_by('-price')
                videos = videos.order_by('-price')
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
            physical_photos = photos.filter(is_printable=True).annotate(
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
                queryset.append({'item': photo, 'serializer': PhysicalPhotoListSerializer(photo)})

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
    queryset = Photo.objects.filter(is_active=True, is_printable=True)
    serializer_class = PhysicalPhotoDetailSerializer
    permission_classes = [AllowAny]

class VideoDetailView(generics.RetrieveAPIView):
    queryset = Video.objects.filter(is_active=True)
    serializer_class = VideoDetailSerializer
    permission_classes = [IsDigitalGalleryAuthorized]

class LicenseRequestCreateView(generics.CreateAPIView):
    queryset = LicenseRequest.objects.all()
    serializer_class = LicenseRequestSerializer
    permission_classes = [AllowAny]
    throttle_classes = [SharedScopedRateThrottle]
    throttle_scope = 'license_request'

    def _notify_admin_of_license_request(self, obj):
        try:
            send_licence_admin_notification_email(obj)
        except Exception:
            logger.exception(
                "Failed to send admin notification for licence request %s",
                obj.id,
            )

    def perform_create(self, serializer):
        created = serializer.save()
        self._notify_admin_of_license_request(created)

    def create(self, request, *args, **kwargs):
        asset_ids = request.data.get('asset_ids')
        if asset_ids is None:
            return super().create(request, *args, **kwargs)
        if not isinstance(asset_ids, list):
            return Response(
                {"asset_ids": "asset_ids must be a list when provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(asset_ids) == 0:
            return Response(
                {"asset_ids": "Provide at least one asset id."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(asset_ids) == 1:
            payload = dict(request.data)
            payload.pop('asset_ids', None)
            payload['asset_id'] = asset_ids[0]
            serializer = self.get_serializer(data=payload)
            serializer.is_valid(raise_exception=True)
            created = serializer.save()
            self._notify_admin_of_license_request(created)
            headers = self.get_success_headers(serializer.data)
            return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

        created_items = []
        errors = {}
        for asset_id in asset_ids:
            payload = dict(request.data)
            payload.pop('asset_ids', None)
            payload['asset_id'] = asset_id
            serializer = self.get_serializer(data=payload)
            if serializer.is_valid():
                created = serializer.save()
                self._notify_admin_of_license_request(created)
                created_items.append(
                    {
                        "id": created.id,
                        "asset_id": created.object_id,
                        "status": created.status,
                        "created_at": created.created_at,
                    }
                )
            else:
                errors[str(asset_id)] = serializer.errors

        if errors:
            return Response(
                {"created": created_items, "errors": errors},
                status=207 if created_items else status.HTTP_400_BAD_REQUEST,
            )
        return Response({"created": created_items}, status=status.HTTP_201_CREATED)

class AILicenseDraftQueueView(APIView):
    """Secure endpoint for the local AI worker to fetch pending requests."""
    authentication_classes = [] 
    permission_classes = [IsAIWorkerAuthorized]

    NEGOTIATION_STATUSES = ['SUBMITTED', 'NEEDS_INFO', 'APPROVED', 'AWAITING_CLIENT_CONFIRMATION']
    PAYMENT_DRAFT_STATUSES = ['AWAITING_CLIENT_CONFIRMATION', 'PAYMENT_PENDING']

    def _serialize_queue_item(self, req, draft_mode, offer=None):
        payload = {
            "id": req.id,
            "draft_mode": draft_mode,
            "client_name": req.client_name,
            "company": req.company or "N/A",
            "project_type": req.get_project_type_display(),
            "duration": req.get_duration_display(),
            "territory": req.get_territory_display() if req.territory else "Not Specified",
            "permitted_media": req.get_permitted_media_display() if req.permitted_media else "Not Specified",
            "exclusivity": req.get_exclusivity_display() if req.exclusivity else "Not Specified",
            "reach_caps": req.reach_caps or "None",
            "message": req.message or "No additional details provided.",
            "asset_name": str(req.asset),
            "quoted_price": str(req.quoted_price) if req.quoted_price is not None else None,
        }
        if draft_mode == "payment_link":
            current_offer = offer or get_current_offer(req)
            agreed_snapshot = _agreed_snapshot_for_queue(req, current_offer)
            offer_currency = current_offer.currency if current_offer and current_offer.currency else "EUR"
            payload.update(
                {
                    "payment_link": (
                        current_offer.stripe_payment_link_url
                        if current_offer and current_offer.stripe_payment_link_url
                        else None
                    ),
                    "offer_version": current_offer.version if current_offer else None,
                    "offer_currency": offer_currency,
                    "offer_expires_at": (
                        _to_iso8601_utc(current_offer.expires_at)
                        if current_offer and current_offer.expires_at
                        else None
                    ),
                    "agreed_scope_summary": _build_agreed_scope_summary(
                        agreed_snapshot,
                        payload,
                        currency=offer_currency,
                    ),
                    "approved_permitted_media": (
                        agreed_snapshot.get("permitted_media_display") or payload["permitted_media"]
                    ),
                    "approved_territory": (
                        agreed_snapshot.get("territory_display") or payload["territory"]
                    ),
                    "approved_duration": (
                        agreed_snapshot.get("duration_display") or payload["duration"]
                    ),
                    "approved_exclusivity": (
                        agreed_snapshot.get("exclusivity_display") or payload["exclusivity"]
                    ),
                    "approved_reach_caps": (
                        agreed_snapshot.get("reach_caps") or payload["reach_caps"]
                    ),
                }
            )
        return payload

    def get(self, request):
        negotiation_qs = LicenseRequest.objects.filter(
            Q(ai_draft_response__isnull=True) | Q(ai_draft_response__exact=""),
            client_confirmed_at__isnull=True,
            status__in=self.NEGOTIATION_STATUSES,
        ).order_by('created_at')
        payment_candidates = LicenseRequest.objects.filter(
            Q(ai_payment_draft_response__isnull=True) | Q(ai_payment_draft_response__exact=""),
            status__in=self.PAYMENT_DRAFT_STATUSES,
        ).filter(
            Q(client_confirmed_at__isnull=False) | Q(status='PAYMENT_PENDING')
        ).prefetch_related(
            Prefetch(
                'offers',
                queryset=LicenceOffer.objects.filter(status='ACTIVE').order_by('-version'),
                to_attr='prefetched_active_offers',
            )
        ).order_by('created_at')
        default_limit = getattr(settings, 'AI_WORKER_MAX_BATCH', 25)
        hard_max = getattr(settings, 'AI_WORKER_MAX_BATCH_HARD', 100)
        try:
            limit = int(request.query_params.get('limit', default_limit))
        except (TypeError, ValueError):
            limit = default_limit
        limit = max(1, min(limit, hard_max))

        pending = []
        for req in negotiation_qs[:hard_max]:
            pending.append((req.created_at, self._serialize_queue_item(req, "negotiation")))
        for req in payment_candidates[:hard_max]:
            current_offer = get_current_offer(req)
            if not current_offer or not current_offer.stripe_payment_link_url:
                logger.warning(
                    "Skipping payment-link draft queue item for license request %s because no valid current offer/link exists.",
                    req.id,
                )
                continue
            pending.append((req.created_at, self._serialize_queue_item(req, "payment_link", offer=current_offer)))

        pending.sort(key=lambda item: item[0])
        data = [payload for _, payload in pending[:limit]]
        return Response(data, status=status.HTTP_200_OK)

class AILicenseDraftUpdateView(APIView):
    """Secure endpoint for the local AI worker to post the finished draft."""
    authentication_classes = [] 
    permission_classes = [IsAIWorkerAuthorized]

    def post(self, request, pk):
        serializer = AIDraftUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        draft_mode = serializer.validated_data["draft_mode"]
        draft_text = serializer.validated_data["draft_text"]
        if draft_mode == "payment_link":
            allowed_statuses = ['AWAITING_CLIENT_CONFIRMATION', 'PAYMENT_PENDING']
            updated = 0
            with transaction.atomic():
                req_obj = (
                    LicenseRequest.objects.select_for_update()
                    .prefetch_related(
                        Prefetch(
                            'offers',
                            queryset=LicenceOffer.objects.filter(status='ACTIVE').order_by('-version'),
                            to_attr='prefetched_active_offers',
                        )
                    )
                    .filter(pk=pk)
                    .first()
                )
                if req_obj and req_obj.status in allowed_statuses:
                    current_offer = get_current_offer(req_obj)
                    if (
                        current_offer
                        and current_offer.stripe_payment_link_url
                        and (req_obj.client_confirmed_at or req_obj.status == 'PAYMENT_PENDING')
                        and not (req_obj.ai_payment_draft_response or "").strip()
                    ):
                        req_obj.ai_payment_draft_response = draft_text
                        req_obj.save(update_fields=['ai_payment_draft_response', 'updated_at'])
                        updated = 1
        else:
            allowed_statuses = ['SUBMITTED', 'NEEDS_INFO', 'APPROVED', 'AWAITING_CLIENT_CONFIRMATION']
            now = timezone.now()
            with transaction.atomic():
                updated = LicenseRequest.objects.filter(
                    pk=pk,
                    client_confirmed_at__isnull=True,
                    status__in=allowed_statuses
                ).filter(
                    Q(ai_draft_response__isnull=True) | Q(ai_draft_response__exact="")
                ).update(ai_draft_response=draft_text, updated_at=now)

        if updated == 1:
            return Response(
                {"status": "Draft successfully saved", "draft_mode": draft_mode},
                status=status.HTTP_200_OK,
            )

        req_obj = LicenseRequest.objects.filter(pk=pk).only(
            'status',
            'ai_draft_response',
            'ai_payment_draft_response',
            'client_confirmed_at',
        ).first()
        if not req_obj:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        if req_obj.status not in allowed_statuses:
            return Response(
                {"error": "Draft updates are not allowed for this status."},
                status=status.HTTP_409_CONFLICT
            )
        if draft_mode == "payment_link":
            current_offer = get_current_offer(req_obj)
            if (
                not current_offer
                or not current_offer.stripe_payment_link_url
                or (
                    not req_obj.client_confirmed_at and req_obj.status != 'PAYMENT_PENDING'
                )
            ):
                return Response(
                    {"error": "Payment-link draft updates require a confirmed request with a valid non-expired payment offer."},
                    status=status.HTTP_409_CONFLICT,
                )
        if draft_mode == "negotiation" and req_obj.client_confirmed_at:
            return Response(
                {"error": "Negotiation drafts are not allowed once client confirmation has been recorded."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {"error": "Draft already exists for this request."},
            status=status.HTTP_409_CONFLICT
        )

class ProductDetailView(generics.RetrieveAPIView):
    queryset = ProductVariant.objects.filter(photo__is_active=True, photo__is_printable=True)
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
            queryset = ProductVariant.objects.filter(
                photo__is_active=True,
                photo__is_printable=True,
            )
        else:
            raise Http404("Invalid product type.")
        
        return generics.get_object_or_404(queryset, pk=product_pk)
    
class ShoppingBagRecommendationsView(APIView):
    """
    Returns up to 4 active photos to display as recommendations
    on the Shopping Bag / Cart page.
    """
    permission_classes = [AllowAny]
    RECOMMENDATION_LIMIT = 4

    def _pick_recommendation_ids(self, queryset, limit):
        total = queryset.count()
        if total == 0:
            return []
        if total <= limit:
            return list(queryset.order_by('-created_at').values_list('id', flat=True))

        # Random start index is unbiased across active rows and avoids
        # expensive ORDER BY RANDOM() on large tables.
        start_index = random.randint(0, total - 1)
        ordered_ids = queryset.order_by('id').values_list('id', flat=True)
        ids = list(
            ordered_ids[start_index:start_index + limit]
        )
        if len(ids) < limit:
            ids.extend(
                list(
                    ordered_ids[: limit - len(ids)]
                )
            )
        return ids

    def get(self, request):
        has_gallery_access = bool(
            request.user.is_authenticated
            and getattr(getattr(request.user, "userprofile", None), "can_access_gallery", False)
        )
        active_photos = (
            Photo.objects.filter(is_active=True)
            if has_gallery_access
            else Photo.objects.filter(
                is_active=True,
                is_printable=True,
                variants__isnull=False,
            ).distinct()
        )
        selected_ids = self._pick_recommendation_ids(active_photos, self.RECOMMENDATION_LIMIT)

        if not selected_ids:
            return Response([])

        preserved_order = Case(
            *[When(id=photo_id, then=position) for position, photo_id in enumerate(selected_ids)],
            output_field=IntegerField(),
        )
        photos = Photo.objects.filter(id__in=selected_ids, is_active=True)
        if has_gallery_access:
            photos = photos.order_by(preserved_order)
            serializer = PhotoListSerializer(photos, many=True)
        else:
            photos = (
                photos.filter(is_printable=True, variants__isnull=False)
                .annotate(starting_price=Min('variants__price'))
                .distinct()
                .order_by(preserved_order)
            )
            serializer = PhysicalPhotoListSerializer(photos, many=True)
        return Response(serializer.data)


class PersonalUseLicenceTextView(APIView):
    """
    Public endpoint exposing the full Personal Use Licence text.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        licence_text = get_personal_licence_text()
        if not licence_text:
            raise Http404("Personal Use Licence text is not available.")

        terms_version = get_personal_terms_version()
        content = f"Personal Terms Version: {terms_version}\n\n{licence_text}"
        response = HttpResponse(content, content_type="text/plain; charset=utf-8")
        response["X-Personal-Terms-Version"] = terms_version
        return response


class PersonalLicenceDownloadView(APIView):
    """
    Serves a one-time downloadable PDF for a purchased personal-use licence.
    """
    permission_classes = [AllowAny]

    def get(self, request, token):
        with transaction.atomic():
            token_obj = (
                PersonalLicenceToken.objects
                .select_related("order")
                .select_for_update()
                .filter(token=token)
                .first()
            )
            if not token_obj or not token_obj.is_valid:
                raise Http404("Licence download link has expired or was already used.")

            order = token_obj.order
            pdf_bytes = generate_personal_licence_pdf(order)
            token_obj.used_at = timezone.now()
            token_obj.save(update_fields=["used_at"])

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{build_personal_licence_filename(order)}"'
        )
        response["X-Personal-Terms-Version"] = (
            order.personal_terms_version or get_personal_terms_version()
        )
        return response


class ProtectedDownloadView(APIView):
    """
    Securely serves the high-res file ONLY if the user has purchased it.
    """
    permission_classes = [IsAuthenticated]

    def _latest_purchase_for_user(self, user, model_class, product_id):
        content_type = ContentType.objects.get_for_model(model_class)
        order_item = (
            OrderItem.objects
            .filter(
                order__user_profile__user=user,
                content_type=content_type,
                object_id=product_id,
            )
            .select_related("order")
            .order_by("-order__date")
            .first()
        )
        return order_item

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

        # 2. Verify purchase and load the latest matching order item.
        order_item = self._latest_purchase_for_user(user, model_class, product_id)
        if not order_item and not user.is_staff:
            raise PermissionDenied("You have not purchased this item.")

        terms_version = (
            order_item.order.personal_terms_version
            if order_item and order_item.order.personal_terms_version
            else get_personal_terms_version()
        )

        if request.query_params.get("preview") in {"1", "true", "yes"}:
            download_path = reverse("secure-download", args=[product_type, product_id])
            personal_terms_url = (
                build_personal_licence_download_url(order_item.order, request=request)
                if order_item
                else get_personal_licence_url(request=request)
            )
            return Response(
                {
                    "download_url": request.build_absolute_uri(download_path),
                    "personal_terms_version": terms_version,
                    "personal_terms_url": personal_terms_url,
                    "personal_terms_summary": get_personal_licence_summary(),
                },
                status=status.HTTP_200_OK,
            )

        # 3. Fetch the product and stream the private file.
        product = get_object_or_404(model_class, id=product_id)

        asset_file_name = get_asset_file_name(product)
        redirect_response = _redirect_to_private_asset_if_supported(
            product,
            asset_file_name,
            (asset_file_name or "").rsplit("/", 1)[-1],
        )
        if redirect_response is not None:
            return redirect_response

        file_handle = open_asset_file(product, "rb")
        if not file_handle:
            raise Http404("File not attached to product")

        filename = (get_asset_file_name(product) or "").rsplit("/", 1)[-1]
        if not filename:
            try:
                file_handle.close()
            except Exception:
                pass
            raise Http404("File not available")
        return FileResponse(file_handle, as_attachment=True, filename=filename)


class LicenceAssetDownloadView(APIView):
    """
    Serves licensed assets via a one-time, expiring token link.
    """
    permission_classes = [AllowAny]

    def get(self, request, token):
        with transaction.atomic():
            token_obj = LicenceDeliveryToken.objects.select_for_update().filter(token=token).first()
            if not token_obj or not token_obj.is_valid:
                raise Http404("Download link has expired or was already used.")

            license_request = token_obj.license_request
            asset = license_request.asset
            asset_file_name = get_asset_file_name(asset)
            redirect_response = _redirect_to_private_asset_if_supported(
                asset,
                asset_file_name,
                (asset_file_name or "").rsplit("/", 1)[-1],
            )
            if redirect_response is not None:
                token_obj.used_at = timezone.now()
                token_obj.save(update_fields=["used_at"])
                return redirect_response

            file_field = open_asset_file(asset, "rb")
            if not file_field:
                raise Http404("File not attached to asset")

            token_obj.used_at = timezone.now()
            token_obj.save(update_fields=["used_at"])

        filename = (get_asset_file_name(asset) or "").rsplit("/", 1)[-1]
        if not filename:
            try:
                file_field.close()
            except Exception:
                pass
            raise Http404("File not available")
        return FileResponse(file_field, as_attachment=True, filename=filename)


class PersonalAssetDownloadView(APIView):
    """
    Serves personal-use digital purchases via an expiring token link.
    """
    permission_classes = [AllowAny]

    def get(self, request, token):
        token_obj = PersonalDownloadToken.objects.select_related("order_item").filter(token=token).first()
        if not token_obj or not token_obj.is_valid:
            raise Http404("Download link has expired or was already used.")

        order_item = token_obj.order_item
        asset = order_item.product
        asset_file_name = get_asset_file_name(asset)
        redirect_response = _redirect_to_private_asset_if_supported(
            asset,
            asset_file_name,
            (asset_file_name or "").rsplit("/", 1)[-1],
        )
        if redirect_response is not None:
            PersonalDownloadToken.objects.filter(pk=token_obj.pk).update(used_at=timezone.now())
            return redirect_response

        file_field = open_asset_file(asset, "rb")
        if not file_field:
            raise Http404("File not attached to asset")

        PersonalDownloadToken.objects.filter(pk=token_obj.pk).update(used_at=timezone.now())

        filename = (get_asset_file_name(asset) or "").rsplit("/", 1)[-1]
        if not filename:
            try:
                file_field.close()
            except Exception:
                pass
            raise Http404("File not available")
        return FileResponse(file_field, as_attachment=True, filename=filename)
