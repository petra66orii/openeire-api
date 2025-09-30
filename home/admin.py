from django.contrib import admin
from .models import Testimonial, NewsletterSubscriber
from openeire_api.admin import custom_admin_site

@admin.register(Testimonial, site=custom_admin_site)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ('name', 'rating', 'text')


@admin.register(NewsletterSubscriber, site=custom_admin_site)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ('email', 'created_at')