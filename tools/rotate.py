#!/usr/bin/env python3
"""
SINator - Rotation Tool V19 (SIN-Browser-Tools, 2026-06-01)

Fireworks flow via SIN-Browser-Tools. Bot Chrome bleibt GEÖFFNET bis API Key.
GMX flow in User Chrome (Profile 73, CDP).
OTP polling via User Chrome (GMX session).
"""
import sys
import os
import asyncio
import time
import logging
import argparse
import socket
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "agent_toolbox" / "core"))

logging.basicConfig(level=logging.DEBUG if os.environ.get("LOG_LEVEL") == "DEBUG" else logging.INFO, format='%(message)s')
logger = logging.getLogger("rotate")


def _find_free_port(start: int = 9230) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
    raise RuntimeError("No free port found")


async def main():
    parser = argparse.ArgumentParser(description="GMX + Fireworks Rotation V19")
    parser.add_argument("alias", nargs="?", help="Optional alias name")
    parser.add_argument("--gmx-email", help="GMX account email (required)")
    parser.add_argument("--gmx-password", help="GMX account password (required)")
    parser.add_argument("--password", help="Fireworks account password (required)")
    parser.add_argument("--save", action="store_true", default=True, help="Save API key to pool")
    parser.add_argument("--cdp-port", type=int, default=0, help="CDP port (0 = chromium.launch)")
    parser.add_argument("--debug", action="store_true", help="Enable DEBUG logging")
    args = parser.parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        for h in logging.getLogger().handlers:
            h.setLevel(logging.DEBUG)

    from agent_toolbox.core.config_manager import get_config
    cfg = get_config()
    if not args.gmx_email:
        args.gmx_email = cfg.gmx_email
    if not args.gmx_password:
        args.gmx_password = cfg.gmx_password
    if not args.password:
        args.password = cfg.fireworks_password

    t0 = time.time()

    from playwright.async_api import async_playwright
    p = await async_playwright().start()

    # ══════════════════════════════════════════════════════════════════
    # User Chrome (GMX)
    # ══════════════════════════════════════════════════════════════════
    gmx_browser = None
    ctx = None

    if args.cdp_port:
        logger.info(f"=== Connecting to User Chrome on CDP port {args.cdp_port} ===")
        gmx_browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{args.cdp_port}")
        logger.info("Connected to User Chrome")
    else:
        cdp_port = _find_free_port()
        docker_profile = os.environ.get("SIN_DOCKER_PROFILE", "")
        if docker_profile and os.path.isdir(docker_profile):
            logger.info(f"Docker mode: launching Chromium with persistent profile at {docker_profile}")
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=docker_profile,
                headless=False,
                channel="chromium",
                args=[
                    f'--remote-debugging-port={cdp_port}',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--password-store=basic',
                ]
            )
            gmx_browser = ctx  # persistent context IS the browser+context
        else:
            gmx_browser = await p.chromium.launch(
                headless=False,
                args=[
                    f'--remote-debugging-port={cdp_port}',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                ]
            )

    fw_mgr = None
    alias = None
    ctx = None
    work_tab = None
    inbox_tab = None
    created_ctx = False
    persistent_ctx = False  # True when using launch_persistent_context (Docker)
    try:
        from gmx_service import GmxService
        gmx = GmxService()

        # launch_persistent_context returns a BrowserContext, not a Browser
        docker_profile = os.environ.get("SIN_DOCKER_PROFILE", "")
        if docker_profile and os.path.isdir(docker_profile):
            # Docker: gmx_browser IS the context (from launch_persistent_context)
            ctx = gmx_browser
            persistent_ctx = True
            # Set viewport for coordinate-based consent click
            for page in ctx.pages:
                await page.set_viewport_size({"width": 1920, "height": 1080})
            logger.info(f"Using persistent context with {len(ctx.pages)} existing pages")
            if ctx.pages:
                work_tab = ctx.pages[0]
                logger.info(f"Reusing existing page: {work_tab.url[:60]}")
            else:
                work_tab = await ctx.new_page()
                await work_tab.set_viewport_size({"width": 1920, "height": 1080})
        elif args.cdp_port and gmx_browser.contexts:
            # CDP: use existing context
            ctx = gmx_browser.contexts[0]
            for page in ctx.pages:
                await page.set_viewport_size({"width": 1920, "height": 1080})
            logger.info(f"Using existing browser context ({len(ctx.pages)} pages)")
            if ctx.pages:
                work_tab = ctx.pages[0]
                logger.info(f"Reusing existing page: {work_tab.url[:60]}")
            else:
                work_tab = await ctx.new_page()
                await work_tab.set_viewport_size({"width": 1920, "height": 1080})
        else:
            ctx = await gmx_browser.new_context(
                viewport={"width": 1920, "height": 1080}
            )
            created_ctx = True
            work_tab = await ctx.new_page()
        gmx.work_tab = work_tab

        # Create SEPARATE inbox tab — alias rotation navigates work_tab away
        inbox_tab = await ctx.new_page()
        await inbox_tab.set_viewport_size({"width": 1920, "height": 1080})
        gmx.inbox_tab = inbox_tab
        logger.info(f"Created separate inbox tab: {inbox_tab.url[:60]}")
        await work_tab.bring_to_front()

        # Step 0: GMX Login (with retry — Bug 4 fix)
        logged_in = False
        # Bug 3 fix: check existing page URL first instead of blindly navigating
        current_url = work_tab.url or ""
        if "navigator.gmx.net/mail" in current_url and "login" not in current_url.lower():
            logger.info(f"GMX session active (existing page): {current_url[:60]}")
            logged_in = True
        else:
            # Try navigator.gmx.net/mail to check if session cookies are still valid
            await work_tab.goto("https://navigator.gmx.net/mail", wait_until="domcontentloaded")
            await asyncio.sleep(0.5)
            if "navigator.gmx.net/mail" in work_tab.url and "login" not in work_tab.url.lower():
                logger.info("GMX session active via cookie persistence")
                logged_in = True
            else:
                logger.info("GMX login required — attempting login (up to 2 retries)")
                for login_attempt in range(2):
                    if login_attempt > 0:
                        logger.info(f"Login retry {login_attempt + 1}/2 — waiting 5s before retry")
                        await asyncio.sleep(5)
                    logged_in = await gmx._login(work_tab, email=args.gmx_email, password=args.gmx_password)
                    if logged_in:
                        logger.info(f"GMX login succeeded on attempt {login_attempt + 1}")
                        break
                    logger.warning(f"GMX login attempt {login_attempt + 1} failed")
                if not logged_in:
                    logger.error("GMX Login failed after 2 attempts")
                    return

        sid_match = re.search(r"[?&]sid=([a-f0-9]{40,})", work_tab.url)
        gmx_sid = sid_match.group(1) if sid_match else None
        gmx_work_url = work_tab.url

        # Step 1: GMX Alias Rotation
        logger.info("=== GMX Alias Rotation ===")
        result = await gmx.rotate_alias(new_alias_name=args.alias, page=work_tab)
        if result.get('status') not in ('success', 'partial'):
            logger.error(f"GMX rotation failed: {result.get('error')}")
            return
        alias = result.get('created_alias')
        logger.info(f"GMX Alias: {alias}")
        if not alias:
            logger.error("No alias created")
            return

        # Step 3.5: Connect to existing Fireworks browser for billing
        logger.info("=== Billing via Existing Fireworks Browser ===")
        # The fw_mgr already has the authenticated Fireworks browser 
        # We'll use the existing page for billing instead of launching Chrome CDP

        launch_result = await launch()
        fw_mgr = launch_result.get("browser_manager")
        logger.info("Bot Chrome launched and registered with SIN-Browser-Tools")

        # Step 2: Fireworks Signup
        logger.info("=== Fireworks Signup ===")
        signup_result = await signup_fireworks(alias, args.password)
        steps_done = signup_result.get('steps_completed', [])
        logger.info(f"Signup: {signup_result.get('status')} - steps: {steps_done}")
        if signup_result.get('status') == 'error':
            logger.error(f"Signup failed: {signup_result.get('error')} — aborting")
            return
        if 'passwords_filled' not in steps_done or 'create_clicked' not in steps_done:
            logger.error(f"Signup incomplete (steps: {steps_done}) — no account created, aborting")
            return

        # Step 3: OTP Poll (User Chrome)
        logger.info("=== OTP Polling (User Chrome) ===")
        # Both tabs share the same BrowserContext → cookies from work_tab's login
        # are available to inbox_tab. Navigate to homepage, then click "Zum Postfach".
        # NEVER call _login() on inbox_tab — it opens a duplicate login window.
        await gmx.inbox_tab.bring_to_front()
        await gmx.inbox_tab.goto("https://www.gmx.net/", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        inbox_url = gmx.inbox_tab.url
        logger.info(f"Inbox tab homepage URL: {inbox_url[:80]}")
        
        # Click "Zum Postfach" to reach the webmail client
        postfach_clicked = False
        try:
            postfach = gmx.inbox_tab.locator('text=Zum Postfach')
            if await postfach.count() > 0 and await postfach.first.is_visible(timeout=5000):
                await postfach.first.click()
                postfach_clicked = True
                logger.info("Clicked 'Zum Postfach'")
        except Exception as e:
            logger.debug(f"Zum Postfach not found: {e}")
        
        if not postfach_clicked:
            # Fallback: navigate directly to navigator.gmx.net/mail
            await gmx.inbox_tab.goto("https://navigator.gmx.net/mail", wait_until="domcontentloaded")
            logger.info("Navigated to navigator.gmx.net/mail")
        
        await asyncio.sleep(5)
        inbox_url = gmx.inbox_tab.url
        logger.info(f"Inbox tab URL after navigation: {inbox_url[:80]}")

        # Wait for the mail frame (webmailer.gmx.net) to appear
        logger.info("Waiting for mail frame to load...")
        mail_frame_found = False
        for _wait in range(15):
            for f in gmx.inbox_tab.frames:
                if f.name == "mail" or "webmailer.gmx.net" in (f.url or ""):
                    mail_frame_found = True
                    logger.info(f"Mail frame found: {f.url[:60]}")
                    break
            if mail_frame_found:
                break
            await asyncio.sleep(1)
        if not mail_frame_found:
            logger.warning("Mail frame not found after 15s, proceeding anyway...")

        verify_ok = False
        otp_url = None
        try:
            otp_result = await gmx.read_otp_main_frame_only(sender_keyword="fireworks", timeout=80)
            otp_url = otp_result.get("otp_url")
        except AttributeError:
            logger.info("Fallback to CDP AXTree OTP scanner")
            otp_result = await gmx.read_otp_cdp_axtree(sender_keyword="fireworks", timeout=80)
            otp_url = otp_result.get("otp_url")

        if otp_url:
            logger.info(f"OTP-URL: {otp_url[:60]}")
            verify_ok = await verify_account(otp_url)
            logger.info(f"Verify: {'OK' if verify_ok else 'Failed'}")
        else:
            logger.warning(f"OTP nicht gefunden: {otp_result.get('error')}")

        # Step 4: Login after verify (session doesn't persist from signup)
        logger.info("Waiting 3s for account activation after verify...")
        await asyncio.sleep(3)

        logger.info("=== Login after verify ===")
        login_result = await login_fireworks(alias, args.password)
        logger.info(f"Login: {login_result.get('status')} - steps: {login_result.get('steps_completed')}")
        if login_result.get('status') != 'success':
            logger.error(f"Login failed after verify: {login_result.get('error')}")
            return

        # Step 4.5: Credit check + billing fallback
        logger.info("=== Checking Credits ===")
        credit_result = await check_credits()
        credits = credit_result.get("credits", 0.0)
        has_credits = credit_result.get("has_credits", False)
        logger.info(f"Credits: ${credits:.2f} (has_credits={has_credits})")

        if not has_credits:
            logger.info("No free credits — attempting billing step via Chrome CDP...")
            billing_result = await add_billing(alias, args.password)
            billing_status = billing_result.get("status")
            logger.info(f"Billing result: {billing_status} — {billing_result.get('message', billing_result.get('error', ''))}")
            if billing_status == "hcaptcha_pending":
                logger.warning("hCaptcha detected — manual intervention may be needed")
        else:
            logger.info(f"Free credits available (${credits:.2f}) — skipping billing")

        # Step 5: API Key
        logger.info("=== API Key ===")
        key_name = alias.split("@")[0].split("-")[0] if alias else "sinator-key"
        api_result = await create_api_key(key_name=key_name)
        api_key = api_result.get("api_key")

        if not api_key:
            logger.error(f"API Key creation failed: {api_result.get('error')}")
            return

        logger.info(f"API Key: {api_key}")

        # Step 6: Save to pool
        if args.save:
            try:
                from pool_manager import PoolManager
                pool = PoolManager()
                pool.add_key(api_key=api_key, alias_email=alias, key_name=key_name)
                logger.info(f"Saved to pool ({pool.get_stats()['total']} keys total)")

                # Step 7: Notify SINator backend to reload pool
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        "http://localhost:8000/api/v1/pool/reload",
                        method="POST",
                    )
                    resp = urllib.request.urlopen(req, timeout=5)
                    logger.info(f"Backend pool reloaded: {resp.status}")
                except Exception as reload_err:
                    logger.warning(f"Backend pool reload failed (non-critical): {reload_err}")
            except Exception as e:
                logger.warning(f"Pool save skipped: {e}")

    finally:
        elapsed = time.time() - t0
        logger.info("=== Shutdown ===")
        if fw_mgr:
            logger.info("Closing Bot Chrome (Fireworks)")
            await cleanup_bot(fw_mgr)
        # Close inbox_tab and work_tab
        for tab in [inbox_tab, work_tab]:
            if tab and not args.cdp_port:
                try:
                    await tab.close()
                except Exception:
                    pass
        if created_ctx and ctx:
            try:
                await ctx.close()
            except Exception:
                pass
        if gmx_browser:
            if args.cdp_port:
                logger.info("Disconnecting from User Chrome (CDP — NOT closing)")
            elif persistent_ctx:
                logger.info("Closing persistent Chrome context (Docker)")
                await gmx_browser.close()
            else:
                logger.info("Closing User Chrome (GMX)")
                await gmx_browser.close()
        await p.stop()
        logger.info(f"\nROTATION COMPLETE - {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
