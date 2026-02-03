#!/usr/bin/env python3
"""Test with VISIBLE browser."""

from playwright.sync_api import sync_playwright
import time

print("Starting VISIBLE browser test...")
print("Browser window will open - watch what happens!")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=1000)  # Slow down so you can see
    page = browser.new_page()
    
    print("\n1. Logging in...")
    page.goto('http://10.0.0.160:3000')
    page.fill('input[name="user"]', 'oigadmin')
    page.fill('input[name="password"]', 'oig123')
    page.click('button[type="submit"]')
    
    # Wait for login to complete
    page.wait_for_timeout(2000)
    
    print("2. Loading dashboard...")
    page.goto('http://10.0.0.160:3000/d/oig-box-influx?orgId=1&var-device_id=2303234502&from=now-5m&to=now', wait_until='domcontentloaded')
    
    print("3. Waiting 10 seconds for panels to load...")
    for i in range(10, 0, -1):
        print(f"   {i}...", end=" ", flush=True)
        time.sleep(1)
    print()
    
    content = page.content()
    
    print("\n4. RESULTS:")
    mode_found = any(x in content for x in ['OFFLINE', 'ONLINE', 'LOCAL', 'offline', 'online', 'local'])
    print(f"   Mode (offline/online/local): {'✓ FOUND' if mode_found else '✗ NOT FOUND'}")
    print(f"   Version (1.4.2): {'✓ FOUND' if '1.4.2' in content else '✗ NOT FOUND'}")
    print(f"   'No data' messages: {content.count('No data')}")
    
    # Debug - find what's actually in Mode panel area
    if 'Mode' in content:
        mode_idx = content.find('Mode', content.find('Mode') + 1)  # Find second occurrence
        snippet = content[max(0, mode_idx-500):mode_idx+500]
        print(f"\n   DEBUG - Text around second 'Mode':")
        import re
        clean = re.sub(r'<[^>]+>', ' ', snippet)
        clean = re.sub(r'\s+', ' ', clean)
        print(f"   {clean[:300]}...")
    
    print("\n5. Keeping browser open for 5 more seconds so you can see...")
    time.sleep(5)
    
    browser.close()

print("\n=== DONE ===")
