"""WSGI entry point for Q-Bet's Django shell."""

from __future__ import annotations

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")
application = get_wsgi_application()
