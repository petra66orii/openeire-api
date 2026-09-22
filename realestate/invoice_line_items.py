from decimal import Decimal, ROUND_HALF_UP

from .package_catalogue import get_included_add_ons, get_package


MONEY = Decimal("0.01")

ADD_ON_PRICES = {
    "floor_plan": Decimal("75.00"),
    "virtual_tour_3d": Decimal("150.00"),
    "rush_delivery": Decimal("75.00"),
    "extended_drone_video": Decimal("150.00"),
    "additional_social_cuts": Decimal("50.00"),
}

ADD_ON_DESCRIPTIONS = {
    "floor_plan": "Measured 2D floor plan",
    "virtual_tour_3d": "Hosted 3D virtual tour",
    "rush_delivery": "Rush same-day still-photography delivery",
    "extended_drone_video": "Extended drone video, up to 3 minutes",
}


def money(value):
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _line(description, *, quantity=1, unit_amount):
    quantity = int(quantity)
    unit_amount = money(unit_amount)
    amount = money(unit_amount * quantity)
    return {
        "description": str(description).strip(),
        "quantity": quantity,
        "unit_amount": f"{unit_amount:.2f}",
        "amount": f"{amount:.2f}",
    }


def _social_video_description(package_code):
    if package_code in {"pro", "premium"}:
        return "Additional social-media cut, alternative format or edit"
    return "Vertical 9:16 social-media property video"


def _full_invoice_components(enquiry):
    package = get_package(enquiry.preferred_package)
    if not package or package.price_eur is None:
        return []

    package_description = f"{package.name} Package"
    if package.included_photographs:
        package_description += f" — {package.included_photographs} edited ground photographs"
    lines = [_line(package_description, unit_amount=package.price_eur)]

    included_add_ons = get_included_add_ons(enquiry.preferred_package)
    for key in enquiry.add_ons or []:
        if key in included_add_ons:
            continue
        if key == "additional_stills":
            quantity = enquiry.additional_stills_quantity
            if quantity:
                lines.append(
                    _line(
                        "Additional edited photographs",
                        quantity=quantity,
                        unit_amount=Decimal("10.00"),
                    )
                )
            continue
        if key == "travel_supplement":
            if enquiry.travel_supplement_amount:
                lines.append(
                    _line(
                        "Travel supplement beyond 40 km",
                        unit_amount=enquiry.travel_supplement_amount,
                    )
                )
            continue
        if key == "additional_social_cuts":
            lines.append(
                _line(
                    _social_video_description(enquiry.preferred_package),
                    unit_amount=ADD_ON_PRICES[key],
                )
            )
            continue
        if key in ADD_ON_PRICES:
            lines.append(
                _line(
                    ADD_ON_DESCRIPTIONS.get(key, enquiry.ADD_ON_LABELS.get(key, key)),
                    unit_amount=ADD_ON_PRICES[key],
                )
            )
    return lines


def build_invoice_line_items(enquiry, invoice_type, invoice_total, default_description):
    """Build the immutable customer-facing price breakdown for an invoice.

    Full invoices can show the package and each priced add-on. Deposit and
    balance invoices remain one payment-stage line because each is only a
    fraction of the agreed services rather than a purchase of specific items.
    """
    invoice_total = money(invoice_total)
    if invoice_type != "full":
        return [_line(default_description, unit_amount=invoice_total)]

    lines = _full_invoice_components(enquiry)
    if not lines:
        return [_line(default_description, unit_amount=invoice_total)]

    component_total = sum((money(item["amount"]) for item in lines), Decimal("0.00"))
    original_total = money(enquiry.original_required_total)
    agreed_price_difference = money(original_total - component_total)
    if agreed_price_difference:
        lines.append(
            _line(
                "Agreed package price adjustment",
                unit_amount=agreed_price_difference,
            )
        )

    for adjustment in enquiry.financial_adjustments.filter(
        reversed_at__isnull=True
    ).order_by("created_at"):
        lines.append(
            _line(
                adjustment.customer_description,
                unit_amount=-money(adjustment.amount),
            )
        )

    snapshot_total = sum((money(item["amount"]) for item in lines), Decimal("0.00"))
    if snapshot_total != invoice_total:
        # Never send an invoice whose visible lines do not reconcile to its total.
        return [_line(default_description, unit_amount=invoice_total)]
    return lines


def get_invoice_line_items(invoice):
    lines = invoice.line_items_snapshot or []
    if lines:
        return lines
    return [_line(invoice.description, unit_amount=invoice.total)]


def line_item_label(item):
    quantity = int(item.get("quantity") or 1)
    description = str(item.get("description") or "Service")
    if quantity <= 1:
        return description
    return f"{description} ({quantity} × EUR {money(item['unit_amount']):.2f})"
