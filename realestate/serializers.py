import re

from django.utils import timezone
from rest_framework import serializers

from .models import RealEstateEnquiry
from .package_catalogue import (
    CURRENT_CATALOGUE_VERSION,
    LEGACY_CATALOGUE_VERSION,
    get_included_add_ons,
)


IRISH_COUNTIES = (
    "Carlow", "Cavan", "Clare", "Cork", "Donegal", "Dublin", "Galway",
    "Kerry", "Kildare", "Kilkenny", "Laois", "Leitrim", "Limerick",
    "Longford", "Louth", "Mayo", "Meath", "Monaghan", "Offaly",
    "Roscommon", "Sligo", "Tipperary", "Waterford", "Westmeath", "Wexford",
    "Wicklow",
)
EIRCODE_RE = re.compile(r"^(?:[AC-FHKNPRTV-Y]\d{2}|D6W)\s?[0-9AC-FHKNPRTV-Y]{4}$", re.I)
PHONE_ALLOWED_RE = re.compile(r"^[0-9+().\s-]+$")


class RealEstateEnquirySerializer(serializers.ModelSerializer):
    """Validate both rollout-safe legacy payloads and strict V2 submissions.

    An omitted ``form_schema_version`` (or explicit version 1) uses the
    pre-scoping public contract. Version 2 activates every structured
    shoot-scoping requirement. The submitted version is persisted so legacy
    traffic can be measured and retired safely.
    """

    LEGACY_REQUIRED_TEXT_FIELDS = (
        "name", "phone", "property_address", "county", "property_type",
    )
    V2_REQUIRED_FIELDS = (
        "bedroom_count", "floor_count", "secondary_accommodation",
        "outbuildings", "grounds_size", "occupancy_status", "access_provider",
        "scheduling_preference", "preferred_time_window", "on_camera",
    )
    V3_PUBLIC_PACKAGES = {"starter", "pro", "premium", "custom", "not_sure"}
    V3_PUBLIC_ADD_ONS = {
        "additional_stills",
        "floor_plan",
        "virtual_tour_3d",
        "rush_delivery",
        "extended_property_film",
        "additional_social_cuts",
        "luxury_architectural",
        "twilight_dusk",
    }
    TRIMMED_FIELDS = (
        "name", "phone", "company_name", "property_address", "eircode",
        "location_details", "property_type", "property_type_details",
        "secondary_accommodation_details", "outbuildings_details",
        "property_features", "access_contact_name", "access_contact_phone",
        "access_notes", "on_camera_people", "audio_requirements", "message",
        "custom_review_notes",
    )
    # These were unrestricted strings in the deployed legacy form. V2 choices
    # are enforced in validate() so old clients are not rejected mid-rollout.
    county = serializers.CharField(max_length=100)
    property_type = serializers.CharField(max_length=100)
    eircode = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )
    form_schema_version = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=3
    )
    add_ons = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        default=list,
    )
    custom_review_reasons = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        default=list,
    )
    requires_custom_review = serializers.BooleanField(read_only=True)
    package_summary = serializers.CharField(
        source="get_preferred_package_summary",
        read_only=True,
    )
    turnaround_code = serializers.CharField(
        source="get_preferred_package_turnaround_code",
        read_only=True,
    )
    turnaround_label = serializers.CharField(
        source="get_preferred_package_turnaround_label",
        read_only=True,
    )
    included_photograph_count = serializers.IntegerField(
        source="get_included_photograph_count",
        read_only=True,
        allow_null=True,
    )
    included_photographs_label = serializers.CharField(
        source="get_included_photographs_label",
        read_only=True,
    )

    class Meta:
        model = RealEstateEnquiry
        fields = (
            "id", "form_schema_version", "catalogue_version", "name", "email", "phone",
            "client_type", "company_name", "property_address", "county",
            "eircode", "no_eircode", "location_details", "property_type",
            "property_type_details", "bedroom_count", "floor_count",
            "secondary_accommodation", "secondary_accommodation_details",
            "outbuildings", "outbuildings_details", "grounds_size",
            "internal_floor_area", "internal_floor_area_unit",
            "property_features", "occupancy_status", "access_provider",
            "access_contact_name", "access_contact_phone", "access_notes",
            "readiness_acknowledged", "preferred_package", "add_ons",
            "additional_stills_quantity", "scheduling_preference",
            "preferred_date", "alternative_date", "preferred_time_window",
            "on_camera", "on_camera_people", "audio_requirements", "how_heard",
            "print_source", "message", "custom_review_reasons", "custom_review_notes",
            "requires_custom_review", "consent_to_contact", "status", "package_summary",
            "included_photograph_count", "included_photographs_label",
            "turnaround_code", "turnaround_label",
        )
        read_only_fields = ("id", "status", "catalogue_version")
        extra_kwargs = {
            "consent_to_contact": {"required": True},
            "internal_floor_area": {"min_value": 1, "max_value": 1000000},
        }

    def _is_v2(self, attrs=None):
        if attrs is not None:
            return (attrs.get("form_schema_version") or 0) >= 2
        raw_version = getattr(self, "initial_data", {}).get("form_schema_version")
        return str(raw_version).strip() in {"2", "3"}

    def _is_v3(self, attrs=None):
        if attrs is not None:
            return attrs.get("form_schema_version") == 3
        return str(getattr(self, "initial_data", {}).get("form_schema_version")).strip() == "3"

    @staticmethod
    def _plausible_phone(value):
        value = str(value or "").strip()
        digits = re.sub(r"\D", "", value)
        return bool(PHONE_ALLOWED_RE.fullmatch(value)) and 7 <= len(digits) <= 15

    def validate_consent_to_contact(self, value):
        # Consent was already mandatory in the deployed legacy contract.
        if value is not True:
            raise serializers.ValidationError("Consent to contact is required.")
        return value

    def validate_add_ons(self, value):
        valid_keys = set(RealEstateEnquiry.ADD_ON_LABELS)
        invalid_keys = [item for item in value if item not in valid_keys]
        if invalid_keys:
            raise serializers.ValidationError(
                f"Invalid add-ons: {', '.join(invalid_keys)}."
            )
        if "extended_drone_video" in value:
            raise serializers.ValidationError(
                "Extended drone video is a historical service key and is not available for new public selection."
            )

        # The deployed form may submit travel and does not attach a stills
        # quantity. Preserve that behavior until legacy acceptance is retired.
        if self._is_v2():
            if len(value) != len(set(value)):
                raise serializers.ValidationError(
                    "Add-ons must not contain duplicates."
                )
            if "travel_supplement" in value:
                raise serializers.ValidationError(
                    "Travel is assessed internally after the property location is reviewed."
                )
        if self._is_v3():
            unavailable = sorted(set(value) - self.V3_PUBLIC_ADD_ONS)
            if unavailable:
                raise serializers.ValidationError(
                    f"These add-ons are not available for new public selection: {', '.join(unavailable)}."
                )
        return value

    def validate(self, attrs):
        errors = {}
        for field_name in self.TRIMMED_FIELDS:
            if field_name in attrs:
                attrs[field_name] = str(attrs.get(field_name) or "").strip()

        for field_name in self.LEGACY_REQUIRED_TEXT_FIELDS:
            if not attrs.get(field_name):
                errors[field_name] = "This field may not be blank."

        add_ons = set(attrs.get("add_ons") or [])
        package = attrs.get("preferred_package")
        catalogue_version = (
            CURRENT_CATALOGUE_VERSION if self._is_v3(attrs) else LEGACY_CATALOGUE_VERSION
        )
        conflicts = add_ons & get_included_add_ons(package, catalogue_version)
        if conflicts:
            labels = ", ".join(
                RealEstateEnquiry.ADD_ON_LABELS[key] for key in sorted(conflicts)
            )
            errors["add_ons"] = f"Already included with the selected package: {labels}."

        if package == RealEstateEnquiry.PreferredPackage.ESSENTIAL:
            errors["preferred_package"] = (
                "Essential is not available for new public enquiries. Choose Starter, "
                "Pro, Premium, Custom / POA or request a review."
            )

        if not self._is_v2(attrs):
            if errors:
                raise serializers.ValidationError(errors)
            return attrs

        if self._is_v3(attrs) and package not in self.V3_PUBLIC_PACKAGES:
            errors["preferred_package"] = (
                "Choose Starter, Pro, Premium, Custom / POA or request a review."
            )

        review_reasons = attrs.get("custom_review_reasons") or []
        invalid_review_reasons = sorted(
            set(review_reasons) - set(RealEstateEnquiry.CUSTOM_REVIEW_REASON_LABELS)
        )
        if invalid_review_reasons:
            errors["custom_review_reasons"] = (
                f"Invalid review reasons: {', '.join(invalid_review_reasons)}."
            )
        elif len(review_reasons) != len(set(review_reasons)):
            errors["custom_review_reasons"] = "Review reasons must not contain duplicates."
        if self._is_v3(attrs) and review_reasons and package not in {"custom", "not_sure"}:
            errors["preferred_package"] = (
                "This scope requires Custom / POA review and cannot use an automatic package selection."
            )

        for field_name in self.V2_REQUIRED_FIELDS:
            if not attrs.get(field_name):
                errors[field_name] = "This field is required for version 2 submissions."

        if attrs.get("county") not in IRISH_COUNTIES:
            errors["county"] = "Choose a valid Irish county."
        valid_property_types = set(RealEstateEnquiry.PropertyType.values)
        if attrs.get("property_type") not in valid_property_types:
            errors["property_type"] = "Choose a valid property category."
        if not self._plausible_phone(attrs.get("phone")):
            errors["phone"] = "Enter a plausible phone number."

        if attrs.get("client_type") in {
            RealEstateEnquiry.ClientType.ESTATE_AGENT,
            RealEstateEnquiry.ClientType.DEVELOPER,
        } and not attrs.get("company_name"):
            errors["company_name"] = "Company or agency name is required for this client type."

        eircode = attrs.get("eircode", "").upper()
        attrs["eircode"] = eircode
        no_eircode = attrs.get("no_eircode", False)
        if no_eircode:
            if eircode:
                errors["eircode"] = "Remove the Eircode when confirming the property has none."
            if not attrs.get("location_details"):
                errors["location_details"] = (
                    "Provide precise location details or a Google Maps link."
                )
        elif not eircode:
            errors["eircode"] = "Provide an Eircode or confirm that the property has none."
        elif not EIRCODE_RE.fullmatch(eircode):
            errors["eircode"] = "Enter a valid Irish Eircode."

        if (
            attrs.get("property_type") == RealEstateEnquiry.PropertyType.OTHER
            and not attrs.get("property_type_details")
        ):
            errors["property_type_details"] = "Describe the property category."
        if (
            attrs.get("secondary_accommodation")
            == RealEstateEnquiry.YesNoNotSure.YES
            and not attrs.get("secondary_accommodation_details")
        ):
            errors["secondary_accommodation_details"] = (
                "Describe the secondary accommodation."
            )
        if (
            attrs.get("outbuildings") == RealEstateEnquiry.YesNoNotSure.YES
            and not attrs.get("outbuildings_details")
        ):
            errors["outbuildings_details"] = "Describe the outbuildings."

        area = attrs.get("internal_floor_area")
        area_unit = attrs.get("internal_floor_area_unit")
        if area and not area_unit:
            errors["internal_floor_area_unit"] = "Choose a floor-area unit."
        if area_unit and not area:
            errors["internal_floor_area"] = "Enter the approximate floor area."

        access_provider = attrs.get("access_provider")
        if access_provider != RealEstateEnquiry.AccessProvider.ENQUIRER:
            if not attrs.get("access_contact_name"):
                errors["access_contact_name"] = "Provide the access contact's name."
            access_phone = attrs.get("access_contact_phone")
            if not access_phone:
                errors["access_contact_phone"] = "Provide the access contact's phone number."
            elif not self._plausible_phone(access_phone):
                errors["access_contact_phone"] = "Enter a plausible phone number."

        if attrs.get("readiness_acknowledged") is not True:
            errors["readiness_acknowledged"] = (
                "Please confirm the property will be ready at the agreed arrival time."
            )

        today = timezone.localdate()
        scheduling = attrs.get("scheduling_preference")
        preferred_date = attrs.get("preferred_date")
        alternative_date = attrs.get("alternative_date")
        if scheduling == RealEstateEnquiry.SchedulingPreference.REQUEST_DATE:
            if not preferred_date:
                errors["preferred_date"] = "Choose a preferred date."
        elif preferred_date:
            errors["preferred_date"] = (
                "Remove the preferred date when choosing flexible scheduling."
            )
        if preferred_date and preferred_date < today:
            errors["preferred_date"] = "Preferred date cannot be in the past."
        if alternative_date and alternative_date < today:
            errors["alternative_date"] = "Alternative date cannot be in the past."

        if attrs.get("on_camera") == RealEstateEnquiry.YesNoNotSure.YES:
            if not attrs.get("on_camera_people"):
                errors["on_camera_people"] = "Tell us who will appear on camera."
            if not attrs.get("audio_requirements"):
                errors["audio_requirements"] = (
                    "Describe the spoken-audio or microphone requirements."
                )

        quantity = attrs.get("additional_stills_quantity")
        if "additional_stills" in add_ons and quantity is None:
            errors["additional_stills_quantity"] = (
                "Choose how many additional edited photographs are required (maximum 50)."
            )
        elif "additional_stills" not in add_ons and quantity is not None:
            errors["additional_stills_quantity"] = (
                "Only provide a quantity when additional stills are selected."
            )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        validated_data["catalogue_version"] = (
            CURRENT_CATALOGUE_VERSION
            if validated_data.get("form_schema_version") == 3
            else LEGACY_CATALOGUE_VERSION
        )
        return super().create(validated_data)
