import os

import django


# Normal local test runs use a disposable localhost PostgreSQL database by
# default. CI supplies the same URL explicitly. The shared operational
# QBET_DATABASE_URL is never inherited by pytest.
_LOCAL_TEST_DATABASE_URL = "postgresql://qbet:qbet@127.0.0.1:5432/qbet_test"
operational_database_url = os.environ.get("QBET_DATABASE_URL", "").strip()
test_database_url = os.environ.get(
    "QBET_TEST_DATABASE_URL",
    _LOCAL_TEST_DATABASE_URL,
).strip()

if operational_database_url and test_database_url == operational_database_url:
    raise RuntimeError(
        "QBET_TEST_DATABASE_URL must not point at the operational Q-Bet database"
    )

os.environ["QBET_DATABASE_URL"] = test_database_url
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")
django.setup()
