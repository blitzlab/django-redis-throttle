import pytest
from django.test import RequestFactory

from redis_throttle.keys import client_ip, resolve_key
from tests.auth import FakeUser

rf = RequestFactory()


def request_with(remote="203.0.113.7", forwarded=None, **headers):
    meta = {"REMOTE_ADDR": remote}
    if forwarded:
        meta["HTTP_X_FORWARDED_FOR"] = forwarded
    return rf.get("/", headers=headers, **meta)


class TestClientIp:
    def test_uses_remote_addr_by_default(self):
        assert client_ip(request_with(forwarded="1.1.1.1")) == "203.0.113.7"

    def test_ignores_spoofed_entries_left_of_trusted_proxies(self, settings):
        settings.REDIS_THROTTLE = {**settings.REDIS_THROTTLE, "TRUSTED_PROXY_DEPTH": 2}
        # client sent "6.6.6.6"; edge proxy appended the real client; inner proxy appended the edge.
        request = request_with(remote="10.0.0.2", forwarded="6.6.6.6, 198.51.100.4, 10.0.0.1")
        assert client_ip(request) == "198.51.100.4"

    def test_falls_back_when_header_is_shorter_than_depth(self, settings):
        settings.REDIS_THROTTLE = {**settings.REDIS_THROTTLE, "TRUSTED_PROXY_DEPTH": 3}
        assert client_ip(request_with(forwarded="198.51.100.4")) == "203.0.113.7"

    def test_ipv6_clients_are_grouped_by_prefix(self):
        a = client_ip(request_with(remote="2001:db8:abcd:12::1"))
        b = client_ip(request_with(remote="2001:db8:abcd:12:ffff::9"))
        assert a == b == "2001:db8:abcd:12::/64"

    def test_ipv4_mapped_addresses_are_unwrapped(self):
        assert client_ip(request_with(remote="::ffff:192.0.2.10")) == "192.0.2.10"


class TestResolveKey:
    def test_builtin_keys(self):
        request = request_with()
        request.user = FakeUser(42)
        assert resolve_key("ip", request) == "ip:203.0.113.7"
        assert resolve_key("user", request) == "user:42"
        assert resolve_key("user_or_ip", request) == "user:42"

    def test_user_key_skips_anonymous_requests(self):
        from django.contrib.auth.models import AnonymousUser

        request = request_with()
        request.user = AnonymousUser()
        assert resolve_key("user", request) is None
        assert resolve_key("user_or_ip", request) == "ip:203.0.113.7"

    def test_header_key(self):
        assert resolve_key("header:X-Api-Key", request_with(X_Api_Key="abc")) == "header:x-api-key:abc"
        assert resolve_key("header:X-Api-Key", request_with()) is None

    def test_callable_and_dotted_path(self):
        assert resolve_key(lambda r: "tenant:7", request_with()) == "tenant:7"
        assert resolve_key("tests.test_keys.tenant_key", request_with()) == "tenant:acme"


def tenant_key(request):
    return "tenant:acme"


@pytest.mark.parametrize("bad", ["not-an-ip", ""])
def test_unparseable_addresses_pass_through(bad):
    assert client_ip(request_with(remote=bad)) == bad
