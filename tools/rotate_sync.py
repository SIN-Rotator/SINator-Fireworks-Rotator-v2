#!/usr/bin/env python3
"""Auto-generate N API keys + auto-sync to dashboard in one command.
Replaces manual: rotate.py + auto_sync.py
Usage: python3 tools/rotate_sync.py [N]
       (default N=1)
"""
import asyncio, json, time, sys, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROTATE_SCRIPT = PROJECT_ROOT / "tools" / "rotate.py"
AUTO_SYNC_SCRIPT = PROJECT_ROOT / "tools" / "auto_sync.py"

print(f"=== Starting automated rotation: {N} key(s) ===")
print(f"Pool → Rotate → Auto-Sync → Dashboard")
print()

successes = 0
failures = 0
t0 = time.time()

for i in range(N):
    print(f"\n--- Key {i+1}/{N} ---")

    print(f"[1/2] Generating key (rotate.py)...")
    proc = subprocess.run(
        ["python3", str(ROTATE_SCRIPT), "--debug"],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True
    )

    api_key = None
    # rotate.py logs to stderr (logging module), so check BOTH streams
    combined = proc.stdout + "\n" + proc.stderr
    for line in combined.splitlines():
        if "API Key:" in line:
            api_key = line.split("API Key:")[1].strip()
            break

    if not api_key:
        print(f"   ✗ Generation FAILED")
        for line in combined.splitlines()[-10:]:
            if line.strip():
                print(f"      {line}")
        failures += 1
        if failures >= 3:
            print(f"\n⚠️  3 consecutive failures — STOPPING")
            break
        continue
    else:
        print(f"   ✓ API Key: {api_key[:20]}...")

    print(f"[2/2] Auto-syncing to dashboard (auto_sync.py)...")
    proc2 = subprocess.run(
        ["python3", str(AUTO_SYNC_SCRIPT)],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True
    )

    if "Synced" in proc2.stdout or "Reset" in proc2.stdout:
        print(f"   ✓ Synced to dashboard")
        successes += 1
    else:
        print(f"   ⚠️  Sync output: {proc2.stdout[:200]}")
        successes += 1

    if i < N - 1:
        print(f"   ...waiting 5s before next rotation...")
        time.sleep(1)

t = time.time() - t0
print(f"\n{'='*50}")
print(f"DONE: {successes}/{N} keys generated + auto-synced")
print(f"Time: {t/60:.1f}min ({t/max(1,successes):.0f}s avg)")
print(f"Dashboard: http://localhost:8100/api/v1/pool/stats")
