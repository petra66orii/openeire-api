from rest_framework import serializers

from .models import RealEstateEnquiry


class RealEstateEnquirySerializer(serializers.ModelSerializer):
    REQUIRED_TEXT_FIELDS = (
        "name",
        "phone",
        "property_address",
        "county",
        "property_type",
    )

    add_ons = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        default=list,
    )

    class Meta:
        model = RealEstateEnquiry
        fields = (
            "id",
            "name",
            "email",
            "phone",
            "client_type",
            "property_address",
            "county",
            "property_type",
            "preferred_package",
            "consent_to_contact",
            "company_name",
            "eircode",
            "add_ons",
            "preferred_date",
            "how_heard",
            "message",
            "status",
        )
        read_only_fields = ("id", "status")

    def validate_consent_to_contact(self, value):
        if value is not True:
            raise serializers.ValidationError("Consent to contact is required.")
        return value

    def validate_add_ons(self, value):
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("Add-ons must be provided as a list.")

        valid_keys = set(RealEstateEnquiry.ADD_ON_LABELS.keys())
        invalid_keys = [item for item in value if item not in valid_keys]
        if invalid_keys:
            raise serializers.ValidationError(
                f"Invalid add-ons: {', '.join(invalid_keys)}."
            )
        return value

    def validate(self, attrs):
        attrs["company_name"] = str(attrs.get("company_name", "") or "").strip()
        attrs["eircode"] = str(attrs.get("eircode", "") or "").strip()
        attrs["message"] = str(attrs.get("message", "") or "").strip()
        attrs["county"] = str(attrs.get("county", "") or "").strip()
        attrs["property_type"] = str(attrs.get("property_type", "") or "").strip()
        attrs["property_address"] = str(attrs.get("property_address", "") or "").strip()
        attrs["phone"] = str(attrs.get("phone", "") or "").strip()
        attrs["name"] = str(attrs.get("name", "") or "").strip()

        for field_name in self.REQUIRED_TEXT_FIELDS:
            if field_name in attrs and not attrs[field_name]:
                raise serializers.ValidationError(
                    {field_name: "This field may not be blank."}
                )
        return attrs
