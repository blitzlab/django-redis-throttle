"""Test views and URL patterns."""

from django.http import HttpResponse, JsonResponse
from django.urls import path
from django.utils.decorators import method_decorator
from django.views import View
from rest_framework.response import Response
from rest_framework.views import APIView

from redis_throttle import ratelimit
from redis_throttle.drf import AnonRedisThrottle, ScopedRedisThrottle, UserRedisThrottle


@ratelimit("2/m")
def limited(request):
    return HttpResponse("ok")


@ratelimit("2/m", methods=["POST"])
def post_only(request):
    return HttpResponse("ok")


@ratelimit("3/m", key="user")
def per_user(request):
    return HttpResponse("ok")


def plan_rate(group, request):
    # Premium users are unlimited; everyone else gets 1/m.
    return None if request.headers.get("X-Plan") == "premium" else "1/m"


@ratelimit(plan_rate)
def per_plan(request):
    return HttpResponse("ok")


@ratelimit("2/m", block=False)
def soft(request):
    return JsonResponse({"allowed": request.ratelimit.allowed})


@ratelimit("100/h", group="stacked")
@ratelimit("2/m", group="stacked-burst")
def stacked(request):
    return HttpResponse("ok")


@ratelimit("2/m", algorithm="token_bucket")
async def async_view(request):
    return HttpResponse("ok")


@method_decorator(ratelimit("2/m", key="header:X-Api-Key"), name="get")
class KeyedView(View):
    def get(self, request):
        return HttpResponse("ok")


def login(request):
    return HttpResponse("ok")


class AnonView(APIView):
    throttle_classes = [AnonRedisThrottle]

    def get(self, request):
        return Response({"ok": True})


class UserView(APIView):
    throttle_classes = [UserRedisThrottle]

    def get(self, request):
        return Response({"ok": True})


class UploadView(APIView):
    throttle_classes = [ScopedRedisThrottle]
    throttle_scope = "uploads"

    def get(self, request):
        return Response({"ok": True})


urlpatterns = [
    path("limited/", limited),
    path("post-only/", post_only),
    path("per-user/", per_user),
    path("per-plan/", per_plan),
    path("soft/", soft),
    path("stacked/", stacked),
    path("async/", async_view),
    path("keyed/", KeyedView.as_view()),
    path("login/", login),
    path("api/anon/", AnonView.as_view()),
    path("api/user/", UserView.as_view()),
    path("api/uploads/", UploadView.as_view()),
]
