"""Administrator Smart Polling strategy controls backed by PostgreSQL."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from qbet.data.polling import PollingTarget
from qbet.storage.polling import PollingStrategyPersistenceError, PollingStrategyRepository
from qbet.web.controls import presentation_preferences
from qbet.web.forms import PollingStrategyForm


def _is_staff(user: object) -> bool:
    return bool(getattr(user, "is_staff", False))


def _default_initial() -> dict[str, object]:
    return {
        "transport": "api",
        "target": "market",
        "enabled": True,
        "freshness_minutes": 5,
        "market_interval_minutes": 5,
        "latest_market_poll_before_event_minutes": 1,
        "result_retry_minutes": 10,
        "max_attempts": 3,
        "capacity_class": "free",
        "request_cost_units": 1,
    }


def _render(
    request: HttpRequest,
    *,
    form: PollingStrategyForm,
    strategies: tuple[object, ...],
    error: bool = False,
    status: int = 200,
) -> HttpResponse:
    return render(
        request,
        "qbet_web/polling_settings.html",
        {
            "preferences": presentation_preferences(request.session),
            "polling_form": form,
            "polling_strategies": strategies,
            "polling_error": error,
        },
        status=status,
    )


@user_passes_test(_is_staff, login_url="login")
@require_http_methods(["GET", "POST"])
def polling_settings(request: HttpRequest) -> HttpResponse:
    repository = PollingStrategyRepository()
    try:
        strategies = repository.list()
    except PollingStrategyPersistenceError:
        return _render(
            request,
            form=PollingStrategyForm(initial=_default_initial()),
            strategies=(),
            error=True,
            status=503,
        )

    if request.method == "POST":
        if request.POST.get("action") == "toggle":
            enabled_value = request.POST.get("enabled")
            try:
                if enabled_value not in {"true", "false"}:
                    raise ValueError("invalid enabled value")
                repository.set_enabled(
                    provider_id=str(request.POST["provider_id"]),
                    source_id=str(request.POST["source_id"]),
                    target=PollingTarget(str(request.POST["target"])),
                    engine=str(request.POST.get("engine") or "") or None,
                    enabled=enabled_value == "true",
                )
            except (KeyError, ValueError):
                return _render(
                    request,
                    form=PollingStrategyForm(initial=_default_initial()),
                    strategies=strategies,
                    status=400,
                )
            except PollingStrategyPersistenceError:
                return _render(
                    request,
                    form=PollingStrategyForm(initial=_default_initial()),
                    strategies=strategies,
                    error=True,
                    status=503,
                )
            messages.success(request, "Smart Polling strategy state updated.")
            return redirect("admin-polling-settings")

        form = PollingStrategyForm(request.POST)
        if not form.is_valid():
            return _render(request, form=form, strategies=strategies, status=400)
        try:
            repository.save(form.to_strategy())
        except PollingStrategyPersistenceError:
            return _render(
                request,
                form=form,
                strategies=strategies,
                error=True,
                status=503,
            )
        messages.success(request, "Smart Polling strategy saved.")
        return redirect("admin-polling-settings")

    return _render(
        request,
        form=PollingStrategyForm(initial=_default_initial()),
        strategies=strategies,
    )
