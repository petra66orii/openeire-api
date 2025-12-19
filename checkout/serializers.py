from rest_framework import serializers
from .models import Order, OrderItem
from products.models import Photo, Video, ProductVariant
from django.contrib.contenttypes.models import ContentType
from django_countries.serializer_fields import CountryField
from products.serializers import PhotoListSerializer, VideoListSerializer, ProductListSerializer
from userprofiles.models import UserProfile

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
            'stripe_pid', 'items'
        )
        read_only_fields = ('order_number', 'delivery_cost', 'order_total', 'total_price')

    def create(self, validated_data):
        """
        Custom create method to handle creating the order and its items.
        """
        items_data = validated_data.pop('items')
        user_profile = validated_data.pop('user_profile', None)

        order = Order.objects.create(user_profile=user_profile, **validated_data)

        # Map frontend strings to actual Models
        model_map = {
            'photo': Photo, 
            'video': Video, 
            'physical': ProductVariant 
        }
        
        order_total = 0

        for item_data in items_data:
            product_id = item_data['product_id']
            product_type_str = item_data['product_type']
            quantity = item_data['quantity']
            # 👇 Extract options (defaults to empty dict)
            options = item_data.get('options', {})
            
            model_class = model_map.get(product_type_str)
            if not model_class:
                continue

            try:
                product_instance = model_class.objects.get(id=product_id)
                
                # --- PRICE LOGIC ---
                price = 0
                
                if product_type_str == 'physical':
                    # For physical variants, price is fixed on the variant model
                    price = product_instance.price
                
                elif product_type_str in ['photo', 'video']:
                    # For digital, price depends on the License selected!
                    license_type = options.get('license', 'hd') # Default to HD if missing
                    if license_type == '4k':
                        price = product_instance.price_4k
                    else:
                        price = product_instance.price_hd

                item_total = price * quantity
                order_total += item_total

                # Create OrderItem
                # Note: passing 'product=product_instance' automatically handles the GenericForeignKey
                OrderItem.objects.create(
                    order=order,
                    product=product_instance,
                    quantity=quantity,
                    item_total=item_total,
                    details=options # 👇 Save the options to the JSONField
                )

            except model_class.DoesNotExist:
                continue
        
        # Calculate totals
        order.order_total = order_total
        # You can add logic here for delivery thresholds later (e.g. Free shipping over €100)
        order.total_price = order.order_total + order.delivery_cost
        order.save()

        return order

class OrderHistoryItemSerializer(serializers.ModelSerializer):
    product = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = ('id', 'product', 'quantity', 'item_total', 'details')

    def get_product(self, obj):
        if isinstance(obj.product, Photo):
            return PhotoListSerializer(obj.product, context=self.context).data
        if isinstance(obj.product, Video):
            return VideoListSerializer(obj.product, context=self.context).data
        if isinstance(obj.product, ProductVariant):
            return ProductListSerializer(obj.product, context=self.context).data
        return None

class OrderHistoryListSerializer(serializers.ModelSerializer):
    items = OrderHistoryItemSerializer(many=True, read_only=True)
    country = CountryField(name_only=True) 

    class Meta:
        model = Order
        fields = (
            'order_number', 'date', 'order_total', 'total_price', 
            'street_address1', 'town', 'country', 'items'
        )