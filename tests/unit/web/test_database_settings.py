import pytest
from django.core.exceptions import ImproperlyConfigured

from qbet.web.settings import database_config_from_url, require_database_url


def test_database_config_from_url_parses_supabase_pooler_connection() -> None:
    config = database_config_from_url(
        "postgresql://postgres.project-ref:p%3Dword@pooler.example.com:6543/postgres?sslmode=prefer",
        require_ssl=True,
    )

    assert config == {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "postgres",
        "USER": "postgres.project-ref",
        "PASSWORD": "p=word",
        "HOST": "pooler.example.com",
        "PORT": 6543,
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"prepare_threshold": None, "sslmode": "require"},
    }


def test_database_config_from_url_rejects_non_postgres_scheme() -> None:
    with pytest.raises(ImproperlyConfigured, match="postgres"):
        database_config_from_url("mysql://user:pass@example.test/qbet")


def test_database_url_is_required_without_local_fallback() -> None:
    with pytest.raises(ImproperlyConfigured, match="required"):
        require_database_url("   ")
