from urllib.parse import urljoin

from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from .models import Order, OrderItem
from products.models import Photo, Video, ProductVariant
from products.file_access import asset_file_exists
from products.personal_downloads import ensure_personal_download_token
from products.personal_licence import (
    build_personal_licence_download_url,
    get_personal_terms_version,
)
from django.contrib.contenttypes.models import ContentType
from django_countries.serializer_fields import CountryField
from django.urls import reverse
from products.serializers import PhotoListSerializer, VideoListSerializer, ProductListSerializer
from userprofiles.models import UserProfile
from .address_validation import validate_physical_shipping_address
from .shipping import calculate_physical_shipping_quote


class OrderItemSerializer(serializers.ModelSerializer):
    """
    Serializer for the OrderItem model.
    """
    # Custom fields to receive product identity from the frontend
    product_id = serializers.IntegerField(write_only=True)
    product_type = serializers.CharField(write_only=True)
    
    # 👇 NEW: Accept the options object (e.g. { license: '4k' })
    options = serializers.JSONField(write_only=True, required=False)

    class Meta:
        model = OrderItem
        fields = (
            'id',
            'product_id',
            'product_type',
            'quantity',
            'item_total',
            'options', # Added to fields
        )
        read_only_fields = ('item_total',) 

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    user_profile = serializers.PrimaryKeyRelatedField(
        queryset=UserProfile.objects.all(),
        required=False, 
        allow_null=True
    )

    class Meta:
        model = Order
        fields = (
            'id', 'order_number', 'user_profile', 
            'first_name', 'email', 'phone_number', 
            'street_address1', 'street_address2', 
            'town', 'county', 'postcode', 'country', 
            'delivery_cost', 'order_total', 'total_price', 
            'stripe_pid', 'items', 'shipping_method', 'personal_terms_version'
        )
        read_only_fields = ('order_number', 'delivery_cost', 'order_total', 'total_price', 'personal_terms_version')

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        user_profile = validated_data.pop('user_profile', None)
        
        # 1. Capture the shipping country from the order data
        shipping_country = validated_data.get('country') 
        shipping_method = validated_data.get('shipping_method', 'budget')

        model_map = {
            'photo': Photo, 
            'video': Video, 
            'physical': ProductVariant 
        }
        
        order_total = 0
        calculated_delivery_cost = 0  # Start at 0
        has_consumer_digital_item = False
        order_items_to_create = []
        physical_line_items = []

        for item_data in items_data:
            product_id = item_data['product_id']
            product_type_str = item_data['product_type']
            quantity = item_data['quantity']
            options = item_data.get('options') or {}
            if not isinstance(options, dict):
                options = {}
            
            model_class = model_map.get(product_type_str)
            if not model_class:
                continue

            try:
                if product_type_str == 'physical':
                    product_instance = model_class.objects.get(
                        id=product_id,
                        photo__is_active=True,
                        photo__is_printable=True,
                    )
                elif product_type_str == 'photo':
                    product_instance = model_class.objects.get(id=product_id, is_active=True)
                elif product_type_str == 'video':
                    product_instance = model_class.objects.get(id=product_id, is_active=True)
                else:
                    product_instance = model_class.objects.get(id=product_id)
                
                # --- PRICE LOGIC ---
                price = 0
                
                if product_type_str == 'physical':
                    price = product_instance.price
                    physical_line_items.append((product_instance, quantity))
                
                elif product_type_str in ['photo', 'video']:
                    # Digital items have NO shipping cost and now use one price.
                    if not asset_file_exists(product_instance):
                        raise serializers.ValidationError(
                            {
                                "items": (
                                    f"Digital product {product_id} is unavailable for delivery."
                                )
                            }
                        )
                    has_consumer_digital_item = True
                    price = product_instance.price

                item_total = price * quantity
                order_total += item_total

                order_items_to_create.append(
                    {
                        "product": product_instance,
                        "quantity": quantity,
                        "item_total": item_total,
                        "details": options,
                    }
                )

            except model_class.DoesNotExist:
                if product_type_str == 'physical':
                    raise serializers.ValidationError(
                        {
                            "items": (
                                f"Physical product {product_id} is no longer available for sale."
                            )
                        }
                    )
                continue

        shipping_quote = calculate_physical_shipping_quote(
            line_items=physical_line_items,
            shipping_country=shipping_country,
            shipping_method=shipping_method,
        )
        calculated_delivery_cost = shipping_quote.delivery_cost

        with transaction.atomic():
            order = Order.objects.create(user_profile=user_profile, **validated_data)
            OrderItem.objects.bulk_create(
                [
                    OrderItem(order=order, **item_kwargs)
                    for item_kwargs in order_items_to_create
                ]
            )

            # 3. Save the calculated values to the order
            order.order_total = order_total
            order.delivery_cost = calculated_delivery_cost # Update the field
            order.total_price = order.order_total + order.delivery_cost
            if has_consumer_digital_item:
                order.personal_terms_version = get_personal_terms_version()
            order.save()

        return order

    def validate(self, data):
        """
        Validate physical shipping addresses and digital item payload shape.
        """
        country = data.get('country')
        items = data.get('items', [])
        has_digital_items = any(item.get('product_type') in {'photo', 'video'} for item in items)
        if has_digital_items and not data.get('user_profile'):
            raise serializers.ValidationError(
                {"user_profile": "Authentication is required to purchase digital items."}
            )
        
        has_physical_items = any(item.get('product_type') == 'physical' for item in items)
        if has_physical_items:
            shipping_errors = validate_physical_shipping_address(
                country=country,
                line1=data.get('street_address1'),
                town=data.get('town'),
                postcode=data.get('postcode'),
                county=data.get('county'),
            )
            if shipping_errors:
                raise serializers.ValidationError(shipping_errors)

        # Validate digital item options payload shape only.
        for item in items:
            p_type = item.get('product_type')

            if p_type in ['photo', 'video']:
                options = item.get('options') or {}
                if not isinstance(options, dict):
                    raise serializers.ValidationError(
                        {"items": f"Invalid options payload for {p_type} item."}
                    )
                product_id = item.get('product_id')
                model_class = Photo if p_type == 'photo' else Video
                try:
                    product = model_class.objects.get(id=product_id, is_active=True)
                except model_class.DoesNotExist:
                    raise serializers.ValidationError(
                        {"items": f"Digital product {product_id} is no longer available for sale."}
                    )
                if not asset_file_exists(product):
                    raise serializers.ValidationError(
                        {"items": f"Digital product {product_id} is unavailable for delivery."}
                    )

        return data

