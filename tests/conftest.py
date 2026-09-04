import os

import django


# Normal test runs must stay isolated from external operational databases.
# A developer may keep QBET_DATABASE_URL set in the parent shell for explicit
# migration/admin commands; pytest removes it only inside this process.
os.environ.pop("QBET_DATABASE_URL", None)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")
django.setup()
