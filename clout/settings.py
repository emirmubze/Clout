import os

from pathlib import Path

from dotenv import load_dotenv

import dj_database_url

from django.core.exceptions import ImproperlyConfigured


# =========================================================
# BASE DIRECTORY
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent


# =========================================================
# ENVIRONMENT
# =========================================================

load_dotenv(
    BASE_DIR / ".env"
)


# =========================================================
# SECURITY
# =========================================================

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-clout-production-persistent-session-secret-key-2026-secure",
)


DEBUG = (
    os.getenv(
        "DEBUG",
        "False",
    ).lower()
    == "true"
)


configured_hosts = [
    host.strip()
    for host in os.getenv(
        "ALLOWED_HOSTS",
        "",
    ).split(",")
    if host.strip()
]

render_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
ALLOWED_HOSTS = list(dict.fromkeys([
    "localhost",
    "127.0.0.1",
    "testserver",
    "clout.onrender.com",
    "clout.courses",
    "www.clout.courses",
    *configured_hosts,
    render_hostname,
]))
ALLOWED_HOSTS = [host for host in ALLOWED_HOSTS if host]

configured_csrf = [
    origin.strip()
    for origin in os.getenv(
        "CSRF_TRUSTED_ORIGINS",
        "",
    ).split(",")
    if origin.strip()
]

CSRF_TRUSTED_ORIGINS = list(dict.fromkeys([
    "https://clout.onrender.com",
    "https://clout.courses",
    "https://www.clout.courses",
    "http://localhost",
    "http://127.0.0.1",
    *(f"https://{host}" for host in ALLOWED_HOSTS if host and not host.startswith(("http://", "https://"))),
    *(f"http://{host}" for host in ALLOWED_HOSTS if host and not host.startswith(("http://", "https://"))),
    *configured_csrf,
]))
CSRF_TRUSTED_ORIGINS = [origin for origin in CSRF_TRUSTED_ORIGINS if origin]


# =========================================================
# APPLICATIONS
# =========================================================

INSTALLED_APPS = [

    "django.contrib.admin",

    "django.contrib.auth",

    "django.contrib.contenttypes",

    "django.contrib.sessions",

    "django.contrib.messages",

    "django.contrib.staticfiles",

    "storages",

    "shop",
]


# =========================================================
# MIDDLEWARE
# =========================================================

MIDDLEWARE = [

    "django.middleware.security.SecurityMiddleware",

    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    "django.middleware.common.CommonMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "shop.middleware.SingleDeviceSessionMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# =========================================================
# URL CONFIGURATION
# =========================================================

ROOT_URLCONF = "clout.urls"


# =========================================================
# TEMPLATES
# =========================================================

TEMPLATES = [

    {
        "BACKEND":
            "django.template.backends.django.DjangoTemplates",

        "DIRS": [],

        "APP_DIRS": True,

        "OPTIONS": {

            "context_processors": [

                "django.template.context_processors.request",

                "django.contrib.auth.context_processors.auth",

                "django.contrib.messages.context_processors.messages",

            ],
        },
    },
]


# =========================================================
# WSGI
# =========================================================

WSGI_APPLICATION = "clout.wsgi.application"


# =========================================================
# PERSISTENT STORAGE PATH RESOLUTION
# =========================================================

PERSISTENT_DATA_DIR_ENV = (
    os.getenv("PERSISTENT_DATA_DIR", "").strip()
    or os.getenv("DATA_DIR", "").strip()
)

if PERSISTENT_DATA_DIR_ENV:
    PERSISTENT_DATA_DIR = Path(PERSISTENT_DATA_DIR_ENV)
elif Path("/var/data").exists() and Path("/var/data").is_dir():
    PERSISTENT_DATA_DIR = Path("/var/data")
else:
    PERSISTENT_DATA_DIR = BASE_DIR

try:
    PERSISTENT_DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


# =========================================================
# DATABASE (PERSISTENT STORAGE SUPPORT)
# =========================================================

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_ENGINE = os.getenv("DB_ENGINE", "").strip()
DB_HOST = os.getenv("DB_HOST", "").strip()

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
            ssl_require=bool(
                "postgres" in DATABASE_URL
                and (
                    "render.com" in os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
                    or "sslmode=require" in DATABASE_URL
                    or (not DEBUG and "127.0.0.1" not in DATABASE_URL and "localhost" not in DATABASE_URL)
                )
            ),
        )
    }
