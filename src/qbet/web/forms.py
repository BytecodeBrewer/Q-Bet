from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class RegistrationForm(UserCreationForm):
    """Registration form that keeps account-existence errors non-enumerable."""

    class Meta:
        model = User
        fields = ("username", "password1", "password2")