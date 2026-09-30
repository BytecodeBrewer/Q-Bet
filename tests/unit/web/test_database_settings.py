import pytest
from django.core.exceptions import ImproperlyConfigured

from qbet.web.settings import (
    database_config_from_url,
    exact_vercel_host,
    hosted_runtime_from_environment,
    require_database_url,
)


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
        "OPTIONS": {
            "prepare_threshold": None,
            "connect_timeout": 5,
            "sslmode": "require",
        },
    }


def test_database_config_from_url_rejects_non_postgres_scheme() -> None:
    with pytest.raises(ImproperlyConfigured, match="postgres"):
        database_config_from_url("mysql://user:pass@example.test/qbet")


def test_database_url_is_required_without_local_fallback() -> None:
    with pytest.raises(ImproperlyConfigured, match="required"):
        require_database_url("   ")


def test_hosted_runtime_detects_vercel_preview_and_production() -> None:
    assert hosted_runtime_from_environment({"VERCEL_ENV": "preview"})
    assert hosted_runtime_from_environment({"VERCEL_ENV": "production"})
    assert hosted_runtime_from_environment({"QBET_HOSTED_RUNTIME": "true"})
    assert hosted_runtime_from_environment({"QBET_HOSTED_PREVIEW": "true"})
    assert not hosted_runtime_from_environment({})
    assert not hosted_runtime_from_environment({"VERCEL_ENV": "development"})


def test_exact_vercel_host_accepts_only_exact_credential_free_https_hosts() -> None:
    assert exact_vercel_host("q-bet-git-feature.example.vercel.app") == (
        "q-bet-git-feature.example.vercel.app"
    )
    assert exact_vercel_host("https://q-bet.vercel.app") == "q-bet.vercel.app"
    assert exact_vercel_host("https://user:secret@q-bet.vercel.app") == ""
    assert exact_vercel_host("https://q-bet.vercel.app/path") == ""
