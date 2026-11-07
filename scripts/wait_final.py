"""Wait until the FINAL download is published (polls the public config.json every 20 s), then exit 0.

    python scripts/wait_final.py [--max-min 120]
The site states final_unlock_at in UTC (e.g. 18:00Z = 19:00 in Madrid in November): this prints both clocks.
"""
import sys
import time
from datetime import datetime, timezone

import requests

SITE = "https://hackathon.usetalky.com"
max_min = float(sys.argv[sys.argv.index("--max-min") + 1]) if "--max-min" in sys.argv else 120
t0 = time.time()
while True:
    try:
        cfg = requests.get(f"{SITE}/config.json", timeout=20).json()
        d = next(x for x in cfg["downloads"] if x["id"] == "final")
        try:
            unlock = requests.get(f"{SITE}/api/leaderboard", timeout=20).json().get("final_unlock_at")
        except Exception:
            unlock = None
        now = datetime.now(timezone.utc)
        print(f"[{datetime.now():%H:%M:%S} local | {now:%H:%M:%S} UTC] FINAL available={d.get('available')} "
              f"unlock_at={unlock}", flush=True)
        if d.get("available"):
            sys.exit(0)
    except Exception as e:  # network blips: keep polling
        print(f"[{datetime.now():%H:%M:%S}] poll error {type(e).__name__}; retrying", flush=True)
    if time.time() - t0 > max_min * 60:
        sys.exit(1)
    time.sleep(20)
