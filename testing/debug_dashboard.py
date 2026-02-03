#!/usr/bin/env python3
"""Debug script to analyze Grafana dashboard content."""

from playwright.sync_api import sync_playwright
import re

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    # Login
    page.goto('http://10.0.0.160:3000')
    page.fill('input[name="user"]', 'oigadmin')
    page.fill('input[name="password"]', 'oig123')
    page.click('button[type="submit"]')
    
    # Go to dashboard
    page.goto('http://10.0.0.160:3000/d/oig-box-influx?orgId=1&var-device_id=2303234502&from=now-5m&to=now')
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(5000)
    
    content = page.content()
    
    print("=== DASHBOARD ANALYSIS ===\n")
    
    # Check for Mode
    if 'OFFLINE' in content:
        print("✓ Found: OFFLINE")
    elif 'ONLINE' in content:
        print("✓ Found: ONLINE")
    elif 'LOCAL' in content:
        print("✓ Found: LOCAL")
    else:
        print("✗ No mode value found (ONLINE/OFFLINE/LOCAL)")
        
    # Check for No data
    no_data_count = content.count('No data')
    print(f"'No data' occurrences: {no_data_count}")
    
    # Check for version
    version_match = re.search(r'\d+\.\d+\.\d+', content)
    if version_match:
        print(f"✓ Found version: {version_match.group()}")
    else:
        print("✗ No version found")
    
    # Look for panel titles
    print("\n=== Panel Titles Found ===")
    for title in ['Mode', 'BOX', 'Cloud', 'Uptime', 'Version', 'Errors']:
        if title in content:
            print(f"✓ {title}")
        else:
            print(f"✗ {title}")
    
    # Extract area around "Mode" to see what's there
    mode_idx = content.find('Mode')
    if mode_idx > 0:
        snippet = content[mode_idx:mode_idx+500]
        # Remove HTML tags for readability
        snippet_clean = re.sub(r'<[^>]+>', ' ', snippet)
        print(f"\n=== Content around 'Mode' ===\n{snippet_clean[:200]}")
    
    # Save full HTML
    with open('/tmp/grafana_debug.html', 'w') as f:
        f.write(content)
    print("\n✓ Full HTML saved to /tmp/grafana_debug.html")
    
    browser.close()
