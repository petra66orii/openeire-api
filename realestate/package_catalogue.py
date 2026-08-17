from dataclasses import dataclass


ADDITIONAL_PHOTOGRAPH_PRICE_EUR = 10
ADDITIONAL_PHOTOGRAPH_COPY = (
    "Additional edited photographs may be agreed at EUR 10 per photograph."
)
COMBINED_PROPERTY_VIDEO_DELIVERABLE = (
    "One combined 4K property film — 60–90 sec ground footage + 60–90 sec "
    "aerial footage (approx. 2–3 min total)"
)


@dataclass(frozen=True)
class RealEstatePackage:
    name: str
    price_eur: int | None
    included_photographs: int | None
    other_deliverables: str = ""
    included_add_ons: frozenset[str] = frozenset()

    @property
    def included_photographs_label(self):
        if self.included_photographs is None:
            return "Included photographs as specifically agreed"
        return (
            f"{self.included_photographs} professionally edited interior "
            "and exterior ground photographs"
        )

    @property
    def summary(self):
        price = f"EUR {self.price_eur}" if self.price_eur is not None else "POA"
        scope = self.included_photographs_label
        if self.other_deliverables:
            scope = f"{scope} + {self.other_deliverables}"
        return f"{self.name} - {price} - {scope}"


REAL_ESTATE_PACKAGE_CATALOGUE = {
    "essential": RealEstatePackage(
        name="Essential",
        price_eur=175,
        included_photographs=10,
    ),
    "starter": RealEstatePackage(
        name="Starter",
        price_eur=259,
        included_photographs=25,
        other_deliverables=(
            "5-8 aerial drone stills in addition to the ground photographs + "
            "2D measured floor plan"
        ),
        included_add_ons=frozenset({"floor_plan"}),
    ),
    "pro": RealEstatePackage(
        name="Pro",
        price_eur=419,
        included_photographs=30,
        other_deliverables=(
            "5-8 aerial drone stills in addition to the ground photographs + "
            f"2D measured floor plan + {COMBINED_PROPERTY_VIDEO_DELIVERABLE} + "
            "one vertical 9:16 social-media video"
        ),
        included_add_ons=frozenset({"floor_plan"}),
    ),
    "premium": RealEstatePackage(
        name="Premium",
        price_eur=549,
        included_photographs=35,
        other_deliverables=(
            "5-8 aerial drone stills in addition to the ground photographs + "
            f"2D measured floor plan + {COMBINED_PROPERTY_VIDEO_DELIVERABLE} + "
            "one vertical 9:16 social-media video + "
            "hosted 3D virtual tour"
        ),
        included_add_ons=frozenset({"floor_plan", "virtual_tour_3d"}),
    ),
    "custom": RealEstatePackage(
        name="Custom",
        price_eur=None,
        included_photographs=None,
    ),
    "not_sure": RealEstatePackage(
        name="Not sure yet",
        price_eur=None,
        included_photographs=None,
    ),
}

PACKAGE_SUMMARIES = {
    package_code: package.summary
    for package_code, package in REAL_ESTATE_PACKAGE_CATALOGUE.items()
}


def get_package(package_code):
    return REAL_ESTATE_PACKAGE_CATALOGUE.get(package_code)


def get_package_summary(package_code, fallback=""):
    package = get_package(package_code)
    return package.summary if package else fallback


def get_included_photographs_label(package_code, fallback="Specifically agreed"):
    package = get_package(package_code)
    return package.included_photographs_label if package else fallback


def get_included_photograph_count(package_code):
    package = get_package(package_code)
    return package.included_photographs if package else None


def get_included_add_ons(package_code):
    package = get_package(package_code)
    return package.included_add_ons if package else frozenset()
