from django.contrib import admin
from .models import Testimonial
from openeire_api.admin import custom_admin_site

@admin.register(Testimonial, site=custom_admin_site)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ('name', 'rating', 'text')