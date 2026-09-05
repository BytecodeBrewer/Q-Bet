"""Django settings for Q-Bet's PostgreSQL-backed local and hosted web shell."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[3]
_LOCAL_SECRET_KEY = "qbet-local-development-only-secret"


def parse_allowed_hosts(value: str) -> list[str]:
    """Normalize the documented comma-separated Django host allowlist."""

    return [host.strip() for host in value.split(",") if host.strip()]


def _environment_flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() == "true"


def require_database_url(value: str) -> str:
    """Require the shared PostgreSQL source of truth instead of falling back locally."""

    normalized = value.strip()
    if not normalized:
        raise ImproperlyConfigured(
            "QBET_DATABASE_URL is required; Q-Bet no longer falls back to SQLite"
        )
    return normalized


def database_config_from_url(value: str, *, require_ssl: bool = False) -> dict[str, object]:
    """Build a Django PostgreSQL configuration from the namespaced database URL."""

    parsed = urlsplit(value)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured(
            "QBET_DATABASE_URL must use the postgres:// or postgresql:// scheme"
        )
    try:
        port = parsed.port or 5432
    except ValueError as error:
        raise ImproperlyConfigured("QBET_DATABASE_URL contains an invalid port") from error

    database_name = unquote(parsed.path.lstrip("/"))
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if not parsed.hostname or not database_name or not username:
        raise ImproperlyConfigured(
            "QBET_DATABASE_URL must include a host, database name, and username"
        )

    query = parse_qs(parsed.query)
    options: dict[str, object] = {"prepare_threshold": None}
    sslmode = query.get("sslmode", [None])[-1]
    if require_ssl:
        sslmode = "require"
    if sslmode:
        options["sslmode"] = sslmode

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": database_name,
        "USER": username,
        "PASSWORD": password,
        "HOST": parsed.hostname,
        "PORT": port,
        "CONN_MAX_AGE": 0,
        "OPTIONS": options,
    }


QBET_HOSTED_PREVIEW = _environment_flag("QBET_HOSTED_PREVIEW")
QBET_DATABASE_URL = require_database_url(os.environ.get("QBET_DATABASE_URL", ""))
SECRET_KEY = os.environ.get("QBET_DJANGO_SECRET_KEY", _LOCAL_SECRET_KEY)
DEBUG = _environment_flag("QBET_DJANGO_DEBUG", "true")
ALLOWED_HOSTS = parse_allowed_hosts(
    os.environ.get("QBET_DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
)

if QBET_HOSTED_PREVIEW and SECRET_KEY == _LOCAL_SECRET_KEY:
    raise ImproperlyConfigured(
        "QBET_DJANGO_SECRET_KEY must be configured for hosted preview deployments"
    )
if QBET_HOSTED_PREVIEW and DEBUG:
    raise ImproperlyConfigured("QBET_DJANGO_DEBUG must be false for hosted preview deployments")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "qbet.storage.apps.QBetStorageConfig",
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
WSGI_APPLICATION = "vercel_wsgi.application"

<<<<<<< Updated upstream
DATABASES = {
    "default": database_config_from_url(
        QBET_DATABASE_URL,
        require_ssl=QBET_HOSTED_PREVIEW,
=======
if QBET_HOSTED_PREVIEW:
    QBET_SIMULATION_REPORT_DB = None
    QBET_SIMULATION_MODE_ENABLED = False
    if QBET_DATABASE_URL:
        DATABASES = {"default": database_config_from_url(QBET_DATABASE_URL, require_ssl=True)}
    else:
        DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
        SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
else:
    QBET_SIMULATION_REPORT_DB = Path(
        os.environ.get(
            "QBET_SIMULATION_REPORT_DB",
            str(BASE_DIR / "qbet-simulation.sqlite3"),
        )
>>>>>>> Stashed changes
    )
}
QBET_SIMULATION_MODE_ENABLED = _environment_flag("QBET_SIMULATION_MODE_ENABLED")

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

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
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
