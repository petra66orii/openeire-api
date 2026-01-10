from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from .models import UserProfile
from openeire_api.admin import custom_admin_site

class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'default_phone_number', 'default_country', 'can_access_gallery')
    list_editable = ('can_access_gallery',)
    search_fields = ('user__username', 'default_phone_number')

custom_admin_site.register(UserProfile, UserProfileAdmin)
custom_admin_site.register(User, UserAdmin)