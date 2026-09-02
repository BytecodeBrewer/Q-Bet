from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from qbet.web import views

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("register/", views.register, name="register"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("monitoring/", views.monitoring, name="monitoring"),
    path("admin-area/", views.admin_area, name="admin-area"),
    path("account/", views.account_boundary, name="account-boundary"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