elif DB_HOST or DB_ENGINE or os.getenv("USE_POSTGRES", "").lower() in ("1", "true", "yes"):
    DATABASES = {
        "default": {
            "ENGINE": DB_ENGINE or "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "clout"),
            "USER": os.getenv("DB_USER", "postgres"),
            "PASSWORD": os.getenv("DB_PASSWORD", "Mubashir@66"),
            "HOST": DB_HOST or "127.0.0.1",
            "PORT": os.getenv("DB_PORT", "5432"),
            "CONN_MAX_AGE": 600,
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    sqlite_path = os.getenv("SQLITE_PATH", "").strip()
    if sqlite_path:
        db_path = Path(sqlite_path)
    else:
        db_path = PERSISTENT_DATA_DIR / "db.sqlite3"

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if (
            not db_path.exists()
            and (BASE_DIR / "db.sqlite3").exists()
            and db_path.resolve() != (BASE_DIR / "db.sqlite3").resolve()
        ):
            import shutil
            shutil.copy2(BASE_DIR / "db.sqlite3", db_path)
    except Exception:
        pass

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": db_path,
        }
    }


# =========================================================
# PASSWORD VALIDATION
# =========================================================

AUTH_PASSWORD_VALIDATORS = [

    {
        "NAME":
            "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },

    {
        "NAME":
            "django.contrib.auth.password_validation.MinimumLengthValidator",
    },

    {
        "NAME":
            "django.contrib.auth.password_validation.CommonPasswordValidator",
    },

    {
        "NAME":
            "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# =========================================================
# AUTHENTICATION
# =========================================================

AUTHENTICATION_BACKENDS = [

    "shop.backends.EmailOrUsernameModelBackend",

    "django.contrib.auth.backends.ModelBackend",
]


# =========================================================
# LANGUAGE / TIMEZONE
# =========================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Kolkata"

USE_I18N = True

USE_TZ = True


# =========================================================
# STATIC FILES
# =========================================================

STATIC_URL = "/static/"

STATICFILES_DIRS = [

    BASE_DIR / "shop" / "static",

]

STATIC_ROOT = (
    BASE_DIR / "staticfiles"
)


# =========================================================
# CLOUDFLARE R2 / S3 & PERSISTENT MEDIA STORAGE
# =========================================================

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME", "clout").strip()
AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "auto").strip()
AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL", "").strip().rstrip("/")
AWS_S3_CUSTOM_DOMAIN = (
    os.getenv("AWS_S3_CUSTOM_DOMAIN", "")
    .strip()
    .removeprefix("https://")
    .removeprefix("http://")
    .rstrip("/")
)

explicit_use_s3 = os.getenv("USE_S3", "").strip().lower()
has_s3_credentials = bool(
    AWS_ACCESS_KEY_ID
    and AWS_SECRET_ACCESS_KEY
    and AWS_STORAGE_BUCKET_NAME
    and (AWS_S3_ENDPOINT_URL or AWS_S3_CUSTOM_DOMAIN or AWS_S3_REGION_NAME)
)

r2_configured = (
    has_s3_credentials
    and (explicit_use_s3 in ("true", "1", "yes") or explicit_use_s3 == "")
)

if r2_configured:
    USE_S3 = True
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_ADDRESSING_STYLE = "path"
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = False

    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "access_key": AWS_ACCESS_KEY_ID,
                "secret_key": AWS_SECRET_ACCESS_KEY,
                "bucket_name": AWS_STORAGE_BUCKET_NAME,
                "endpoint_url": AWS_S3_ENDPOINT_URL,
                "region_name": AWS_S3_REGION_NAME,
                "signature_version": "s3v4",
                "addressing_style": "path",
                "file_overwrite": False,
                "querystring_auth": False,
            },
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
        },
    }

    if AWS_S3_CUSTOM_DOMAIN:
        MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/"
    elif AWS_S3_ENDPOINT_URL:
        MEDIA_URL = f"{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/"
    else:
        MEDIA_URL = "/media/"
    MEDIA_ROOT = PERSISTENT_DATA_DIR / "media"
