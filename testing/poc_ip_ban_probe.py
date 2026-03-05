#!/usr/bin/env python3
import argparse
import json
import re
import socket
import time
from dataclasses import dataclass, asdict


FRAME_END = b"</Frame>"


def _parse_tags(xml: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in ("Result", "Reason", "TblName", "TblItem", "NewValue", "ID_Device"):
        m = re.search(rf"<{tag}>([^<]*)</{tag}>", xml)
        if m:
            out[tag] = m.group(1)
    return out


def _read_until_frame(sock: socket.socket, timeout_s: float) -> bytes:
    sock.settimeout(timeout_s)
    chunks: list[bytes] = []
    started = time.monotonic()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        data = b"".join(chunks)
        if FRAME_END in data:
            break
        if (time.monotonic() - started) > timeout_s:
            break
    return b"".join(chunks)


def _normalize_frame(text: str) -> bytes:
    raw = text.strip()
    if not raw.endswith("\r\n"):
        raw += "\r\n"
    return raw.encode("utf-8", errors="strict")


@dataclass
class ProbeResult:
    attempt: int
    ok_connect: bool
    ok_send: bool
    got_response: bool
    rtt_ms: float | None
    response_len: int
    response_tags: dict[str, str]
    response_head: str
    error: str | None


def run_probe(host: str, port: int, frame: bytes, timeout_s: float, attempt: int) -> ProbeResult:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.sendall(frame)
            response = _read_until_frame(sock, timeout_s=timeout_s)
            rtt_ms = (time.monotonic() - started) * 1000.0
            if not response:
                return ProbeResult(
                    attempt=attempt,
                    ok_connect=True,
                    ok_send=True,
                    got_response=False,
                    rtt_ms=round(rtt_ms, 1),
                    response_len=0,
                    response_tags={},
                    response_head="",
                    error=None,
                )

            text = response.decode("utf-8", errors="replace")
            tags = _parse_tags(text)
            return ProbeResult(
                attempt=attempt,
                ok_connect=True,
                ok_send=True,
                got_response=True,
                rtt_ms=round(rtt_ms, 1),
                response_len=len(response),
                response_tags=tags,
                response_head=text[:300],
                error=None,
            )
    except Exception as exc:
        return ProbeResult(
            attempt=attempt,
            ok_connect=False,
            ok_send=False,
            got_response=False,
            rtt_ms=None,
            response_len=0,
            response_tags={},
            response_head="",
            error=f"{type(exc).__name__}: {exc}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PoC A/B probe for cloud behavior from different public IPs"
    )
    parser.add_argument("--host", default="oigservis.cz")
    parser.add_argument("--port", type=int, default=5710)
    parser.add_argument("--frame-file", required=True, help="Path to XML frame file to send")
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    with open(args.frame_file, "r", encoding="utf-8") as f:
        frame = _normalize_frame(f.read())

    print(
        json.dumps(
            {
                "target": f"{args.host}:{args.port}",
                "attempts": args.attempts,
                "timeout_s": args.timeout,
                "label": args.label,
                "frame_len": len(frame),
            },
            ensure_ascii=True,
        )
    )

    results: list[ProbeResult] = []
    for i in range(1, args.attempts + 1):
        res = run_probe(args.host, args.port, frame, args.timeout, i)
        results.append(res)
        print(json.dumps(asdict(res), ensure_ascii=True))
        time.sleep(0.6)

    ok = sum(1 for r in results if r.got_response)
    print(
        json.dumps(
            {
                "summary": {
                    "responses": ok,
                    "no_response": len(results) - ok,
                    "connect_errors": sum(1 for r in results if not r.ok_connect),
                }
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
