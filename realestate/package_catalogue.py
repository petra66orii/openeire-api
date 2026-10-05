from dataclasses import dataclass


LEGACY_CATALOGUE_VERSION = "residential_2026_09"
CURRENT_CATALOGUE_VERSION = "residential_2026_10"
CATALOGUE_VERSION_CHOICES = (
    (LEGACY_CATALOGUE_VERSION, "Residential catalogue — September 2026 (legacy)"),
    (CURRENT_CATALOGUE_VERSION, "Residential catalogue — October 2026"),
)


ADDITIONAL_PHOTOGRAPH_PRICE_EUR = 10
ADDITIONAL_PHOTOGRAPH_COPY = "Additional edited photographs — EUR 10 each"
GALLERY_QUALIFIER = (
    "Photo quantities are indicative rather than a fixed minimum. The final gallery "
    "depends on the property's size, layout, presentation, access and agreed brief, "
    "with priority given to a strong, non-repetitive set of marketing images."
)
COMBINED_PROPERTY_VIDEO_DELIVERABLE = (
    "One combined cinematic 4K property film using ground and aerial footage, "
    "approximately 2–3 minutes where the property and agreed brief justify it"
)
SOCIAL_EDIT_DELIVERABLE = "One separate vertical 9:16 social-media edit"
PREMIUM_3D_DELIVERABLE = "One hosted 3D virtual tour for a suitable standard-sized property"


@dataclass(frozen=True)
class RealEstatePackage:
    name: str
    price_eur: int | None
    included_photographs: int | None
    included_photographs_max: int | None = None
    other_deliverables: tuple[str, ...] = ()
    included_add_ons: frozenset[str] = frozenset()
    public_description: str = ""
    public: bool = True
    indicative_photographs: bool = False

    @property
    def included_photographs_label(self):
        if self.included_photographs is None:
            return "Included photographs as specifically agreed"
        if self.indicative_photographs and self.included_photographs_max:
            return (
                f"Typically {self.included_photographs}–{self.included_photographs_max} "
                "professionally edited interior and exterior photographs, selected "
                "according to the property and agreed brief"
            )
        return f"{self.included_photographs} professionally edited interior and exterior ground photographs"

    @property
    def summary(self):
        price = f"EUR {self.price_eur}" if self.price_eur is not None else "POA"
        scope = self.included_photographs_label
        if self.other_deliverables:
            scope = f"{scope} + {' + '.join(self.other_deliverables)}"
        return f"{self.name} - {price} - {scope}"


