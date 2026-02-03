"""
Playwright tests for Grafana OIG dashboards.
"""
import re
from playwright.sync_api import Page, expect


GRAFANA_URL = "http://10.0.0.160:3000"
GRAFANA_USER = "oigadmin"
GRAFANA_PASS = "oig123"


def test_box_detail_mode_panel(page: Page):
    """Test that Mode panel displays value (not 'No data')."""
    # Login
    page.goto(GRAFANA_URL)
    page.fill('input[name="user"]', GRAFANA_USER)
    page.fill('input[name="password"]', GRAFANA_PASS)
    page.click('button[type="submit"]')
    
    # Navigate to BOX Detail dashboard
    page.goto(f"{GRAFANA_URL}/d/oig-box-influx?orgId=1&var-device_id=2303234502&from=now-5m&to=now")
    
    # Wait for dashboard panels to load
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)
    
    # Get all text from page
    content = page.content()
    
    # Check Mode panel exists and doesn't show "No data"
    assert "Mode" in content, "Mode panel not found on page"
    assert "No data" not in content or content.count("No data") < 3, \
        f"Too many 'No data' messages, Mode panel likely empty"
    
    # Try to find mode value (ONLINE, OFFLINE, LOCAL)
    has_mode = any(mode in content for mode in ["ONLINE", "OFFLINE", "LOCAL"])
    if not has_mode:
        # Print content for debugging
        print(f"\n=== PAGE CONTENT ===\n{content[:2000]}\n")
    
    assert has_mode, "Mode value (ONLINE/OFFLINE/LOCAL) not found on page"


def test_box_detail_version_panel(page: Page):
    """Test that Version panel displays value (not 'No data')."""
    page.goto(GRAFANA_URL)
    page.fill('input[name="user"]', GRAFANA_USER)
    page.fill('input[name="password"]', GRAFANA_PASS)
    page.click('button[type="submit"]')
    
    page.goto(f"{GRAFANA_URL}/d/oig-box-influx?orgId=1&var-device_id=2303234502&from=now-5m&to=now")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)
    
    content = page.content()
    
    assert "Version" in content, "Version panel not found"
    
    # Should show version number like 1.4.2
    has_version = re.search(r'\d+\.\d+\.\d+', content)
    assert has_version, f"Version number not found on page"


def test_box_detail_all_stat_panels(page: Page):
    """Test that all stat panels show data."""
    page.goto(GRAFANA_URL)
    page.fill('input[name="user"]', GRAFANA_USER)
    page.fill('input[name="password"]', GRAFANA_PASS)
    page.click('button[type="submit"]')
    
    page.goto(f"{GRAFANA_URL}/d/oig-box-influx?orgId=1&var-device_id=2303234502&from=now-5m&to=now")
    
    # Wait for page to load
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)
    
    content = page.content()
    
    # Check all stat panels exist
    stat_panels = ["Mode", "BOX", "Cloud", "Uptime", "Version", "Errors"]
    
    for panel_name in stat_panels:
        assert panel_name in content, f"{panel_name} panel not found"
        print(f"✓ {panel_name} panel found")
    
    # Count "No data" occurrences - should be minimal
    no_data_count = content.count("No data")
    print(f"\n'No data' occurrences: {no_data_count}")
    assert no_data_count < 2, f"Too many panels showing 'No data': {no_data_count}"


def test_box_detail_screenshot(page: Page):
    """Take screenshot of BOX Detail dashboard for debugging."""
    page.goto(GRAFANA_URL)
    page.fill('input[name="user"]', GRAFANA_USER)
    page.fill('input[name="password"]', GRAFANA_PASS)
    page.click('button[type="submit"]')
    
    page.goto(f"{GRAFANA_URL}/d/oig-box-influx?orgId=1&var-device_id=2303234502&from=now-5m&to=now")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)
    
    # Take screenshot
    screenshot_path = "/tmp/grafana_box_detail.png"
    page.screenshot(path=screenshot_path, full_page=True)
    print(f"\nScreenshot saved to: {screenshot_path}")
    
    # Get and print HTML content sample
    content = page.content()
    print(f"\n=== Page title ===\n{page.title()}")
    print(f"\n=== Looking for Mode/Version in content ===")
    print(f"Mode found: {'Mode' in content}")
    print(f"OFFLINE found: {'OFFLINE' in content}")
    print(f"Version found: {'Version' in content}")
    print(f"No data count: {content.count('No data')}")


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        try:
            print("Testing Mode panel...")
            test_box_detail_mode_panel(page)
            print("✓ Mode panel OK")
        except Exception as e:
            print(f"✗ Mode panel FAILED: {e}")
        
        page = browser.new_page()
        try:
            print("\nTesting Version panel...")
            test_box_detail_version_panel(page)
            print("✓ Version panel OK")
        except Exception as e:
            print(f"✗ Version panel FAILED: {e}")
        
        page = browser.new_page()
        try:
            print("\nTesting all stat panels...")
            test_box_detail_all_stat_panels(page)
            print("✓ All stat panels OK")
        except Exception as e:
            print(f"✗ Stat panels FAILED: {e}")
        
        page = browser.new_page()
        try:
            print("\nTaking screenshot for debugging...")
            test_box_detail_screenshot(page)
            print("✓ Screenshot saved")
        except Exception as e:
            print(f"✗ Screenshot FAILED: {e}")
        
        browser.close()
        print("\n=== TESTS COMPLETE ===")
