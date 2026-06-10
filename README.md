# SINator Fireworks Rotator v2 — V19.3 GMX Delete Fix

[![GitNexus](https://img.shields.io/badge/GitNexus-knowledge%20graph-8B5CF6)](.gitnexus/)

**Standalone-Fork** des [SINator-FireworksAI](https://github.com/SIN-Rotator/SINator-FireworksAI) Haupt-Repos, der **ausschließlich den v19.3-gmx-delete-fixed Code** enthält. Wurde am 10. Juni 2026 erstellt, um den funktionierenden Rotation-Stand als eigenes Repo zu verewigen.

## ⚠️ Zweck dieses Repos

Dies ist **NICHT** die aktiv entwickelte Version. Für aktive Entwicklung benutze:
- **Haupt-Repo (modern, mit 2 chirurgischen Fixes gepatched):** https://github.com/SIN-Rotator/SINator-FireworksAI
- **Standalone-Test (dieses Repo, eingefroren):** https://github.com/SIN-Rotator/SINator-Fireworks-Rotator-v2

## 🏷️ Immortal Tags (UNZERSTÖRBAR)

| Tag | Status | Bedeutung |
|-----|--------|-----------|
| `v19.3-gmx-delete-fixed` | ✅ Pushed | Letzter Commit des originalen v19.3-Branches |
| `v19.3-gmx-delete-fixed-working` | ✅ Pushed | **VERIFIED WORKING** — End-to-End-Test bestanden in 164.6s |

## ✅ Verifizierter E2E-Flow (164.6s am 10. Juni 2026)

```bash
python3 tools/rotate.py --gmx-email <EMAIL> --gmx-password <PW> --password <PW>
# → GMX Login → Alias Delete (alt) → Alias Create (neu) → Fireworks Signup
# → OTP aus GMX (CDP-AXTree) → Verify → Login → Onboarding → API Key → Pool Save
```

**Test-Ergebnis:**
```
ROTATION COMPLETE - 164.6s
API Key: fw_HxidQ9fkpb2bzgSgDxAdVs
Pool gespeichert: 243 Keys (242 → 243, +1 NEU)
Neuer API-Key hinzugefügt: 97aafae9...
Login OK: ['login_page', 'email_filled', 'password_filled', 
           'form_submitted', 'onboarding_complete', 'login_success']
```

## 🔧 Die kritischen Fixes in diesem Repo

### Fix 1: `_delete_alias` in `agent_toolbox/core/gmx_service.py`
- **Problem:** Playwright's `row.hover()` triggert Wicket `:hover` CSS nicht zuverlässig
- **Lösung:** `page.mouse.move(0,0) → page.mouse.move(cx,cy)` Pattern + 3x retry
- **Selector:** GMX nutzt `<div class="table_body-row table_row">`, NICHT `<tr>`/`<li>` (kleinste BBox-Strategie)
- **Delete-Icon:** `a.table-hover_icon[title*="löschen"]` mit Fallback auf alle `<a>` mit "lösch" im title

### Fix 2: Multi-Tab Architektur
- `work_tab` (Alias/Fireworks) + `inbox_tab` (OTP) — isolation
- Browser stays single process, but logical separation

### Fix 3: CDP-AXTree OTP-Extraktion
- `Accessibility.getFullAXTree` mit `pierce: True`
- Durchdringt OOPIFs und Shadow DOM
- Bulletproof gegen 60+ Ad-Frames

## ⚠️ Bekannte Probleme (vom Original v19.3)

- 5 Bugs in Onboarding-Flow (alle V19.2 gefixt): Account-ID-Überschreiben, Carousel-Klick, Cookie-Banner, Wait-Time, os-Import
- OTP-Verzögerung: bis zu 180s (Polling 25×8s)
- Account-Suspension: $5-Credits aufgebraucht = suspended

## 🔗 Verwandte Repos

| Repo | GitHub | Funktion |
|------|--------|----------|
| **SINator-FireworksAI** (Haupt) | [SIN-Rotator/SINator-FireworksAI](https://github.com/SIN-Rotator/SINator-FireworksAI) | Modern + 2 chirurgische Fixes gepatched (gmx delete + consent redirect) |
| **SINator-FireworksRotator-v2** (dieses) | [SIN-Rotator/SINator-Fireworks-Rotator-v2](https://github.com/SIN-Rotator/SINator-Fireworks-Rotator-v2) | Standalone-Test, eingefroren auf v19.3-gmx-delete-fixed |
| **SINator-dashboard** | [SIN-Rotator/SINator-dashboard](https://github.com/SIN-Rotator/SINator-dashboard) | Tauri Dashboard + Setup |
| **OpenCode Config** | [OpenSIN-Code/SIN-Code-FireworksAI-OpenCode-Config](https://github.com/OpenSIN-Code/SIN-Code-FireworksAI-OpenCode-Config) | opencode.json mit 12 Modellen |
| **Hermes Bundle** | [SIN-Hermes-Bundles/SIN-Hermes-Provider-Bundle](https://github.com/SIN-Hermes-Bundles/SIN-Hermes-Provider-Bundle) | Hermes Provider Config |

---

*Stand: 2026-06-10 | Tag v19.3-gmx-delete-fixed-working | Verifiziert: 164.6s E2E | Standalone-Fork (NICHT aktiv entwickelt)*
