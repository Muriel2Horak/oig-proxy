import csv
from datetime import datetime

def analyze_settings():
    print("Loading db_dump.csv...")
    events = []
    with open("db_dump.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            events.append(row)
            
    print(f"Loaded {len(events)} frames from db_dump.csv.")
    events.sort(key=lambda x: x["ts"])
    
    results = []
    for idx, event in enumerate(events):
        if event.get("table_name") == "tbl_events" and event.get("direction") == "box_to_proxy":
            raw = event.get("raw", "")
            # We specifically want parameter changes containing "->"
            if "<Type>Change</Type>" in raw and "->" in raw:
                ack_time_str = event["ts"]
                if ack_time_str.endswith("+00:00"): ack_time_str = ack_time_str[:-6]
                ack_time = datetime.fromisoformat(ack_time_str)
                print(f"\n--- Found Setting ACK at {ack_time} ---")
                
                # Look backwards for cloud_to_proxy
                trigger = None
                for i in range(idx - 1, max(-1, idx - 1000), -1):
                    prev = events[i]
                    if prev.get("direction") == "cloud_to_proxy":
                        raw_prev = prev.get("raw", "")
                        # We want a cloud response that actually sets something, not just END
                        if "<Result>END</Result>" not in raw_prev and len(raw_prev) > 80:
                            if "<TblName>setting</TblName>" in raw_prev or "<ToDo>" in raw_prev or "<cmd=" in raw_prev or "setting" in raw_prev.lower() or "tbl_invertor_prms" in raw_prev.lower() or "tbl_batt_prms" in raw_prev.lower() or "tbl_ac_in" in raw_prev.lower():
                                trigger = prev
                                break
                                
                if trigger:
                    trigger_time_str = trigger["ts"]
                    if trigger_time_str.endswith("+00:00"): trigger_time_str = trigger_time_str[:-6]
                    trigger_time = datetime.fromisoformat(trigger_time_str)
                    rtt = (ack_time - trigger_time).total_seconds()
                    print(f"TRIGGER found at {trigger_time} (RTT: {rtt}s)")
                    results.append({ "ack": event, "trigger": trigger, "rtt": rtt })
                else:
                    print("NO TRIGGER FOUND in db_dump.csv within 1000 preceding frames.")
                    
    # Save findings
    with open(".sisyphus/notepads/settings_analysis/findings.md", "w") as f:
        f.write("# Settings Analysis Findings\n\n")
        for i, res in enumerate(results):
            f.write(f"## Event {i+1}\n")
            f.write(f"**ACK Time:** {res['ack']['ts']}\n")
            f.write(f"**Trigger Time:** {res['trigger']['ts']}\n")
            f.write(f"**RTT:** {res['rtt']} seconds\n\n")
            f.write("### Cloud Trigger (CLOUD \u2192 BOX)\n")
            f.write("```xml\n")
            f.write(res['trigger']['raw'])
            f.write("\n```\n\n")
            f.write("### Box ACK (BOX \u2192 CLOUD)\n")
            f.write("```xml\n")
            f.write(res['ack']['raw'])
            f.write("\n```\n\n")

if __name__ == "__main__":
    analyze_settings()
