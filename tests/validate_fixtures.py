#!/usr/bin/env python3
"""
Validate setting reproduction fixtures for offline and hybrid-offline modes.
Checks required fields and schema compliance.
"""

import json
import sys
from pathlib import Path


def validate_fixture(fixture_path: Path) -> tuple[bool, list[str]]:  # pylint: disable=too-many-branches
    """Validate a single fixture file."""
    errors = []

    if not fixture_path.exists():
        errors.append(f"File does not exist: {fixture_path}")
        return False, errors

    try:
        with open(fixture_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"Invalid JSON: {e}")
        return False, errors

    # Check required top-level fields
    required_fields = [
        'mode', 'conn_id', 'setting_tx_id', 'delivered_at',
        'closed_at', 'ack_seen', 'expected_outcome'
    ]

    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    # Check field types
    if 'conn_id' in data and not isinstance(data['conn_id'], int):
        errors.append(f"conn_id must be integer, got {type(data['conn_id'])}")

    if 'delivered_at' in data and not isinstance(data['delivered_at'], int):
        errors.append(f"delivered_at must be integer (epoch_ms), got {type(data['delivered_at'])}")

    if 'closed_at' in data and not isinstance(data['closed_at'], int):
        errors.append(f"closed_at must be integer (epoch_ms), got {type(data['closed_at'])}")

    if 'ack_seen' in data and not isinstance(data['ack_seen'], bool):
        errors.append(f"ack_seen must be boolean, got {type(data['ack_seen'])}")

    if 'expected_outcome' in data and not isinstance(data['expected_outcome'], str):
        errors.append(f"expected_outcome must be string, got {type(data['expected_outcome'])}")

    # Check source_provenance
    if 'source_provenance' not in data:
        errors.append("Missing required field: source_provenance")
    else:
        provenance = data['source_provenance']
        if 'evidence_files' not in provenance:
            errors.append("source_provenance missing evidence_files")

    # Check timing consistency
    if all(k in data for k in ['delivered_at', 'closed_at']):
        if data['closed_at'] < data['delivered_at']:
            errors.append("closed_at must be >= delivered_at")

    # Check state transitions
    if 'state_transitions' not in data:
        errors.append("Missing required field: state_transitions")
    elif not isinstance(data['state_transitions'], list):
        errors.append("state_transitions must be a list")
    elif len(data['state_transitions']) < 6:
        errors.append(f"Expected at least 6 state transitions, got {len(data['state_transitions'])}")

    # Check mode label
    if 'mode' in data:
        valid_modes = ['offline', 'hybrid-offline']
        if data['mode'] not in valid_modes:
            errors.append(f"Invalid mode '{data['mode']}', expected one of {valid_modes}")

    # Check timing_breakdown_ms
    if 'timing_breakdown_ms' not in data:
        errors.append("Missing required field: timing_breakdown_ms")

    # Check bug_characteristics
    if 'bug_characteristics' not in data:
        errors.append("Missing required field: bug_characteristics")

    return len(errors) == 0, errors


def main():
    """Main validation routine."""
    fixtures_dir = Path("tests/fixtures")
    fixtures = [
        fixtures_dir / "setting_reproduction_offline.json",
        fixtures_dir / "setting_reproduction_hybrid_offline.json"
    ]

    all_valid = True
    validation_results = []

    for fixture_path in fixtures:
        print(f"\nValidating: {fixture_path}")
        is_valid, errors = validate_fixture(fixture_path)

        if is_valid:
            print(f"  ✓ PASSED")

            # Load and display key info
            with open(fixture_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            print(f"    mode: {data['mode']}")
            print(f"    conn_id: {data['conn_id']}")
            print(f"    setting_tx_id: {data['setting_tx_id']}")
            print(f"    delivered_at: {data['delivered_at']} ({data['delivered_at'] / 1000:.1f}s from epoch)")
            print(f"    closed_at: {data['closed_at']} (T+{(data['closed_at'] - data['delivered_at']) / 1000:.1f}s)")
            print(f"    ack_seen: {data['ack_seen']}")
            print(f"    expected_outcome: {data['expected_outcome']}")
            print(f"    state_transitions: {len(data['state_transitions'])}")

            validation_results.append({
                'file': str(fixture_path),
                'status': 'PASSED',
                'mode': data['mode'],
                'conn_id': data['conn_id']
            })
        else:
            print(f"  ✗ FAILED")
            all_valid = False

            for error in errors:
                print(f"    - {error}")

            validation_results.append({
                'file': str(fixture_path),
                'status': 'FAILED',
                'errors': errors
            })

    # Summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    total = len(fixtures)
    passed = sum(1 for r in validation_results if r['status'] == 'PASSED')
    print(f"Total fixtures: {total}")
    print(f"Passed: {passed}")
    print(f"Failed: {total - passed}")

    if all_valid:
        print("\n✓ All fixtures validated successfully")
        return 0
    print("\n✗ Some fixtures failed validation")
    return 1


if __name__ == "__main__":
    sys.exit(main())
