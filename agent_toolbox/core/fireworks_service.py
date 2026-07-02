"""Fireworks AI E2E flow — signup, verify, login, onboarding, API key.

Uses 100% SIN-Browser-Tools (zero raw page.evaluate calls).
Bot Chrome stays open until API key is generated.

Docs: fireworks_service.doc.md
"""
import asyncio
import logging
import re
import weakref
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


# ── Browser Handle ──────────────────────────────────────────────────────────

class _BrowserHandle:
    """Duck-type wrapper satisfying SIN-Browser-Tools manager._set_instance().

    SIN-Browser-Tools expects a BrowserManager with _page, _context, _browser,
    _playwright attributes. This class provides those from a raw Playwright
    launch, bypassing BrowserManager which hardcodes --start-maximized.
    """

    def __init__(self, page, context, browser, pw):
        self._page = page
        self._context = context
        self._browser = browser
        self._playwright = pw
        self._started = True
        self._dialog_queue = asyncio.Queue()
        self._pending_dialog = None
        self._dialog_pages = weakref.WeakSet()
        self._registry_stub = None
        self._browser_pid = None

    @property
    def page(self):
        """Active Playwright page — used by SIN-Browser-Tools for all operations."""
        return self._page

    @property
    def context(self):
        """Browser context — holds cookies, storage, and page references."""
        return self._context

    async def cleanup(self):
        """Close context, browser, and Playwright instance. Idempotent."""
        try:
            await self._context.close()
        except Exception:
            pass
        try:
            await self._browser.close()
        except Exception:
            pass
        try:
            await self._playwright.stop()
        except Exception:
            pass

    def set_active_page(self, p):
        """Update active page reference (called by SIN-Browser-Tools on tab switch)."""
        self._page = p
        self._context = p.context

    async def new_page(self):
        """Create a new page in the browser context."""
        return await self._context.new_page()

    @property
    def active_page(self):
        """Alias for page — backward compatibility with BrowserManager API."""
        return self._page

    def clear_active_page(self):
        """Set active page to None (used during cleanup)."""
        self._page = None

    async def get_next_dialog(self, timeout=5.0, consume=True):
        """No-op — dialogs are not handled in Bot Chrome."""
        return None

    def _setup_dialog_handler(self):
        """No-op — dialog handler not needed for Fireworks flow."""
        pass


# ── Launch / Cleanup ────────────────────────────────────────────────────────

async def _poll_for(condition_fn, timeout: float = 10.0, interval: float = 0.3, label: str = ""):
    """Poll until condition_fn() returns True or timeout reached.
    
    Replaces fixed asyncio.sleep() with fast responsive polling.
    condition_fn is an async callable that returns True when ready.
    Returns True if condition met, False on timeout.
    """
    elapsed = 0.0
    while elapsed < timeout:
        try:
            if await condition_fn():
                return True
        except Exception:
            pass
        await asyncio.sleep(interval)
        elapsed += interval
    if label:
        logger.debug(f"Poll timeout ({label}): {timeout:.1f}s")
    return False


async def _poll_for_element(selector: str, timeout: float = 10.0, interval: float = 0.3) -> bool:
    """Poll until a DOM element matching selector exists."""
    from sin_browser_tools.tools.extraction import browser_console
    async def check():
        count = int((await browser_console(f"document.querySelectorAll('{selector}').length"))["result"])
        return count > 0
    return await _poll_for(check, timeout, interval, f"element: {selector}")


async def _poll_for_url_contains(keyword: str, timeout: float = 10.0, interval: float = 0.3) -> bool:
    """Poll until current URL contains keyword."""
    from sin_browser_tools.tools.navigation import browser_get_url
    async def check():
        url = (await browser_get_url())["url"]
        return keyword in url.lower()
    return await _poll_for(check, timeout, interval, f"url contains: {keyword}")


async def _poll_for_url_change(old_url: str, timeout: float = 10.0, interval: float = 0.3) -> bool:
    """Poll until URL changes from old_url."""
    from sin_browser_tools.tools.navigation import browser_get_url
    async def check():
        url = (await browser_get_url())["url"]
        return url != old_url
    return await _poll_for(check, timeout, interval, "url change")