class OrderHistoryItemSerializer(serializers.ModelSerializer):
    product = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    personal_terms_version = serializers.SerializerMethodField()
    personal_terms_url = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = (
            'id',
            'product',
            'quantity',
            'item_total',
            'details',
            'download_url',
            'personal_terms_version',
            'personal_terms_url',
        )

    def _is_digital_item(self, obj):
        return isinstance(obj.product, (Photo, Video))

    def get_product(self, obj):
        if isinstance(obj.product, Photo):
            return PhotoListSerializer(obj.product, context=self.context).data
        if isinstance(obj.product, Video):
            return VideoListSerializer(obj.product, context=self.context).data
        if isinstance(obj.product, ProductVariant):
            return ProductListSerializer(obj.product, context=self.context).data
        return None

    def get_download_url(self, obj):
        if not self._is_digital_item(obj):
            return None
        request = self.context.get('request')
        if not request:
            return None
        token_obj = ensure_personal_download_token(obj)
        path = reverse('personal-asset-download', args=[str(token_obj.token)])
        base_url = getattr(settings, "PERSONAL_DOWNLOAD_BASE_URL", None)
        if base_url:
            return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        return request.build_absolute_uri(path)

    def get_personal_terms_version(self, obj):
        if not self._is_digital_item(obj):
            return None
        return obj.order.personal_terms_version or get_personal_terms_version()

    def get_personal_terms_url(self, obj):
        if not self._is_digital_item(obj):
            return None
        try:
            return build_personal_licence_download_url(
                obj.order,
                request=self.context.get('request'),
            )
        except RuntimeError:
            return None

class OrderHistoryListSerializer(serializers.ModelSerializer):
    items = OrderHistoryItemSerializer(many=True, read_only=True)
    country = CountryField(name_only=True) 

    class Meta:
        model = Order
        fields = (
            'order_number', 'date', 'order_total', 'total_price', 
            'street_address1', 'town', 'country', 'items', 'shipping_method', 'delivery_cost',
            'personal_terms_version',
        )
