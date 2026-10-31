"""Decorator, middleware, DRF throttles and system checks, end to end."""

import pytest
from django.core.checks import run_checks
from django.test import AsyncClient

pytestmark = pytest.mark.usefixtures("redis_client")


def statuses(client, path, n, method="get", **headers):
    return [getattr(client, method)(path, headers=headers).status_code for _ in range(n)]


class TestDecorator:
    def test_blocks_with_429_and_standard_headers(self, client):
        assert statuses(client, "/limited/", 2) == [200, 200]
        response = client.get("/limited/")
        assert response.status_code == 429
        assert response.json()["detail"] == "Too many requests."
        assert response["RateLimit-Limit"] == "2"
        assert response["RateLimit-Remaining"] == "0"
        assert int(response["Retry-After"]) >= 1

    def test_allowed_responses_carry_headers(self, client):
        response = client.get("/limited/")
        assert response["RateLimit-Limit"] == "2"
        assert response["RateLimit-Remaining"] == "1"
        assert "Retry-After" not in response

    def test_methods_filter(self, client):
        assert statuses(client, "/post-only/", 5) == [200] * 5
        assert statuses(client, "/post-only/", 3, method="post") == [200, 200, 429]

    def test_per_user_limits_are_separate(self, client):
        assert statuses(client, "/per-user/", 4, X_Test_User="1") == [200, 200, 200, 429]
        assert statuses(client, "/per-user/", 1, X_Test_User="2") == [200]
        # Anonymous requests have no "user" identity, so this rule skips them.
        assert statuses(client, "/per-user/", 5) == [200] * 5

    def test_dynamic_rate_per_plan(self, client):
        assert statuses(client, "/per-plan/", 2) == [200, 429]
        assert statuses(client, "/per-plan/", 5, X_Plan="premium") == [200] * 5

    def test_block_false_lets_the_view_decide(self, client):
        results = [client.get("/soft/").json()["allowed"] for _ in range(3)]
        assert results == [True, True, False]

    def test_stacked_limits_report_the_tightest(self, client):
        first = client.get("/stacked/")
        assert first["RateLimit-Limit"] == "2"  # the 2/m burst limit, not 100/h
        assert statuses(client, "/stacked/", 2) == [200, 429]

    def test_class_based_view_with_header_key(self, client):
        assert statuses(client, "/keyed/", 3, X_Api_Key="k1") == [200, 200, 429]
        assert statuses(client, "/keyed/", 1, X_Api_Key="k2") == [200]

    def test_custom_ratelimited_view(self, client, settings):
        settings.REDIS_THROTTLE = {**settings.REDIS_THROTTLE, "RATELIMITED_VIEW": "tests.test_django.custom_429"}
        statuses(client, "/limited/", 2)
        response = client.get("/limited/")
        assert response.status_code == 429
        assert response.content == b"slow down"
        assert "Retry-After" in response


def custom_429(request, decision):
    from django.http import HttpResponse

    return HttpResponse("slow down", status=429)


def test_async_view_is_limited(redis_client):
    import asyncio

    async def run():
        client = AsyncClient()
        return [(await client.get("/async/")).status_code for _ in range(3)]

    assert asyncio.run(run()) == [200, 200, 429]


class TestMiddleware:
    def test_rules_apply_by_path_and_method(self, client):
        assert statuses(client, "/login/", 4, method="post") == [200, 200, 200, 429]
        assert statuses(client, "/login/", 2) == [200, 200]  # GET is not covered by the rule

    def test_rule_headers_are_added(self, client):
        response = client.post("/login/")
        assert response["RateLimit-Limit"] == "3"


class TestDRF:
    def test_anon_throttle(self, client):
        assert statuses(client, "/api/anon/", 4) == [200, 200, 200, 429]

    def test_anon_throttle_ignores_authenticated_users(self, client):
        assert statuses(client, "/api/anon/", 5, X_Test_User="9") == [200] * 5

    def test_user_throttle_returns_retry_after(self, client):
        assert statuses(client, "/api/user/", 5, X_Test_User="1") == [200] * 5
        response = client.get("/api/user/", headers={"X-Test-User": "1"})
        assert response.status_code == 429
        assert int(response["Retry-After"]) >= 1

    def test_scoped_throttle(self, client):
        assert statuses(client, "/api/uploads/", 3) == [200, 200, 429]

    def test_middleware_adds_headers_for_drf_decisions(self, client):
        response = client.get("/api/uploads/")
        assert response["RateLimit-Limit"] == "2"
        assert response["RateLimit-Remaining"] == "1"


class TestSystemChecks:
    def test_valid_configuration_passes(self):
        assert [e for e in run_checks() if e.id.startswith("redis_throttle")] == []

    def test_invalid_configuration_is_reported(self, settings):
        settings.REDIS_THROTTLE = {
            "DEFAULT_ALGORITHM": "leaky",
            "TRUSTED_PROXY_DEPTH": -1,
            "RULES": [
                {"path": "^/a/"},
                {"path": "^/b/", "rate": "lots"},
                {"path": "^/c/", "rate": "1/m", "algorithm": "x"},
            ],
        }
        ids = sorted(e.id for e in run_checks() if e.id.startswith("redis_throttle"))
        assert ids == [
            "redis_throttle.E001",
            "redis_throttle.E002",
            "redis_throttle.E003",
            "redis_throttle.E004",
            "redis_throttle.E005",
        ]
