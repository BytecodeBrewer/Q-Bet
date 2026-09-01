"""Server-rendered views for the minimal Q-Bet web shell."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

ENGINE_STATUSES = (
    {
        "name": "BonusEngine",
        "status": "green",
        "detail": "Promotional strategy calculations ready for simulation",
    },
    {
        "name": "SportsCapitalEngine",
        "status": "amber",
        "detail": "Arbitrage and dutching workflow in progress",
    },
)


def health(_: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "service": "q-bet-web"})


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "qbet_web/home.html", {"engine_statuses": ENGINE_STATUSES})


@login_required
def account_boundary(_: HttpRequest) -> HttpResponse:
    return HttpResponse("Authenticated Q-Bet web-shell boundary.")
