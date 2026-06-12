from django.contrib import admin

from openeire_api.admin import custom_admin_site

from .models import RealEstateEnquiry


@admin.register(RealEstateEnquiry, site=custom_admin_site)
class RealEstateEnquiryAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "name",
        "company_name",
        "county",
        "preferred_package",
        "preferred_date",
        "status",
        "quoted_price",
        "shoot_date",
    )
    list_filter = (
        "status",
        "preferred_package",
        "county",
        "client_type",
        "how_heard",
        "created_at",
    )
    search_fields = (
        "name",
        "email",
        "phone",
        "company_name",
        "property_address",
        "eircode",
    )
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "Contact",
            {
                "fields": (
                    "name",
                    "email",
                    "phone",
                    "company_name",
                    "client_type",
                    "how_heard",
                    "consent_to_contact",
                )
            },
        ),
        (
            "Property",
            {
                "fields": (
                    "property_address",
                    "county",
                    "eircode",
                    "property_type",
                )
            },
        ),
        (
            "Package & Add-ons",
            {
                "fields": (
                    "preferred_package",
                    "add_ons",
                    "preferred_date",
                    "message",
                )
            },
        ),
        (
            "Pipeline",
            {
                "fields": (
                    "status",
                    "quoted_price",
                    "shoot_date",
                )
            },
        ),
        (
            "Notes",
            {
                "fields": ("internal_notes",)
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

