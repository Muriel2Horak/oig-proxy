#!/usr/bin/env python3
import argparse
import json
import re
import socket
import time
from datetime import datetime
from pathlib import Path


FRAME_END = b"</Frame>"


def _tag(xml: str, name: str) -> str | None:
    m = re.search(rf"<{name}>([^<]*)</{name}>", xml)
    return m.group(1) if m else None


def _recv_one_frame(sock: socket.socket, timeout_s: float) -> bytes:
    sock.settimeout(timeout_s)
    out = b""
    started = time.monotonic()
    while True:
        if (time.monotonic() - started) > timeout_s:
            break
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            break
        if not chunk:
            break
        out += chunk
        if FRAME_END in out:
            break
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay exported HA BOX session to direct cloud target"
    )
    parser.add_argument("--session-file", required=True)
    parser.add_argument("--host", default="185.25.185.30")
    parser.add_argument("--port", type=int, default=5710)
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--max-sleep", type=float, default=8.0)
    parser.add_argument("--hold-after", type=float, default=20.0)
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    session_path = Path(args.session_file)
    data = json.loads(session_path.read_text(encoding="utf-8"))
    frames = [f for f in data.get("frames", []) if f.get("direction") == "box_to_proxy"]
    if not frames:
        raise SystemExit("No box_to_proxy frames in session file")

    parsed: list[tuple[int, datetime, str]] = []
    for f in frames:
        ts = datetime.fromisoformat(f["ts"])
        parsed.append((int(f["id"]), ts, f.get("raw") or ""))
    parsed.sort(key=lambda x: x[0])

    print(
        json.dumps(
            {
                "label": args.label,
                "target": f"{args.host}:{args.port}",
                "session_file": str(session_path),
                "source_conn_id": data.get("conn_id"),
                "frames": len(parsed),
                "hold_after_s": args.hold_after,
            },
            ensure_ascii=True,
        )
    )

    sock = socket.create_connection((args.host, args.port), timeout=8.0)
    print(json.dumps({"event": "conn_open"}, ensure_ascii=True))

    prev_t: datetime | None = None
    try:
        for frame_id, ts, raw in parsed:
            if prev_t is not None:
                delay = (ts - prev_t).total_seconds()
                if delay > 0:
                    time.sleep(min(delay, args.max_sleep))
            prev_t = ts

            payload = raw.encode("utf-8", errors="ignore")
            if not payload.endswith(b"\r\n"):
                payload += b"\r\n"

            try:
                sock.sendall(payload)
            except OSError as exc:
                print(
                    json.dumps(
                        {
                            "event": "send_error",
                            "id": frame_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                        ensure_ascii=True,
                    )
                )
                break
            response = _recv_one_frame(sock, timeout_s=args.timeout)
            txt = response.decode("utf-8", errors="replace") if response else ""

            print(
                json.dumps(
                    {
                        "event": "frame",
                        "id": frame_id,
                        "sent_len": len(payload),
                        "sent_result": _tag(raw, "Result"),
                        "sent_reason": _tag(raw, "Reason"),
                        "sent_tbl": _tag(raw, "TblName"),
                        "got_resp": bool(response),
                        "resp_len": len(response),
                        "resp_result": _tag(txt, "Result"),
                        "resp_reason": _tag(txt, "Reason"),
                    },
                    ensure_ascii=True,
                )
            )

        if args.hold_after > 0:
            print(json.dumps({"event": "hold", "seconds": args.hold_after}, ensure_ascii=True))
            time.sleep(args.hold_after)

        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
    finally:
        sock.close()
        print(json.dumps({"event": "conn_close"}, ensure_ascii=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
