from rest_framework import serializers
from .models import Order, OrderItem, ProductShipping
from products.models import Photo, Video, ProductVariant, PrintTemplate
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
            'stripe_pid', 'items', 'shipping_method'
        )
        read_only_fields = ('order_number', 'delivery_cost', 'order_total', 'total_price')

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        user_profile = validated_data.pop('user_profile', None)
        
        # 1. Capture the shipping country from the order data
        shipping_country = validated_data.get('country') 
        shipping_method = validated_data.get('shipping_method', 'budget')

        order = Order.objects.create(user_profile=user_profile, **validated_data)

        model_map = {
            'photo': Photo, 
            'video': Video, 
            'physical': ProductVariant 
        }
        
        order_total = 0
        calculated_delivery_cost = 0  # Start at 0

        for item_data in items_data:
            product_id = item_data['product_id']
            product_type_str = item_data['product_type']
            quantity = item_data['quantity']
            options = item_data.get('options', {})
            
            model_class = model_map.get(product_type_str)
            if not model_class:
                continue

            try:
                product_instance = model_class.objects.get(id=product_id)
                
                # --- PRICE LOGIC ---
                price = 0
                
                if product_type_str == 'physical':
                    price = product_instance.price
                    try:
                        template = PrintTemplate.objects.get(
                            material=product_instance.material,
                            size=product_instance.size
                        )
                        
                        # Find the exact cost from our fixtures
                        shipping_rule = ProductShipping.objects.get(
                            product=template,
                            country=shipping_country,
                            method=shipping_method
                        )
                        
                        # Add to total delivery cost (Cost * Quantity)
                        # Note: Prodigi sometimes bundles, but charging per item is safer for now
                        calculated_delivery_cost += (shipping_rule.cost * quantity)

                    except (PrintTemplate.DoesNotExist, ProductShipping.DoesNotExist):
                        # Fallback if data is missing (prevents crash)
                        print(f"Warning: No shipping rule found for {product_instance}")
                        pass # or add a default flat rate
                
                elif product_type_str in ['photo', 'video']:
                    # Digital items have NO shipping cost
                    license_type = options.get('license', 'hd')
                    if license_type == '4k':
                        price = product_instance.price_4k
                    else:
                        price = product_instance.price_hd

                item_total = price * quantity
                order_total += item_total

                OrderItem.objects.create(
                    order=order,
                    product=product_instance,
                    quantity=quantity,
                    item_total=item_total,
                    details=options
                )

            except model_class.DoesNotExist:
                continue
        
        # 3. Save the calculated values to the order
        order.order_total = order_total
        order.delivery_cost = calculated_delivery_cost # Update the field
        order.total_price = order.order_total + order.delivery_cost
        order.save()

        return order

    def validate(self, data):
        """
        Ensure physical products are only shipped to allowed countries.
        """
        country = data.get('country')
        items = data.get('items', [])
        
        # valid shipping destinations for physical goods
        ALLOWED_SHIPPING_COUNTRIES = ['IE', 'US']

        # Loop through items to check if any are physical
        for item in items:
            # We assume 'product_type' is passed from the frontend for each item
            p_type = item.get('product_type')

            if p_type == 'physical':
                # Check if the selected country is in our allowed list
                # Note: 'country' comes in as a Country object, so we convert to string
                if str(country) not in ALLOWED_SHIPPING_COUNTRIES:
                    raise serializers.ValidationError(
                        {"country": f"Physical products can currently only be shipped to Ireland (IE) or the US. You selected {country}."}
                    )
        
        return data

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
            'street_address1', 'town', 'country', 'items', 'shipping_method', 'delivery_cost'
        )