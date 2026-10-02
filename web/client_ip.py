"""
web/client_ip.py
─────────────────
The caller's IP address — for the per-IP sign-in lockout, the sign-up cap and the audit trail (SF-022).

`X-Forwarded-For` is just a request header: anyone can send `X-Forwarded-For: 1.2.3.4`. Trusting its
first hop let one client pose as a new address on every request, so the per-IP limits never tripped
and the audit trail recorded whatever the caller typed.

It is honoured only when the connection itself comes from a proxy listed in `ZF_TRUSTED_PROXIES`
(IPs or CIDRs, comma-separated — the load balancer's subnet in Phase 12), and then the client is the
right-most hop that is not one of those proxies: hops to its left were written by the client and
prove nothing. By default nothing is trusted and the address is the socket peer — which uvicorn has
already resolved for a reverse proxy on the same host (its own `forwarded_allow_ips`, 127.0.0.1).
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from starlette.requests import Request

from zenflow.settings import get_settings

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=8)
def parse_networks(spec: str) -> tuple[Network, ...]:
    """`"10.0.0.0/8, 192.168.1.4"` → networks. Raises ValueError on a malformed entry."""
    return tuple(
        ipaddress.ip_network(part.strip(), strict=False) for part in spec.split(",") if part.strip()
    )


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _trusted(host: str, networks: tuple[Network, ...]) -> bool:
    addr = _address(host)
    return addr is not None and any(addr in net for net in networks)


def client_ip(request: Request) -> str | None:
    """The real client's address; the socket peer unless that peer is a trusted proxy."""
    peer = request.client.host if request.client else None
    networks = parse_networks(get_settings().flags.trusted_proxies)
    if not networks or not peer or not _trusted(peer, networks):
        return peer
    hops = [h.strip() for h in (request.headers.get("x-forwarded-for") or "").split(",")]
    for hop in reversed([h for h in hops if h]):
        if not _trusted(hop, networks):
            # the hop our own proxy appended; anything malformed is not an address — use the peer
            return hop if _address(hop) is not None else peer
    return peer
