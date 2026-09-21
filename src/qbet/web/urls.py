from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from qbet.web import views
from qbet.web.engine_runtime import engine_runtime_control, sandbox_execution_control
from qbet.web.execution_approvals import execution_approval_decision, execution_approvals
from qbet.web.polling_settings import polling_settings
from qbet.web.routing_settings import routing_settings

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("metrics/", views.metrics, name="metrics"),
    path("register/", views.register, name="register"),
    path("profile/", views.profile, name="profile"),
    path("notifications/", views.notification_inbox, name="notification-inbox"),
    path(
        "notifications/<uuid:task_id>/read/",
        views.notification_inbox_read,
        name="notification-inbox-read",
    ),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/layout/", views.dashboard_layout_update, name="dashboard-layout"),
    path("execution/approvals/", execution_approvals, name="execution-approvals"),
    path(
        "execution/approvals/<uuid:execution_id>/decision/",
        execution_approval_decision,
        name="execution-approval-decision",
    ),
    path(
        "engines/<str:engine_id>/<str:mode>/<str:action>/",
        engine_runtime_control,
        name="engine-runtime-control",
    ),
    path(
        "admin-area/sandbox-execution/<str:engine_id>/<str:action>/",
        sandbox_execution_control,
        name="sandbox-execution-control",
    ),
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
    path(
        "monitoring/export/<str:export_format>/", views.monitoring_export, name="monitoring-export"
    ),
    path("admin-area/", views.admin_area, name="admin-area"),
    path("admin-area/gui-settings/", routing_settings, name="admin-gui-settings"),
    path("admin-area/polling/", polling_settings, name="admin-polling-settings"),
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
