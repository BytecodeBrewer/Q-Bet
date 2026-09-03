import os

import django


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")
django.setup()
