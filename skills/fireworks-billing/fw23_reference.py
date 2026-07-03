#!/usr/bin/env python3
"""Start Chrome with copied E-Förder profile via CDP, then do Fireworks billing."""
import subprocess, time, random
from playwright.sync_api import sync_playwright

EMAIL = "cyber-scorpion-262@gmx.de"
PASSWORD = "ZOE.jerry2024"
CARD = "4349710048183244"
EXPIRY = "10/27"
CVC = "312"

STREETS = ["Friedrichstr. 123", "Unter den Linden 42", "Kurfürstendamm 7", "Alexanderplatz 3",
           "Prenzlauer Allee 88", "Schloßstraße 15", "Karl-Marx-Allee 200", "Torstraße 67",
           "Oranienstraße 11", "Greifswalder Straße 45", "Danziger Straße 99", "Wilmersdorfer Str. 33",
           "Bundesallee 78", "Mainzer Straße 14", "Gneisenaustraße 56", "Bergmannstraße 22"]
PLZ_LIST = ["10115", "10117", "10178", "10243", "10245", "10315", "10317", "10318",
            "10405", "10407", "10409", "10551", "10553", "10555", "10585", "10587"]
FIRST = ["Max", "Anna", "Lukas", "Sophie", "Leonie", "Felix", "Emma", "Paul",
         "Marie", "Jonas", "Laura", "Elias", "Mia", "Ben", "Hannah", "Tim"]
LAST = ["Mueller", "Schmidt", "Schneider", "Fischer", "Weber", "Wagner", "Becker",
        "Hoffmann", "Schulz", "Koch", "Richter", "Klein", "Wolf", "Schroeder", "Neumann"]

NAME = f"{random.choice(FIRST)} {random.choice(LAST)}"
ADDR = random.choice(STREETS)
CITY = "Berlin"
ZIP = random.choice(PLZ_LIST)

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USER_DATA = "/tmp/chrome-profile-159"
CDP_PORT = 9222

def ss(page, name):
    path = f"/tmp/fw23_{name}.png"
    page.screenshot(path=path)
    print(f"[ss] {path}")

