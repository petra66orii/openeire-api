from django.contrib import admin
from .models import Testimonial, NewsletterSubscriber
from openeire_api.admin import custom_admin_site

@admin.register(Testimonial, site=custom_admin_site)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ('name', 'rating', 'text')


@admin.register(NewsletterSubscriber, site=custom_admin_site)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = (
        'email',
        'first_name',
        'source',
        'brevo_sync_status',
        'brevo_synced_at',
        'created_at',
    )
    search_fields = ('email', 'first_name', 'source')
