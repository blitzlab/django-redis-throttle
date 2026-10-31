"""Turn a request into the identity a limit applies to."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable

from django.http import HttpRequest
from django.utils.module_loading import import_string

from .conf import get_setting

KeySpec = str | Callable[[HttpRequest], "str | None"]


def client_ip(request: HttpRequest) -> str:
    """The client address, safe against X-Forwarded-For spoofing.

    Each trusted proxy appends the address it received the request from, so
    with N trusted proxies the real client is the Nth entry from the right.
    Anything further left was supplied by the client and cannot be trusted.
    """
    address = request.META.get("REMOTE_ADDR", "")
    depth = get_setting("TRUSTED_PROXY_DEPTH")
    if depth:
        forwarded = [hop.strip() for hop in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if hop.strip()]
        if len(forwarded) >= depth:
            address = forwarded[-depth]
    return _aggregate_ipv6(address)


def _aggregate_ipv6(address: str) -> str:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 4:
        return str(ip)
    if ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    prefix = get_setting("IPV6_PREFIX_LENGTH")
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def _user(request: HttpRequest) -> str | None:
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return f"user:{user.pk}"
    return None


def _user_or_ip(request: HttpRequest) -> str:
    return _user(request) or f"ip:{client_ip(request)}"


BUILTIN_KEYS: dict[str, Callable[[HttpRequest], str | None]] = {
    "ip": lambda request: f"ip:{client_ip(request)}",
    "user": _user,
    "user_or_ip": _user_or_ip,
}


def resolve_key(spec: KeySpec, request: HttpRequest) -> str | None:
    """Return the identity for `request`, or None to skip limiting.

    `spec` may be "ip", "user", "user_or_ip", "header:<Name>", a dotted path to
    a callable, or a callable taking the request.
    """
    if callable(spec):
        return spec(request)
    if spec in BUILTIN_KEYS:
        return BUILTIN_KEYS[spec](request)
    if spec.startswith("header:"):
        name = spec.split(":", 1)[1]
        value = request.headers.get(name)
        return f"header:{name.lower()}:{value}" if value else None
    return import_string(spec)(request)
