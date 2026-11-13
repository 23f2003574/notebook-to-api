"""Shared outbound-destination policy for server-side fetches.

The dashboard's notebook URL import and the generated apps' webhook delivery both
fetch caller-supplied URLs, so both must refuse the same destinations. The generated
app is standalone and embeds its own copy of this policy (``_webhook_ip_unsafe`` in
api_generator.py); tests/test_destination_guard_parity.py keeps the two in lockstep.
"""
import ipaddress

# Cloud metadata endpoints that look globally routable.
BLOCKED_IPS = frozenset({
    ipaddress.ip_address("168.63.129.16"),    # Azure platform / wireserver
    ipaddress.ip_address("169.254.169.254"),  # AWS / GCP / Azure instance metadata
    ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud metadata
    ipaddress.ip_address("fd00:ec2::254"),    # AWS IPv6 metadata
})


def ip_is_unsafe(address):
    """True when ``address`` must never be fetched: anything not globally routable
    (private, loopback, link-local, CGNAT, reserved, multicast, unspecified) or a
    known metadata address. IPv4-mapped IPv6 is judged by its IPv4 address."""
    ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    return (
        ip in BLOCKED_IPS or not ip.is_global
        or ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )
