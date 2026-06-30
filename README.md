# SINator Fireworks Rotator v2 — VM Key Generation

[![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-CDP-2EAD33?logo=playwright&logoColor=white)](https://playwright.dev/)

**Aktiver Rotator** für den [SINator-FireworksAI](https://github.com/SIN-Rotator/SINator-FireworksAI) Key Pool. Läuft auf einer OCI VM (Oracle Cloud, ARM64) und generiert automatisch Fireworks API Keys via GMX Alias Rotation + Playwright Browser Automation.

## Architektur

```
OCI VM (sin-supabase, 92.5.60.87)
  |
  ├── systemd timer (every 10min)
  |     └── auto_keygen_vm.py
  |           ├── check pool stats (sinator.delqhi.com)
  |           └── if available < 5: rotate_vm.py 10
  |
  ├── rotate_vm.py N (batch generator)
  |     └── rotate.py (single key, Playwright CDP)
  |           ├── GMX login (Chrome CDP :9222)
  |           ├── Alias rotation (delete old, create new)
  |           ├── Fireworks signup (Bot Chrome)
  |           ├── OTP polling (GMX inbox)
  |           ├── Login + Onboarding
  |           ├── API key extraction
  |           └── Push to Mac backend (sinator.delqhi.com/api/v1/pool/add)
  |
  ├── Xvfb :99 (virtual display)
  ├── Chromium CDP :9222 (Snap, --no-sandbox)
  └── noVNC :6080 (manual login fallback)
```

## VM Setup

```bash
# SSH
ssh sin-supabase    # ubuntu@92.5.60.87

# Services
sudo systemctl status sinator-auto-rotator.timer
sudo systemctl restart sinator-chromium
sudo systemctl stop sinator-auto-rotator.timer   # before manual batch
sudo systemctl start sinator-auto-rotator.timer   # resume auto

# noVNC (manual GMX login)
ssh -L 6080:localhost:6080 sin-supabase
# → open http://localhost:6080/vnc.html
```

## Key Generation

### Automated (default)
The systemd timer fires every 10min. If pool available < 5, it generates 10 keys.

### Manual batch
```bash
ssh sin-supabase
sudo rm -f /tmp/sinator-rotate.lock
cd /opt/sinator-fireworks
DISPLAY=:99 MAC_BACKEND_URL=https://sinator.delqhi.com \
  .venv/bin/python3 -u tools/rotate_vm.py 20 2>&1
```

### Single key (foreground, debug)
```bash
ssh sin-supabase "cd /opt/sinator-fireworks && DISPLAY=:99 \
  MAC_BACKEND_URL=https://sinator.delqhi.com \
  .venv/bin/python3 -u tools/rotate_vm.py 1 --debug 2>&1"
```

## Performance (v2.0 polling optimization)

All fixed `asyncio.sleep()` calls replaced with responsive polling (0.2-0.3s intervals).

| Metric | Before | After |
|--------|--------|-------|
| Time per key | 137-170s | ~80-100s |
| Onboarding poll | 5s intervals | 1s intervals |
| Login redirect | Fixed 5s sleep | Poll URL change 0.3s |
| Create API Key | Fixed 0.3s sleep | Poll for input element 0.2s |
| Between-key delay | 15s | 5s |

## Crash Recovery

| Problem | Fix |
|--------|-----|
| Stale lock file after reboot | `ExecStartPre` cleans `/tmp/sinator-rotate.lock` |
| Lock permission (root vs ubuntu) | `chmod 0666` on lock files |
| GMX account blocked | 60s login cooldown + noVNC manual login |
| GMX session lost | Hourly cron backup + `restore_gmx_session.sh` |
| Concurrent runs | `fcntl.flock` prevents, no `ExecStartPre` in auto-rotator |

## Key Files

| File | Purpose |
|------|---------|
| `tools/rotate_vm.py` | Batch key generation (N keys, lock-protected) |
| `tools/rotate.py` | Single key generation (Playwright CDP) |
| `tools/auto_keygen_vm.py` | Auto-keygen: check pool → generate if low |
| `tools/backup_gmx_session.sh` | Backup Snap Chromium cookies (hourly cron) |
| `tools/restore_gmx_session.sh` | Restore GMX session from backup |
| `agent_toolbox/core/gmx_service.py` | GMX login, alias rotation, OTP polling |
| `agent_toolbox/core/fireworks_service.py` | Fireworks signup, login, onboarding, API key |
| `/opt/data/config.json` | GMX email/password + Fireworks password |

## Related Repos

| Repo | Function |
|------|----------|
| **SINator-FireworksAI** (Mac) | Key pool + proxy + backend |
| **SINator-Fireworks-Rotator-v2** (this, VM) | Key generation via Playwright |
| [SINator-dashboard](https://github.com/SIN-Rotator/SINator-dashboard) | Tauri dashboard |

## License

MIT
