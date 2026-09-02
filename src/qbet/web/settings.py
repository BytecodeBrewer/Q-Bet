"""Minimal Django settings for the local Q-Bet web shell."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]


def parse_allowed_hosts(value: str) -> list[str]:
    """Normalize the documented comma-separated Django host allowlist."""

    return [host.strip() for host in value.split(",") if host.strip()]


SECRET_KEY = os.environ.get(
    "QBET_DJANGO_SECRET_KEY", "qbet-local-development-only-secret"
)
DEBUG = os.environ.get("QBET_DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = parse_allowed_hosts(
    os.environ.get("QBET_DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "qbet.web.apps.QBetWebConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "qbet.web.middleware.RequestCorrelationMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "qbet.web.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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
WSGI_APPLICATION = "qbet.web.wsgi.application"

QBET_SIMULATION_REPORT_DB = (
    Path(value) if (value := os.environ.get("QBET_SIMULATION_REPORT_DB")) else None
)
QBET_SIMULATION_MODE_ENABLED = (
    os.environ.get("QBET_SIMULATION_MODE_ENABLED", "false").lower() == "true"
)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "qbet-web.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "home"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "request_json": {"()": "qbet.web.logging.SafeRequestJSONFormatter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "request_json"},
    },
    "loggers": {
        "qbet.web.request": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
