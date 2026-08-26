"""Server-rendered views for the minimal Q-Bet web shell."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render


ENGINE_STATUSES = (
    {"name": "Base", "status": "amber", "detail": "Calculation slice in progress"},
    {"name": "Yield", "status": "red", "detail": "Sandbox adapter pending"},
    {"name": "Alpha", "status": "red", "detail": "Sandbox adapter pending"},
)


def health(_: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "service": "q-bet-web"})


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "qbet_web/home.html", {"engine_statuses": ENGINE_STATUSES})


@login_required
def account_boundary(_: HttpRequest) -> HttpResponse:
    return HttpResponse("Authenticated Q-Bet web-shell boundary.")