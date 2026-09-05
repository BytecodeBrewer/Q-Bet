import os

import django
from django.core.management import call_command


# Pytest never inherits the operational QBET_DATABASE_URL. CI supplies an
# explicit disposable PostgreSQL URL; local runs default to the documented
# localhost qbet_test database.
_LOCAL_TEST_DATABASE_URL = "postgresql://qbet:qbet@127.0.0.1:5432/qbet_test"
test_database_url = os.environ.get(
    "QBET_TEST_DATABASE_URL",
    _LOCAL_TEST_DATABASE_URL,
).strip()
if not test_database_url:
    raise RuntimeError("QBET_TEST_DATABASE_URL must not be empty")

os.environ["QBET_DATABASE_URL"] = test_database_url
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")
django.setup()

# Pytest runs Django TestCase classes without Django's test runner creating the
# database schema for us. The target database is disposable, so migrate it once
# at session bootstrap. In CI this is a harmless no-op after the workflow's
# explicit migration step.
call_command("migrate", interactive=False, verbosity=0)
