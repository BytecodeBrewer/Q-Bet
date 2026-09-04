import os

import django


# Tests must never inherit the shared operational Supabase URL. They require an
# explicit disposable PostgreSQL target with the same database semantics.
test_database_url = os.environ.get("QBET_TEST_DATABASE_URL", "").strip()
if not test_database_url:
    raise RuntimeError(
        "QBET_TEST_DATABASE_URL is required for tests; never point tests at production Supabase"
    )
os.environ["QBET_DATABASE_URL"] = test_database_url
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")
django.setup()
