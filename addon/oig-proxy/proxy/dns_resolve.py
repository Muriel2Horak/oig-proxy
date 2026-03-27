#!/usr/bin/env python3
"""
DNS resolution using an explicit DNS server.

Bypasses /etc/resolv.conf and the system resolver entirely so that
the proxy process always reaches the real cloud IP — even when the
system resolver forwards to a local DNS (e.g. dnsmasq on HA) that
overrides oigservis.cz → HA IP for LAN clients.
"""

from __future__ import annotations

import logging
import random
import socket
import struct

logger = logging.getLogger(__name__)


def _skip_dns_name(data: bytes, offset: int) -> int:
    """Skip over a DNS name field, handling compression pointers."""
    while offset < len(data):
        length = data[offset]
        if length == 0:
            return offset + 1
        if (length & 0xC0) == 0xC0:
            # Compression pointer — always 2 bytes, terminates the name
            return offset + 2
        offset += length + 1
    return offset


def resolve_a_record(
    hostname: str,
    dns_server: str = "8.8.8.8",
    timeout: float = 5.0,
) -> str | None:
    """Resolve *hostname* to an IPv4 address using *dns_server* directly.

    Sends a raw UDP DNS A-record query to *dns_server*:53 without going
    through the OS resolver (/etc/resolv.conf).

    If *hostname* is already a dotted-quad IP address it is returned
    unchanged (no query is sent).

    Returns the first A record as a dotted-quad string, or ``None`` if
    resolution fails for any reason.
    """
    try:
        socket.inet_aton(hostname)
        return hostname
    except OSError:
        pass

    query_id = random.randint(0, 65535)

    # DNS header: ID | flags(RD=1) | QDCOUNT=1 | ANCOUNT=0 | NSCOUNT=0 | ARCOUNT=0
    header = struct.pack(">HHHHHH", query_id, 0x0100, 1, 0, 0, 0)

    # Encode question: labels + null terminator + QTYPE=A + QCLASS=IN
    question = b""
    for label in hostname.rstrip(".").split("."):
        encoded = label.encode("ascii")
        question += bytes([len(encoded)]) + encoded
    question += b"\x00"
    question += struct.pack(">HH", 1, 1)  # QTYPE=A(1), QCLASS=IN(1)

    packet = header + question

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(packet, (dns_server, 53))
            response, _ = sock.recvfrom(512)
    except OSError as exc:
        logger.warning("DNS query for %s via %s failed: %s", hostname, dns_server, exc)
        return None

    if len(response) < 12:
        logger.warning("DNS response for %s too short (%d bytes)", hostname, len(response))
        return None

    resp_id, _flags, qdcount, ancount = struct.unpack(">HHHH", response[0:8])
    if resp_id != query_id:
        logger.warning("DNS response ID mismatch for %s", hostname)
        return None
    if ancount == 0:
        logger.warning("DNS response for %s has no answers", hostname)
        return None

    try:
        offset = 12
        for _ in range(qdcount):
            offset = _skip_dns_name(response, offset)
            offset += 4  # QTYPE + QCLASS

        for _ in range(ancount):
            offset = _skip_dns_name(response, offset)
            if offset + 10 > len(response):
                break
            rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", response[offset:offset + 10])
            offset += 10
            if rtype == 1 and rdlen == 4 and offset + 4 <= len(response):
                ip = socket.inet_ntoa(response[offset:offset + 4])
                return ip
            offset += rdlen

    except (IndexError, struct.error) as exc:
        logger.warning("DNS response parse error for %s: %s", hostname, exc)
        return None

    return None
