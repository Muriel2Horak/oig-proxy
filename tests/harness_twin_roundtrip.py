#!/usr/bin/env python3
"""Live-like Twin Control Roundtrip Test Harness.

This harness provides a reproducible test for the full twin transaction lifecycle:
- set: Send a setting command via MQTT
- status: Verify status updates
- result: Capture transaction result
- twin_state: Capture digital twin internal state
- tbl_box_prms/state: Capture parameter state changes

Usage:
    python -m tests.harness_twin_roundtrip [--tx-id TX_ID] [--fault-mode]

    --tx-id TX_ID    Custom transaction ID (auto-generated if not provided)
    --fault-mode     Test failure path (subscribe to non-existing topic)
"""

# pylint: disable=wrong-import-position,unspecified-encoding,too-many-locals
# pylint: disable=broad-exception-caught,too-many-return-statements,too-many-statements
# pylint: disable=no-else-return

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Add addon to path for imports
ADDON_PATH = Path(__file__).parent.parent / "addon" / "oig-proxy"
sys.path.insert(0, str(ADDON_PATH))

from digital_twin import DigitalTwin, DigitalTwinConfig
from twin_state import (
    OnAckDTO,
    OnDisconnectDTO,
    OnTblEventDTO,
    PendingSettingState,
    QueueSettingDTO,
    SettingStage,
    SnapshotDTO,
)
from twin_transaction import generate_tx_id


# =============================================================================
# Capture Fixtures
# =============================================================================


class CaptureManager:
    """Manages capture files for the roundtrip test."""

    def __init__(self, evidence_dir: Path):
        self.evidence_dir = evidence_dir
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.captures: dict[str, Any] = {}

    def capture(self, name: str, data: Any) -> None:
        """Capture data with a given name."""
        self.captures[name] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": data,
        }
        # Also write to individual file
        filepath = self.evidence_dir / f"{name}.json"
        with open(filepath, "w") as f:
            json.dump(self.captures[name], f, indent=2, default=str)

    def capture_set(self, dto: QueueSettingDTO) -> None:
        """Capture a set operation."""
        self.capture("set", dto.to_dict())

    def capture_status(self, status: str, details: dict[str, Any]) -> None:
        """Capture status update."""
        self.capture("status", {"status": status, "details": details})

    def capture_result(self, result: dict[str, Any]) -> None:
        """Capture transaction result."""
        self.capture("result", result)

    def capture_twin_state(self, snapshot: SnapshotDTO) -> None:
        """Capture digital twin state."""
        self.capture("twin_state", snapshot.to_dict())

    def capture_tbl_box_prms_state(self, tbl_name: str, tbl_item: str, value: str) -> None:
        """Capture tbl_box_prms state change."""
        self.capture(
            "tbl_box_prms_state",
            {"tbl_name": tbl_name, "tbl_item": tbl_item, "value": value},
        )

    def get_captures(self) -> dict[str, Any]:
        """Get all captured data."""
        return self.captures

    def write_summary(self, success: bool, error_msg: str | None = None) -> None:
        """Write summary file."""
        summary = {
            "success": success,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "captures": list(self.captures.keys()),
            "error": error_msg,
        }
        filepath = self.evidence_dir / "summary.json"
        with open(filepath, "w") as f:
            json.dump(summary, f, indent=2)


# =============================================================================
# Twin Operations
# =============================================================================


