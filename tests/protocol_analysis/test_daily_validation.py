import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "protocol_analysis"))

from validate_daily_collection import (
    DEFAULT_MAX_NULL_RATE,
    DEFAULT_MIN_FRAME_COUNT,
    DEFAULT_NULL_RATE_COLUMNS,
    DEFAULT_REQUIRED_SIGNALS,
    check_frame_count,
    check_null_rates,
    check_required_signals,
    check_schema,
    main,
    open_db,
    run_validation,
)


def _make_db(rows: list[tuple]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE frames ("
        "id INTEGER PRIMARY KEY, "
        "ts TEXT, "
        "device_id TEXT, "
        "table_name TEXT, "
        "direction TEXT, "
        "raw TEXT"
        ")"
    )
    conn.executemany(
        "INSERT INTO frames (ts, device_id, table_name, direction) VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def _healthy_rows(n: int = 1500) -> list[tuple]:
    rows = []
    signals = ["IsNewSet", "IsNewWeather", "IsNewFW", "END", "ACK"]
    directions = ["box_to_proxy", "cloud_to_proxy", "proxy_to_box"]
    for i in range(n):
        rows.append((
            f"2026-01-01T00:{i//60:02d}:{i%60:02d}Z",
            "device_1",
            signals[i % len(signals)],
            directions[i % len(directions)],
        ))
    return rows


class TestOpenDb:
    def test_opens_existing_db(self, tmp_path):
        db = tmp_path / "test.db"
        sqlite3.connect(str(db)).close()
        conn = open_db(str(db))
        assert conn is not None
        conn.close()

    def test_returns_none_for_missing_db(self):
        conn = open_db("/nonexistent/path/totally_missing.db")
        assert conn is None


class TestCheckSchema:
    def test_pass_when_all_columns_present(self, tmp_path):
        db_path = _make_db([])
        conn = sqlite3.connect(str(db_path))
        result = check_schema(conn)
        conn.close()
        assert result["status"] == "PASS"
        assert result["details"]["missing_columns"] == []

    def test_fail_when_table_missing(self, tmp_path):
        db = tmp_path / "empty.db"
        sqlite3.connect(str(db)).close()
        conn = sqlite3.connect(str(db))
        result = check_schema(conn)
        conn.close()
        assert result["status"] == "FAIL"
        assert "does not exist" in result["message"]

    def test_fail_when_columns_missing(self, tmp_path):
        db = tmp_path / "partial.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE frames (id INTEGER, ts TEXT)")
        conn.commit()
        result = check_schema(conn)
        conn.close()
        assert result["status"] == "FAIL"
        assert len(result["details"]["missing_columns"]) > 0


class TestCheckFrameCount:
    def test_pass_when_above_threshold(self):
        db_path = _make_db(_healthy_rows(1500))
        conn = sqlite3.connect(str(db_path))
        result = check_frame_count(conn, 1000)
        conn.close()
        assert result["status"] == "PASS"
        assert result["details"]["total_frames"] == 1500

    def test_fail_when_below_threshold(self):
        db_path = _make_db(_healthy_rows(500))
        conn = sqlite3.connect(str(db_path))
        result = check_frame_count(conn, 1000)
        conn.close()
        assert result["status"] == "FAIL"
        assert "below minimum" in result["message"]

    def test_pass_when_exactly_at_threshold(self):
        db_path = _make_db(_healthy_rows(1000))
        conn = sqlite3.connect(str(db_path))
        result = check_frame_count(conn, 1000)
        conn.close()
        assert result["status"] == "PASS"

    def test_fail_empty_table(self):
        db_path = _make_db([])
        conn = sqlite3.connect(str(db_path))
        result = check_frame_count(conn, 1000)
        conn.close()
        assert result["status"] == "FAIL"


class TestCheckRequiredSignals:
    def test_pass_when_all_present(self):
        db_path = _make_db(_healthy_rows(1500))
        conn = sqlite3.connect(str(db_path))
        result = check_required_signals(conn, list(DEFAULT_REQUIRED_SIGNALS))
        conn.close()
        assert result["status"] == "PASS"
        assert result["details"]["missing_signals"] == []

    def test_fail_when_signal_missing(self):
        rows = [(f"2026-01-01T{i:04d}Z", "dev", "IsNewSet", "box_to_proxy") for i in range(100)]
        db_path = _make_db(rows)
        conn = sqlite3.connect(str(db_path))
        result = check_required_signals(conn, ["IsNewSet", "ACK", "END"])
        conn.close()
        assert result["status"] == "FAIL"
        assert "ACK" in result["details"]["missing_signals"]
        assert "END" in result["details"]["missing_signals"]

    def test_signal_counts_reported(self):
        db_path = _make_db(_healthy_rows(1500))
        conn = sqlite3.connect(str(db_path))
        result = check_required_signals(conn, ["IsNewSet"])
        conn.close()
        assert result["details"]["signal_counts"]["IsNewSet"] > 0

    def test_empty_table_fails_all(self):
        db_path = _make_db([])
        conn = sqlite3.connect(str(db_path))
        result = check_required_signals(conn, list(DEFAULT_REQUIRED_SIGNALS))
        conn.close()
        assert result["status"] == "FAIL"
        assert len(result["details"]["missing_signals"]) == len(DEFAULT_REQUIRED_SIGNALS)


class TestCheckNullRates:
    def test_pass_when_no_nulls(self):
        db_path = _make_db(_healthy_rows(1000))
        conn = sqlite3.connect(str(db_path))
        result = check_null_rates(conn, ["ts", "table_name", "direction"], 0.01)
        conn.close()
        assert result["status"] == "PASS"

    def test_fail_when_null_rate_exceeds_threshold(self, tmp_path):
        db = tmp_path / "nulls.db"
        conn_w = sqlite3.connect(str(db))
        conn_w.execute(
            "CREATE TABLE frames (id INTEGER PRIMARY KEY, ts TEXT, device_id TEXT, "
            "table_name TEXT, direction TEXT)"
        )
        rows = []
        for i in range(100):
            ts = None if i < 5 else f"2026-01-01T{i:04d}Z"
            rows.append((ts, "dev", "ACK", "box_to_proxy"))
        conn_w.executemany(
            "INSERT INTO frames (ts, device_id, table_name, direction) VALUES (?,?,?,?)",
            rows,
        )
        conn_w.commit()
        conn_w.close()

        conn = sqlite3.connect(str(db))
        result = check_null_rates(conn, ["ts"], 0.01)
        conn.close()

        assert result["status"] == "FAIL"
        assert result["details"]["columns"]["ts"]["status"] == "FAIL"

    def test_pass_with_per_column_override(self, tmp_path):
        db = tmp_path / "nulls2.db"
        conn_w = sqlite3.connect(str(db))
        conn_w.execute(
            "CREATE TABLE frames (id INTEGER PRIMARY KEY, ts TEXT, device_id TEXT, "
            "table_name TEXT, direction TEXT)"
        )
        rows = [(None if i < 5 else f"2026-01-01T{i:04d}Z", "dev", "ACK", "box_to_proxy") for i in range(100)]
        conn_w.executemany(
            "INSERT INTO frames (ts, device_id, table_name, direction) VALUES (?,?,?,?)",
            rows,
        )
        conn_w.commit()
        conn_w.close()

        conn = sqlite3.connect(str(db))
        result = check_null_rates(conn, ["ts"], 0.01, per_column_max={"ts": 0.10})
        conn.close()

        assert result["status"] == "PASS"

    def test_skip_missing_column(self):
        db_path = _make_db(_healthy_rows(100))
        conn = sqlite3.connect(str(db_path))
        result = check_null_rates(conn, ["nonexistent_col"], 0.01)
        conn.close()
        assert result["details"]["columns"]["nonexistent_col"]["status"] == "SKIP"

    def test_fail_on_empty_table(self):
        db_path = _make_db([])
        conn = sqlite3.connect(str(db_path))
        result = check_null_rates(conn, ["ts"], 0.01)
        conn.close()
        assert result["status"] == "FAIL"
        assert "No frames" in result["message"]


class TestRunValidation:
    def test_all_pass_on_healthy_db(self):
        db_path = _make_db(_healthy_rows(2000))
        report = run_validation(str(db_path))
        assert report["overall"] == "PASS"
        assert report["error"] is None
        assert report["summary"]["failed"] == 0
        assert report["summary"]["passed"] == 4

    def test_fail_on_missing_db(self):
        report = run_validation("/no/such/database.db")
        assert report["overall"] == "FAIL"
        assert report["error"] is not None

    def test_fail_propagates_on_low_frame_count(self):
        db_path = _make_db(_healthy_rows(10))
        report = run_validation(str(db_path), min_frame_count=1000)
        assert report["overall"] == "FAIL"
        frame_check = next(c for c in report["checks"] if c["check"] == "min_frame_count")
        assert frame_check["status"] == "FAIL"

    def test_fail_propagates_on_missing_signal(self):
        rows = [("2026-01-01T0000Z", "dev", "ACK", "box_to_proxy")] * 2000
        db_path = _make_db(rows)
        report = run_validation(
            str(db_path),
            min_frame_count=100,
            required_signals=["ACK", "IsNewSet"],
        )
        assert report["overall"] == "FAIL"
        sig_check = next(c for c in report["checks"] if c["check"] == "required_signal_classes")
        assert "IsNewSet" in sig_check["details"]["missing_signals"]

    def test_thresholds_recorded_in_report(self):
        db_path = _make_db(_healthy_rows(2000))
        report = run_validation(
            str(db_path),
            min_frame_count=500,
            required_signals=["ACK"],
            max_null_rate=0.05,
        )
        assert report["thresholds"]["min_frame_count"] == 500
        assert report["thresholds"]["max_null_rate"] == 0.05
        assert report["thresholds"]["required_signals"] == ["ACK"]

    def test_report_has_timestamp(self):
        db_path = _make_db(_healthy_rows(2000))
        report = run_validation(str(db_path))
        assert "validation_timestamp" in report
        assert report["validation_timestamp"].endswith("+00:00")

    def test_no_silent_downgrade_all_fail_means_overall_fail(self, tmp_path):
        db = tmp_path / "bad.db"
        sqlite3.connect(str(db)).close()
        report = run_validation(str(db))
        assert report["overall"] == "FAIL"
        for check in report["checks"]:
            assert check["status"] != "PASS" or report["overall"] == "PASS"

    def test_per_column_overrides_applied(self, tmp_path):
        db = tmp_path / "overrides.db"
        conn_w = sqlite3.connect(str(db))
        conn_w.execute(
            "CREATE TABLE frames (id INTEGER PRIMARY KEY, ts TEXT, device_id TEXT, "
            "table_name TEXT, direction TEXT)"
        )
        signals = DEFAULT_REQUIRED_SIGNALS
        rows = []
        for i in range(2000):
            ts = None if i < 30 else f"2026-01-01T{i%3600:04d}Z"
            rows.append((ts, "dev", signals[i % len(signals)], "box_to_proxy"))
        conn_w.executemany(
            "INSERT INTO frames (ts, device_id, table_name, direction) VALUES (?,?,?,?)",
            rows,
        )
        conn_w.commit()
        conn_w.close()

        report_tight = run_validation(str(db), max_null_rate=0.01, per_column_max=None)
        assert report_tight["overall"] == "FAIL"

        report_relaxed = run_validation(
            str(db), max_null_rate=0.01, per_column_max={"ts": 0.05}
        )
        assert report_relaxed["overall"] == "PASS"


class TestMain:
    def test_exit_0_on_pass(self):
        db_path = _make_db(_healthy_rows(2000))
        code = main(["--db", str(db_path), "--quiet"])
        assert code == 0

    def test_exit_1_on_fail_low_frames(self):
        db_path = _make_db(_healthy_rows(10))
        code = main(["--db", str(db_path), "--quiet"])
        assert code == 1

    def test_exit_2_on_missing_db(self):
        code = main(["--db", "/totally/nonexistent.db", "--quiet"])
        assert code == 2

    def test_json_output_written(self, tmp_path):
        db_path = _make_db(_healthy_rows(2000))
        out = tmp_path / "result.json"
        code = main(["--db", str(db_path), "--out", str(out), "--quiet"])
        assert code == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["overall"] == "PASS"
        assert len(data["checks"]) == 4

    def test_configurable_min_frame_count(self):
        db_path = _make_db(_healthy_rows(100))
        code_fail = main(["--db", str(db_path), "--quiet", "--min-frame-count", "1000"])
        code_pass = main(["--db", str(db_path), "--quiet", "--min-frame-count", "50"])
        assert code_fail == 1
        assert code_pass == 0

    def test_configurable_required_signals(self):
        rows = [("2026-01-01T0000Z", "dev", "ACK", "box_to_proxy")] * 2000
        db_path = _make_db(rows)
        code_fail = main(["--db", str(db_path), "--quiet", "--required-signals", "ACK", "IsNewSet"])
        code_pass = main(["--db", str(db_path), "--quiet", "--required-signals", "ACK"])
        assert code_fail == 1
        assert code_pass == 0

    def test_configurable_max_null_rate(self, tmp_path):
        db = tmp_path / "nr.db"
        conn_w = sqlite3.connect(str(db))
        conn_w.execute(
            "CREATE TABLE frames (id INTEGER PRIMARY KEY, ts TEXT, device_id TEXT, "
            "table_name TEXT, direction TEXT)"
        )
        signals = DEFAULT_REQUIRED_SIGNALS
        rows = []
        for i in range(2000):
            ts = None if i < 30 else f"2026-01-01T{i%3600:04d}Z"
            rows.append((ts, "dev", signals[i % len(signals)], "box_to_proxy"))
        conn_w.executemany(
            "INSERT INTO frames (ts, device_id, table_name, direction) VALUES (?,?,?,?)",
            rows,
        )
        conn_w.commit()
        conn_w.close()

        code_fail = main(["--db", str(db), "--quiet", "--max-null-rate", "0.001"])
        code_pass = main(["--db", str(db), "--quiet", "--max-null-rate", "0.05"])
        assert code_fail == 1
        assert code_pass == 0

    def test_per_column_null_rate_override(self, tmp_path):
        db = tmp_path / "pcnr.db"
        conn_w = sqlite3.connect(str(db))
        conn_w.execute(
            "CREATE TABLE frames (id INTEGER PRIMARY KEY, ts TEXT, device_id TEXT, "
            "table_name TEXT, direction TEXT)"
        )
        signals = DEFAULT_REQUIRED_SIGNALS
        rows = []
        for i in range(2000):
            ts = None if i < 30 else f"2026-01-01T{i%3600:04d}Z"
            rows.append((ts, "dev", signals[i % len(signals)], "box_to_proxy"))
        conn_w.executemany(
            "INSERT INTO frames (ts, device_id, table_name, direction) VALUES (?,?,?,?)",
            rows,
        )
        conn_w.commit()
        conn_w.close()

        code_fail = main(["--db", str(db), "--quiet", "--max-null-rate", "0.001"])
        code_pass = main([
            "--db", str(db), "--quiet",
            "--max-null-rate", "0.001",
            "--max-null-rate-ts", "0.05",
        ])
        assert code_fail == 1
        assert code_pass == 0
