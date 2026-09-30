"""Administrator Smart Polling strategy controls backed by PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core import signing
from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from qbet.data.models import DataSourceMetadata
from qbet.data.polling import (
    PollingStrategy,
    PollingStrategyResolutionError,
    PollingStrategyResolver,
    PollingTarget,
)
from qbet.storage.polling import PollingStrategyPersistenceError, PollingStrategyRepository
from qbet.web.controls import presentation_preferences
from qbet.web.forms import PollingStrategyForm


_ENGINE_LABELS = (
    ("bonus", "BonusEngine"),
    ("sports_capital", "SportsCapitalEngine"),
)
_IDENTITY_FIELDS = ("provider_id", "source_id", "transport", "target", "engine")
_CONTROL_FIELDS = {
    "action",
    "preset",
    "edit_provider",
    "edit_source",
    "edit_target",
    "edit_engine",
    "preview_token",
}

_PREVIEW_TOKEN_SALT = "qbet.polling-settings.preview"
_PREVIEW_TOKEN_MAX_AGE_SECONDS = 30 * 60


@dataclass(frozen=True)
class PollingPreset:
    key: str
    label: str
    description: str
    freshness_minutes: int
    market_refresh_points_minutes: tuple[int, ...]
    latest_market_poll_before_event_minutes: int
    result_retry_minutes: int
    max_attempts: int

    def values_for(self, target: PollingTarget) -> dict[str, object]:
        return {
            "freshness_minutes": self.freshness_minutes,
            "market_refresh_points_minutes": (
                ",".join(str(value) for value in self.market_refresh_points_minutes)
                if target is PollingTarget.MARKET
                else ""
            ),
            "market_interval_minutes": None,
            "latest_market_poll_before_event_minutes": (
                self.latest_market_poll_before_event_minutes
            ),
            "result_retry_minutes": self.result_retry_minutes,
            "max_attempts": self.max_attempts,
        }


POLLING_PRESETS = (
    PollingPreset(
        key="conservative",
        label="Conservative",
        description="Fewer refreshes and slower result retries.",
        freshness_minutes=15,
        market_refresh_points_minutes=(1440, 120, 15),
        latest_market_poll_before_event_minutes=5,
        result_retry_minutes=20,
        max_attempts=2,
    ),
    PollingPreset(
        key="standard",
        label="Standard",
        description="Phase 3 default balance of freshness and provider usage.",
        freshness_minutes=5,
        market_refresh_points_minutes=(1440, 720, 120, 15),
        latest_market_poll_before_event_minutes=1,
        result_retry_minutes=10,
        max_attempts=3,
    ),
    PollingPreset(
        key="frequent",
        label="Frequent",
        description="More near-event refresh points and faster result retries.",
        freshness_minutes=2,
        market_refresh_points_minutes=(1440, 720, 120, 30, 10),
        latest_market_poll_before_event_minutes=1,
        result_retry_minutes=5,
        max_attempts=5,
    ),
)


@dataclass(frozen=True)
class StrategyView:
    strategy: PollingStrategy
    role_label: str
    timing: str
    freshness: str
    retries: str
    capacity: str
    edit_query: str


@dataclass(frozen=True)
class EffectiveRouteView:
    engine: str
    engine_label: str
    strategy: StrategyView | None
    origin_label: str
    replaces_default: bool
    unavailable_label: str | None


@dataclass(frozen=True)
class StrategyGroupView:
    source: DataSourceMetadata
    target: PollingTarget
    default: StrategyView | None
    routes: tuple[EffectiveRouteView, ...]


@dataclass(frozen=True)
class PreviewImpact:
    route: str
    before: str
    after: str


@dataclass(frozen=True)
class StrategyPreview:
    candidate: StrategyView
    impacts: tuple[PreviewImpact, ...]


@dataclass(frozen=True)
class EditIdentity:
    provider_id: str
    source_id: str
    target: PollingTarget
    engine: str | None


def _is_staff(user: object) -> bool:
    return bool(getattr(user, "is_staff", False))


def _preset(key: str) -> PollingPreset:
    for preset in POLLING_PRESETS:
        if preset.key == key:
            return preset
    raise ValueError("unsupported polling preset")


def _default_initial() -> dict[str, object]:
    initial: dict[str, object] = {
        "transport": "api",
        "target": PollingTarget.MARKET.value,
        "enabled": True,
        "capacity_class": "free",
        "capacity_units": None,
        "request_cost_units": 1,
    }
    initial.update(_preset("standard").values_for(PollingTarget.MARKET))
    return initial


def _format_duration(value: timedelta) -> str:
    seconds = int(value.total_seconds())
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} h"
    if seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} sec"


def _strategy_timing(strategy: PollingStrategy) -> str:
    if strategy.target is PollingTarget.RESULT:
        return f"Retry every {_format_duration(strategy.result_retry_interval)}"
    if strategy.market_refresh_points:
        points = " → ".join(
            f"T-{_format_duration(point)}" for point in strategy.market_refresh_points
        )
        return f"{points}; stop inside T-{_format_duration(strategy.latest_market_poll_before_event)}"
    interval = strategy.market_interval
    assert interval is not None
    return (
        f"Every {_format_duration(interval)}; "
        f"stop inside T-{_format_duration(strategy.latest_market_poll_before_event)}"
    )


def _row_key(strategy: PollingStrategy) -> tuple[str, str, PollingTarget, str | None]:
    return (
        strategy.source.provider_id,
        strategy.source.source_id,
        strategy.target,
        strategy.engine,
    )


def _strategy_view(strategy: PollingStrategy) -> StrategyView:
    query = urlencode(
        {
            "edit_provider": strategy.source.provider_id,
            "edit_source": strategy.source.source_id,
            "edit_target": strategy.target.value,
            "edit_engine": strategy.engine or "",
        }
    )
    capacity_units = (
        "unbounded"
        if strategy.capacity_units is None
        else f"{strategy.capacity_units} units"
    )
    return StrategyView(
        strategy=strategy,
        role_label="Default" if strategy.engine is None else "Override",
        timing=_strategy_timing(strategy),
        freshness=_format_duration(strategy.freshness_window),
        retries=(
            f"{strategy.max_attempts} attempts · "
            f"{_format_duration(strategy.result_retry_interval)} result retry"
        ),
        capacity=(
            f"{strategy.capacity_class.value.title()} · {capacity_units} · "
            f"{strategy.request_cost_units} unit/request"
        ),
        edit_query=f"?{query}",
    )


def _resolution(
    strategies: tuple[PollingStrategy, ...],
    *,
    source: DataSourceMetadata,
    target: PollingTarget,
    engine: str,
) -> PollingStrategy | PollingStrategyResolutionError:
    try:
        return PollingStrategyResolver(strategies).resolve_for(
            source=source,
            target=target,
            engine=engine,
        )
    except PollingStrategyResolutionError as error:
        return error


def _resolution_label(
    strategies: tuple[PollingStrategy, ...],
    *,
    source: DataSourceMetadata,
    target: PollingTarget,
    engine: str,
) -> str:
    resolved = _resolution(
        strategies,
        source=source,
        target=target,
        engine=engine,
    )
    if isinstance(resolved, PollingStrategyResolutionError):
        if resolved.reason_code == "ambiguous_polling_strategy":
            return "Configuration conflict"
        return "No strategy configured"
    view = _strategy_view(resolved)
    origin = "Override" if resolved.engine == engine else "Default"
    state = "enabled" if resolved.enabled else "disabled"
    return (
        f"{origin} · {state} · freshness {view.freshness} · {view.timing} · "
        f"{view.retries} · {view.capacity}"
    )


def _strategy_groups(
    strategies: tuple[PollingStrategy, ...],
) -> tuple[StrategyGroupView, ...]:
    scopes: list[tuple[DataSourceMetadata, PollingTarget]] = []
    for strategy in strategies:
        scope = (strategy.source, strategy.target)
        if not any(existing_source == scope[0] and existing_target is scope[1] for existing_source, existing_target in scopes):
            scopes.append(scope)

    groups: list[StrategyGroupView] = []
    for source, target in sorted(
        scopes,
        key=lambda item: (
            item[0].provider_id,
            item[0].source_id,
            item[1].value,
            item[0].transport.value,
        ),
    ):
        default_strategy = next(
            (
                strategy
                for strategy in strategies
                if strategy.source == source
                and strategy.target is target
                and strategy.engine is None
            ),
            None,
        )
        routes: list[EffectiveRouteView] = []
        for engine, engine_label in _ENGINE_LABELS:
            resolved = _resolution(
                strategies,
                source=source,
                target=target,
                engine=engine,
            )
            if isinstance(resolved, PollingStrategyResolutionError):
                unavailable = (
                    "Configuration conflict"
                    if resolved.reason_code == "ambiguous_polling_strategy"
                    else "No strategy configured"
                )
                routes.append(
                    EffectiveRouteView(
                        engine=engine,
                        engine_label=engine_label,
                        strategy=None,
                        origin_label="Unavailable",
                        replaces_default=False,
                        unavailable_label=unavailable,
                    )
                )
                continue
            routes.append(
                EffectiveRouteView(
                    engine=engine,
                    engine_label=engine_label,
                    strategy=_strategy_view(resolved),
                    origin_label="Override" if resolved.engine == engine else "Default",
                    replaces_default=resolved.engine == engine and default_strategy is not None,
                    unavailable_label=None,
                )
            )

        groups.append(
            StrategyGroupView(
                source=source,
                target=target,
                default=(
                    _strategy_view(default_strategy)
                    if default_strategy is not None
                    else None
                ),
                routes=tuple(routes),
            )
        )
    return tuple(groups)


def _edit_identity(data: QueryDict) -> EditIdentity | None:
    provider_id = str(data.get("edit_provider") or "").strip()
    source_id = str(data.get("edit_source") or "").strip()
    target_value = str(data.get("edit_target") or "").strip()
    engine = str(data.get("edit_engine") or "").strip() or None
    if not any((provider_id, source_id, target_value, engine)):
        return None
    if not provider_id or not source_id or not target_value:
        raise ValueError("incomplete polling edit identity")
    if engine not in {None, "bonus", "sports_capital"}:
        raise ValueError("unsupported polling edit engine")
    return EditIdentity(
        provider_id=provider_id,
        source_id=source_id,
        target=PollingTarget(target_value),
        engine=engine,
    )


def _find_edit_strategy(
    strategies: tuple[PollingStrategy, ...],
    identity: EditIdentity,
) -> PollingStrategy:
    for strategy in strategies:
        if _row_key(strategy) == (
            identity.provider_id,
            identity.source_id,
            identity.target,
            identity.engine,
        ):
            return strategy
    raise Http404("Polling strategy not found")


def _configure_edit_form(form: PollingStrategyForm) -> None:
    for field_name in _IDENTITY_FIELDS:
        form.fields[field_name].disabled = True


def _form_data(post: QueryDict) -> QueryDict:
    data = post.copy()
    for field in _CONTROL_FIELDS:
        data.pop(field, None)
    return data


def _base_initial_from_post(
    post: QueryDict,
    *,
    base: dict[str, object] | None = None,
    lock_identity: bool = False,
) -> dict[str, object]:
    initial = dict(base or _default_initial())
    for field_name in PollingStrategyForm.base_fields:
        if lock_identity and field_name in _IDENTITY_FIELDS:
            continue
        if field_name == "enabled":
            initial[field_name] = field_name in post
        elif field_name in post:
            initial[field_name] = post.get(field_name)
    return initial


def _prospective_strategies(
    current: tuple[PollingStrategy, ...],
    candidate: PollingStrategy,
) -> tuple[PollingStrategy, ...]:
    candidate_key = _row_key(candidate)
    replaced = tuple(
        strategy for strategy in current if _row_key(strategy) != candidate_key
    )
    return (*replaced, candidate)


def _preview(
    current: tuple[PollingStrategy, ...],
    candidate: PollingStrategy,
) -> StrategyPreview:
    prospective = _prospective_strategies(current, candidate)
    previous = next(
        (strategy for strategy in current if _row_key(strategy) == _row_key(candidate)),
        None,
    )

    scopes: list[tuple[DataSourceMetadata, PollingTarget]] = [
        (candidate.source, candidate.target)
    ]
    if previous is not None and (
        previous.source != candidate.source or previous.target is not candidate.target
    ):
        scopes.append((previous.source, previous.target))

    impacts: list[PreviewImpact] = []
    for source, target in scopes:
        for engine, engine_label in _ENGINE_LABELS:
            before = _resolution_label(
                current,
                source=source,
                target=target,
                engine=engine,
            )
            after = _resolution_label(
                prospective,
                source=source,
                target=target,
                engine=engine,
            )
            if before != after:
                impacts.append(
                    PreviewImpact(
                        route=(
                            f"{engine_label} · {source.provider_id}/{source.source_id} "
                            f"· {target.value.title()}"
                        ),
                        before=before,
                        after=after,
                    )
                )

    return StrategyPreview(candidate=_strategy_view(candidate), impacts=tuple(impacts))


def _preview_token_payload(
    current: tuple[PollingStrategy, ...],
    candidate: PollingStrategy,
) -> dict[str, object]:
    ordered = sorted(
        current,
        key=lambda strategy: (
            strategy.source.provider_id,
            strategy.source.source_id,
            strategy.source.transport.value,
            strategy.target.value,
            strategy.engine or "",
        ),
    )
    return {
        "candidate": candidate.model_dump(mode="json"),
        "current": [strategy.model_dump(mode="json") for strategy in ordered],
    }


def _preview_token(
    current: tuple[PollingStrategy, ...],
    candidate: PollingStrategy,
) -> str:
    return signing.dumps(
        _preview_token_payload(current, candidate),
        salt=_PREVIEW_TOKEN_SALT,
        compress=True,
    )


def _preview_token_matches(
    token: str,
    current: tuple[PollingStrategy, ...],
    candidate: PollingStrategy,
) -> bool:
    if not token:
        return False
    try:
        payload = signing.loads(
            token,
            salt=_PREVIEW_TOKEN_SALT,
            max_age=_PREVIEW_TOKEN_MAX_AGE_SECONDS,
        )
    except signing.BadSignature:
        return False
    return payload == _preview_token_payload(current, candidate)


def _render(
    request: HttpRequest,
    *,
    form: PollingStrategyForm,
    strategies: tuple[PollingStrategy, ...],
    error: bool = False,
    status: int = 200,
    edit_identity: EditIdentity | None = None,
    preview: StrategyPreview | None = None,
    preview_token: str | None = None,
    preview_confirmation_required: bool = False,
    selected_preset: str = "standard",
) -> HttpResponse:
    return render(
        request,
        "qbet_web/polling_settings.html",
        {
            "preferences": presentation_preferences(request.session),
            "polling_form": form,
            "polling_strategies": tuple(_strategy_view(item) for item in strategies),
            "polling_groups": _strategy_groups(strategies),
            "polling_error": error,
            "polling_presets": POLLING_PRESETS,
            "polling_selected_preset": selected_preset,
            "polling_edit_identity": edit_identity,
            "polling_preview": preview,
            "polling_preview_token": preview_token,
            "polling_preview_confirmation_required": preview_confirmation_required,
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

    source_data = request.POST if request.method == "POST" else request.GET
    try:
        edit_identity = _edit_identity(source_data)
    except ValueError:
        return _render(
            request,
            form=PollingStrategyForm(initial=_default_initial()),
            strategies=strategies,
            status=400,
        )

    edit_strategy = (
        _find_edit_strategy(strategies, edit_identity)
        if edit_identity is not None
        else None
    )
    edit_initial = (
        PollingStrategyForm.initial_from_strategy(edit_strategy)
        if edit_strategy is not None
        else _default_initial()
    )

    if request.method == "POST":
        action = str(request.POST.get("action") or "save")

        if action == "toggle":
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
                    form=PollingStrategyForm(initial=edit_initial),
                    strategies=strategies,
                    edit_identity=edit_identity,
                    status=400,
                )
            except PollingStrategyPersistenceError:
                return _render(
                    request,
                    form=PollingStrategyForm(initial=edit_initial),
                    strategies=strategies,
                    edit_identity=edit_identity,
                    error=True,
                    status=503,
                )
            messages.success(request, "Smart Polling strategy state updated.")
            return redirect("admin-polling-settings")

        if action == "apply_preset":
            try:
                selected = _preset(str(request.POST.get("preset") or "standard"))
                base = _base_initial_from_post(
                    request.POST,
                    base=(
                        PollingStrategyForm.initial_from_strategy(edit_strategy)
                        if edit_strategy is not None
                        else None
                    ),
                    lock_identity=edit_strategy is not None,
                )
                target = PollingTarget(str(base.get("target") or PollingTarget.MARKET.value))
                base.update(selected.values_for(target))
            except ValueError:
                return _render(
                    request,
                    form=PollingStrategyForm(initial=edit_initial),
                    strategies=strategies,
                    edit_identity=edit_identity,
                    status=400,
                )
            form = PollingStrategyForm(initial=base)
            if edit_strategy is not None:
                _configure_edit_form(form)
            return _render(
                request,
                form=form,
                strategies=strategies,
                edit_identity=edit_identity,
                selected_preset=selected.key,
            )

        if action not in {"save", "preview"}:
            return _render(
                request,
                form=PollingStrategyForm(initial=edit_initial),
                strategies=strategies,
                edit_identity=edit_identity,
                status=400,
            )

        form = PollingStrategyForm(_form_data(request.POST), initial=edit_initial)
        if edit_strategy is not None:
            _configure_edit_form(form)
        if not form.is_valid():
            return _render(
                request,
                form=form,
                strategies=strategies,
                edit_identity=edit_identity,
                status=400,
                selected_preset=str(request.POST.get("preset") or "standard"),
            )

        candidate = form.to_strategy()
        if action == "preview":
            return _render(
                request,
                form=form,
                strategies=strategies,
                edit_identity=edit_identity,
                preview=_preview(strategies, candidate),
                preview_token=_preview_token(strategies, candidate),
                selected_preset=str(request.POST.get("preset") or "standard"),
            )

        posted_preview_token = str(request.POST.get("preview_token") or "")
        if not _preview_token_matches(posted_preview_token, strategies, candidate):
            return _render(
                request,
                form=form,
                strategies=strategies,
                edit_identity=edit_identity,
                preview=_preview(strategies, candidate),
                preview_token=_preview_token(strategies, candidate),
                preview_confirmation_required=True,
                status=409,
                selected_preset=str(request.POST.get("preset") or "standard"),
            )

        try:
            repository.save(candidate)
        except PollingStrategyPersistenceError:
            return _render(
                request,
                form=form,
                strategies=strategies,
                edit_identity=edit_identity,
                error=True,
                status=503,
                selected_preset=str(request.POST.get("preset") or "standard"),
            )
        messages.success(request, "Smart Polling strategy saved.")
        return redirect("admin-polling-settings")

    form = PollingStrategyForm(initial=edit_initial)
    if edit_strategy is not None:
        _configure_edit_form(form)
    return _render(
        request,
        form=form,
        strategies=strategies,
        edit_identity=edit_identity,
    )
