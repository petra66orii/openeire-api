import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
import dj_database_url
from dotenv import load_dotenv
from datetime import timedelta
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key
from corsheaders.defaults import default_headers
from openeire_api.cache_config import build_cache_settings, env_bool, infer_runtime_env

load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG') == 'True'
RUNNING_TESTS = "test" in sys.argv
USING_TEST_SETTINGS = os.getenv("DJANGO_SETTINGS_MODULE", "").endswith("settings_test")
IS_TEST_ENV = RUNNING_TESTS or USING_TEST_SETTINGS

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG or IS_TEST_ENV:
        SECRET_KEY = get_random_secret_key()
    else:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is False.")


def _is_weak_secret_key(value):
    if not value:
        return True
    if value.startswith("django-insecure-"):
        return True
    if len(value) < 50:
        return True
    if len(set(value)) < 5:
        return True
    return False


ENFORCE_STRONG_SECRET_KEY = env_bool(
    os.getenv("ENFORCE_STRONG_SECRET_KEY"),
    default=False,
)
if ENFORCE_STRONG_SECRET_KEY and _is_weak_secret_key(SECRET_KEY):
    raise ImproperlyConfigured(
        "SECRET_KEY appears weak. Use a long, random key with high entropy."
    )

# --- R2 CONFIG (Shared Across Environments) ---
R2_ACCESS_KEY_ID = os.getenv('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.getenv('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.getenv('R2_BUCKET_NAME')
R2_ENDPOINT_URL = os.getenv('R2_ENDPOINT_URL')
R2_CUSTOM_DOMAIN = os.getenv('R2_CUSTOM_DOMAIN')

R2_PRIVATE_BUCKET_NAME = os.getenv('R2_PRIVATE_BUCKET_NAME')
R2_PRIVATE_ACCESS_KEY_ID = os.getenv('R2_PRIVATE_ACCESS_KEY_ID', R2_ACCESS_KEY_ID)
R2_PRIVATE_SECRET_ACCESS_KEY = os.getenv('R2_PRIVATE_SECRET_ACCESS_KEY', R2_SECRET_ACCESS_KEY)

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "api.openeire.ie",
    "api.openeire.online",
]

RENDER_EXTERNAL_HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites', 
    'django.contrib.sitemaps',     
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'rest_framework.authtoken',
    'dj_rest_auth',
    'dj_rest_auth.registration',
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'storages',
    'django_countries',
    'django_summernote',
    'userprofiles',
    'products',
    'checkout',
    'blog',
    'home',
    'taggit',
]

SITE_ID = 1

THROTTLE_CACHE_ALIAS = "throttle"
CACHE_REDIS_URL = os.getenv("CACHE_REDIS_URL") or os.getenv("REDIS_URL")
APP_ENV = infer_runtime_env(
    app_env=os.getenv("APP_ENV"),
    render_environment=os.getenv("RENDER_ENVIRONMENT"),
    debug=DEBUG,
    running_tests=IS_TEST_ENV,
)
REQUIRE_SHARED_THROTTLE_CACHE = False if IS_TEST_ENV else env_bool(
    os.getenv("REQUIRE_SHARED_THROTTLE_CACHE"),
    default=(not DEBUG),
)
THROTTLE_FAIL_OPEN = env_bool(
    os.getenv("THROTTLE_FAIL_OPEN"),
    default=DEBUG,
)
JWT_USE_HTTPONLY_COOKIES = env_bool(
    os.getenv("JWT_USE_HTTPONLY_COOKIES"),
    default=False,
)
JWT_ACCESS_COOKIE_NAME = os.getenv("JWT_ACCESS_COOKIE_NAME", "openeire_access")
JWT_REFRESH_COOKIE_NAME = os.getenv("JWT_REFRESH_COOKIE_NAME", "openeire_refresh")
JWT_COOKIE_SECURE = env_bool(
    os.getenv("JWT_COOKIE_SECURE"),
    default=(not DEBUG),
)
JWT_COOKIE_SAMESITE = os.getenv("JWT_COOKIE_SAMESITE", "Lax")
JWT_COOKIE_DOMAIN = os.getenv("JWT_COOKIE_DOMAIN") or None
JWT_COOKIE_CSRF_PROTECTION = env_bool(
    os.getenv("JWT_COOKIE_CSRF_PROTECTION"),
    default=JWT_USE_HTTPONLY_COOKIES,
)
JWT_CSRF_COOKIE_NAME = os.getenv("JWT_CSRF_COOKIE_NAME", "openeire_csrf")
JWT_CSRF_HEADER_NAME = os.getenv("JWT_CSRF_HEADER_NAME", "HTTP_X_CSRFTOKEN")

