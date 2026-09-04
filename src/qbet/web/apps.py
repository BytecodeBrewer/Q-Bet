from django.apps import AppConfig


class QBetWebConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "qbet.web"
    verbose_name = "Q-Bet Web Shell"

    def ready(self) -> None:
        from qbet.web import signals  # noqa: F401
