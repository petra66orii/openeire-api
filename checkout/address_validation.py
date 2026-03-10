import re


US_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
# Irish routing key is one letter + two digits, except special Dublin key D6W.
IE_EIRCODE_RE = re.compile(r"^(?:[AC-FHKNPRTV-Y]\d{2}|D6W)[AC-FHKNPRTV-Y0-9]{4}$")
ALLOWED_PHYSICAL_SHIPPING_COUNTRIES = {"IE", "US"}


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalize_country(country):
    return _clean(country).upper()


def validate_physical_shipping_address(*, country, line1, town, postcode, county):
    """
    Returns a dict of field-level validation errors for physical shipping addresses.
    Empty dict means valid.
    """
    errors = {}
    country_code = _normalize_country(country)
    clean_line1 = _clean(line1)
    clean_town = _clean(town)
    clean_postcode = _clean(postcode)
    clean_county = _clean(county)

    if not country_code:
        errors["country"] = "Shipping country is required for physical orders."
        return errors

    if country_code not in ALLOWED_PHYSICAL_SHIPPING_COUNTRIES:
        errors["country"] = (
            f"Physical products can currently only be shipped to Ireland (IE) "
            f"or the US. You selected {country_code}."
        )
        return errors

    if not clean_line1:
        errors["street_address1"] = "Street address is required for physical orders."
    if not clean_town:
        errors["town"] = "Town/City is required for physical orders."
    if not clean_postcode:
        errors["postcode"] = "Postcode/ZIP is required for physical orders."

    if country_code == "US":
        if not clean_county:
            errors["county"] = "State is required for US shipping addresses."
        if clean_postcode and not US_ZIP_RE.match(clean_postcode):
            errors["postcode"] = "Enter a valid US ZIP code (12345 or 12345-6789)."
    elif country_code == "IE":
        normalized_eircode = clean_postcode.replace(" ", "").upper()
        if clean_postcode and not IE_EIRCODE_RE.match(normalized_eircode):
            errors["postcode"] = "Enter a valid Irish Eircode (e.g., D01 F5P2)."

    return errors