else:
    USE_S3 = False
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage" if not DEBUG else "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
    MEDIA_URL = "/media/"
    MEDIA_ROOT = PERSISTENT_DATA_DIR / "media"

# Ensure all media subdirectories exist in persistent storage
try:
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    for sub_dir in ["profiles", "course_videos", "course_thumbnails", "lesson_thumbnails", "contact_images", "contact_videos", "subtitles"]:
        (MEDIA_ROOT / sub_dir).mkdir(parents=True, exist_ok=True)
except Exception:
    pass


# =========================================================
# DEFAULT PRIMARY KEY
# =========================================================

DEFAULT_AUTO_FIELD = (
    "django.db.models.BigAutoField"
)


# =========================================================
# CUSTOM USER
# =========================================================

AUTH_USER_MODEL = "shop.CustomUser"


# =========================================================
# LOGIN / LOGOUT
# =========================================================

LOGIN_URL = "login"

LOGIN_REDIRECT_URL = "index"

LOGOUT_REDIRECT_URL = "index"


# =========================================================
# SESSION
# =========================================================

SESSION_ENGINE = (
    "django.contrib.sessions.backends.db"
)

SESSION_COOKIE_HTTPONLY = True

SESSION_COOKIE_SAMESITE = "Lax"

SESSION_COOKIE_SECURE = not DEBUG

SESSION_COOKIE_AGE = (
    60 * 60 * 24 * 14
)

SESSION_SAVE_EVERY_REQUEST = True

X_FRAME_OPTIONS = "SAMEORIGIN"


# =========================================================
# EMAIL
# =========================================================

EMAIL_HOST = os.getenv(
    "EMAIL_HOST",
    "smtp.gmail.com",
)

EMAIL_PORT = int(
    os.getenv(
        "EMAIL_PORT",
        "587",
    )
)

EMAIL_USE_TLS = (
    os.getenv(
        "EMAIL_USE_TLS",
        "True",
    ).lower()
    == "true"
)

EMAIL_USE_SSL = (
    os.getenv(
        "EMAIL_USE_SSL",
        "False",
    ).lower()
    == "true"
)

EMAIL_HOST_USER = os.getenv(
    "EMAIL_HOST_USER",
    "support@clout.courses",
)

EMAIL_HOST_PASSWORD = os.getenv(
    "EMAIL_HOST_PASSWORD",
    "",
).replace(" ", "").strip()

EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST_PASSWORD
    else "django.core.mail.backends.console.EmailBackend",
)

DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL",
    "Clout <noreply@clout.courses>",
)

SERVER_EMAIL = os.getenv(
    "SERVER_EMAIL",
    "support@clout.courses",
)

EMAIL_TIMEOUT = 10

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "Clout <noreply@clout.courses>").strip()
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()


# =========================================================
# RAZORPAY
# =========================================================

RAZORPAY_KEY_ID = os.getenv(
    "RAZORPAY_KEY_ID",
    "",
)

RAZORPAY_KEY_SECRET = os.getenv(
    "RAZORPAY_KEY_SECRET",
    "",
)


# =========================================================
# GROQ
# =========================================================

GROQ_API_KEY = os.getenv(
    "GROQ_API_KEY",
    "",
)


# =========================================================
# SESSIONS & AUTHENTICATION PERSISTENCE
# =========================================================

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 1209600  # 14 days
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"


# =========================================================
# PRODUCTION / SECURITY
# =========================================================

X_FRAME_OPTIONS = "SAMEORIGIN"

if not DEBUG:

    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True