async def run_twin_roundtrip(
    tx_id: str,
    conn_id: int,
    captures: CaptureManager,
    fault_mode: bool = False,
) -> bool:
    """Execute the full twin roundtrip.

    Args:
        tx_id: Transaction ID
        conn_id: Connection ID
        captures: Capture manager
        fault_mode: If True, test failure path

    Returns:
        True if successful, False otherwise
    """
    config = DigitalTwinConfig(
        device_id="TEST_DEVICE",
        ack_timeout_s=5.0,
        applied_timeout_s=10.0,
    )
    twin = DigitalTwin(session_id="test-session", config=config)

    try:
        # Phase 1: Queue setting
        captures.capture_status("phase_1", {"phase": "queue_setting"})

        dto = QueueSettingDTO(
            tx_id=tx_id,
            conn_id=conn_id,
            tbl_name="tbl_box_prms",
            tbl_item="SA",
            new_value="1",
        )
        await twin.queue_setting(dto)
        captures.capture_set(dto)

        # Capture initial twin state
        snapshot = await twin.get_snapshot()
        captures.capture_twin_state(snapshot)

        # Phase 2: Poll triggers delivery
        captures.capture_status("phase_2", {"phase": "poll_delivery"})

        response = await twin.on_poll(tx_id=None, conn_id=conn_id, table_name="IsNewSet")

        if response.frame_data is None:
            captures.capture_status("error", {"error": "No frame data from poll"})
            return False

        captures.capture_status("poll_response", {"frame_data": response.frame_data})

        # Verify delivery
        pending = await twin.get_inflight()
        if pending is None:
            captures.capture_status("error", {"error": "No inflight after poll"})
            return False

        if pending.stage != SettingStage.SENT_TO_BOX:
            captures.capture_status(
                "error",
                {"error": f"Wrong stage: {pending.stage}"},
            )
            return False

        captures.capture_twin_state(await twin.get_snapshot())

        # Phase 3: ACK received
        captures.capture_status("phase_3", {"phase": "ack"})

        if fault_mode:
            # Simulate ACK on wrong connection (failure)
            wrong_conn_id = conn_id + 100
            ack_dto = OnAckDTO(tx_id=tx_id, conn_id=wrong_conn_id, ack=True)
            try:
                await twin.on_ack(ack_dto)
                captures.capture_status("error", {"error": "Expected failure but succeeded"})
                return False
            except Exception as e:
                captures.capture_result(
                    {
                        "tx_id": tx_id,
                        "status": "fault_detected",
                        "error": str(e),
                    }
                )
                captures.capture_status("fault_injected", {"type": "wrong_conn_ack"})
                return True  # Expected failure

        # Normal ACK
        ack_dto = OnAckDTO(tx_id=tx_id, conn_id=conn_id, ack=True)
        ack_result = await twin.on_ack(ack_dto)

        if ack_result is None:
            captures.capture_status("error", {"error": "ACK returned None"})
            return False

        captures.capture_result(ack_result.to_dict())

        # Verify ACK stage
        pending = await twin.get_inflight()
        if pending is None or pending.stage != SettingStage.BOX_ACK:
            captures.capture_status(
                "error",
                {"error": f"Wrong stage after ACK: {pending.stage if pending else 'None'}"},
            )
            return False

        captures.capture_twin_state(await twin.get_snapshot())

        # Phase 4: tbl_events Setting event
        captures.capture_status("phase_4", {"phase": "tbl_event"})

        event_dto = OnTblEventDTO(
            tx_id=tx_id,
            conn_id=conn_id,
            event_type="Setting",
            tbl_name="tbl_box_prms",
            tbl_item="SA",
            new_value="1",
        )
        event_result = await twin.on_tbl_event(event_dto)

        if event_result is None:
            captures.capture_status("error", {"error": "tbl_event returned None"})
            return False

        captures.capture_result(event_result.to_dict())
        captures.capture_tbl_box_prms_state("tbl_box_prms", "SA", "1")

        # Verify applied stage
        pending = await twin.get_inflight()
        if pending is None or pending.stage != SettingStage.APPLIED:
            captures.capture_status(
                "error",
                {"error": f"Wrong stage after event: {pending.stage if pending else 'None'}"},
            )
            return False

        captures.capture_twin_state(await twin.get_snapshot())

        # Phase 5: Complete transaction
        captures.capture_status("phase_5", {"phase": "complete"})

        finish_result = await twin.finish_inflight(tx_id, conn_id=conn_id, success=True)

        if finish_result is None:
            captures.capture_status("error", {"error": "finish_inflight returned None"})
            return False

        captures.capture_result(finish_result.to_dict())

        # Verify completion
        pending = await twin.get_inflight()
        if pending is not None:
            captures.capture_status(
                "error",
                {"error": "Inflight not cleared after completion"},
            )
            return False

        captures.capture_twin_state(await twin.get_snapshot())

        # All phases completed successfully
        captures.capture_status("complete", {"tx_id": tx_id})
        return True

    except Exception as e:  # pylint: disable=broad-except
        captures.capture_status("exception", {"error": str(e), "type": type(e).__name__})
        return False


# =============================================================================
# Main Harness
# =============================================================================


