from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from qbet.web import views

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("register/", views.register, name="register"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/layout/", views.dashboard_layout_update, name="dashboard-layout"),
    path("engines/<str:engine_id>/", views.engine_detail, name="engine-detail"),
    path("simulation/", views.simulation, name="simulation"),
    path("simulation/start/", views.simulation_start, name="simulation-start"),
    path("settings/presentation/", views.presentation_settings, name="presentation-settings"),
    path("reports/", views.report_history, name="report-history"),
    path("reports/<uuid:run_id>/", views.report_detail, name="report-detail"),
    path(
        "reports/<uuid:run_id>/export/<str:export_format>/",
        views.report_export,
        name="report-export",
    ),
    path("monitoring/", views.monitoring, name="monitoring"),
    path("admin-area/", views.admin_area, name="admin-area"),
    path("admin-area/gui-settings/", views.admin_gui_settings, name="admin-gui-settings"),
    path(
        "admin-area/gui-settings/simulation/",
        views.admin_simulation_availability,
        name="admin-simulation-availability",
    ),
    path("account/", views.account_boundary, name="account-boundary"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
