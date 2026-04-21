"""Testy pro proxy/dns_resolve.py — resolve_a_record()."""
from __future__ import annotations

import socket
import struct
from unittest.mock import MagicMock, patch

import pytest

from proxy.dns_resolve import resolve_a_record, _skip_dns_name


def _build_dns_response(query_id: int, hostname: str, ip: str) -> bytes:
    """Build a minimal DNS A-record response for testing."""
    # Header
    header = struct.pack(">HHHHHH", query_id, 0x8180, 1, 1, 0, 0)

    # Question section
    question = b""
    for label in hostname.rstrip(".").split("."):
        encoded = label.encode("ascii")
        question += bytes([len(encoded)]) + encoded
    question += b"\x00"
    question += struct.pack(">HH", 1, 1)

    # Answer section (compression pointer to question name at offset 12)
    answer = struct.pack(">H", 0xC00C)  # name pointer → offset 12
    answer += struct.pack(">HHIH", 1, 1, 300, 4)  # TYPE=A, CLASS=IN, TTL, RDLEN=4
    answer += socket.inet_aton(ip)

    return header + question + answer


class TestSkipDnsName:
    def test_simple_name(self):
        data = b"\x03foo\x03bar\x00extra"
        result = _skip_dns_name(data, 0)
        assert result == 9

    def test_compression_pointer(self):
        data = b"\xc0\x0c"
        result = _skip_dns_name(data, 0)
        assert result == 2

    def test_empty_name(self):
        data = b"\x00"
        result = _skip_dns_name(data, 0)
        assert result == 1


class TestResolveARecord:
    def test_ip_passthrough(self):
        assert resolve_a_record("192.168.1.1", "8.8.8.8") == "192.168.1.1"

    def test_ip_passthrough_no_socket(self):
        with patch("socket.socket") as mock_sock_cls:
            result = resolve_a_record("10.0.0.1")
        mock_sock_cls.assert_not_called()
        assert result == "10.0.0.1"

    def test_successful_resolution(self):
        hostname = "oigservis.cz"
        expected_ip = "178.238.45.2"

        mock_sock = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)

        captured_query_id: list[int] = []

        def fake_sendto(data: bytes, addr: tuple) -> None:
            qid = struct.unpack(">H", data[0:2])[0]
            captured_query_id.append(qid)
            response = _build_dns_response(qid, hostname, expected_ip)
            mock_sock.recvfrom.return_value = (response, (addr[0], 53))

        mock_sock.sendto.side_effect = fake_sendto

        with patch("socket.socket", return_value=mock_sock):
            result = resolve_a_record(hostname, "8.8.8.8")

        assert result == expected_ip

    def test_socket_error_returns_none(self):
        mock_sock = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.sendto.side_effect = OSError("unreachable")

        with patch("socket.socket", return_value=mock_sock):
            result = resolve_a_record("oigservis.cz", "8.8.8.8")

        assert result is None

    def test_short_response_returns_none(self):
        mock_sock = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.sendto = MagicMock()
        mock_sock.recvfrom.return_value = (b"\x00\x01", ("8.8.8.8", 53))

        with patch("socket.socket", return_value=mock_sock):
            result = resolve_a_record("oigservis.cz", "8.8.8.8")

        assert result is None

    def test_zero_ancount_returns_none(self):
        hostname = "oigservis.cz"

        mock_sock = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)

        def fake_sendto(data: bytes, addr: tuple) -> None:
            qid = struct.unpack(">H", data[0:2])[0]
            question = b""
            for label in hostname.split("."):
                enc = label.encode("ascii")
                question += bytes([len(enc)]) + enc
            question += b"\x00" + struct.pack(">HH", 1, 1)
            header = struct.pack(">HHHHHH", qid, 0x8180, 1, 0, 0, 0)
            mock_sock.recvfrom.return_value = (header + question, (addr[0], 53))

        mock_sock.sendto.side_effect = fake_sendto

        with patch("socket.socket", return_value=mock_sock):
            result = resolve_a_record(hostname, "8.8.8.8")

        assert result is None

    def test_id_mismatch_returns_none(self):
        mock_sock = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.sendto = MagicMock()
        wrong_id_response = _build_dns_response(0xDEAD, "oigservis.cz", "1.2.3.4")
        mock_sock.recvfrom.return_value = (wrong_id_response, ("8.8.8.8", 53))

        with patch("proxy.dns_resolve.os.urandom", return_value=b"\x12\x34"):
            with patch("socket.socket", return_value=mock_sock):
                result = resolve_a_record("oigservis.cz", "8.8.8.8")

        assert result is None
