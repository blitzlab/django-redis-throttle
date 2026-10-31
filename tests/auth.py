"""A fake, header-driven user so tests don't need a database."""

from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import BaseAuthentication


class FakeUser:
    is_authenticated = True

    def __init__(self, pk):
        self.pk = pk


def user_from(request):
    user_id = request.headers.get("X-Test-User")
    return FakeUser(user_id) if user_id else AnonymousUser()


class HeaderUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.user = user_from(request)
        return self.get_response(request)


class HeaderAuthentication(BaseAuthentication):
    def authenticate(self, request):
        user = user_from(request._request)
        return (user, None) if user.is_authenticated else None
