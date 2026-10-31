import os

SECRET_KEY = "tests-only"
DEBUG = False
ALLOWED_HOSTS = ["*"]
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "redis_throttle",
]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "tests.auth.HeaderUserMiddleware",
    "redis_throttle.middleware.RateLimitMiddleware",
]

ROOT_URLCONF = "tests.app"

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/15")

REDIS_THROTTLE = {
    "REDIS_URL": REDIS_URL,
    "KEY_PREFIX": "test",
    "RULES": [
        {"path": r"^/login/$", "methods": ["POST"], "rate": "3/m", "key": "ip", "algorithm": "fixed_window"},
    ],
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["tests.auth.HeaderAuthentication"],
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
    "DEFAULT_THROTTLE_RATES": {"anon": "3/m", "user": "5/m", "uploads": "2/m"},
}
