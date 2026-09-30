"""Django settings for Q-Bet's PostgreSQL-backed local and hosted web shell."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[3]
_LOCAL_SECRET_KEY = "qbet-local-development-only-secret"
_POSTGRES_CONNECT_TIMEOUT_SECONDS = 5


def parse_allowed_hosts(value: str) -> list[str]:
    """Normalize the documented comma-separated Django host allowlist."""

    return [host.strip() for host in value.split(",") if host.strip()]


def _environment_flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() == "true"


def _environment_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ImproperlyConfigured(f"{name} must be a positive integer") from error
    if value <= 0:
        raise ImproperlyConfigured(f"{name} must be a positive integer")
    return value


def hosted_runtime_from_environment(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Detect every supported hosted Vercel runtime, not Preview only."""

    values = os.environ if environ is None else environ
    explicit = values.get("QBET_HOSTED_RUNTIME", "").strip().lower() == "true"
    legacy_preview = values.get("QBET_HOSTED_PREVIEW", "").strip().lower() == "true"
    vercel_environment = values.get("VERCEL_ENV", "").strip().lower()
    return explicit or legacy_preview or vercel_environment in {"preview", "production"}


def exact_vercel_host(value: str) -> str:
    """Return one exact credential-free Vercel host, or an empty string."""

    normalized = value.strip()
    if not normalized:
        return ""
    parsed = urlsplit(normalized if "://" in normalized else f"https://{normalized}")
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return ""
    return parsed.hostname or ""


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
    options: dict[str, object] = {
        "prepare_threshold": None,
        "connect_timeout": _POSTGRES_CONNECT_TIMEOUT_SECONDS,
    }
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


_VERCEL_ENVIRONMENT = os.environ.get("VERCEL_ENV", "").strip().lower()
QBET_HOSTED_PREVIEW = _environment_flag("QBET_HOSTED_PREVIEW") or _VERCEL_ENVIRONMENT == "preview"
QBET_HOSTED_RUNTIME = hosted_runtime_from_environment()
QBET_HOSTED_PRODUCTION = _VERCEL_ENVIRONMENT == "production"
QBET_DATABASE_URL = require_database_url(os.environ.get("QBET_DATABASE_URL", ""))
SECRET_KEY = os.environ.get("QBET_DJANGO_SECRET_KEY", _LOCAL_SECRET_KEY)
DEBUG = _environment_flag("QBET_DJANGO_DEBUG", "true")
_DEFAULT_ALLOWED_HOSTS = "" if QBET_HOSTED_RUNTIME else "localhost,127.0.0.1,testserver"
ALLOWED_HOSTS = parse_allowed_hosts(
    os.environ.get("QBET_DJANGO_ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS)
)
_CURRENT_VERCEL_HOST = exact_vercel_host(os.environ.get("VERCEL_URL", ""))
if _CURRENT_VERCEL_HOST and _CURRENT_VERCEL_HOST not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_CURRENT_VERCEL_HOST)

if QBET_HOSTED_RUNTIME and SECRET_KEY == _LOCAL_SECRET_KEY:
    raise ImproperlyConfigured(
        "QBET_DJANGO_SECRET_KEY must be configured for hosted deployments"
    )
if QBET_HOSTED_RUNTIME and DEBUG:
    raise ImproperlyConfigured("QBET_DJANGO_DEBUG must be false for hosted deployments")
if QBET_HOSTED_RUNTIME and any(
    host == "*" or host.startswith(".") for host in ALLOWED_HOSTS
):
    raise ImproperlyConfigured(
        "Hosted QBET_DJANGO_ALLOWED_HOSTS must contain exact hosts, not wildcards"
    )
if QBET_HOSTED_RUNTIME and not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "Hosted deployments require QBET_DJANGO_ALLOWED_HOSTS or an exact VERCEL_URL"
    )

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
    "qbet.web.middleware.BrowserSecurityHeadersMiddleware",
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
                "qbet.web.approval_context.approval_navigation",
                "qbet.web.shell_context.authenticated_shell",
            ],
        },
    },
]
WSGI_APPLICATION = "vercel_wsgi.application"

DATABASES = {
    "default": database_config_from_url(
        QBET_DATABASE_URL,
        require_ssl=QBET_HOSTED_RUNTIME,
    )
}


def optional_external_url(name: str) -> str:
    """Accept only a credential-free HTTP(S) infrastructure link."""

    value = os.environ.get(name, "").strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return value.rstrip("/")


