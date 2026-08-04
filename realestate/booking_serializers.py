import re

from django.utils import timezone
from rest_framework import serializers

from .models import RealEstateBookingCredential, RealEstateEnquiry
from .serializers import EIRCODE_RE, IRISH_COUNTIES


PHONE_ALLOWED_RE = re.compile(r"^[0-9+().\s-]+$")
FORBIDDEN_IDENTITY_FIELDS = {
    "client_id", "client_public_id", "name", "email", "phone",
    "client", "returning_credential", "returning_credential_id",
    "submission_source", "client_type", "company_name", "agency_name",
}


class BookingExchangeSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    secret = serializers.CharField(max_length=2048, trim_whitespace=False)


class BookingSessionSerializer(serializers.Serializer):
    session = serializers.CharField(max_length=2048, trim_whitespace=False)


class ReturningBookingSubmissionSerializer(serializers.Serializer):
    submission_id = serializers.UUIDField()
    property_address = serializers.CharField(max_length=2000)
    county = serializers.ChoiceField(choices=IRISH_COUNTIES)
    eircode = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    no_eircode = serializers.BooleanField(default=False)
    location_details = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")
    property_type = serializers.ChoiceField(choices=RealEstateEnquiry.PropertyType.choices)
    property_type_details = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    bedroom_count = serializers.ChoiceField(choices=RealEstateEnquiry.BedroomCount.choices)
    floor_count = serializers.ChoiceField(choices=RealEstateEnquiry.FloorCount.choices)
    preferred_package = serializers.ChoiceField(choices=RealEstateEnquiry.PreferredPackage.choices)
    scheduling_preference = serializers.ChoiceField(choices=RealEstateEnquiry.SchedulingPreference.choices)
    preferred_date = serializers.DateField(required=False, allow_null=True)
    alternative_date = serializers.DateField(required=False, allow_null=True)
    preferred_time_window = serializers.ChoiceField(choices=RealEstateEnquiry.PreferredTimeWindow.choices)
    access_provider = serializers.ChoiceField(choices=RealEstateEnquiry.AccessProvider.choices)
    access_contact_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    access_contact_phone = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    access_notes = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")
    message = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")
    readiness_acknowledged = serializers.BooleanField()
    saved_details_confirmed = serializers.BooleanField()
    privacy_acknowledged = serializers.BooleanField()
    contact_update_requested = serializers.BooleanField(default=False)
    contact_update_request = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")

    internal_floor_area = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=1000000)
    internal_floor_area_unit = serializers.ChoiceField(
        choices=RealEstateEnquiry.FloorAreaUnit.choices,
        required=False,
        allow_blank=True,
        default="",
    )
    secondary_accommodation = serializers.ChoiceField(
        choices=RealEstateEnquiry.YesNoNotSure.choices,
        required=False,
        allow_blank=True,
        default="",
    )
    secondary_accommodation_details = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    outbuildings = serializers.ChoiceField(
        choices=RealEstateEnquiry.YesNoNotSure.choices,
        required=False,
        allow_blank=True,
        default="",
    )
    outbuildings_details = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    grounds_size = serializers.ChoiceField(
        choices=RealEstateEnquiry.GroundsSize.choices,
        required=False,
        allow_blank=True,
        default="",
    )
    property_features = serializers.CharField(max_length=3000, required=False, allow_blank=True, default="")
    occupancy_status = serializers.ChoiceField(
        choices=RealEstateEnquiry.OccupancyStatus.choices,
        required=False,
        allow_blank=True,
        default="",
    )
    add_ons = serializers.ListField(
        child=serializers.ChoiceField(choices=tuple(RealEstateEnquiry.ADD_ON_LABELS)),
        required=False,
        allow_empty=True,
        default=list,
    )
    additional_stills_quantity = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=50)
    on_camera = serializers.ChoiceField(
        choices=RealEstateEnquiry.YesNoNotSure.choices,
        required=False,
        allow_blank=True,
        default="",
    )
    on_camera_people = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    audio_requirements = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        supplied = set(getattr(self, "initial_data", {}) or {})
        forbidden = sorted(supplied & FORBIDDEN_IDENTITY_FIELDS)
        if forbidden:
            raise serializers.ValidationError({
                "detail": "Client identity fields are loaded from the verified booking session."
            })
        unknown = sorted(supplied - set(self.fields))
        if unknown:
            raise serializers.ValidationError({
                "detail": "The booking request contains unsupported fields."
            })

        errors = {}
        for field in (
            "property_address", "eircode", "location_details", "property_type_details",
            "access_contact_name", "access_contact_phone", "access_notes", "message",
            "contact_update_request", "secondary_accommodation_details",
            "outbuildings_details", "property_features", "on_camera_people",
            "audio_requirements",
        ):
            if field in attrs:
                attrs[field] = str(attrs.get(field) or "").strip()

        attrs["eircode"] = attrs.get("eircode", "").upper()
        if attrs.get("no_eircode"):
            if attrs["eircode"]:
                errors["eircode"] = "Remove the Eircode when confirming the property has none."
            if not attrs.get("location_details"):
                errors["location_details"] = "Provide precise directions or a Google Maps link."
        elif not attrs["eircode"]:
            errors["eircode"] = "Provide an Eircode or confirm that the property has none."
        elif not EIRCODE_RE.fullmatch(attrs["eircode"]):
            errors["eircode"] = "Enter a valid Irish Eircode."

        if attrs.get("property_type") == RealEstateEnquiry.PropertyType.OTHER and not attrs.get("property_type_details"):
            errors["property_type_details"] = "Describe the property category."

        access_provider = attrs.get("access_provider")
        if access_provider != RealEstateEnquiry.AccessProvider.ENQUIRER:
            if not attrs.get("access_contact_name"):
                errors["access_contact_name"] = "Provide the access contact's name."
            phone = attrs.get("access_contact_phone", "")
            digits = re.sub(r"\D", "", phone)
            if not phone or not PHONE_ALLOWED_RE.fullmatch(phone) or not 7 <= len(digits) <= 15:
                errors["access_contact_phone"] = "Enter a plausible access contact number."

        today = timezone.localdate()
        preferred_date = attrs.get("preferred_date")
        alternative_date = attrs.get("alternative_date")
        if attrs.get("scheduling_preference") == RealEstateEnquiry.SchedulingPreference.REQUEST_DATE:
            if not preferred_date:
                errors["preferred_date"] = "Choose a preferred date."
        elif preferred_date:
            errors["preferred_date"] = "Remove the preferred date when choosing flexible scheduling."
        if preferred_date and preferred_date < today:
            errors["preferred_date"] = "Preferred date cannot be in the past."
        if alternative_date and alternative_date < today:
            errors["alternative_date"] = "Alternative date cannot be in the past."

        if attrs.get("internal_floor_area") and not attrs.get("internal_floor_area_unit"):
            errors["internal_floor_area_unit"] = "Choose a floor-area unit."
        if attrs.get("internal_floor_area_unit") and not attrs.get("internal_floor_area"):
            errors["internal_floor_area"] = "Enter the approximate floor area."
        if attrs.get("secondary_accommodation") == RealEstateEnquiry.YesNoNotSure.YES and not attrs.get("secondary_accommodation_details"):
            errors["secondary_accommodation_details"] = "Describe the secondary accommodation."
        if attrs.get("outbuildings") == RealEstateEnquiry.YesNoNotSure.YES and not attrs.get("outbuildings_details"):
            errors["outbuildings_details"] = "Describe the outbuildings."
        if attrs.get("property_type") in {
            RealEstateEnquiry.PropertyType.SITE_LAND,
            RealEstateEnquiry.PropertyType.AGRICULTURAL,
        } and not attrs.get("grounds_size"):
            errors["grounds_size"] = "Provide the approximate land or grounds size."
        if (
            attrs.get("property_type") == RealEstateEnquiry.PropertyType.AGRICULTURAL
            and not attrs.get("outbuildings")
        ):
            errors["outbuildings"] = "Confirm whether outbuildings are included."

        add_ons = attrs.get("add_ons") or []
        if len(add_ons) != len(set(add_ons)):
            errors["add_ons"] = "Add-ons must not contain duplicates."
        if "travel_supplement" in add_ons:
            errors["add_ons"] = "Travel is assessed internally after the location is reviewed."
        quantity = attrs.get("additional_stills_quantity")
        if "additional_stills" in add_ons and quantity is None:
            errors["additional_stills_quantity"] = "Choose the number of additional photographs."
        if "additional_stills" not in add_ons and quantity is not None:
            errors["additional_stills_quantity"] = "Select additional photographs before entering a quantity."
        conflicts = {
            RealEstateEnquiry.PreferredPackage.PRO: {"additional_social_cuts"},
            RealEstateEnquiry.PreferredPackage.PREMIUM: {"floor_plan", "virtual_tour_3d", "additional_social_cuts"},
        }
        if set(add_ons) & conflicts.get(attrs.get("preferred_package"), set()):
            errors["add_ons"] = "One or more selected add-ons are already included in the package."

        if attrs.get("on_camera") == RealEstateEnquiry.YesNoNotSure.YES:
            if not attrs.get("on_camera_people"):
                errors["on_camera_people"] = "Tell us who will appear or speak."
            if not attrs.get("audio_requirements"):
                errors["audio_requirements"] = "Describe the spoken-audio or microphone requirements."

        if attrs.get("readiness_acknowledged") is not True:
            errors["readiness_acknowledged"] = "Confirm the property will be ready at the agreed arrival time."
        if attrs.get("saved_details_confirmed") is not True:
            errors["saved_details_confirmed"] = "Confirm that the saved contact details can be used for this request."
        if attrs.get("privacy_acknowledged") is not True:
            errors["privacy_acknowledged"] = "Acknowledge that we may contact you about this booking request."
        if attrs.get("contact_update_requested") and not attrs.get("contact_update_request"):
            errors["contact_update_request"] = "Describe the contact detail that staff should review."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


def credential_for_public_id(public_id):
    return RealEstateBookingCredential.objects.select_related("client").filter(
        public_id=public_id
    ).first()
