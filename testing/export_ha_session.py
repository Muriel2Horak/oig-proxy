#!/usr/bin/env python3
import argparse
import json
import shlex
import subprocess
from pathlib import Path


def _run_ssh(ssh_host: str, inner_cmd: str) -> str:
    proc = subprocess.run(
        ["ssh", ssh_host, inner_cmd],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _fetch_conn_ids(ssh_host: str, min_frames: int, limit: int) -> list[tuple[int, int, int, int, str, str]]:
    query = (
        "SELECT conn_id, "
        "COUNT(*) AS total, "
        "SUM(CASE WHEN direction='box_to_proxy' THEN 1 ELSE 0 END) AS b2p, "
        "SUM(CASE WHEN direction='cloud_to_proxy' THEN 1 ELSE 0 END) AS c2p, "
        "MIN(ts) AS min_ts, "
        "MAX(ts) AS max_ts "
        "FROM frames "
        "GROUP BY conn_id "
        f"HAVING b2p >= {int(min_frames)} "
        "ORDER BY conn_id DESC "
        f"LIMIT {int(limit)}"
    )
    py = (
        "import sqlite3, json;"
        "c=sqlite3.connect('/data/payloads.db');"
        "cur=c.cursor();"
        f"rows=cur.execute({query!r}).fetchall();"
        "print(json.dumps(rows));"
        "c.close()"
    )
    cmd = (
        "sudo docker exec addon_d7b5d5b1_oig_proxy "
        f"python3 -c {shlex.quote(py)}"
    )
    out = _run_ssh(ssh_host, cmd)
    return [tuple(x) for x in json.loads(out)]


def _fetch_session_rows(ssh_host: str, conn_id: int) -> list[dict]:
    query = (
        "SELECT id, ts, direction, table_name, raw, length "
        "FROM frames "
        f"WHERE conn_id={int(conn_id)} "
        "ORDER BY id"
    )
    py = (
        "import sqlite3, json;"
        "c=sqlite3.connect('/data/payloads.db');"
        "cur=c.cursor();"
        f"rows=cur.execute({query!r}).fetchall();"
        "out=[];"
        "[out.append({'id':r[0],'ts':r[1],'direction':r[2],'table_name':r[3],'raw':r[4],'length':r[5]}) for r in rows];"
        "print(json.dumps(out));"
        "c.close()"
    )
    cmd = (
        "sudo docker exec addon_d7b5d5b1_oig_proxy "
        f"python3 -c {shlex.quote(py)}"
    )
    out = _run_ssh(ssh_host, cmd)
    return json.loads(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export one full BOX session from HA payloads.db for offline replay"
    )
    parser.add_argument("--ssh-host", default="ha")
    parser.add_argument("--conn-id", type=int, default=None)
    parser.add_argument("--min-box-frames", type=int, default=8)
    parser.add_argument("--scan-limit", type=int, default=30)
    parser.add_argument("--output", default="testing/replay_session_latest.json")
    args = parser.parse_args()

    conn_id = args.conn_id
    if conn_id is None:
        candidates = _fetch_conn_ids(args.ssh_host, args.min_box_frames, args.scan_limit)
        if not candidates:
            raise SystemExit("No session candidates found")
        conn_id = int(candidates[0][0])
        print("Picked conn_id:", conn_id)
        print("Top candidates (conn_id,total,b2p,c2p,min_ts,max_ts):")
        for row in candidates[:5]:
            print("  ", row)

    rows = _fetch_session_rows(args.ssh_host, conn_id)
    if not rows:
        raise SystemExit(f"No frames found for conn_id={conn_id}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "source": "ha:/data/payloads.db",
        "conn_id": conn_id,
        "frame_count": len(rows),
        "exported_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "frames": rows,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    print(f"Exported {len(rows)} frames -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
