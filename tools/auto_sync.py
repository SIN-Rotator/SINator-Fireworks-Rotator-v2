#!/usr/bin/env python3
"""Auto-sync v2 pool → v3 pool + reset used flag for new keys.
Runs after every rotate.py to make keys available in dashboard.
Usage: python3 tools/auto_sync.py"""
import json
import sys
import urllib.request
from pathlib import Path

V2_POOL = Path("/Users/jeremy/dev/SINator-Fireworks-Rotator-v2/data/fireworksai-pool.json")
V3_POOL = Path("/Users/jeremy/dev/SIN-Rotator-SINator-FireworksAI/data/fireworksai-pool.json")
BACKEND_URL = "http://localhost:8100/api/v1/pool"

def reload_backend():
    """Tell the v3 backend to reload its in-memory pool from disk."""
    try:
        req = urllib.request.Request(f"{BACKEND_URL}/reload", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"✅ Backend reloaded: {resp.read().decode()[:100]}")
    except Exception as e:
        print(f"⚠️  Backend reload failed (non-fatal): {e}")

def sync_v2_to_v3():
    if not V2_POOL.exists():
        print(f"❌ v2 pool not found: {V2_POOL}")
        return False
    if not V3_POOL.exists():
        print(f"❌ v3 pool not found: {V3_POOL}")
        return False

    v2_keys = json.load(open(V2_POOL))
    v3_keys = json.load(open(V3_POOL))

    v3_ids = {k.get('id', '') for k in v3_keys if isinstance(k, dict)}
    new_keys = []
    for k in v2_keys:
        if isinstance(k, dict) and k.get('id', '') not in v3_ids:
            new_keys.append(k)

    print(f"Found {len(new_keys)} new keys to sync from v2 to v3")

    if new_keys:
        v3_keys.extend(new_keys)
        json.dump(v3_keys, open(V3_POOL, 'w'), indent=2)
        print(f"✅ Synced {len(new_keys)} keys to v3 pool")

    reset_count = 0
    for k in v3_keys:
        if isinstance(k, dict) and not k.get('suspended', True):
            if k.get('used', False):
                k['used'] = False
                k['used_at'] = None
                reset_count += 1

    if reset_count > 0:
        json.dump(v3_keys, open(V3_POOL, 'w'), indent=2)
        print(f"✅ Reset used flag for {reset_count} keys")

    return True

if __name__ == "__main__":
    sync_v2_to_v3()
    reload_backend()