if JWT_USE_HTTPONLY_COOKIES and JWT_COOKIE_SAMESITE.lower() == "none" and not JWT_COOKIE_CSRF_PROTECTION:
    raise ImproperlyConfigured(
        "JWT cookie mode with SameSite=None requires JWT_COOKIE_CSRF_PROTECTION=True."
    )

CACHES = build_cache_settings(
    cache_redis_url=None if IS_TEST_ENV else CACHE_REDIS_URL,
    cache_key_prefix=os.getenv("CACHE_KEY_PREFIX", f"openeire-api:{APP_ENV}"),
    cache_redis_connect_timeout_seconds=os.getenv("CACHE_REDIS_CONNECT_TIMEOUT_SECONDS"),
    cache_redis_socket_timeout_seconds=os.getenv("CACHE_REDIS_SOCKET_TIMEOUT_SECONDS"),
    throttle_cache_alias=THROTTLE_CACHE_ALIAS,
    require_shared_throttle_cache=REQUIRE_SHARED_THROTTLE_CACHE,
)

# Simple JWT Configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'license_request': '10/hour',
        'gallery_access_request': '5/hour',
        'gallery_access_verify': '20/hour',
        'prodigi_callback': '30/minute',
    },
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=600),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,

    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'VERIFYING_KEY': None,
    'AUDIENCE': None,
    'ISSUER': None,
    'JWK_URL': None,
    'LEEWAY': 0,

    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'USER_AUTHENTICATION_RULE': 'rest_framework_simplejwt.authentication.default_user_authentication_rule',

    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',
    'TOKEN_USER_CLASS': 'rest_framework_simplejwt.models.TokenUser',

    'JTI_CLAIM': 'jti',

    'SLIDING_TOKEN_REFRESH_EXP_CLAIM': 'refresh_exp',
    'SLIDING_TOKEN_LIFETIME': timedelta(minutes=5),
    'SLIDING_TOKEN_REFRESH_LIFETIME': timedelta(days=1),
}

REST_AUTH = {
    'USE_JWT': True,
    'JWT_AUTH_COOKIE': JWT_ACCESS_COOKIE_NAME if JWT_USE_HTTPONLY_COOKIES else None,
    'JWT_AUTH_REFRESH_COOKIE': JWT_REFRESH_COOKIE_NAME if JWT_USE_HTTPONLY_COOKIES else None,
    'JWT_AUTH_HTTPONLY': JWT_USE_HTTPONLY_COOKIES,
}

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'openeire_api.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'openeire_api.wsgi.application'

# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

SQLITE_TIMEOUT_SECONDS = int(os.getenv('SQLITE_TIMEOUT_SECONDS', '30'))
SQLITE_SAVE_RETRY_ATTEMPTS = int(os.getenv('SQLITE_SAVE_RETRY_ATTEMPTS', '6'))
SQLITE_SAVE_RETRY_DELAY_SECONDS = float(os.getenv('SQLITE_SAVE_RETRY_DELAY_SECONDS', '0.3'))

DATABASES = {
    'default': dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        conn_health_checks=True,
    )
}

# Apply the SQLite timeout only if we are actually using SQLite locally
if DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
    DATABASES['default']['OPTIONS'] = {'timeout': SQLITE_TIMEOUT_SECONDS}


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 8,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

# --- STORAGE CONFIGURATION ---

