import os
from pathlib import Path
import sys
from dotenv import load_dotenv
from datetime import timedelta
from corsheaders.defaults import default_headers
from openeire_api.cache_config import build_cache_settings, env_bool, infer_runtime_env

load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG') == 'True'
RUNNING_TESTS = "test" in sys.argv

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
    running_tests=RUNNING_TESTS,
)
REQUIRE_SHARED_THROTTLE_CACHE = env_bool(
    os.getenv("REQUIRE_SHARED_THROTTLE_CACHE"),
    default=(not DEBUG and not RUNNING_TESTS),
)
THROTTLE_FAIL_OPEN = env_bool(
    os.getenv("THROTTLE_FAIL_OPEN"),
    default=DEBUG,
)

CACHES = build_cache_settings(
    cache_redis_url=None if RUNNING_TESTS else CACHE_REDIS_URL,
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
    'JWT_AUTH_COOKIE': None,
    'JWT_AUTH_REFRESH_COOKIE': None,
    'JWT_AUTH_HTTPONLY': False,
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
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': SQLITE_TIMEOUT_SECONDS,
        },
    }
}


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

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
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
]

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
DEFAULT_FROM_EMAIL = os.getenv('EMAIL_HOST_USER')

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

STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')

REST_USE_JWT = True
# JWT_AUTH_COOKIE = 'my-app-auth'
# JWT_AUTH_REFRESH_COOKIE = 'my-app-refresh-token'

# Social Auth Configuration
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': [
            'profile',
            'email',
        ],
        'AUTH_PARAMS': {
            'access_type': 'online',
        },
        'VERIFIED_EMAIL': True,
    }
}

# Disable email verification for social accounts (Google already verifies them)
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_EMAIL_REQUIRED = False

SOCIALACCOUNT_AUTO_SIGNUP = True

ACCOUNT_AUTHENTICATION_METHOD = 'email'
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_USERNAME_REQUIRED = False
