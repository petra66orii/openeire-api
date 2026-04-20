import os


def get_admin_path() -> str:
    raw_path = os.getenv("DJANGO_ADMIN_URL", "admin/")
    normalized = raw_path.strip("/")
    if not normalized:
        return "admin/"
    return f"{normalized}/"