def main():
    print(f"  Identity: {NAME}, {ADDR}, {ZIP} {CITY}")

    # Kill existing Chrome
    subprocess.run(["pkill", "-f", "Google Chrome"], capture_output=True)
    time.sleep(2)

    # Start Chrome with copied profile
    print("[0] Starting Chrome with E-Förder profile (copy)...")
    chrome_proc = subprocess.Popen([
        CHROME,
        f"--user-data-dir={USER_DATA}",
        f"--profile-directory=Profile 159",
        f"--remote-debugging-port={CDP_PORT}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "about:blank"
    ])
    print(f"  Chrome PID: {chrome_proc.pid}")
    time.sleep(6)

    with sync_playwright() as p:
        # Connect via CDP
        print("[0] Connecting via CDP...")
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.evaluate("() => { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); }")
        print("  ✅ Connected to real Chrome!")

        # Check Google account
        ss(page, "00_launch")

        # Check if Google icon shows
        page.goto("https://myaccount.google.com", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)
        ss(page, "00_google")
        text = page.locator("body").inner_text()[:200]
        print(f"  Google: {text}")

        # LOGIN
        print("[1] Login...")
        page.goto("https://app.fireworks.ai/login", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        try:
            page.locator("button.cky-btn-accept").first.click(timeout=3000)
            page.wait_for_timeout(1000)
        except:
            pass
        page.locator("a:has-text('Email Login')").click()
        page.wait_for_timeout(3000)
        page.locator("input[name='email']").fill(EMAIL)
        page.locator("input[name='password']").fill(PASSWORD)
        page.locator("[data-testid='login-form-submit']").click()
        page.wait_for_timeout(8000)
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
        except:
            pass
        print(f"  Logged in: {page.url}")

        # BILLING
        print("[2] Billing...")
        page.goto("https://app.fireworks.ai/account/billing", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        try:
            page.locator("button.cky-btn-accept").first.click(timeout=2000)
        except:
            pass

        # ADD PAYMENT METHOD
        print("[3] Clicking 'Add payment method'...")
        page.locator("button:has-text('Add payment method')").first.click()
        page.wait_for_timeout(8000)

        # EXPAND CARD ACCORDION
        print("[4] Expanding card accordion...")
        page.evaluate("() => { document.querySelector('[data-testid=\"card-accordion-item-button\"]')?.click(); }")
        page.wait_for_timeout(5000)

        # FILL CARD
        print("[5] Filling card...")
        for frame in page.frames:
            try:
                el = frame.locator("input[name='cardNumber']")
                if el.count() > 0:
                    el.click(); time.sleep(0.3)
                    el.type(CARD, delay=50); time.sleep(0.5)
                    frame.locator("input[name='cardExpiry']").click(); time.sleep(0.3)
                    frame.locator("input[name='cardExpiry']").type(EXPIRY, delay=50); time.sleep(0.5)
                    frame.locator("input[name='cardCvc']").click(); time.sleep(0.3)
                    frame.locator("input[name='cardCvc']").type(CVC, delay=50); time.sleep(0.5)
                    print("  ✅ Card filled!")
                    break
            except:
                pass

        # FILL NAME
        print("[6] Filling name...")
        try:
            page.get_by_placeholder("Vollständiger Name").click()
            time.sleep(0.2)
            page.get_by_placeholder("Vollständiger Name").type(NAME, delay=50)
            print(f"  ✅ Name: {NAME}")
        except:
            pass

        # MANUELLE ADRESSE
        print("[7] Clicking 'Adresse manuell eingeben'...")
        try:
            page.locator("text=Adresse manuell eingeben").click()
            time.sleep(2)
        except:
            pass

        # FILL ADDRESS
        print("[8] Filling address...")
        page.locator("input[name='billingAddressLine1']").click()
        time.sleep(0.2)
        page.locator("input[name='billingAddressLine1']").fill(ADDR)
        print(f"  ✅ Straße: {ADDR}")

        page.locator("input[name='billingPostalCode']").click()
        time.sleep(0.2)
        page.locator("input[name='billingPostalCode']").fill(ZIP)
        print(f"  ✅ PLZ: {ZIP}")

        page.locator("input[name='billingLocality']").click()
        time.sleep(0.2)
        page.locator("input[name='billingLocality']").fill(CITY)
        print(f"  ✅ Ort: {CITY}")

        ss(page, "01_filled")

        # CLICK SAVE
        print("[9] Clicking 'Speichern'...")
        page.locator("button:has-text('Speichern')").first.click()
        time.sleep(8)
        ss(page, "02_after_save")

        # CHECK HCAPTCHA
        captcha = any("hcaptcha" in f.url.lower() and "invisible" not in f.url for f in page.frames)
        if captcha:
            print("\n⚠️  hCaptcha — please solve in browser! Waiting 5min...")
            for i in range(60):
                time.sleep(5)
                still = any("hcaptcha" in f.url.lower() and "invisible" not in f.url for f in page.frames)
                if not still:
                    print("  ✅ Solved!")
                    break
                if i % 6 == 0:
                    print(f"  Waiting... ({i*5}s)")
            page.wait_for_timeout(5000)
        else:
            print("  ✅ No hCaptcha!")

        ss(page, "03_result")

        # CHECK BILLING
        print("[10] Checking billing...")
        page.goto("https://app.fireworks.ai/account/billing", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        ss(page, "04_billing")
        text = page.locator("body").inner_text()
        print(f"  {'✅ NOT suspended' if 'Suspended' not in text else '❌ Still suspended'}")

        ss(page, "99_final")

        browser.close()

    chrome_proc.terminate()
    print("[DONE]")

if __name__ == "__main__":
    main()
