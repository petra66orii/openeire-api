from rest_framework import serializers
from .models import Order, OrderItem
from products.models import Photo, Video, Product
from django.contrib.contenttypes.models import ContentType

class OrderItemSerializer(serializers.ModelSerializer):
    """
    Serializer for the OrderItem model.
    Includes custom fields for identifying the generic product.
    """
    # Custom fields to receive product identity from the frontend
    product_id = serializers.IntegerField(write_only=True)
    product_type = serializers.CharField(write_only=True)

    class Meta:
        model = OrderItem
        fields = (
            'id',
            'product_id',
            'product_type',
            'quantity',
            'item_total',
        )
        read_only_fields = ('item_total',) # This will be calculated on the backend

class OrderSerializer(serializers.ModelSerializer):
    """
    Serializer for the Order model, with nested OrderItems.
    """
    items = OrderItemSerializer(many=True)

    class Meta:
        model = Order
        fields = (
            'id',
            'order_number',
            'first_name',
            'email',
            'phone_number',
            'street_address1',
            'street_address2',
            'town',
            'county',
            'postcode',
            'country',
            'delivery_cost',
            'order_total',
            'total_price',
            'stripe_pid',
            'items', # The nested list of order items
        )
        read_only_fields = ('order_number', 'delivery_cost', 'order_total', 'total_price')

    def create(self, validated_data):
        """
        Custom create method to handle creating the order and its items.
        """
        items_data = validated_data.pop('items')
        order = Order.objects.create(**validated_data)

        # A map to get the correct model class from the string 'product_type'
        model_map = {'photo': Photo, 'video': Video, 'physical': Product}
        
        order_total = 0

        for item_data in items_data:
            product_id = item_data['product_id']
            product_type_str = item_data['product_type']
            quantity = item_data['quantity']
            
            model_class = model_map.get(product_type_str)
            if not model_class:
                # Handle error if an invalid product_type is sent
                continue

            try:
                product_instance = model_class.objects.get(id=product_id)
                # Determine price
                price = getattr(product_instance, 'price', getattr(product_instance, 'price_hd', 0))
                item_total = price * quantity
                order_total += item_total

                OrderItem.objects.create(
                    order=order,
                    product=product_instance,
                    quantity=quantity,
                    item_total=item_total
                )
            except model_class.DoesNotExist:
                # Handle case where product ID is invalid
                continue
        
        # Here you would calculate delivery_cost based on order_total or other logic
        # For now, we'll keep it simple
        order.order_total = order_total
        order.total_price = order.order_total + order.delivery_cost
        order.save()

        return order