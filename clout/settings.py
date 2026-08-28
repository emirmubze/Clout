import os

from pathlib import Path

from dotenv import load_dotenv

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
    "django-insecure-change-this-in-production",
)


DEBUG = (
    os.getenv(
        "DEBUG",
        "False",
    ).lower()
    == "true"
)


ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        "ALLOWED_HOSTS",
        "localhost,127.0.0.1",
    ).split(",")
    if host.strip()
]


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
# DATABASE
# =========================================================

DATABASES = {

    "default": {

        "ENGINE":
            "django.db.backends.sqlite3",

        "NAME":
            BASE_DIR / "db.sqlite3",
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
# CLOUDFLARE R2
# =========================================================

USE_S3 = (
    os.getenv(
        "USE_S3",
        "False",
    ).lower()
    == "true"
)


# Production MUST use R2.
if not DEBUG and not USE_S3:

    raise ImproperlyConfigured(
        "USE_S3 must be True in production."
    )


if USE_S3:

    # -----------------------------------------------------
    # R2 CREDENTIALS
    # -----------------------------------------------------

    AWS_ACCESS_KEY_ID = os.getenv(
        "AWS_ACCESS_KEY_ID",
        "",
    ).strip()


    AWS_SECRET_ACCESS_KEY = os.getenv(
        "AWS_SECRET_ACCESS_KEY",
        "",
    ).strip()


    AWS_STORAGE_BUCKET_NAME = os.getenv(
        "AWS_STORAGE_BUCKET_NAME",
        "clout",
    ).strip()


    # -----------------------------------------------------
    # R2 REGION
    # -----------------------------------------------------

    AWS_S3_REGION_NAME = os.getenv(
        "AWS_S3_REGION_NAME",
        "auto",
    ).strip()


    # -----------------------------------------------------
    # R2 S3 API ENDPOINT
    #
    # Example:
    # https://xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.r2.cloudflarestorage.com
    # -----------------------------------------------------

    AWS_S3_ENDPOINT_URL = (
        os.getenv(
            "AWS_S3_ENDPOINT_URL",
            "",
        )
        .strip()
        .rstrip("/")
    )


    # -----------------------------------------------------
    # PUBLIC R2 DOMAIN
    #
    # Example:
    # pub-xxxxxxxxxxxxxxxx.r2.dev
    #
    # Can also be:
    # https://pub-xxxxxxxxxxxxxxxx.r2.dev
    # -----------------------------------------------------

    AWS_S3_CUSTOM_DOMAIN = (
        os.getenv(
            "AWS_S3_CUSTOM_DOMAIN",
            "",
        )
        .strip()
        .removeprefix("https://")
        .removeprefix("http://")
        .rstrip("/")
    )


    # -----------------------------------------------------
    # PRODUCTION CHECK
    # -----------------------------------------------------

    if not DEBUG:

        missing_storage_settings = [

            name

            for name, value in {

                "AWS_ACCESS_KEY_ID":
                    AWS_ACCESS_KEY_ID,

                "AWS_SECRET_ACCESS_KEY":
                    AWS_SECRET_ACCESS_KEY,

                "AWS_STORAGE_BUCKET_NAME":
                    AWS_STORAGE_BUCKET_NAME,

                "AWS_S3_ENDPOINT_URL":
                    AWS_S3_ENDPOINT_URL,

                "AWS_S3_CUSTOM_DOMAIN":
                    AWS_S3_CUSTOM_DOMAIN,

            }.items()

            if not value
        ]


        if missing_storage_settings:

            raise ImproperlyConfigured(

                "Missing production R2 settings: "

                + ", ".join(
                    missing_storage_settings
                )
            )


    # -----------------------------------------------------
    # S3 / R2 SETTINGS
    # -----------------------------------------------------

    AWS_S3_SIGNATURE_VERSION = "s3v4"

    AWS_S3_ADDRESSING_STYLE = "path"

    AWS_S3_FILE_OVERWRITE = False

    AWS_DEFAULT_ACL = None


    # IMPORTANT:
    # Don't generate signed URLs for public course videos.
    AWS_QUERYSTRING_AUTH = False


    # -----------------------------------------------------
    # STORAGE
    # -----------------------------------------------------

    STORAGES = {

        "default": {

            "BACKEND":
                "storages.backends.s3.S3Storage",

            "OPTIONS": {

                "access_key":
                    AWS_ACCESS_KEY_ID,

                "secret_key":
                    AWS_SECRET_ACCESS_KEY,

                "bucket_name":
                    AWS_STORAGE_BUCKET_NAME,

                "endpoint_url":
                    AWS_S3_ENDPOINT_URL,

                "region_name":
                    AWS_S3_REGION_NAME,

                "signature_version":
                    "s3v4",

                "addressing_style":
                    "path",

                "file_overwrite":
                    False,

                "querystring_auth":
                    False,
            },
        },


        "staticfiles": {

            "BACKEND":
                "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }


    # -----------------------------------------------------
    # PUBLIC MEDIA URL
    # -----------------------------------------------------

    if AWS_S3_CUSTOM_DOMAIN:

        MEDIA_URL = (
            "https://"
            + AWS_S3_CUSTOM_DOMAIN
            + "/"
        )

    else:

        MEDIA_URL = "/media/"


else:

    # =====================================================
    # LOCAL DEVELOPMENT STORAGE
    # =====================================================

    STORAGES = {

        "default": {

            "BACKEND":
                "django.core.files.storage.FileSystemStorage",
        },


        "staticfiles": {

            "BACKEND":
                "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }


    MEDIA_URL = "/media/"

    MEDIA_ROOT = (
        BASE_DIR / "media"
    )


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


# =========================================================
# EMAIL
# =========================================================

EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
)


EMAIL_HOST = os.getenv(
    "EMAIL_HOST",
    "smtp.privateemail.com",
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


EMAIL_HOST_USER = os.getenv(
    "EMAIL_HOST_USER",
    "admin@clout.courses",
)


EMAIL_HOST_PASSWORD = os.getenv(
    "EMAIL_HOST_PASSWORD",
    "",
)


DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL",
    "Clout <admin@clout.courses>",
)


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
# PRODUCTION / RENDER SECURITY
# =========================================================

if not DEBUG:

    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )

    SESSION_COOKIE_SECURE = True