QBET_PROFILE_WEB_REQUESTS = _environment_flag("QBET_PROFILE_WEB_REQUESTS") or (
    os.environ.get("VERCEL_ENV", "").strip().lower() == "preview"
)
QBET_METRICS_TOKEN = os.environ.get("QBET_METRICS_TOKEN", "")
QBET_EXECUTION_TICK_TOKEN = os.environ.get("QBET_EXECUTION_TICK_TOKEN", "")
QBET_EXECUTION_TICK_MAX_WORK = _environment_positive_int("QBET_EXECUTION_TICK_MAX_WORK", 10)
QBET_POLLING_TICK_TOKEN = os.environ.get("QBET_POLLING_TICK_TOKEN", "")
QBET_POLLING_TICK_MAX_WORK = _environment_positive_int("QBET_POLLING_TICK_MAX_WORK", 10)
QBET_POLLING_CLAIM_SECONDS = _environment_positive_int("QBET_POLLING_CLAIM_SECONDS", 120)
QBET_POLLING_DEFER_SECONDS = _environment_positive_int("QBET_POLLING_DEFER_SECONDS", 300)
QBET_GRAFANA_URL = optional_external_url("QBET_GRAFANA_URL")
QBET_VERCEL_DASHBOARD_URL = optional_external_url("QBET_VERCEL_DASHBOARD_URL")
QBET_SUPABASE_DASHBOARD_URL = optional_external_url("QBET_SUPABASE_DASHBOARD_URL")
QBET_SUPABASE_URL = optional_external_url("QBET_SUPABASE_URL")
QBET_SUPABASE_STORAGE_SECRET_KEY = os.environ.get(
    "QBET_SUPABASE_STORAGE_SECRET_KEY", ""
).strip()
QBET_SIMULATION_MODE_ENABLED = _environment_flag("QBET_SIMULATION_MODE_ENABLED")
QBET_SIMULATION_SPORTS_SOURCE = (
    os.environ.get("QBET_SIMULATION_SPORTS_SOURCE", "fixture").strip().lower()
)
QBET_SIMULATION_ODDS_SPORT = os.environ.get("QBET_SIMULATION_ODDS_SPORT", "").strip()
QBET_SIMULATION_ODDS_EVENT_ID = os.environ.get("QBET_SIMULATION_ODDS_EVENT_ID", "").strip()
QBET_SIMULATION_ODDS_MARKET = os.environ.get("QBET_SIMULATION_ODDS_MARKET", "").strip()
QBET_SIMULATION_ODDS_EVENT_STARTS_AT = os.environ.get(
    "QBET_SIMULATION_ODDS_EVENT_STARTS_AT", ""
).strip()
QBET_SIMULATION_ASSUMED_LIQUIDITY = os.environ.get("QBET_SIMULATION_ASSUMED_LIQUIDITY", "").strip()
QBET_SIMULATION_REQUESTED_TOTAL_STAKE = os.environ.get(
    "QBET_SIMULATION_REQUESTED_TOTAL_STAKE", ""
).strip()
QBET_SIMULATION_STAKE_PRECISION = os.environ.get("QBET_SIMULATION_STAKE_PRECISION", "").strip()
QBET_BONUS_SPORTSBOOK_FINANCIAL_TERMS = os.environ.get(
    "QBET_BONUS_SPORTSBOOK_FINANCIAL_TERMS",
    "",
).strip()
QBET_BONUS_COVERAGE_COMPARE_OFFERS = _environment_positive_int(
    "QBET_BONUS_COVERAGE_COMPARE_OFFERS",
    5,
)
QBET_BONUS_COVERAGE_COMPARE_PROVIDERS = _environment_positive_int(
    "QBET_BONUS_COVERAGE_COMPARE_PROVIDERS",
    2,
)
QBET_BONUS_COVERAGE_GOOD_OFFERS = _environment_positive_int(
    "QBET_BONUS_COVERAGE_GOOD_OFFERS",
    10,
)
QBET_BONUS_COVERAGE_GOOD_PROVIDERS = _environment_positive_int(
    "QBET_BONUS_COVERAGE_GOOD_PROVIDERS",
    3,
)
QBET_BONUS_COVERAGE_HEALTHY_OFFERS = _environment_positive_int(
    "QBET_BONUS_COVERAGE_HEALTHY_OFFERS",
    15,
)
QBET_BONUS_COVERAGE_HEALTHY_PROVIDERS = _environment_positive_int(
    "QBET_BONUS_COVERAGE_HEALTHY_PROVIDERS",
    4,
)

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

_DEFAULT_EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if QBET_HOSTED_RUNTIME
    else "django.core.mail.backends.locmem.EmailBackend"
)
EMAIL_BACKEND = os.environ.get("QBET_EMAIL_BACKEND", _DEFAULT_EMAIL_BACKEND)
DEFAULT_FROM_EMAIL = os.environ.get("QBET_DEFAULT_FROM_EMAIL", "qbet@localhost")
EMAIL_HOST = os.environ.get("QBET_EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("QBET_EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("QBET_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("QBET_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = _environment_flag("QBET_EMAIL_USE_TLS")
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24
QBET_EMAIL_VERIFICATION_TIMEOUT = 60 * 60 * 24

SESSION_COOKIE_SECURE = QBET_HOSTED_RUNTIME
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = _environment_positive_int("QBET_SESSION_COOKIE_AGE_SECONDS", 60 * 60 * 12)
CSRF_COOKIE_SECURE = QBET_HOSTED_RUNTIME
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_SSL_REDIRECT = QBET_HOSTED_RUNTIME
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "no-referrer"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365 if QBET_HOSTED_PRODUCTION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "request_json": {"()": "qbet.web.logging.SafeRequestJSONFormatter"},
    },
    "filters": {
        "redact_account_tokens": {"()": "qbet.web.logging.AccountTokenRedactionFilter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "request_json",
            "filters": ["redact_account_tokens"],
        },
    },
    "loggers": {
        "qbet.web.request": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