# 1. STATIC FILES (CSS/JS)
# We use WhiteNoise to serve these directly from Render.
# R2 is too slow for tiny CSS/JS requests.
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# 2. MEDIA FILES (Uploads)
if RUNNING_TESTS:
    MEDIA_URL = '/media/'
    MEDIA_ROOT = BASE_DIR / "test_media"
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
elif not DEBUG:
    # --- PUBLIC BUCKET CONFIGURATION ---
    AWS_ACCESS_KEY_ID = R2_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY = R2_SECRET_ACCESS_KEY
    AWS_STORAGE_BUCKET_NAME = R2_BUCKET_NAME
    AWS_S3_ENDPOINT_URL = R2_ENDPOINT_URL
    
    AWS_S3_REGION_NAME = 'auto' 
    AWS_S3_SIGNATURE_VERSION = 's3v4'
    AWS_S3_FILE_OVERWRITE = False
    AWS_S3_CUSTOM_DOMAIN = R2_CUSTOM_DOMAIN

    if AWS_S3_CUSTOM_DOMAIN:
        MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/'
    else:
        MEDIA_URL = f'{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/'

    # --- DEFINE STORAGES ---
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage", # Public bucket for standard uploads
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
        # We don't define the private bucket in the STORAGES dict because 
        # we are injecting it directly via the PrivateR2Storage class in your models!
    }
else:
    # Local Development settings
    MEDIA_URL = '/media/'
    MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
    
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

FRONTEND_URL = os.getenv("FRONTEND_URL")

def _frontend_origin_from_url(value):
    if not value:
        return None
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImproperlyConfigured(
            "FRONTEND_URL must be an absolute URL such as https://app.example.com."
        )
    return f"{parsed.scheme}://{parsed.netloc}"

FRONTEND_ORIGIN = _frontend_origin_from_url(FRONTEND_URL)

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://openeire.onrender.com",
]

CORS_ALLOW_HEADERS = list(default_headers) + [
    'x-gallery-access-token',
]

CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://openeire.ie",
    "https://openeire.online",
    "https://openeire.onrender.com",
]

if FRONTEND_ORIGIN and FRONTEND_ORIGIN not in CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS.append(FRONTEND_ORIGIN)
    CSRF_TRUSTED_ORIGINS.append(FRONTEND_ORIGIN)

SECURE_SSL_REDIRECT = env_bool(
    os.getenv("SECURE_SSL_REDIRECT"),
    default=(not DEBUG),
)
SESSION_COOKIE_SECURE = env_bool(
    os.getenv("SESSION_COOKIE_SECURE"),
    default=(not DEBUG),
)
CSRF_COOKIE_SECURE = env_bool(
    os.getenv("CSRF_COOKIE_SECURE"),
    default=(not DEBUG),
)
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
CSRF_COOKIE_SAMESITE = os.getenv("CSRF_COOKIE_SAMESITE", "Lax")
SECURE_HSTS_SECONDS = int(
    os.getenv("SECURE_HSTS_SECONDS", "31536000" if not DEBUG else "0")
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool(
    os.getenv("SECURE_HSTS_INCLUDE_SUBDOMAINS"),
    default=(not DEBUG),
)
SECURE_HSTS_PRELOAD = env_bool(
    os.getenv("SECURE_HSTS_PRELOAD"),
    default=(not DEBUG),
)
SECURE_REFERRER_POLICY = os.getenv("SECURE_REFERRER_POLICY", "strict-origin-when-cross-origin")
X_FRAME_OPTIONS = os.getenv("X_FRAME_OPTIONS", "DENY")
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Email Configuration
if not DEBUG:
     EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

EMAIL_HOST = 'smtp-relay.brevo.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL')
LICENCE_ADMIN_NOTIFICATION_RECIPIENTS = [
    email.strip()
    for email in os.getenv('LICENCE_ADMIN_NOTIFICATION_RECIPIENTS', '').split(',')
    if email.strip()
]
if (
    EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend'
    and not IS_TEST_ENV
    and not DEFAULT_FROM_EMAIL
):
    raise ImproperlyConfigured(
        "DEFAULT_FROM_EMAIL must be set when the SMTP email backend is enabled."
    )

