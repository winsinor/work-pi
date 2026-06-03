#!/usr/bin/env python3
"""Migrate work_layout.json to 320x240, pull latest main, then restore your layout.

Run once on the Pi:
    python3 /home/pi/work-pi/migrate_layout.py
Then:
    deploy
"""
import json, math, os, shutil, subprocess, sys

REPO = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(REPO, "work_layout.json")

# ── Load current layout ────────────────────────────────────────────────────

with open(PATH) as f:
    layout = json.load(f)

cw = layout.get("canvas", {}).get("width",  480)
ch = layout.get("canvas", {}).get("height", 320)

# ── Migrate coordinates if needed ─────────────────────────────────────────

if cw == 320 and ch == 240:
    print("Canvas already 320x240 — skipping coordinate migration.")
else:
    sx = 320 / cw
    sy = 240 / ch
    sf = math.sqrt(sx * sy)

    def sx_(v): return round(v * sx) if v is not None else None
    def sy_(v): return round(v * sy) if v is not None else None
    def sf_(v): return max(8, round(v * sf)) if v is not None else None

    layout.setdefault("canvas", {})["width"]  = 320
    layout.setdefault("canvas", {})["height"] = 240

    ic = layout.get("icon", {})
    grid_h = sy_(layout.get("grid", {}).get("height", 0)) or 0
    max_r  = min((240 - grid_h) // 2 - 4, 320 // 4)
    if ic.get("radius") is not None: ic["radius"] = min(sf_(ic["radius"]), max_r)
    if ic.get("gap")    is not None: ic["gap"]    = sx_(ic["gap"])
    if ic.get("x")      is not None: ic["x"]      = sx_(ic["x"])
    if ic.get("y")      is not None: ic["y"]      = sy_(ic["y"])

    aq = layout.get("aqi", {})
    if aq.get("cx")         is not None: aq["cx"]         = sx_(aq["cx"])
    if aq.get("y")          is not None: aq["y"]          = sy_(aq["y"])
    if aq.get("label_size") is not None: aq["label_size"] = sf_(aq["label_size"])
    if aq.get("value_size") is not None: aq["value_size"] = sf_(aq["value_size"])

    gr = layout.get("grid", {})
    if gr.get("height")     is not None: gr["height"]     = sy_(gr["height"])
    if gr.get("label_size") is not None: gr["label_size"] = sf_(gr["label_size"])
    if gr.get("temp_size")  is not None: gr["temp_size"]  = sf_(gr["temp_size"])
    if gr.get("rain_size")  is not None: gr["rain_size"]  = sf_(gr["rain_size"])
    if gr.get("hum_size")   is not None: gr["hum_size"]   = sf_(gr["hum_size"])
    if gr.get("wind_size")  is not None: gr["wind_size"]  = sf_(gr["wind_size"])

    for page_lines in layout.get("line_positions", {}).values():
        for line in page_lines:
            if line.get("x") is not None: line["x"] = sx_(line["x"])
            if line.get("y") is not None: line["y"] = sy_(line["y"])
            if line.get("h") is not None: line["h"] = sf_(line["h"])
            # all other fields (visible, etc.) are left untouched

    print(f"Migrated coordinates from {cw}x{ch} → 320x240")

# ── Hold migrated layout in memory, pull git cleanly ──────────────────────

migrated_json = json.dumps(layout, indent=2)

print("Restoring work_layout.json to HEAD so git pull is clean…")
subprocess.run(["git", "checkout", "work_layout.json"], cwd=REPO, check=True)

print("Pulling latest main…")
result = subprocess.run(["git", "pull", "origin", "main"], cwd=REPO)
if result.returncode != 0:
    print("\nERROR: git pull failed. Your original layout is untouched.")
    print("Fix the git issue then re-run this script.")
    sys.exit(1)

# ── Write your migrated layout back ───────────────────────────────────────

shutil.copy(PATH, PATH + ".bak")
with open(PATH, "w") as f:
    f.write(migrated_json)

print(f"Restored your layout to {PATH}  (backup: {PATH}.bak)")
print("\nAll done. Now run:  deploy")
