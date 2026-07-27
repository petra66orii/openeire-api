NEXT_BUSINESS_DAY = "next_business_day"
TWO_BUSINESS_DAYS = "two_business_days"
SPECIFICALLY_AGREED = "specifically_agreed"

PACKAGE_TURNAROUND_CODES = {
    "essential": NEXT_BUSINESS_DAY,
    "starter": NEXT_BUSINESS_DAY,
    "pro": TWO_BUSINESS_DAYS,
    "premium": TWO_BUSINESS_DAYS,
    "custom": SPECIFICALLY_AGREED,
    "not_sure": SPECIFICALLY_AGREED,
}

TURNAROUND_LABELS = {
    NEXT_BUSINESS_DAY: "Next-business-day delivery",
    TWO_BUSINESS_DAYS: "Delivery within 2 business days",
    SPECIFICALLY_AGREED: "Turnaround as specifically agreed",
}

TURNAROUND_DETAILS = {
    NEXT_BUSINESS_DAY: (
        "This package is normally delivered by the end of the next business day."
    ),
    TWO_BUSINESS_DAYS: (
        "This package is normally delivered within two business days due to the "
        "additional video-production workload."
    ),
    SPECIFICALLY_AGREED: (
        "Turnaround will be set out in the specifically agreed quotation."
    ),
}

TURNAROUND_CONTEXT = (
    "Turnaround begins once the shoot is complete and all required property and "
    "client information has been supplied. Weather-dependent return visits and "
    "agreed changes to the scope may affect delivery."
)


def get_package_turnaround_code(package):
    return PACKAGE_TURNAROUND_CODES.get(str(package or ""), SPECIFICALLY_AGREED)


def get_package_turnaround_label(package):
    return TURNAROUND_LABELS[get_package_turnaround_code(package)]


def get_package_turnaround_detail(package):
    return TURNAROUND_DETAILS[get_package_turnaround_code(package)]
