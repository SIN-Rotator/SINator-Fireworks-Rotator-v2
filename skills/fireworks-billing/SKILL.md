---
name: sinator-fireworks-billing
description: Fireworks AI Zahlungsinformation hinzufügen — Stripe Checkout mit hCaptcha. Chrome Profil + CDP + manuel lösen.
license: MIT
---

# SINator Fireworks Billing

## Überblick
Zahlungsmethode zu Fireworks-Account hinzufügen um Suspendierung aufzuheben.
Free Credits erfordern aktive Zahlungsmethode — ohne diese werden ALLE Keys suspended (412).

## Architektur

```
Playwright (channel="chrome") + CDP
    ↓ Launch real Chrome with Profile
Chrome-Tab (sichtbar für User)
    ↓ Login → Billing → Add Payment
Stripe Checkout (checkout.stripe.com)
    ↓ Card + Address ausfüllen
hCaptcha (manuell vom User lösen)
    ↓ Payment Method gespeichert
Fireworks Account → Unsuspended
```

## Kritische Erkenntnisse

### 1. Chrome Profil MUSS Google-Account haben
**Ohne Google-Account im Chrome → hCaptcha immer.**
- Profile 159 (E-Förder) funktioniert ✅
- Default-Profil (kein Google) → hCaptcha ❌
- `launch_persistent_context` mit echtem Profil → Google-Account wird NICHT geladen ❌
- **Lösung:** Chrome manuell mit `--user-data-dir` + `--profile-directory` starten, dann via CDP verbinden ✅

### 2. CDP erfordert eigenes user-data-dir
```bash
# FALSCH — Chrome verweigert CDP mit Default-Dir
chrome --remote-debugging-port=9222

# RICHTIG — Kopiertes Profil in eigenem Verzeichnis
cp -a "~/Library/Application Support/Google/Chrome/Profile 159" "/tmp/chrome-profile-159/Profile 159"
chrome --user-data-dir=/tmp/chrome-profile-159 --profile-directory="Profile 159" --remote-debugging-port=9222
```

### 3. Login Flow (geändert seit Juli 2026)
```
/login → a:has-text('Email Login') klicken → /login/email?redirectURI=...
input[name='email'] (NICHT type=email!)
input[name='password']
[data-testid='login-form-submit'] (NICHT button[type='submit'] — matched mehrere!)
→ 8s warten → Escape für Dialog
```

### 4. Billing Flow
```
/account/billing (NICHT /settings/billing — 404!)
button:has-text('Add payment method') (lowercase! NICHT "Payment Method")
→ 8s warten auf Stripe Checkout
```

### 5. Stripe Checkout (checkout.stripe.com)
**Card-Accordion per JS expandieren:**
```javascript
document.querySelector('[data-testid="card-accordion-item-button"]')?.click();
```
**Card Fields (direkt im DOM, NICHT in iframes):**
- `input[name='cardNumber']` — type() mit delay=50
- `input[name='cardExpiry']` — type() mit delay=50
- `input[name='cardCvc']` — type() mit delay=50
- `input[name='billingName']` — placeholder "Vollständiger Name"

**Adresse manuell eingeben:**
```python
page.locator("text=Adresse manuell eingeben").click()  # Google Places ausblenden
```

**Address Fields:**
- `input[name='billingAddressLine1']` — placeholder "Adresszeile 1"
- `input[name='billingPostalCode']` — placeholder "Postleitzahl"
- `input[name='billingLocality']` — placeholder "Ort"

**Speichern:**
```python
page.locator("button:has-text('Speichern')").first.click()  # NICHT "Save"
```

### 6. hCaptcha
Erscheint IMMER nach "Speichern". User MUSS manuell lösen.
Script wartet bis 5 Minuten. Nach Lösung → Payment gespeichert.

## Setup

### Chrome Profil kopieren
```bash
rm -rf /tmp/chrome-profile-159
mkdir -p /tmp/chrome-profile-159
cp -a "~/Library/Application Support/Google/Chrome/Profile 159" "/tmp/chrome-profile-159/Profile 159"
cp "~/Library/Application Support/Google/Chrome/Local State" "/tmp/chrome-profile-159/"
```

### Chrome starten + CDP verbinden
```python
import subprocess
from playwright.sync_api import sync_playwright

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USER_DATA = "/tmp/chrome-profile-159"

# Chrome starten
subprocess.Popen([
    CHROME,
    "--user-data-dir=/tmp/chrome-profile-159",
    "--profile-directory=Profile 159",
    "--remote-debugging-port=9222",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled",
    "about:blank"
])

# CDP verbinden
with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = browser.contexts[0]
    page = ctx.pages[0]
    page.evaluate("() => { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); }")
```

## Random Adressen
Jeder Versuch nutzt zufällige Berlin-Adresse:
```python
STREETS = ["Friedrichstr. 123", "Unter den Linden 42", "Kurfürstendamm 7", ...]
PLZ_LIST = ["10115", "10117", "10178", "10243", ...]
FIRST = ["Max", "Anna", "Lukas", "Sophie", ...]
LAST = ["Mueller", "Schmidt", "Schneider", "Fischer", ...]
```

## Referenz-Script
`/tmp/fw23.py` — Vollständiges funktionierendes Script mit:
- Chrome CDP + Profile 159 (E-Förder)
- Zufällige Adresse
- Kompletter Login → Billing → Stripe → hCaptcha Flow

## Bekannte Fallstricke
| Problem | Lösung |
|---------|--------|
| Google-Account zeigt nicht | CDP mit `--user-data-dir` statt `launch_persistent_context` |
| `--remote-debugging-port` verweigert | Eigenes `--user-data-dir` nötig |
| Card-Accordion nicht klickbar | JS `querySelector` statt Playwright click |
| Card Fields nicht füllbar | `type(CARD, delay=50)` statt `fill()` |
| Adresse manuell eingeben klappt nicht | Google Places muss erst aktiviert sein |
| hCaptcha erscheint immer | User muss manuell lösen (5min Timeout) |
| `/settings/billing` → 404 | `/account/billing` nutzen |
| `button[type='submit']` matched zu viel | `[data-testid='login-form-submit']` nutzen |

## Port-Konfiguration
- **Backend:** :8000 (app_proxy.py → :8100)
- **Proxy:** :8888 (Cloudflare Worker DOWN — lokaler Proxy)
- **CDP:** :9222 (Chrome Debugging)