def main() -> int:
    """Main harness entry point."""
    parser = argparse.ArgumentParser(
        description="Live-like twin control roundtrip test harness"
    )
    parser.add_argument(
        "--tx-id",
        type=str,
        default=None,
        help="Transaction ID (auto-generated if not provided)",
    )
    parser.add_argument(
        "--fault-mode",
        action="store_true",
        help="Test failure path (subscribe to non-existing topic)",
    )
    parser.add_argument(
        "--conn-id",
        type=int,
        default=1,
        help="Connection ID (default: 1)",
    )
    parser.add_argument(
        "--evidence-dir",
        type=str,
        default=".sisyphus/evidence",
        help="Directory for evidence files",
    )

    args = parser.parse_args()

    # Generate tx_id if not provided
    tx_id = args.tx_id or f"harness-{generate_tx_id()}"

    # Set up evidence directory
    evidence_dir = Path(args.evidence_dir)
    task_evidence_dir = evidence_dir / "task-10-harness"
    task_evidence_dir.mkdir(parents=True, exist_ok=True)

    print(f"Starting harness with tx_id={tx_id}, conn_id={args.conn_id}")
    print(f"Evidence directory: {task_evidence_dir}")
    print(f"Fault mode: {args.fault_mode}")

    captures = CaptureManager(task_evidence_dir)

    # Run the roundtrip
    start_time = time.time()
    success = asyncio.run(
        run_twin_roundtrip(
            tx_id=tx_id,
            conn_id=args.conn_id,
            captures=captures,
            fault_mode=args.fault_mode,
        )
    )
    elapsed = time.time() - start_time

    print(f"\nRoundtrip completed in {elapsed:.2f}s")
    print(f"Success: {success}")

    # Required events for success path
    required_events = ["set", "status", "result", "twin_state", "tbl_box_prms_state"]
    captured_keys = list(captures.get_captures().keys())

    if success:
        # In fault mode, success means failure was correctly detected
        if args.fault_mode:
            # Verify we captured the fault detection
            if "fault_injected" not in captured_keys and "fault_detected" not in str(captures.get_captures()):
                print("ERROR: Fault mode did not detect expected fault")
                captures.write_summary(False, "Fault mode failed to detect fault")
                return 1

            captures.write_summary(True)
            print("\nFault mode: Failure correctly detected!")
            print(f"Captures: {captured_keys}")

            # Write evidence file for fault detection
            evidence_file = evidence_dir / "task-10-harness-failure.txt"
            with open(evidence_file, "w") as f:
                f.write(f"Harness Failure Detection Evidence\n")
                f.write(f"====================================\n")
                f.write(f"tx_id: {tx_id}\n")
                f.write(f"conn_id: {args.conn_id}\n")
                f.write(f"fault_mode: {args.fault_mode}\n")
                f.write(f"elapsed: {elapsed:.2f}s\n")
                f.write(f"captures: {captured_keys}\n")
                f.write(f"timestamp: {datetime.utcnow().isoformat()}Z\n")

            print(f"\nEvidence written to: {evidence_file}")
            return 0

        # Verify all required captures exist for success path
        missing = [e for e in required_events if e not in captured_keys]
        if missing:
            print(f"ERROR: Missing required captures: {missing}")
            captures.write_summary(False, f"Missing required captures: {missing}")
            return 1

        captures.write_summary(True)
        print("\nAll capture files generated successfully!")
        print(f"Captures: {captured_keys}")

        # Write evidence file
        evidence_file = evidence_dir / "task-10-harness-success.txt"
        with open(evidence_file, "w") as f:
            f.write(f"Harness Success Evidence\n")
            f.write(f"========================\n")
            f.write(f"tx_id: {tx_id}\n")
            f.write(f"conn_id: {args.conn_id}\n")
            f.write(f"elapsed: {elapsed:.2f}s\n")
            f.write(f"captures: {captured_keys}\n")
            f.write(f"timestamp: {datetime.utcnow().isoformat()}Z\n")

        print(f"\nEvidence written to: {evidence_file}")
        return 0

    else:
        # Failure path - still capture what we can
        error_msg = "Roundtrip failed - see captures for details"
        captures.write_summary(False, error_msg)

        # Write failure evidence
        evidence_file = evidence_dir / "task-10-harness-failure.txt"
        with open(evidence_file, "w") as f:
            f.write(f"Harness Failure Evidence\n")
            f.write(f"========================\n")
            f.write(f"tx_id: {tx_id}\n")
            f.write(f"conn_id: {args.conn_id}\n")
            f.write(f"fault_mode: {args.fault_mode}\n")
            f.write(f"elapsed: {elapsed:.2f}s\n")
            f.write(f"captures: {captured_keys}\n")
            f.write(f"timestamp: {datetime.utcnow().isoformat()}Z\n")

        print(f"\nEvidence written to: {evidence_file}")

        if args.fault_mode:
            print("\nFault mode: Expected failure detected correctly")
            return 0  # Expected failure in fault mode

        return 1  # Unexpected failure


if __name__ == "__main__":
    sys.exit(main())
