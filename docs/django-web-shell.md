# Django Web Shell

The first Q-Bet browser entry point is a small Django server-rendered shell. It deliberately contains no calculation logic and uses fixture-only engine statuses.

## Local Start

```powershell
python -m pip install -e .
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000/` for the shell and `http://127.0.0.1:8000/health/` for the JSON health response. Local sign-in is available at `/accounts/login/`; it uses Django's built-in sessions, CSRF protection, and authentication.

## Environment

- `QBET_DJANGO_SECRET_KEY`: required unique secret outside local development.
- `QBET_DJANGO_DEBUG`: `true` locally, `false` in deployed environments.
- `QBET_DJANGO_ALLOWED_HOSTS`: comma-separated host allowlist.

## Future Supabase Auth Seam

Django owns the initial session and authentication boundary. A later integration may map a verified Supabase Auth identity to a Django user at the authentication boundary, then retain Django permissions and session behavior. That integration must add its own credentials and verification tests; this shell does not contact Supabase.