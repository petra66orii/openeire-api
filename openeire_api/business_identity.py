from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class BusinessIdentity:
    display_name: str
    address: str
    email: str
    phone: str
    registration_number: str
    signatory_name: str = ""

    def as_context(self):
        return {
            "business_display_name": self.display_name,
            "business_address": self.address,
            "business_email": self.email,
            "business_phone": self.phone,
            "business_registration_number": self.registration_number,
            "business_signatory_name": self.signatory_name,
        }


def get_business_identity(*, private_legal_document=False):
    signatory_name = ""
    if private_legal_document and getattr(
        settings, "SHOW_SIGNATORY_ON_LEGAL_DOCUMENTS", True
    ):
        signatory_name = str(getattr(settings, "BUSINESS_SIGNATORY_NAME", "") or "").strip()

    return BusinessIdentity(
        display_name=str(
            getattr(settings, "BUSINESS_DISPLAY_NAME", "OpenÉire Studios")
            or "OpenÉire Studios"
        ).strip(),
        address=str(getattr(settings, "BUSINESS_ADDRESS", "") or "").strip(),
        email=str(getattr(settings, "BUSINESS_EMAIL", "") or "").strip(),
        phone=str(getattr(settings, "BUSINESS_PHONE", "") or "").strip(),
        registration_number=str(
            getattr(settings, "BUSINESS_REGISTRATION_NUMBER", "") or ""
        ).strip(),
        signatory_name=signatory_name,
    )


def public_business_context():
    return get_business_identity().as_context()


def private_legal_business_context():
    return get_business_identity(private_legal_document=True).as_context()
