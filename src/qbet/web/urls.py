from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from qbet.web import bonus_offers, views
from qbet.web.engine_runtime import engine_runtime_control, sandbox_execution_control
from qbet.web.execution_approvals import execution_approval_decision, execution_approvals
from qbet.web.polling_settings import polling_settings
from qbet.web.routing_settings import routing_settings

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("metrics/", views.metrics, name="metrics"),
    path("register/", views.register, name="register"),
    path("verification/pending/", views.verification_pending, name="verification-pending"),
    path("verify-email/<str:uidb64>/<str:token>/", views.verify_email, name="verify-email"),
    path("profile/", views.profile, name="profile"),
    path("profile/avatar/", views.account_avatar, name="account-avatar"),
    path("profile/avatar/upload/", views.profile_avatar_update, name="profile-avatar-update"),
    path("profile/avatar/remove/", views.profile_avatar_remove, name="profile-avatar-remove"),
    path("notifications/", views.notification_inbox, name="notification-inbox"),
    path("bonus-offers/", bonus_offers.bonus_offer_list, name="bonus-offer-list"),
    path("bonus-offers/create/", bonus_offers.bonus_offer_create, name="bonus-offer-create"),
    path(
        "notifications/<uuid:task_id>/read/",
        views.notification_inbox_read,
        name="notification-inbox-read",
    ),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("portfolio/", views.portfolio, name="portfolio"),
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
    path("activity/provider/", views.provider_activity, name="provider-activity"),
    path("simulation/start/", views.simulation_start, name="simulation-start"),
    path("simulation/<uuid:run_id>/run/", views.simulation_run, name="simulation-run"),
    path("simulation/<uuid:run_id>/stop/", views.simulation_stop, name="simulation-stop"),
    path("simulation/pipeline-dry-run/", views.pipeline_dry_run, name="pipeline-dry-run"),
    path("settings/presentation/", views.presentation_settings, name="presentation-settings"),
    path(
        "settings/engines/",
        views.user_routing_preferences_update,
        name="user-routing-preferences",
    ),
    path("reports/", views.report_history, name="report-history"),
    path(
        "reports/export/<str:export_format>/",
        views.report_history_export,
        name="report-history-export",
    ),
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
    path(
        "accounts/password/change/",
        auth_views.PasswordChangeView.as_view(
            template_name="registration/password_change_form.html",
            success_url=reverse_lazy("password-change-done"),
        ),
        name="password-change",
    ),
    path(
        "accounts/password/change/done/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="registration/password_change_done.html"
        ),
        name="password-change-done",
    ),
    path(
        "accounts/password/reset/",
        auth_views.PasswordResetView.as_view(
            template_name="registration/password_reset_form.html",
            email_template_name="registration/password_reset_email.txt",
            subject_template_name="registration/password_reset_subject.txt",
            success_url=reverse_lazy("password-reset-done"),
        ),
        name="password-reset",
    ),
    path(
        "accounts/password/reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="registration/password_reset_done.html"
        ),
        name="password-reset-done",
    ),
    path(
        "accounts/password/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            success_url=reverse_lazy("password-reset-complete"),
        ),
        name="password-reset-confirm",
    ),
    path(
        "accounts/password/reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html"
        ),
        name="password-reset-complete",
    ),
    path("admin/", admin.site.urls),
]
