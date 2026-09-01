from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from qbet.web import views

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("monitoring/", views.monitoring, name="monitoring"),
    path("account/", views.account_boundary, name="account-boundary"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]