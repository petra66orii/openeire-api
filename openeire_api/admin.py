from django.contrib import admin
from django.db.models import Sum, Count
from checkout.models import Order

class CustomAdminSite(admin.AdminSite):
    def index(self, request, extra_context=None):
        """
        Override the default admin index to add custom analytics.
        """
        # Calculate stats
        orders = Order.objects.all()
        sales_data = orders.aggregate(
            total_revenue=Sum('total_price'),
            total_orders=Count('id')
        )

        total_revenue = sales_data.get('total_revenue') or 0
        total_orders = sales_data.get('total_orders') or 0
        average_order_value = total_revenue / total_orders if total_orders > 0 else 0

        # Prepare our custom context
        custom_context = {
            'total_revenue': total_revenue,
            'total_orders': total_orders,
            'average_order_value': average_order_value,
        }
        
        # Add our custom context to the default context
        if extra_context:
            extra_context.update(custom_context)
        else:
            extra_context = custom_context

        # Call the original index method to get the default page and app_list
        return super().index(request, extra_context)

# Create an instance of our custom admin site
custom_admin_site = CustomAdminSite(name='customadmin')