# Stripe Configuration

AI_WORKER_SECRET = os.getenv('AI_WORKER_SECRET')
AI_WORKER_IP_ALLOWLIST = [
    ip.strip() for ip in os.getenv('AI_WORKER_IP_ALLOWLIST', '').split(',')
    if ip.strip()
]
AI_WORKER_TRUSTED_PROXY_IPS = [
    ip.strip() for ip in os.getenv('AI_WORKER_TRUSTED_PROXY_IPS', '').split(',')
    if ip.strip()
]
AI_WORKER_MAX_BATCH = int(os.getenv('AI_WORKER_MAX_BATCH', '25'))
AI_WORKER_MAX_BATCH_HARD = int(os.getenv('AI_WORKER_MAX_BATCH_HARD', '100'))
AI_DRAFT_MAX_CHARS = int(os.getenv('AI_DRAFT_MAX_CHARS', '8000'))
STRIPE_TIMEOUT_SECONDS = int(os.getenv('STRIPE_TIMEOUT_SECONDS', '10'))
STRIPE_MAX_NETWORK_RETRIES = int(os.getenv('STRIPE_MAX_NETWORK_RETRIES', '2'))
PRODIGI_CONNECT_TIMEOUT_SECONDS = float(os.getenv('PRODIGI_CONNECT_TIMEOUT_SECONDS', '5'))
PRODIGI_READ_TIMEOUT_SECONDS = float(os.getenv('PRODIGI_READ_TIMEOUT_SECONDS', '20'))
PRODIGI_CALLBACK_BASE_URL = os.getenv("PRODIGI_CALLBACK_BASE_URL")
PRODIGI_CALLBACK_TOKEN = os.getenv("PRODIGI_CALLBACK_TOKEN", "")
FREE_SHIPPING_ENABLED = env_bool(
    os.getenv("FREE_SHIPPING_ENABLED"),
    default=False,
)
FREE_SHIPPING_THRESHOLD = os.getenv("FREE_SHIPPING_THRESHOLD", "150.00")
FREE_SHIPPING_ELIGIBLE_COUNTRIES = [
    code.strip().upper()
    for code in os.getenv("FREE_SHIPPING_ELIGIBLE_COUNTRIES", "IE").split(",")
    if code.strip()
]

STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_OAUTH_SECRET = os.getenv("GOOGLE_OAUTH_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_OAUTH_KEY = os.getenv("GOOGLE_OAUTH_KEY", "")

REST_USE_JWT = True
# JWT_AUTH_COOKIE = 'my-app-auth'
# JWT_AUTH_REFRESH_COOKIE = 'my-app-refresh-token'

# Social Auth Configuration
google_provider_settings = {
    'SCOPE': [
        'profile',
        'email',
    ],
    'AUTH_PARAMS': {
        'access_type': 'online',
    },
    'VERIFIED_EMAIL': True,
}

if GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_SECRET:
    google_provider_settings['APP'] = {
        'client_id': GOOGLE_OAUTH_CLIENT_ID,
        'secret': GOOGLE_OAUTH_SECRET,
        'key': GOOGLE_OAUTH_KEY,
    }
elif GOOGLE_OAUTH_CLIENT_ID or GOOGLE_OAUTH_SECRET:
    raise ImproperlyConfigured(
        "Google OAuth configuration is incomplete. Set both GOOGLE_OAUTH_CLIENT_ID "
        "and GOOGLE_OAUTH_SECRET (or GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)."
    )

SOCIALACCOUNT_PROVIDERS = {
    'google': google_provider_settings,
}

# Disable email verification for social accounts (Google already verifies them)
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_EMAIL_REQUIRED = False

SOCIALACCOUNT_AUTO_SIGNUP = True

ACCOUNT_AUTHENTICATION_METHOD = 'email'
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_USERNAME_REQUIRED = False

# Send Django request/server errors to the platform logs without enabling DEBUG.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'render': {
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'render',
        },
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.server': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'products': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}
