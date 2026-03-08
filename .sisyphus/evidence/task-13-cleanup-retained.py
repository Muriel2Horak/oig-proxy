#!/usr/bin/env python3
"""
Retained MQTT Topic Cleanup Script

Safely removes stale retained messages from MQTT broker.
Supports dry-run mode and explicit allowlist to prevent accidental deletion.

Usage:
    python cleanup_retained_topics.py --dry-run
    python cleanup_retained_topics.py --allowlist topic1,topic2
    python cleanup_retained_topics.py --allowlist-file allowlist.txt --dry-run

Environment Variables:
    MQTT_HOST      - MQTT broker host (default: core-mosquitto)
    MQTT_PORT      - MQTT broker port (default: 1883)
    MQTT_USERNAME  - MQTT username (optional)
    MQTT_PASSWORD  - MQTT password (optional)
    MQTT_NAMESPACE - Topic namespace (default: oig_local)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("ERROR: paho-mqtt not installed. Install with: pip install paho-mqtt")
    sys.exit(1)


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_NAMESPACE = os.getenv("MQTT_NAMESPACE", "oig_local")
STALE_THRESHOLD_HOURS = 24  # Consider retained message stale after 24 hours


# =============================================================================
# Retained Topic Patterns
# =============================================================================

# Control topics (from config.py)
CONTROL_SET_TOPIC = f"{DEFAULT_NAMESPACE}/oig_proxy/control/set"
CONTROL_RESULT_TOPIC = f"{DEFAULT_NAMESPACE}/oig_proxy/control/result"
CONTROL_STATUS_PREFIX = f"{DEFAULT_NAMESPACE}/oig_proxy/control/status"

# Twin state topic (retained)
TWIN_STATE_TOPIC = f"{DEFAULT_NAMESPACE}/oig_proxy/twin_state/state"

# State topics pattern: oig_local/<device_id>/<tbl_name>/state
STATE_TOPIC_PATTERN = f"{DEFAULT_NAMESPACE}/+/+/state"

# Discovery topics pattern (from HA)
DISCOVERY_TOPIC_PATTERN = "homeassistant/+/+/+/config"


# =============================================================================
# Default Allowlist (topics that should NEVER be cleared)
# =============================================================================

DEFAULT_ALLOWLIST = {
    # Control topics - authoritative post-cutover
    CONTROL_SET_TOPIC,
    CONTROL_RESULT_TOPIC,
    f"{CONTROL_STATUS_PREFIX}/#",  # All status topics
    
    # Twin state - authoritative post-cutover
    TWIN_STATE_TOPIC,
    
    # Device state topics - authoritative post-cutover (device_id specific)
    # Note: These should NOT be cleared as they contain current device state
    f"{DEFAULT_NAMESPACE}/oig_proxy/proxy_status/state",
    f"{DEFAULT_NAMESPACE}/oig_proxy/tbl_events/state",
    
    # HA Discovery topics - should be refreshed by HA, not cleared
    "homeassistant/#",
}


def get_default_allowlist() -> set[str]:
    """Return the default allowlist of topics that should never be cleared."""
    return DEFAULT_ALLOWLIST.copy()


# =============================================================================
# Stale Detection Logic
# =============================================================================

def is_stale_timestamp(payload: str, threshold_hours: int = STALE_THRESHOLD_HOURS) -> bool:
    """
    Detect if a retained payload contains stale timestamp/state.
    
    Looks for common timestamp fields in payloads:
    - timestamp, time, ts, datetime
    - last_result.timestamp
    - inflight.timestamp
    """
    try:
        data = json.loads(payload)
        
        # Check for direct timestamp field
        for ts_field in ["timestamp", "time", "ts", "datetime"]:
            if ts_field in data:
                ts_value = data[ts_field]
                if isinstance(ts_value, str):
                    try:
                        # Try parsing ISO format
                        dt = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
                        age_hours = (datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                        return age_hours > threshold_hours
                    except (ValueError, AttributeError):
                        pass
        
        # Check nested timestamp in last_result
        if "last_result" in data and isinstance(data["last_result"], dict):
            lr = data["last_result"]
            if "timestamp" in lr:
                ts_value = lr["timestamp"]
                if isinstance(ts_value, str):
                    try:
                        dt = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
                        age_hours = (datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                        return age_hours > threshold_hours
                    except (ValueError, AttributeError):
                        pass
        
        # Check inflight timestamp
        if "inflight" in data and isinstance(data["inflight"], dict):
            inf = data["inflight"]
            if "timestamp" in inf:
                ts_value = inf["timestamp"]
                if isinstance(ts_value, str):
                    try:
                        dt = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
                        age_hours = (datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                        return age_hours > threshold_hours
                    except (ValueError, AttributeError):
                        pass
        
        # If no valid timestamp found, assume not stale (conservative)
        return False
        
    except (json.JSONDecodeError, TypeError):
        # If payload is not valid JSON, assume not stale
        return False


def analyze_payload_state(payload: str) -> dict[str, Any]:
    """
    Analyze a retained payload and return its state metadata.
    
    Returns:
        dict with keys: is_stale, has_timestamp, timestamp_field, detected_age_hours
    """
    result = {
        "is_stale": False,
        "has_timestamp": False,
        "timestamp_field": None,
        "detected_age_hours": None,
        "status": None,
    }
    
    try:
        data = json.loads(payload)
        
        # Check for direct timestamp field
        for ts_field in ["timestamp", "time", "ts", "datetime"]:
            if ts_field in data:
                result["has_timestamp"] = True
                result["timestamp_field"] = ts_field
                ts_value = data[ts_field]
                if isinstance(ts_value, str):
                    try:
                        dt = datetime.fromisoformat(ts_value.replace("Z", "+00:00"))
                        age_hours = (datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                        result["detected_age_hours"] = round(age_hours, 2)
                        result["is_stale"] = age_hours > STALE_THRESHOLD_HOURS
                    except (ValueError, AttributeError):
                        pass
        
        # Extract status if present
        if "last_result" in data and isinstance(data["last_result"], dict):
            result["status"] = data["last_result"].get("status")
        
        return result
        
    except (json.JSONDecodeError, TypeError):
        return result


# =============================================================================
# Topic Matching
# =============================================================================

def topic_matches_pattern(topic: str, pattern: str) -> bool:
    """
    Check if topic matches a pattern (supports + and # wildcards).
    
    + matches single level
    # matches multiple levels (only at end)
    """
    if pattern.endswith("#"):
        prefix = pattern[:-1]
        return topic.startswith(prefix)
    elif "+" in pattern:
        parts = pattern.split("/")
        topic_parts = topic.split("/")
        if len(parts) != len(topic_parts):
            return False
        return all(p == "+" or p == tp for p, tp in zip(parts, topic_parts))
    else:
        return topic == pattern


def is_allowed(topic: str, allowlist: set[str]) -> bool:
    """Check if topic matches any pattern in allowlist."""
    for pattern in allowlist:
        if topic_matches_pattern(topic, pattern):
            return True
    return False


# =============================================================================
# MQTT Client
# =============================================================================

class RetainedTopicCleaner:
    """Handles MQTT connection and retained topic cleanup."""
    
    def __init__(
        self,
        host: str = "core-mosquitto",
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        
        self.client = mqtt.Client()
        if username and password:
            self.client.username_pw_set(username, password)
        
        self.retained_topics: dict[str, tuple[str, int]] = {}  # topic -> (payload, qos)
        self.discovered_topics: list[str] = []
        
    def connect(self) -> bool:
        """Connect to MQTT broker."""
        try:
            self.client.connect(self.host, self.port, 60)
            self.client.loop_start()
            time.sleep(1)  # Wait for connection
            return True
        except Exception as e:
            print(f"ERROR: Failed to connect to MQTT: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from MQTT broker."""
        self.client.loop_stop()
        self.client.disconnect()
    
    def discover_retained_topics(self, namespace: str = DEFAULT_NAMESPACE):
        """Discover all retained topics by subscribing to # and checking retain flag."""
        # We need to use a workaround since mosquitto doesn't expose retained list directly
        # We'll subscribe to specific patterns and track what we receive
        
        # Subscribe to namespace wildcard to catch state topics
        topics = [
            (f"{namespace}/#", 0),
            ("homeassistant/#", 0),
        ]
        
        self.client.message_callback_add("#", self._on_message)
        self.client.subscribe(topics)
        
        # Wait for messages
        time.sleep(2)
        
        # Unsubscribe
        for topic, qos in topics:
            self.client.unsubscribe(topic)
    
    def _on_message(self, client, userdata, msg):
        """Handle incoming message - track retained topics."""
        if msg.retain:
            topic = msg.topic
            payload = msg.payload.decode("utf-8", errors="replace")
            self.retained_topics[topic] = (payload, msg.qos)
    
    def get_all_retained_topics(self) -> dict[str, tuple[str, int]]:
        """Return all discovered retained topics."""
        return self.retained_topics.copy()
    
    def clear_retained_topic(self, topic: str) -> bool:
        """Clear a specific retained topic by publishing empty payload with retain flag."""
        try:
            result = self.client.publish(topic, "", qos=1, retain=True)
            return result.rc == mqtt.MQTT_ERR_SUCCESS
        except Exception as e:
            print(f"ERROR: Failed to clear topic {topic}: {e}")
            return False


# =============================================================================
# Main Cleanup Logic
# =============================================================================

def analyze_retained_topics(
    retained_topics: dict[str, tuple[str, int]],
    allowlist: set[str],
    stale_threshold_hours: int = STALE_THRESHOLD_HOURS,
) -> dict[str, Any]:
    """
    Analyze retained topics and determine what should be cleaned.
    
    Returns analysis dict with:
    - topics_to_clear: list of topics that are stale and not in allowlist
    - allowed_topics: topics that match allowlist (should NOT be cleared)
    - stale_topics: topics that are stale but in allowlist
    - analysis: per-topic analysis
    """
    analysis = {
        "topics_to_clear": [],
        "allowed_topics": [],
        "stale_but_allowed": [],
        "analysis": {},
        "summary": {
            "total_retained": len(retained_topics),
            "allowed_count": 0,
            "to_clear_count": 0,
            "stale_but_allowed_count": 0,
        }
    }
    
    for topic, (payload, qos) in retained_topics.items():
        topic_analysis = {
            "payload_size": len(payload),
            "qos": qos,
            "is_allowed": False,
            "is_stale": False,
            "state": None,
            "age_hours": None,
        }
        
        # Check if topic is in allowlist
        if is_allowed(topic, allowlist):
            topic_analysis["is_allowed"] = True
            analysis["allowed_topics"].append(topic)
            analysis["summary"]["allowed_count"] += 1
            
            # Also check if stale
            if payload:  # Only check non-empty payloads
                state_info = analyze_payload_state(payload)
                topic_analysis["is_stale"] = state_info["is_stale"]
                topic_analysis["state"] = state_info.get("status")
                topic_analysis["age_hours"] = state_info.get("detected_age_hours")
                
                if state_info["is_stale"]:
                    analysis["stale_but_allowed"].append(topic)
                    analysis["summary"]["stale_but_allowed_count"] += 1
        else:
            # Not in allowlist - check if stale
            if payload:
                state_info = analyze_payload_state(payload)
                topic_analysis["is_stale"] = state_info["is_stale"]
                topic_analysis["state"] = state_info.get("status")
                topic_analysis["age_hours"] = state_info.get("detected_age_hours")
                
                if state_info["is_stale"]:
                    analysis["topics_to_clear"].append(topic)
                    analysis["summary"]["to_clear_count"] += 1
            else:
                # Empty payload - clear it
                analysis["topics_to_clear"].append(topic)
                analysis["summary"]["to_clear_count"] += 1
        
        analysis["analysis"][topic] = topic_analysis
    
    return analysis


def load_allowlist_from_file(filepath: str) -> set[str]:
    """Load allowlist from file (one topic pattern per line)."""
    allowlist = set()
    path = Path(filepath)
    if path.exists():
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    allowlist.add(line)
    return allowlist


def main():
    parser = argparse.ArgumentParser(
        description="Cleanup stale retained MQTT topics safely",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be cleared without actually clearing"
    )
    parser.add_argument(
        "--allowlist",
        type=str,
        help="Comma-separated list of topic patterns to preserve"
    )
    parser.add_argument(
        "--allowlist-file",
        type=str,
        help="File containing topic patterns to preserve (one per line)"
    )
    parser.add_argument(
        "--mqtt-host",
        type=str,
        default=os.getenv("MQTT_HOST", "core-mosquitto"),
        help="MQTT broker host"
    )
    parser.add_argument(
        "--mqtt-port",
        type=int,
        default=int(os.getenv("MQTT_PORT", "1883")),
        help="MQTT broker port"
    )
    parser.add_argument(
        "--mqtt-username",
        type=str,
        default=os.getenv("MQTT_USERNAME", ""),
        help="MQTT username"
    )
    parser.add_argument(
        "--mqtt-password",
        type=str,
        default=os.getenv("MQTT_PASSWORD", ""),
        help="MQTT password"
    )
    parser.add_argument(
        "--namespace",
        type=str,
        default=DEFAULT_NAMESPACE,
        help="MQTT namespace"
    )
    parser.add_argument(
        "--stale-threshold-hours",
        type=int,
        default=STALE_THRESHOLD_HOURS,
        help="Hours after which a retained message is considered stale"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for analysis results (JSON)"
    )
    
    args = parser.parse_args()
    
    # Build allowlist
    allowlist = get_default_allowlist()
    
    if args.allowlist:
        user_patterns = set(p.strip() for p in args.allowlist.split(",") if p.strip())
        allowlist.update(user_patterns)
    
    if args.allowlist_file:
        file_patterns = load_allowlist_from_file(args.allowlist_file)
        allowlist.update(file_patterns)
    
    print("=" * 60)
    print("Retained MQTT Topic Cleanup Tool")
    print("=" * 60)
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"MQTT: {args.mqtt_host}:{args.mqtt_port}")
    print(f"Namespace: {args.namespace}")
    print(f"Stale threshold: {args.stale_threshold_hours} hours")
    print(f"Allowlist patterns: {len(allowlist)}")
    print("-" * 60)
    
    # Connect to MQTT
    username: str | None = args.mqtt_username if args.mqtt_username else None
    password: str | None = args.mqtt_password if args.mqtt_password else None
    
    cleaner = RetainedTopicCleaner(
        host=args.mqtt_host,
        port=args.mqtt_port,
        username=username,
        password=password,
    )
    
    if not cleaner.connect():
        print("ERROR: Could not connect to MQTT broker")
        sys.exit(1)
    
    print("Connected to MQTT broker")
    print("Discovering retained topics...")
    
    # Discover retained topics
    cleaner.discover_retained_topics(args.namespace)
    retained = cleaner.get_all_retained_topics()
    
    print(f"Found {len(retained)} retained topics")
    
    # Analyze
    analysis = analyze_retained_topics(
        retained,
        allowlist,
        args.stale_threshold_hours,
    )
    
    # Output results
    print("\n" + "=" * 60)
    print("ANALYSIS RESULTS")
    print("=" * 60)
    print(f"Total retained topics: {analysis['summary']['total_retained']}")
    print(f"Allowed (in allowlist): {analysis['summary']['allowed_count']}")
    print(f"Stale but allowed: {analysis['summary']['stale_but_allowed_count']}")
    print(f"Topics to clear: {analysis['summary']['to_clear_count']}")
    
    print("\n--- Topics in Allowlist (PRESERVED) ---")
    for topic in sorted(analysis["allowed_topics"])[:10]:
        state = analysis["analysis"][topic]
        age = state.get("age_hours")
        status = state.get("state") or "N/A"
        print(f"  {topic}")
        if age:
            print(f"    -> age: {age}h, status: {status}")
    
    if len(analysis["allowed_topics"]) > 10:
        print(f"  ... and {len(analysis['allowed_topics']) - 10} more")
    
    print("\n--- Topics to CLEAR (stale, not in allowlist) ---")
    if analysis["topics_to_clear"]:
        for topic in sorted(analysis["topics_to_clear"]):
            state = analysis["analysis"][topic]
            age = state.get("age_hours")
            print(f"  {topic}")
            if age:
                print(f"    -> age: {age}h")
    else:
        print("  (none)")
    
    # Save output if requested
    if args.output:
        output_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "dry_run" if args.dry_run else "live",
            "arguments": vars(args),
            "allowlist": list(allowlist),
            "analysis": analysis,
        }
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nResults saved to: {args.output}")
    
    # Execute cleanup if not dry-run
    if not args.dry_run and analysis["topics_to_clear"]:
        print("\n" + "-" * 60)
        print("CLEANING UP...")
        for topic in analysis["topics_to_clear"]:
            success = cleaner.clear_retained_topic(topic)
            status = "OK" if success else "FAILED"
            print(f"  [{status}] Cleared: {topic}")
    elif not args.dry_run:
        print("\nNo topics to clean.")
    
    cleaner.disconnect()
    print("\nDone.")
    
    # Return exit code based on whether there were topics to clear
    return 0 if args.dry_run or not analysis["topics_to_clear"] else 1


if __name__ == "__main__":
    sys.exit(main())