LEGACY_REAL_ESTATE_PACKAGE_CATALOGUE = {
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
            "5-8 aerial drone stills in addition to the ground photographs",
            "2D measured floor plan",
        ),
        included_add_ons=frozenset({"floor_plan"}),
    ),
    "pro": RealEstatePackage(
        name="Pro",
        price_eur=419,
        included_photographs=30,
        other_deliverables=(
            "5-8 aerial drone stills in addition to the ground photographs",
            "2D measured floor plan",
            (
                "One combined 4K property film — 60–90 sec ground footage + "
                "60–90 sec aerial footage (approx. 2–3 min total)"
            ),
            "one vertical 9:16 social-media video",
        ),
        included_add_ons=frozenset({"floor_plan"}),
    ),
    "premium": RealEstatePackage(
        name="Premium",
        price_eur=549,
        included_photographs=35,
        other_deliverables=(
            "5-8 aerial drone stills in addition to the ground photographs",
            "2D measured floor plan",
            (
                "One combined 4K property film — 60–90 sec ground footage + "
                "60–90 sec aerial footage (approx. 2–3 min total)"
            ),
            "one vertical 9:16 social-media video",
            "hosted 3D virtual tour",
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

REAL_ESTATE_PACKAGE_CATALOGUE = {
    # Retained for discretionary internal mini-shoots and historical compatibility.
    "essential": RealEstatePackage(
        name="Essential",
        price_eur=175,
        included_photographs=10,
        public=False,
    ),
    "starter": RealEstatePackage(
        name="Starter",
        price_eur=259,
        included_photographs=25,
        included_photographs_max=30,
        indicative_photographs=True,
        public_description=(
            "Best for standard residential listings that need professional photography, "
            "aerial coverage and a measured floor plan."
        ),
        other_deliverables=(
            "5–8 edited drone stills",
            "Measured 2D floor plan",
            "Full-resolution delivery",
            "Commercial marketing licence",
        ),
        included_add_ons=frozenset({"floor_plan"}),
    ),
    "pro": RealEstatePackage(
        name="Pro",
        price_eur=419,
        included_photographs=30,
        included_photographs_max=35,
        indicative_photographs=True,
        public_description="For listings that benefit from a complete photography and video package.",
        other_deliverables=(
            "5–8 edited drone stills",
            "Measured 2D floor plan",
            COMBINED_PROPERTY_VIDEO_DELIVERABLE,
            SOCIAL_EDIT_DELIVERABLE,
            "Full-resolution delivery",
            "Commercial marketing licence",
        ),
        included_add_ons=frozenset({"floor_plan"}),
    ),
    "premium": RealEstatePackage(
        name="Premium",
        price_eur=549,
        included_photographs=35,
        included_photographs_max=40,
        indicative_photographs=True,
        public_description="For listings that need the fullest standard residential media package.",
        other_deliverables=(
            "5–8 edited drone stills",
            "Measured 2D floor plan",
            COMBINED_PROPERTY_VIDEO_DELIVERABLE,
            SOCIAL_EDIT_DELIVERABLE,
            PREMIUM_3D_DELIVERABLE,
            "Full-resolution delivery",
            "Commercial marketing licence",
        ),
        included_add_ons=frozenset({"floor_plan", "virtual_tour_3d"}),
    ),
    "custom": RealEstatePackage(
        name="Custom / POA",
        price_eur=None,
        included_photographs=None,
        public_description=(
            "Recommended for substantial grounds, multiple buildings or accommodation "
            "units, land-heavy coverage, unusually large properties, extensive twilight "
            "requirements, presenter-led production, bespoke film requirements or "
            "luxury/architectural work materially beyond the standard residential packages."
        ),
    ),
    "not_sure": RealEstatePackage(
        name="Not sure yet",
        price_eur=None,
        included_photographs=None,
    ),
}

PACKAGE_CATALOGUES = {
    LEGACY_CATALOGUE_VERSION: LEGACY_REAL_ESTATE_PACKAGE_CATALOGUE,
    CURRENT_CATALOGUE_VERSION: REAL_ESTATE_PACKAGE_CATALOGUE,
}

PACKAGE_SUMMARIES = {
    package_code: package.summary
    for package_code, package in REAL_ESTATE_PACKAGE_CATALOGUE.items()
}


def get_catalogue(catalogue_version=None):
    return PACKAGE_CATALOGUES.get(
        catalogue_version or CURRENT_CATALOGUE_VERSION,
        REAL_ESTATE_PACKAGE_CATALOGUE,
    )


def get_package(package_code, catalogue_version=None):
    return get_catalogue(catalogue_version).get(package_code)


def get_package_summary(package_code, fallback="", catalogue_version=None):
    package = get_package(package_code, catalogue_version)
    return package.summary if package else fallback


def get_included_photographs_label(
    package_code, fallback="Specifically agreed", catalogue_version=None
):
    package = get_package(package_code, catalogue_version)
    return package.included_photographs_label if package else fallback


def get_included_photograph_count(package_code, catalogue_version=None):
    package = get_package(package_code, catalogue_version)
    return package.included_photographs if package else None


def get_included_add_ons(package_code, catalogue_version=None):
    package = get_package(package_code, catalogue_version)
    return package.included_add_ons if package else frozenset()
