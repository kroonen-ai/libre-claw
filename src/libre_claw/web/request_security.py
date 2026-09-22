# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

"""Keep the local control API out of other websites' security contexts.

The API intentionally grants the owner's shell capabilities to native clients
and its own dashboard. CORS alone does not protect it: a browser can send a
cross-origin form or text/plain request without reading the response, and DNS
rebinding can make an attacker's hostname point to the local listener.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from aiohttp import web


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def control_api_middleware(bind_host: str) -> Callable:
    """Protect browser requests while preserving explicitly configured LAN use.

    Host names are never learned from the request or forwarded headers. A
    wildcard listener accepts its actual socket address, not arbitrary DNS
    names that happen to resolve to it. Originless CLI clients remain usable.
    """
    configured_host = _canonical_host(bind_host.strip("[]"))

    @web.middleware
    async def check_request(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        try:
            hosts = request.headers.getall("Host", [])
            if len(hosts) != 1:
                raise ValueError("A single trusted Host header is required.")
            target = _origin(f"{request.scheme}://{hosts[0]}")
            allowed_hosts = set(_LOOPBACK_HOSTS)
            if configured_host not in _WILDCARD_HOSTS:
                allowed_hosts.add(configured_host)
            if request.transport is not None:
                local_address = request.transport.get_extra_info("sockname")
                if isinstance(local_address, tuple) and local_address:
                    local_host = _canonical_host(str(local_address[0]))
                    allowed_hosts.add(local_host)
                    # Dual-stack IPv6 sockets may report an IPv4 LAN connection
                    # with a mapped address. Allow its literal IPv4 Host too,
                    # without equating different browser origins below.
                    try:
                        local_ip = ipaddress.IPv6Address(local_host)
                    except ipaddress.AddressValueError:
                        pass
                    else:
                        if local_ip.ipv4_mapped is not None:
                            allowed_hosts.add(str(local_ip.ipv4_mapped))
            if target[1] not in allowed_hosts:
                raise ValueError("The Host header does not match this daemon.")

            origins = request.headers.getall("Origin", [])
            if origins and (len(origins) != 1 or _origin(origins[0]) != target):
                raise ValueError("Cross-origin requests to the daemon are not allowed.")

            fetch_sites = request.headers.getall("Sec-Fetch-Site", [])
            if fetch_sites and (len(fetch_sites) != 1 or fetch_sites[0] not in {"same-origin", "none"}):
                # An ordinary link to the dashboard is safe. Foreign websites
                # still cannot load it in a frame, fetch API data, or send forms.
                navigation = (
                    request.method in {"GET", "HEAD"}
                    and request.path in {"/", "/dashboard"}
                    and request.headers.get("Sec-Fetch-Mode") == "navigate"
                    and request.headers.get("Sec-Fetch-Dest") == "document"
                )
                if not navigation:
                    raise ValueError("Cross-site requests to the daemon are not allowed.")
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=403)

        if request.method not in _READ_METHODS and request.can_read_body and request.content_type != "application/json":
            return web.json_response({"error": "Request bodies must use application/json."}, status=415)

        response = await handler(request)
        if request.path in {"/", "/dashboard"}:
            # Keep shell approval controls out of a foreign page's iframe.
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            response.headers["X-Frame-Options"] = "DENY"
        return response

    return check_request


def _canonical_host(host: str) -> str:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host.lower()


def _origin(value: str) -> tuple[str, str, int]:
    # urlsplit tolerates whitespace and userinfo that are invalid in a browser
    # Origin or HTTP Host. Reject them before comparing security boundaries.
    if any(character.isspace() or character in "\\,@" for character in value):
        raise ValueError("Invalid request origin.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Invalid request origin.")
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, _canonical_host(parsed.hostname), port
