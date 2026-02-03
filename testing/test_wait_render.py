#!/usr/bin/env python3
"""Final Grafana dashboard test - wait for rendering."""

from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    # Enable console logging from page
    page.on("console", lambda msg: print(f"BROWSER: {msg.text}"))
    
    # Login
    print("Logging in...")
    page.goto('http://10.0.0.160:3000')
    page.fill('input[name="user"]', 'oigadmin')
    page.fill('input[name="password"]', 'oig123')
    page.click('button[type="submit"]')
    
    # Go to dashboard
    print("Loading dashboard...")
    page.goto('http://10.0.0.160:3000/d/oig-box-influx?orgId=1&var-device_id=2303234502')
    
    # Wait multiple times and take screenshots
    for i in range(5):
        wait_time = 2 + (i * 2)  # 2s, 4s, 6s, 8s, 10s
        print(f"\nWaiting {wait_time}s...")
        page.wait_for_timeout(wait_time * 1000)
        
        content = page.content()
        
        # Check for values
        has_offline = 'offline' in content.lower()
        has_version = '1.4.2' in content
        has_mode_text = 'OFFLINE' in content or 'ONLINE' in content or 'LOCAL' in content
        
        print(f"  'offline' found: {has_offline}")
        print(f"  'OFFLINE/ONLINE/LOCAL' found: {has_mode_text}")
        print(f"  '1.4.2' found: {has_version}")
        
        # Save screenshot
        screenshot_path = f"/tmp/graf from_attempt_{i+1}.png"
        page.screenshot(path=screenshot_path)
        print(f"  Screenshot: {screenshot_path}")
        
        if has_mode_text and has_version:
            print(f"\n✓ SUCCESS at {wait_time}s!")
            break
    else:
        print("\n✗ FAILED - values never appeared")
        
        # Debug: print some HTML
        mode_idx = content.find('mode')
        if mode_idx > 0:
            print(f"\nHTML around 'mode':\n{content[max(0, mode_idx-200):mode_idx+200]}")
    
    browser.close()

print("\n=== TEST COMPLETE ===")