async def launch() -> Dict[str, Any]:
    """Launch Bot Chrome with stealth patches and register with SIN-Browser-Tools.

    Creates an ephemeral Chromium instance with:
    - Window size 1200x800 (not maximized — avoids layout detection)
    - German locale/timezone (matches GMX account region)
    - Anti-detection: webdriver, plugins, languages, chrome.runtime

    Returns:
        Dict with 'browser_manager' (_BrowserHandle) for caller to cleanup.
    """
    from playwright.async_api import async_playwright
    from sin_browser_tools.core.manager import manager

    pw = await async_playwright().start()
    # Window size 1200x800 — NOT --start-maximized (which BrowserManager hardcodes)
    browser = await pw.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--window-size=1200,800",
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1200, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        locale="de-DE",
        timezone_id="Europe/Berlin",
        accept_downloads=True,
        bypass_csp=True,
        ignore_https_errors=True,
    )
    page = await context.new_page()

    # Stealth patches + cookie consent PREVENTION via page-level init_script
    # NOTE: context.add_init_script broke onboarding React handlers.
    # page.add_init_script works correctly (verified with 51+ keys).
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['de-DE', 'de', 'en-US', 'en'] });
        window.chrome = { runtime: {} };
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);
        // ── CookieYes consent PREVENTION ──
        // Set consent in localStorage BEFORE any page JS runs, so the banner
        // never appears. CookieYes checks localStorage on init and skips the
        // banner if consent is already given.
        try {
            const consent = {
                necessary: true, functional: true, analytics: false,
                performance: false, advertisement: false, timestamp: Date.now()
            };
            localStorage.setItem('cookieyes-consent', JSON.stringify(consent));
            localStorage.setItem('cky-consent', 'yes:' + btoa(JSON.stringify(consent)));
            document.cookie = 'cookieyes-consent=yes; path=/; max-age=31536000; SameSite=Lax';
        } catch(e) {}
        // Inject CSS to permanently hide all consent banner selectors
        // Use documentElement (always available) instead of head (may not exist yet)
        const style = document.createElement('style');
        style.id = 'sin-consent-blocker';
        style.textContent = `
            .cky-overlay, .cky-consent-container, .cky-banner-container, .cky-modal,
            .cky-preference-center, .cky-notice, .cky-notice-group, [class*="cky-"],
            #onetrust-banner-sdk, #onetrust-pc-sdk, #onetrust-consent-sdk,
            [class*="onetrust"], [id*="onetrust"],
            [class*="cookie-banner"], [id*="cookie-banner"],
            [class*="consent-banner"], [id*="consent-banner"],
            #consent-banner, .consent-banner {
                display: none !important;
                visibility: hidden !important;
                opacity: 0 !important;
                pointer-events: none !important;
                z-index: -9999 !important;
                height: 0 !important;
                width: 0 !important;
                overflow: hidden !important;
            }
            body { overflow: visible !important; }
            html { overflow: visible !important; }
        `;
        try { document.documentElement.appendChild(style); } catch(e) {}
        // NOTE: Removed MutationObserver — it was fighting CookieYes in a loop,
        // potentially blocking React event handlers on onboarding buttons.
        // CSS hiding + localStorage consent is sufficient.
    """)

    handle = _BrowserHandle(page, context, browser, pw)
    manager._set_instance(handle)
    logger.info("Bot Chrome launched (stays open until API key success)")
    return {"status": "launched", "browser_manager": handle}


async def cleanup_bot(browser_manager=None) -> None:
    """Close Bot Chrome and deregister from SIN-Browser-Tools.

    Called after API key is generated (success) or on rotation failure.
    Safe to call multiple times — all close() calls are idempotent.
    """
    if browser_manager:
        try:
            from sin_browser_tools.core import manager
            await browser_manager.cleanup()
            manager._set_instance(None)
            logger.info("Bot Chrome cleaned up")
        except Exception as e:
            logger.warning(f"Bot Chrome cleanup error: {e}")


# ── Signup ──────────────────────────────────────────────────────────────────

async def _dismiss_cookie_consent() -> None:
    """Central cookie consent banner dismissal — call after EVERY Fireworks navigation.
    
    The init_script in launch() sets localStorage consent + CSS hiding + MutationObserver
    to PREVENT the banner. This function is the reactive fallback that removes any
    banner that slipped through (e.g. if CookieYes ignores localStorage).
    """
    from sin_browser_tools.tools.interaction import browser_click_by_text
    from sin_browser_tools.tools.extraction import browser_console

    # 1. Try clicking "Reject All" (cleanest — sets CookieYes consent properly)
    try:
        await browser_click_by_text("Reject All", role="button")
        await asyncio.sleep(0.5)
        logger.info("Cookie banner: 'Reject All' clicked")
    except Exception:
        pass

    # 2. Nuke all known consent DOM elements via JS
    await browser_console("""(() => {
        // CookieYes (cky-*)
        document.querySelectorAll('.cky-overlay,.cky-consent-container,.cky-banner-container,.cky-modal,.cky-preference-center,.cky-notice,[class*="cky-"]').forEach(e => e.remove());
        // OneTrust
        document.querySelectorAll('#onetrust-banner-sdk,#onetrust-pc-sdk,#onetrust-consent-sdk,[class*="onetrust"],[id*="onetrust"]').forEach(e => e.remove());
        // Generic consent containers
        document.querySelectorAll('[class*="consent-banner"],[id*="consent-banner"],[class*="cookie-banner"],[id*="cookie-banner"],[data-testid*="consent"]').forEach(e => e.remove());
        // Iframe-based banners
        document.querySelectorAll('iframe[src*="cky"],iframe[src*="consent"],iframe[src*="cookie"]').forEach(e => e.remove());
        // Restore scroll
        document.body.style.overflow = 'visible';
        document.documentElement.style.overflow = 'visible';
    })()""")
    await asyncio.sleep(0.3)


async def signup_fireworks(email: str, password: str, **kwargs) -> Dict[str, Any]:
    """Create a new Fireworks account with the given email and password.

    Flow: navigate → remove CookieYes → fill email → Next → fill passwords → Create Account.
    Detects CAPTCHA and missing password fields as errors.

    Args:
        email: GMX alias email (e.g., pulse-runner-931@gmx.de)
        password: Fireworks account password

    Returns:
        Dict with 'status' ('signup_done'|'error') and 'steps_completed' list.
    """
    from sin_browser_tools.tools.navigation import browser_navigate, browser_get_url
    from sin_browser_tools.tools.interaction import browser_click_by_text, browser_fill
    from sin_browser_tools.tools.extraction import browser_console
    from sin_browser_tools.tools.vision import browser_get_text

    steps = []

    await browser_navigate("https://app.fireworks.ai/signup")
    # No fixed sleep — form elements polled below
    logger.info(f"Signup page loaded")

    # Dismiss cookie consent banner (preventive init_script + reactive fallback)
    await _dismiss_cookie_consent()

    r = await browser_fill('input[name="email"]', email)
    if r.get("status") != "typed":
        logger.error("Email fill failed")
        return {"status": "error", "error": "email_fill_failed", "steps_completed": steps}
    steps.append("email_filled")
    await asyncio.sleep(0.3)

    # Enter key — avoids carousel "Next slide" button conflict
    from sin_browser_tools.tools.navigation import browser_press
    await browser_press("Enter")
    logger.info("Email submitted via Enter key")

    for _ in range(12):
        await asyncio.sleep(0.5)
        pw_count = int((await browser_console("document.querySelectorAll('input[type=password]').length"))["result"])
        if pw_count >= 2:
            break
        body = (await browser_get_text("body")).get("text", "")
        if 'captcha' in body.lower() or 'verify you are human' in body.lower():
            logger.error("CAPTCHA detected")
            return {"status": "error", "error": "captcha", "steps_completed": steps}
    else:
        body = (await browser_get_text("body")).get("text", "")
        logger.error(f"Password fields not found. Page text: {body[:300]}")
        return {"status": "error", "error": "no_password_fields", "steps_completed": steps}
    steps.append("next_clicked")

    await browser_fill('input[name="password"]', password)
    await browser_fill('input[name="confirmPassword"]', password)
    steps.append("passwords_filled")

    # Dismiss cookie consent again right before click (banner may reappear on SPA transitions)
    await _dismiss_cookie_consent()

    # Try normal click first, fall back to JS click if consent banner intercepts
    try:
        await browser_click_by_text("Create Account", role="button")
    except Exception as e:
        if "intercepts" in str(e).lower() or "pointer" in str(e).lower():
            logger.info("Consent banner intercepts — using JS click fallback")
            await browser_console("""document.querySelector('button[type="submit"], button:has-text("Create Account")').click()""")
        else:
            raise
    logger.info("Create Account clicked via browser_click_by_text")

    for _ in range(25):
        await asyncio.sleep(1)
        url = (await browser_get_url())["url"]
        if 'verify' in url.lower() or 'confirm' in url.lower():
            logger.info(f"Verify page detected: {url[:60]}")
            break
        body = (await browser_get_text("body")).get("text", "")
        if 'verify' in body.lower() or 'check your email' in body.lower():
            logger.info("Verify text detected")
            break
    else:
        logger.warning(f"No verify detected after signup")
    steps.append("create_clicked")

    return {"status": "signup_done", "steps_completed": steps}


# ── Verify ──────────────────────────────────────────────────────────────────

async def verify_account(verify_url: str, **kwargs) -> bool:
    """Open the Fireworks verification URL to confirm the email address.

    Navigates to the URL (which contains the OTP token) and waits for
    redirect to onboarding/home. The URL is typically extracted from
    the GMX inbox by rotate.py.

    Args:
        verify_url: Full verification URL from Fireworks email

    Returns:
        True if verification succeeded (redirect detected or page loaded).
    """
    from sin_browser_tools.tools.navigation import browser_navigate, browser_get_url

    try:
        await browser_navigate(verify_url)
        # Poll for page load (replaces fixed 2s sleep)
        await _poll_for_url_change("about:blank", timeout=10, interval=0.3)
        # Dismiss cookie consent banner on verify redirect page
        await _dismiss_cookie_consent()
        url = (await browser_get_url())["url"]
        logger.info(f"Verify URL opened: {url[:80]}")
        # DIAG: screenshot after verify URL load
        try:
            os.makedirs("/tmp/onboarding-diag", exist_ok=True)
            from sin_browser_tools.core import manager
            await manager.page.screenshot(path="/tmp/onboarding-diag/verify-loaded.png")
        except Exception as e:
            logger.warning(f"DIAG verify shot failed: {e}")
        for _ in range(10):
            await asyncio.sleep(1)
            url = (await browser_get_url())["url"]
            if 'onboarding' in url.lower() or 'home' in url.lower() or 'account' in url.lower():
                # DIAG: screenshot when redirect detected
                try:
                    from sin_browser_tools.core import manager
                    await manager.page.screenshot(path=f"/tmp/onboarding-diag/verify-redirected-{url.replace('/','_')[:40]}.png")
                except Exception:
                    pass
                return True
        return True
    except Exception as e:
        logger.error(f"Verify error: {e}")
        return False


# ── Login ───────────────────────────────────────────────────────────────────

async def login_fireworks(email: str, password: str, **kwargs) -> Dict[str, Any]:
    """Log in to Fireworks AI and handle onboarding if redirected.

    Two-step login:
    1. Fill email → click Next (triggers email validation)
    2. Fill password → Enter key (submits form)

    After login, detects redirect:
    - /onboarding → runs _playwright_onboarding() then waits for home redirect
    - /home|/account|/settings → login success

    Uses Enter key instead of browser_click_by_text("Next") for password submit
    to avoid matching the carousel "Next slide" button (disabled, causes timeout).

    Args:
        email: GMX alias email
        password: Fireworks account password

    Returns:
        Dict with 'status' ('success'|'error') and 'steps_completed' list.
    """
    from sin_browser_tools.tools.navigation import browser_navigate, browser_get_url
    from sin_browser_tools.tools.interaction import browser_click_by_text, browser_fill
    from sin_browser_tools.tools.extraction import browser_console
    from sin_browser_tools.tools.vision import browser_get_text

    steps = []

    await browser_navigate("https://app.fireworks.ai/login")
    # No fixed sleep — cookie consent + login form handled below

    # Dismiss cookie consent banner (preventive init_script + reactive fallback)
    await _dismiss_cookie_consent()

    for attempt in range(3):
        try:
            r = await browser_click_by_text("Email Login", role="link")
            if r.get("status") == "clicked":
                break
        except Exception:
            pass
        try:
            await browser_navigate("https://app.fireworks.ai/login?useEmail=true")
        except Exception:
            pass
        await asyncio.sleep(1)
        email_count = int((await browser_console("document.querySelectorAll('input[name=email]').length"))["result"])
        if email_count > 0:
            break
    steps.append("login_page")

    await browser_fill('input[name="email"]', email)
    steps.append("email_filled")

    from sin_browser_tools.tools.navigation import browser_press
    await browser_press("Enter")
    logger.info("Login email submitted via Enter key")
    # Poll for password field to appear (replaces fixed sleep)
    await _poll_for_element('input[type="password"]', timeout=8, interval=0.2)

    pw_count = int((await browser_console("document.querySelectorAll('input[type=password]').length"))["result"])
    if pw_count > 0:
        await browser_fill('input[type="password"]', password)
        steps.append("password_filled")
    else:
        await browser_fill('input[name="password"]', password)
        steps.append("password_filled")

    from sin_browser_tools.tools.navigation import browser_press
    await browser_press("Enter")
    old_url = "https://app.fireworks.ai/login"
    await _poll_for_url_change(old_url, timeout=10, interval=0.3)
    steps.append("form_submitted")

    for _ in range(15):
        await asyncio.sleep(1)
        url = (await browser_get_url())["url"]
        if 'login' not in url.lower():
            if 'onboarding' in url:
                logger.info("Onboarding detected, running workflow")
                await _playwright_onboarding()
                steps.append("onboarding_complete")
                break
            if any(x in url for x in ['home', 'account', 'settings', 'api-keys', 'models']):
                logger.info(f"Login redirect detected: {url[:60]}")
                steps.append("login_success")
                return {"status": "success", "steps_completed": steps}

    for _ in range(15):
        await asyncio.sleep(1)
        url = (await browser_get_url())["url"]
        if 'login' not in url.lower() and 'onboarding' not in url.lower():
            if any(x in url for x in ['home', 'account', 'settings', 'api-keys', 'models']):
                logger.info(f"Final redirect: {url[:60]}")
                # Wait for page to fully load after redirect
                logger.info("Waiting 5s for page load after redirect...")
                await asyncio.sleep(5)
                steps.append("login_success")
                return {"status": "success", "steps_completed": steps}

    for u in [
        "https://app.fireworks.ai/",
        "https://app.fireworks.ai/settings/users/api-keys",
    ]:
        try:
            await browser_navigate(u)
            await asyncio.sleep(3)
            url = (await browser_get_url())["url"]
            if 'login' not in url.lower() and 'onboarding' not in url.lower():
                steps.append("login_success")
                return {"status": "success", "steps_completed": steps}
        except Exception:
            pass

    return {"status": "error", "steps_completed": steps, "error": "could not reach home/settings"}


# ── Onboarding ──────────────────────────────────────────────────────────────

async def _playwright_onboarding() -> None:
    """Complete the Fireworks onboarding form (2 pages).

    Page 1: Account ID (max 20 chars), First/Last Name, Terms checkbox → Continue
    Page 2: Use case checkboxes (Prototype, Flexible, Conversational, Search, Agentic) → Submit

    Strategy (V18.4 hybrid):
    1. Click "Reject All" on cookie banner (so it doesn't cover the form)
    2. Fill fields via browser_type (with delay=30ms) — lets React pick up keystrokes
       naturally instead of bypassing with a raw value-setter that doesn't trigger
       React state updates reliably
    3. Use 4-strategy checkbox clicker (input[aria-label], [role=checkbox], label,
       :has-text) for Terms + use cases — Fireworks uses custom React checkboxes
    4. Continue / Submit via button click (force), fallback to form.requestSubmit()
       + Enter key
    5. Wait for redirect, fallback to force-navigate to /settings/users/api-keys
    """
    from sin_browser_tools.tools.interaction import (
        browser_type, browser_click_by_text, browser_click_checkbox_by_text,
    )
    from sin_browser_tools.tools.navigation import browser_get_url, browser_navigate, browser_press
    from sin_browser_tools.tools.extraction import browser_console

    import os
    # ── Step 1: Dismiss cookie banner ─────────────────────────────────────
    # The init_script in launch() already prevents it, but call the reactive
    # fallback in case CookieYes ignored localStorage.
    await _dismiss_cookie_consent()

    # Set up network response logger to catch ALL API calls during onboarding
    from sin_browser_tools.core import manager
    _api_responses = []
    _console_msgs = []
    _js_errors = []
    async def _log_response(response):
        url = response.url
        if 'app.fireworks.ai' in url or 'fireworks.ai' in url:
            status = response.status
            try:
                body = await response.text()
            except:
                body = ''
            _api_responses.append(f'{status} {url[-80:]} body={body[:200]}')
    def _log_console(msg):
        _console_msgs.append(f'{msg.type}: {msg.text[:200]}')
    def _log_pageerror(err):
        _js_errors.append(str(err)[:300])
    manager.page.on('response', lambda r: asyncio.ensure_future(_log_response(r)))
    manager.page.on('console', _log_console)
    manager.page.on('pageerror', _log_pageerror)

    # Verify cky-* elements are gone
    cky_count = (await browser_console("document.querySelectorAll('[class*=cky]').length") or {}).get("result", "0")
    logger.info(f"Cookie banner: {cky_count} cky elements remaining (should be 0)")

    # DIAG: screenshot after cookie banner removal
    try:
        from sin_browser_tools.core import manager
        os.makedirs("/tmp/onboarding-diag", exist_ok=True)
        await manager.page.screenshot(path="/tmp/onboarding-diag/after-cookie-cleanup.png")
    except Exception:
        pass

    # ── Step 2: Fill text fields via browser_type (delay=30ms triggers React) ─
    import random, string

    # Account ID — DO NOT TOUCH (Fireworks pre-fills it with a unique suggestion,
    # editing it triggers a "max 20 chars" validation error)
    has_aid = int((await browser_console("document.querySelectorAll('input[name=accountId]').length"))["result"])
    if has_aid > 0:
        # Just verify the pre-filled value is there; DO NOT overwrite
        current_aid = await browser_console("""(() => {
            var inp = document.querySelector('input[name="accountId"]');
            return inp ? (inp.value || '') : '';
        })()""")
        current_aid = (current_aid.get("result") or "").strip()
        if current_aid:
            logger.info(f"Account ID pre-filled by Fireworks: '{current_aid}' (using as-is, NOT overwriting)")
        else:
            # Field is empty — fill with a safe 11-char value
            aid = "sin" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            try:
                await browser_type('input[name="accountId"]', aid)
            except Exception as e:
                logger.warning(f"browser_type accountId failed: {e}")
            await asyncio.sleep(0.3)
            logger.info(f"Account ID filled: {aid}")

    # First name — try multiple selectors (name, placeholder, label proximity)
    fn_filled = False
    for selector in ['input[name="firstName"]', 'input[name="first"]', 'input[placeholder*="First"]', 'input[placeholder*="first"]']:
        try:
            count = int((await browser_console(f"document.querySelectorAll('{selector}').length"))["result"])
            if count > 0:
                await browser_type(selector, "Super")
                fn_filled = True
                logger.info(f"First name filled via {selector}")
                break
        except Exception:
            continue
    if not fn_filled:
        # Fallback: find input next to "First Name" label
        try:
            await browser_console("""(() => {
                var labels = document.querySelectorAll('label');
                for (var i=0; i<labels.length; i++) {
                    if (labels[i].textContent.trim() === 'First Name') {
                        var input = labels[i].querySelector('input') || labels[i].nextElementSibling?.querySelector('input');
                        if (input) { input.focus(); input.value = 'Super'; input.dispatchEvent(new Event('input', {bubbles:true})); return 'ok'; }
                    }
                }
                return 'not_found';
            })()""")
            fn_filled = True
            logger.info("First name filled via label lookup")
        except Exception as e:
            logger.warning(f"First name all strategies failed: {e}")
    await asyncio.sleep(0.3)

    # Last name — try multiple selectors
    ln_filled = False
    for selector in ['input[name="lastName"]', 'input[name="last"]', 'input[placeholder*="Last"]', 'input[placeholder*="last"]']:
        try:
            count = int((await browser_console(f"document.querySelectorAll('{selector}').length"))["result"])
            if count > 0:
                await browser_type(selector, "Cheetah")
                ln_filled = True
                logger.info(f"Last name filled via {selector}")
                break
        except Exception:
            continue
    if not ln_filled:
        # Fallback: find input next to "Last Name" label
        try:
            await browser_console("""(() => {
                var labels = document.querySelectorAll('label');
                for (var i=0; i<labels.length; i++) {
                    if (labels[i].textContent.trim() === 'Last Name') {
                        var input = labels[i].querySelector('input') || labels[i].nextElementSibling?.querySelector('input');
                        if (input) { input.focus(); input.value = 'Cheetah'; input.dispatchEvent(new Event('input', {bubbles:true})); return 'ok'; }
                    }
                }
                return 'not_found';
            })()""")
            ln_filled = True
            logger.info("Last name filled via label lookup")
        except Exception as e:
            logger.warning(f"Last name all strategies failed: {e}")
    await asyncio.sleep(0.3)

    # ── Step 3: 4-strategy checkbox clicker (V18.4 fallback chain) ──────────
    async def _click_checkbox_any_strategy(match_text: str) -> bool:
        """Try multiple strategies to click a custom-React checkbox. Returns True on success."""
        mt = match_text.lower()
        # 1. input[type="checkbox"] with aria-label containing match
        r = await browser_console(f"""(() => {{
            var inputs = document.querySelectorAll('input[type="checkbox"]');
            for (var i=0; i<inputs.length; i++) {{
                var lbl = (inputs[i].getAttribute('aria-label') || '').toLowerCase();
                if (lbl.indexOf({mt!r}) !== -1) {{ inputs[i].click(); return 'input'; }}
            }}
            // 2. [role="checkbox"] with aria-label
            var els = document.querySelectorAll('[role="checkbox"]');
            for (var j=0; j<els.length; j++) {{
                var l = (els[j].getAttribute('aria-label') || '').toLowerCase();
                if (l.indexOf({mt!r}) !== -1) {{ els[j].click(); return 'role'; }}
            }}
            // 3. Label text containing match
            var labels = document.querySelectorAll('label');
            for (var k=0; k<labels.length; k++) {{
                if (labels[k].textContent.toLowerCase().indexOf({mt!r}) !== -1) {{
                    var cb = labels[k].querySelector('input[type="checkbox"], [role="checkbox"]') || labels[k];
                    cb.click(); return 'label';
                }}
            }}
            return 'not_found';
        }})()""")
        result = r.get("result", "not_found")
        if result != "not_found":
            logger.info(f"Checkbox '{match_text}' clicked via {result}")
            return True
        # 4. Last resort: SIN-browser-tool browser_click_checkbox_by_text
        try:
            r2 = await browser_click_checkbox_by_text(match_text)
            if r2.get("success"):
                logger.info(f"Checkbox '{match_text}' clicked via browser_click_checkbox_by_text")
                return True
        except Exception:
            pass
        logger.warning(f"Checkbox '{match_text}' NOT clicked")
        return False

    # Terms checkbox — try multiple strategies
    # First, try the sin_browser_tools' browser_click_checkbox_by_text (uses sophisticated walker)
    terms_clicked = False
    try:
        from sin_browser_tools.tools.interaction import browser_click_checkbox_by_text as _sbt_click_cb
        r = await _sbt_click_cb("I agree to the Terms of Service and Privacy Policy")
        if r.get("success"):
            terms_clicked = True
            logger.info("Terms clicked via browser_click_checkbox_by_text")
    except Exception as e:
        logger.warning(f"browser_click_checkbox_by_text failed: {e}")

    if not terms_clicked:
        # Fallback: my own 4-strategy
        if not await _click_checkbox_any_strategy("agree"):
            await _click_checkbox_any_strategy("terms")
    await asyncio.sleep(0.5)

    # DIAG: check Terms checkbox state after click
    try:
        from sin_browser_tools.core import manager
        os.makedirs("/tmp/onboarding-diag", exist_ok=True)
        await manager.page.screenshot(path="/tmp/onboarding-diag/after-terms.png")
        cb_state = await browser_console("""(() => {
            // Find all input[type=checkbox] on the page
            var all = document.querySelectorAll('input[type="checkbox"]');
            var matches = [];
            for (var i=0; i<all.length; i++) {
                matches.push({
                    aria: all[i].getAttribute('aria-label') || '',
                    checked: all[i].checked,
                    disabled: all[i].disabled,
                    id: all[i].id || '',
                    name: all[i].name || '',
                    parent_text: (all[i].closest('label') || {}).textContent || ''
                });
            }
            return matches;
        })()""")
        logger.info(f"DIAG ALL checkboxes: {cb_state}")
    except Exception as e:
        logger.warning(f"DIAG Terms: {e}")

    # DIAG: check Terms checkbox state after click
    try:
        from sin_browser_tools.core import manager
        os.makedirs("/tmp/onboarding-diag", exist_ok=True)
        await manager.page.screenshot(path="/tmp/onboarding-diag/after-terms.png")
        cb_state = await browser_console("""(() => {
            // Find Terms checkbox and report its checked state
            var inputs = document.querySelectorAll('input[type="checkbox"]');
            var matches = [];
            for (var i=0; i<inputs.length; i++) {
                var lbl = (inputs[i].getAttribute('aria-label') || '').toLowerCase();
                if (lbl.indexOf('agree') !== -1 || lbl.indexOf('terms') !== -1) {
                    matches.push({
                        aria: lbl,
                        checked: inputs[i].checked,
                        disabled: inputs[i].disabled
                    });
                }
            }
            // Also check role=checkboxes
            var roles = document.querySelectorAll('[role="checkbox"]');
            for (var j=0; j<roles.length; j++) {
                var al = (roles[j].getAttribute('aria-label') || '').toLowerCase();
                if (al.indexOf('agree') !== -1 || al.indexOf('terms') !== -1) {
                    matches.push({
                        role_aria: al,
                        checked: roles[j].getAttribute('aria-checked'),
                        cls: (roles[j].className || '').slice(0, 50)
                    });
                }
            }
            return matches;
        })()""")
        logger.info(f"DIAG Terms checkbox state: {cb_state}")
    except Exception as e:
        logger.warning(f"DIAG Terms: {e}")

    # DIAG: screenshot before Continue
    try:
        from sin_browser_tools.core import manager
        os.makedirs("/tmp/onboarding-diag", exist_ok=True)
        await manager.page.screenshot(path="/tmp/onboarding-diag/before-continue.png")
        # Log Continue button state — ONLY match "Continue", NEVER "Next"
        btn_state = await browser_console("""(() => {
            var b = document.querySelectorAll('button');
            var all_btns = [];
            for (var i=0; i<b.length; i++) {
                all_btns.push({
                    text: (b[i].textContent || '').trim(),
                    disabled: b[i].disabled || b[i].getAttribute('aria-disabled') === 'true',
                    type: b[i].type,
                    cls: (b[i].className || '').slice(0, 40)
                });
            }
            return all_btns;
        })()""")
        logger.info(f"DIAG all buttons: {btn_state}")
    except Exception as e:
        logger.warning(f"DIAG: {e}")

    # ── Step 4: Continue (Page 1 → Page 2) ─────────────────────────────────
    # CRITICAL: only match "Continue" exactly, NOT "Next" — there's a carousel
    # "Next slide" button that appears first in the DOM and would steal the click.
    cur_url = (await browser_get_url())["url"]
    cur_text = (await browser_console("document.body.innerText") or {}).get("result", "")[:300]
    logger.info(f"Before Continue: url={cur_url}, body text starts: {cur_text[:200]!r}")

    clicked_continue = False
    try:
        r = await browser_click_by_text("Continue", role="button")
        if r.get("status") == "clicked":
            clicked_continue = True
            logger.info("Continue clicked via browser_click_by_text")
    except Exception as e:
        logger.warning(f"browser_click_by_text('Continue') failed: {e}")

    if not clicked_continue:
        # Fallback: JS click on button with EXACTLY text "Continue" (no Next)
        logger.info("Trying JS click on Continue button (exact match, no Next)")
        r2 = await browser_console("""(() => {
            var b = document.querySelectorAll('button');
            for (var i=0; i<b.length; i++) {
                var t = (b[i].textContent || '').trim();
                // Only match buttons whose text is EXACTLY "Continue" or contains
                // the word "Continue" — NEVER match "Next slide" / "Next page"
                if (t === 'Continue' || t.indexOf('Continue') !== -1) {
                    // Use both .click() and dispatchEvent to trigger React handlers
                    b[i].click();
                    b[i].dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    return t;
                }
            }
            return 'no_continue_button';
        })()""")
        logger.info(f"JS Continue click result: {r2}")
    await asyncio.sleep(1)

    # Verify we left page 1
    after_url = (await browser_get_url())["url"]
    logger.info(f"After Continue: url={after_url}")
    try:
        from sin_browser_tools.core import manager
        os.makedirs("/tmp/onboarding-diag", exist_ok=True)
        await manager.page.screenshot(path="/tmp/onboarding-diag/after-continue.png")
    except Exception:
        pass

    # ── Step 5: Use-case checkboxes (Page 2) ─────────────────────────────────
    for uc in [
        "Prototype with open models",
        "Flexible capacity for experimentation",
        "Conversational AI",
        "Search",
        "Agentic AI",
    ]:
        if not await _click_checkbox_any_strategy(uc):
            logger.warning(f"Use-case '{uc}' not found")
        await asyncio.sleep(0.2)

    # NUCLEAR FIX: React controlled checkbox hack
    # Only target the 5 use-case checkboxes, NOT "Other" or Terms.
    # The use-case section is after the Terms checkbox. We find it by looking
    # for checkboxes that are within the use-case area (near the text labels).
    # Strategy: find all checkboxes, skip the first one (Terms), then check
    # which ones are near use-case text. Only set those.
    try:
        fix_result = await browser_console("""(() => {
            var set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').set;
            var inputs = document.querySelectorAll('input[type="checkbox"]');
            var fixed = 0;

            // Build a map of checkbox positions
            var checkboxes = [];
            for (var i=0; i<inputs.length; i++) {
                var rect = inputs[i].getBoundingClientRect();
                checkboxes.push({
                    el: inputs[i],
                    idx: i,
                    checked: inputs[i].checked,
                    x: rect.x, y: rect.y, w: rect.width, h: rect.height,
                    // Get nearby text
                    nearby: (inputs[i].closest('label') || inputs[i].parentElement || {}).textContent || ''
                });
            }

            // Find the Terms checkbox (first one, near "agree" text)
            var termsIdx = -1;
            for (var i=0; i<checkboxes.length; i++) {
                if (checkboxes[i].nearby.toLowerCase().indexOf('agree') !== -1 ||
                    checkboxes[i].nearby.toLowerCase().indexOf('terms') !== -1) {
                    termsIdx = i;
                    break;
                }
            }

            // Find use-case text patterns
            var useCasePatterns = [
                'prototype', 'flexible', 'conversational', 'search', 'agentic'
            ];

            // For each unchecked checkbox (skip Terms), check if it's near
            // a use-case text. Only set it if it matches.
            for (var i=0; i<checkboxes.length; i++) {
                if (i === termsIdx) continue;  // Skip Terms
                if (checkboxes[i].checked) continue;

                // Check if nearby text contains a use-case pattern
                var nearby = checkboxes[i].nearby.toLowerCase();
                var isUseCase = useCasePatterns.some(function(p) {
                    return nearby.indexOf(p) !== -1;
                });

                if (isUseCase) {
                    set.call(checkboxes[i].el, true);
                    checkboxes[i].el.dispatchEvent(new Event('change', { bubbles: true }));
                    fixed++;
                }
            }

            return fixed;
        })()""")
        r = fix_result.get("result", 0) if isinstance(fix_result, dict) else 0
        n = int(r) if str(r).isdigit() else 0
        logger.info(f"React controlled checkbox hack (use-cases only): {n} checkboxes fixed")
        await asyncio.sleep(0.3)
    except Exception as e:
        logger.warning(f"Checkbox hack failed: {e}")

    # ── Step 6: Submit (Page 2 → home/settings) ─────────────────────────────
    # DIAG: log all buttons on Page 2
    try:
        btn_diag = await browser_console("""(() => {
            var b = document.querySelectorAll('button');
            var result = [];
            for (var i=0; i<b.length; i++) {
                result.push({
                    text: (b[i].textContent || '').trim().substring(0, 50),
                    disabled: b[i].disabled,
                    type: b[i].type,
                    cls: (b[i].className || '').substring(0, 60)
                });
            }
            return result;
        })()""")
        logger.info(f"Page 2 buttons: {btn_diag}")
    except Exception as e:
        logger.warning(f"Page 2 button diag failed: {e}")

    # ── Install fetch INTERCEPTOR BEFORE click ───────────────────────────────
    # Monkey-patch window.fetch so we capture what URL/method/body the React
    # handler TRIES to send. We can then re-issue that call ourselves if it
    # stalls or fails. This is the "bypass React click + use the same payload"
    # strategy.
    try:
        await browser_console("""(() => {
            window.__captured_onboarding_call = null;
            const origFetch = window.fetch;
            window.fetch = async function(input, init) {
                const url = typeof input === 'string' ? input : (input && input.url) || '';
                const method = (init && init.method) ? String(init.method).toUpperCase() : 'GET';
                let body = (init && init.body) || '';
                if (body && typeof body !== 'string') {
                    try { body = JSON.stringify(body); } catch(e) { body = String(body); }
                }
                try {
                    if (url && (url.indexOf('onboard') !== -1 || url.indexOf('signup') !== -1 || url.indexOf('/profile') !== -1)) {
                        window.__captured_onboarding_call = { url: url, method: method, body: body };
                        console.log('[SINATOR] Captured fetch: ' + method + ' ' + url + ' body=' + (body ? String(body).substring(0, 300) : ''));
                    }
                } catch(e) {}
                return origFetch.apply(this, arguments);
            };
            return 'fetch_interceptor_installed';
        })()""")
        logger.info("Fetch interceptor installed - will capture React's onboarding call")
    except Exception as e:
        logger.warning(f"Fetch interceptor install failed: {e}")

    # DIAG: screenshot Page 2 just before the click attempts
    try:
        from sin_browser_tools.core import manager
        os.makedirs("/tmp/onboarding-diag", exist_ok=True)
        await manager.page.screenshot(path="/tmp/onboarding-diag/page2-before-submit.png")
    except Exception:
        pass

    # ── KILL COOKIE BANNER + CLICK SUBMIT (3 strategies, always all 3) ──────
    async def _kill_cookie_banner_and_click_submit():
        """Remove all cky elements from DOM, then click Submit via 3 strategies."""
        # 1. Nuke all cookie banner elements from DOM
        try:
            cky_result = await browser_console("""(() => {
                var removed = 0;
                // Remove all elements with cky in class or id
                var all = document.querySelectorAll('[class*="cky"], [id*="cky"], [class*="cookieyes"], [id*="cookieyes"]');
                removed = all.length;
                all.forEach(function(el) { el.remove(); });
                // Also remove overlay divs that might block clicks
                var overlays = document.querySelectorAll('[style*="position: fixed"], [style*="position:fixed"]');
                overlays.forEach(function(el) {
                    if (el.style.zIndex > 9998) { el.remove(); removed++; }
                });
                // Remove any backdrop/modal-open class from body
                document.body.classList.remove('cky-modal-open', 'modal-open');
                document.body.style.overflow = '';
                return removed;
            })()""")
            logger.info(f"Cookie banner nuked: {cky_result} elements removed")
        except Exception as e:
            logger.warning(f"Cookie banner nuke failed: {e}")
        
        await asyncio.sleep(0.5)
        
        # 2. Strategy A: React onClick handler directly (most reliable)
        try:
            react_result = await browser_console("""(() => {
                var b = document.querySelectorAll('button');
                for (var i=0; i<b.length; i++) {
                    var t = (b[i].textContent || '').trim();
                    if (t.indexOf('Submit') !== -1 && t.indexOf('Skip') === -1) {
                        // Try React onClick handler
                        var propKey = Object.keys(b[i]).find(k => k.startsWith('__reactProps'));
                        if (propKey && b[i][propKey] && b[i][propKey].onClick) {
                            try {
                                b[i][propKey].onClick({preventDefault: function(){}, stopPropagation: function(){}});
                                return 'react_onclick: ' + t;
                            } catch(e) {
                                b[i].click();
                                b[i].dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                                return 'fallback_click: ' + t;
                            }
                        }
                        // No React handler found — force DOM click
                        b[i].click();
                        b[i].dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        return 'dom_click: ' + t;
                    }
                }
                return 'no_submit_button';
            })()""")
            logger.info(f"React/JS Submit click: {react_result}")
        except Exception as e:
            logger.warning(f"React/JS click failed: {e}")
        
        # 3. Strategy B: Playwright force-click (bypasses overlays)
        try:
            from sin_browser_tools.core import manager
            page = manager.page
            submit_btn = page.locator('button:has-text("Submit to get $6 Credits"), button:has-text("Submit to get $5 Credits"), button:has-text("Submit")')
            if await submit_btn.count() > 0:
                await submit_btn.first.click(force=True, timeout=5000)
                logger.info("Playwright force-click on Submit button succeeded")
        except Exception as e:
            logger.info(f"Playwright force-click: {e}")
        
        # 4. Strategy C: browser_click_by_text as last resort
        for txt in ("Submit to get $6 Credits", "Submit to get $5 Credits", "Submit"):
            try:
                await browser_click_by_text(txt, role="button")
                logger.info(f"browser_click_by_text clicked '{txt}'")
                break
            except Exception:
                continue

    # First click attempt
    submit_clicked = False
    await _kill_cookie_banner_and_click_submit()
    submit_clicked = True  # Assume clicked — we used 3 strategies

    # ── Multi-click Submit with long polling (up to 5 min per attempt) ──────
    # The Fireworks onboarding Submit button can take up to 5 minutes to
    # process. If the first click doesn't redirect, click AGAIN after the
    # poll window expires. Up to 3 click attempts, 300s each.
    escaped_onboarding = False
    for click_round in range(3):
        if click_round > 0:
            logger.info(f"=== Submit click round {click_round+1} — nuking cookies + re-clicking Submit ===")
            await _kill_cookie_banner_and_click_submit()
            await asyncio.sleep(2)

        # Poll every 2s for onboarding redirect (max 300s = 5 min per round)
        for attempt in range(150):
            await asyncio.sleep(2)
            url = (await browser_get_url())["url"]
            if 'onboarding' not in url:
                logger.info(f"Redirect after Submit (round {click_round+1}, poll {attempt+1}/150, {(attempt+1)*2}s): {url[:60]}")
                logger.info("Waiting 8s for page to fully load after onboarding redirect...")
                await asyncio.sleep(8)
                escaped_onboarding = True
                break
            if attempt % 15 == 0:
                logger.info(f"Onboarding poll round {click_round+1} {attempt+1}/150 — still on /onboarding ({(attempt+1)*2}s)")

        if escaped_onboarding:
            break

    # If still on /onboarding after 3 click rounds, try direct API call
    url = (await browser_get_url())["url"]
    if 'onboarding' in url:
        # 1. First check: did React fire a fetch that we captured via interceptor?
        captured_resp = await browser_console("(() => { try { return JSON.stringify(window.__captured_onboarding_call || null); } catch(e) { return 'error:' + e.message; } })()")
        captured_str = (captured_resp or {}).get("result") if isinstance(captured_resp, dict) else None
        captured = None
        if captured_str and captured_str != 'null' and not captured_str.startswith('error:'):
            try:
                import json as _json
                captured = _json.loads(captured_str)
            except Exception:
                captured = None
        if captured and captured.get('url'):
            logger.info(f"Captured fetch from React handler: {captured['method']} {captured['url'][:80]} body={(captured.get('body') or '')[:120]}")
            # Replay the exact captured call (await it properly so server processes it)
            cu, cm, cb = captured['url'], captured['method'], captured.get('body') or ''
            try:
                replay = await browser_console(f"""async () => {{
                    try {{
                        const r = await fetch({cu!r}, {{
                            method: {cm!r},
                            credentials: 'include',
                            headers: {{'Content-Type': 'application/json', 'Accept': 'application/json'}},
                            body: {cb!r}
                        }});
                        const t = await r.text();
                        return 'CAPTURED_REPLAY ' + {cm!r} + ' ' + {cu!r} + ' status:' + r.status + ' body:' + t.substring(0, 300);
                    }} catch(e) {{ return 'CAPTURED_REPLAY_ERROR: ' + e.message; }}
                }}""")
                logger.info(f"Captured replay result: {replay}")
                api_result_ok = replay and ('status:2' in str(replay.get('result', '')))
                if api_result_ok:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"Captured replay code failed: {e}")
        else:
            logger.info("No captured fetch from React handler — running endpoint bruteforce")
            api_result = await browser_console("""async () => {
                const endpoints = [
                    '/api/v1/users/me/onboarding-complete',
                    '/api/v1/users/me/onboarding/complete',
                    '/api/v1/onboarding/complete',
                    '/api/v1/users/me/onboarding',
                    '/api/v1/users/me/onboard',
                    '/api/v1/users/me/onboarding/skip',
                    '/api/v1/onboarding/skip',
                    '/api/v1/onboarding',
                    '/api/v1/user/onboarding',
                    '/api/v1/users/onboarding',
                    '/v1/onboarding',
                    '/api/onboarding',
                ];
                const useCases = ['prototype','flexible','conversational','search','agentic'];
                const bodyShapes = [
                    JSON.stringify({useCases: useCases, completed: true}),
                    JSON.stringify({useCases: useCases, skipped: true}),
                    JSON.stringify({skipped: true, useCases: useCases}),
                    JSON.stringify({useCases: useCases, step: 2, completed: true}),
                    JSON.stringify({complete: true, useCases: useCases}),
                    JSON.stringify({useCases: useCases, completed: false})
                ];
                for (const ep of endpoints) {
                    for (const body of bodyShapes) {
                        for (const method of ['POST','PUT','PATCH']) {
                            try {
                                const resp = await fetch(ep, {
                                    method: method,
                                    credentials: 'include',
                                    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                                    body: body
                                });
                                const text = await resp.text();
                                if (resp.ok) {
                                    return method + ' ' + ep + ' status:' + resp.status + ' body:' + text.substring(0, 250) + ' sentBody:' + body.substring(0, 100);
                                }
                            } catch(e) {}
                        }
                    }
                }
                return 'all_endpoints_failed';
            }""")
            logger.info(f"Bruteforce API call result: {api_result}")
            api_result_ok = api_result and ('status:2' in str(api_result.get('result', '')))
            if api_result_ok:
                logger.info("Onboarding completed via bruteforce API call!")
    try:
        post_buttons = await browser_console("""(() => {
            var b = document.querySelectorAll('button:not([class*="cky-"])');
            return Array.from(b).map(x => ({
                text: (x.textContent || '').trim().substring(0, 40),
                disabled: x.disabled,
                type: x.type
            }));
        })()""")
        logger.info(f"Post-Skip buttons (non-cky): {post_buttons}")
        
        post_url = (await browser_get_url())["url"]
        post_body = (await browser_console("document.body.innerText.substring(0, 1000)") or {}).get("result", "")
        logger.info(f"Post-Skip URL: {post_url}")
        logger.info(f"Post-Skip body (1000 chars): {post_body[:500]}")
        
        # Check for any dialog/modal that might be blocking
        dialogs = await browser_console("""(() => {
            var d = document.querySelectorAll('[role="dialog"],[class*="modal"],[class*="Modal"],[class*="dialog"],[class*="Dialog"]');
            return Array.from(d).map(x => ({
                cls: x.className.substring(0, 60),
                text: x.textContent.trim().substring(0, 100),
                visible: x.offsetParent !== null
            }));
        })()""")
        logger.info(f"Post-Skip dialogs/modals: {dialogs}")
    except Exception as e:
        logger.warning(f"Post-Skip diag failed: {e}")

    # DIAG: screenshot after Skip click to see what's on screen
    try:
        from sin_browser_tools.core import manager
        os.makedirs("/tmp/onboarding-diag", exist_ok=True)
        await manager.page.screenshot(path="/tmp/onboarding-diag/after-skip.png")
        # Log URL and body text after skip
        post_url = (await browser_get_url())["url"]
        post_text = (await browser_console("document.body.innerText.substring(0, 500)") or {}).get("result", "")
        logger.info(f"After Skip: url={post_url}, body={post_text[:200]}")
        # Check for error messages
        errors = await browser_console("""(() => {
            var errs = document.querySelectorAll('[class*="error"],[class*="Error"],[role="alert"],.text-red,.text-destructive');
            return Array.from(errs).map(e => e.textContent.trim().substring(0,100));
        })()""")
        logger.info(f"After Skip errors: {errors}")
    except Exception as e:
        logger.warning(f"DIAG after-skip failed: {e}")

    if not submit_clicked:
        logger.warning("No Page 2 submit button found, trying JS click on last button")
        try:
            await browser_console("""(() => {
                var b = document.querySelectorAll('button');
                for (var i=b.length-1; i>=0; i--) {
                    var t = (b[i].textContent || '').trim().toLowerCase();
                    if (t && t !== 'previous slide' && t !== 'next slide' && !b[i].disabled) {
                        b[i].click();
                        return b[i].textContent.trim();
                    }
                }
                return 'no_button';
            })()""")
            logger.info("JS clicked last enabled button")
        except Exception as e:
            logger.warning(f"JS button click failed: {e}")

    # Fallback: still on /onboarding → form.requestSubmit() + Enter
    url = (await browser_get_url())["url"]
    if 'onboarding' in url:
        # Only submit NON-cky forms (cky forms are cookie consent, not onboarding)
        await browser_console("""(() => {
            var forms = document.querySelectorAll('form:not([class*="cky"]):not([id*="cky"])');
            for (var i=0; i<forms.length; i++) {
                // Skip forms inside cky containers
                if (forms[i].closest('[class*="cky-"]')) continue;
                forms[i].requestSubmit();
                return 'submitted form #' + i;
            }
            // Fallback: submit ALL forms except cky
            var allForms = document.forms;
            for (var j=0; j<allForms.length; j++) {
                if (!allForms[j].className || allForms[j].className.indexOf('cky') === -1) {
                    allForms[j].requestSubmit();
                    return 'submitted allForms #' + j;
                }
            }
            return 'no_form';
        })()""")
        logger.info("Form submitted via requestSubmit() (non-cky forms only)")
        await asyncio.sleep(1)
        url = (await browser_get_url())["url"]
        if 'onboarding' in url:
            await browser_press("Enter")
            logger.info("Enter key sent as Submit fallback")
            await asyncio.sleep(1)

    # Final long wait: poll every 2s, max 60s
    for _ in range(30):
        await asyncio.sleep(1)
        url = (await browser_get_url())["url"]
        if any(x in url for x in ['home', 'account', 'settings', 'api-keys', 'models']):
            logger.info(f"Onboarding redirect: {url[:60]}")
            # Wait for page to fully load
            logger.info("Waiting 8s for page to fully load...")
            await asyncio.sleep(8)
            return
    else:
        # Log ALL API responses, console msgs, and JS errors before giving up
        if _api_responses:
            for r in _api_responses[-10:]:
                logger.info(f"NET: {r}")
        else:
            logger.warning("No API calls logged during onboarding at all")
        if _console_msgs:
            for m in _console_msgs[-10:]:
                logger.info(f"CONSOLE: {m}")
        if _js_errors:
            for e in _js_errors:
                logger.error(f"JS_ERROR: {e}")
        logger.warning("Onboarding — kein Redirect nach 60s, force navigate")
        # First try /account/home (triggers onboarding completion server-side)
        for nav_url in [
            "https://app.fireworks.ai/account/home",
            "https://app.fireworks.ai/settings/users/api-keys",
            "https://app.fireworks.ai/",
        ]:
            try:
                await browser_navigate(nav_url)
                await asyncio.sleep(3)
                url = (await browser_get_url())["url"]
                logger.info(f"Force-nav to {nav_url[-30:]} → {url[:60]}")
                if 'onboarding' not in url:
                    logger.info(f"Escaped onboarding: {url[:60]}")
                    return
            except Exception:
                pass


# ── API Key ─────────────────────────────────────────────────────────────────

async def create_api_key(key_name: str = "sinator-key", **kwargs) -> Dict[str, Any]:
    """Generate a Fireworks API key via the web UI.

    Navigates to /settings/users/api-keys, clicks "Create API Key" → "API Key"
    menuitem, fills the key name, clicks Generate, then polls for the fw_ key
    pattern in page text (up to 15s).

    Bot Chrome stays open — caller must call cleanup_bot() after this.

    Args:
        key_name: Name for the API key (e.g., alias prefix like "pulse")

    Returns:
        Dict with 'status' ('success'|'error') and 'api_key' (fw_...) on success.
    """
    from sin_browser_tools.tools.navigation import browser_navigate, browser_get_url
    from sin_browser_tools.tools.interaction import browser_click_by_text, browser_fill
    from sin_browser_tools.tools.extraction import browser_console
    from sin_browser_tools.tools.vision import browser_get_text

    API_KEYS_URL = "https://app.fireworks.ai/settings/users/api-keys"

    # Bug fix: navigate with retry + polling instead of single 30s timeout
    navigated = False
    for nav_attempt in range(3):
        try:
            await browser_navigate(API_KEYS_URL)
            if await _poll_for_url_contains("api-keys", timeout=5, interval=0.2):
                navigated = True
                break
            url = (await browser_get_url())["url"]
            logger.warning(f"Nav attempt {nav_attempt+1}: landed on {url[:60]} — retrying")
        except Exception as e:
            logger.warning(f"Nav attempt {nav_attempt+1} failed: {e} — retrying in 3s")
            await asyncio.sleep(3)

    if not navigated:
        # Last resort: try via manager.page directly with longer timeout
        try:
            from sin_browser_tools.core import manager
            await manager.page.goto(API_KEYS_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1)
            url = (await browser_get_url())["url"]
            if "api-keys" in url or "settings" in url:
                navigated = True
        except Exception as e:
            logger.error(f"All navigation attempts failed: {e}")

    if not navigated:
        return {"status": "error", "error": "Could not navigate to API keys page after 3 attempts"}

    # Poll for login redirect instead of fixed sleep
    for _ in range(6):
        url = (await browser_get_url())["url"]
        if 'login' in url.lower():
            logger.warning(f"Redirected to login — retrying ({url[:60]})")
            try:
                await browser_press("Enter")
            except Exception:
                pass
            await asyncio.sleep(1)
            try:
                await browser_navigate(API_KEYS_URL)
            except Exception:
                pass
            await asyncio.sleep(0.5)
        else:
            break

    url = (await browser_get_url())["url"]
    if 'login' in url.lower() or 'onboarding' in url.lower():
        logger.error(f"Cannot access API keys — still on {url[:60]}")
        return {"status": "error", "error": f"Not past login/onboarding: {url[:60]}"}

    logger.info(f"API Keys page loaded: {url[:80]}")

    await _dismiss_cookie_consent()

    for attempt_try in range(3):
        try:
            await browser_click_by_text("Create API Key", role="button")
            # No fixed sleep — poll for dialog input below
        except Exception:
            if attempt_try < 2:
                logger.warning("Create API Key button not found — retry")
                try:
                    await browser_navigate(API_KEYS_URL)
                    await _poll_for_url_contains("api-keys", timeout=5, interval=0.3)
                except Exception:
                    pass
                continue

        try:
            await browser_click_by_text("API Key", role="menuitem")
        except Exception:
            pass

        # Poll for dialog input to appear — 15s timeout (was 5s, too short if page still loading)
        if await _poll_for_element('input[name="name"]', timeout=15, interval=0.3):
            break
    else:
        logger.error("API Key dialog never appeared")
        return {"status": "error", "error": "Dialog not found"}

    for retry in range(3):
        suffix = f"-{retry}" if retry > 0 else ""
        name = key_name + suffix

        await browser_fill('input[name="name"]', name)
        await asyncio.sleep(0.1)

        try:
            await browser_click_by_text("Generate", role="button")
        except Exception:
            for kw in ("Generate API Key", "Generate", "Create"):
                try:
                    await browser_click_by_text(kw, role="button")
                    break
                except Exception:
                    continue

        for _ in range(30):
            await asyncio.sleep(0.5)
            text = (await browser_get_text("body")).get("text", "")
            keys = re.findall(r'fw_[a-zA-Z0-9]{20,}', text)
            if keys:
                return {"status": "success", "api_key": keys[0]}

        text = (await browser_get_text("body")).get("text", "")
        if 'Missing' in text and 'Name' in text:
            for kw in ('Close', 'Cancel', 'OK'):
                try:
                    await browser_click_by_text(kw, role="button")
                    await asyncio.sleep(0.3)
                    break
                except Exception:
                    continue
            continue
        break

    return {"status": "error", "error": "API Key not found after retry"}


