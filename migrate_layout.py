#!/usr/bin/env python3
"""Migrate work_layout.json from 480x320 canvas to 320x240.

Run this on the Pi BEFORE pulling the new main branch:
    python3 /home/pi/work-pi/migrate_layout.py

It reads the current work_layout.json, applies the same scale factors that
render.py was previously applying at runtime, and writes the result back.
If the canvas is already 320x240 this script exits without changing anything.
"""
import json, math, sys, shutil, os

PATH = os.path.join(os.path.dirname(__file__), "work_layout.json")

with open(PATH) as f:
    layout = json.load(f)

cw = layout.get("canvas", {}).get("width",  480)
ch = layout.get("canvas", {}).get("height", 320)

if cw == 320 and ch == 240:
    print("Canvas is already 320x240 — nothing to do.")
    sys.exit(0)

sx = 320 / cw
sy = 240 / ch
sf = math.sqrt(sx * sy)

def sx_(v): return round(v * sx) if v is not None else None
def sy_(v): return round(v * sy) if v is not None else None
def sf_(v): return max(8, round(v * sf)) if v is not None else None

# Back up original
shutil.copy(PATH, PATH + ".bak")
print(f"Backed up original to {PATH}.bak")

layout.setdefault("canvas", {})["width"]  = 320
layout.setdefault("canvas", {})["height"] = 240

ic = layout.get("icon", {})
if ic.get("radius") is not None: ic["radius"] = sf_(ic["radius"])
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

with open(PATH, "w") as f:
    json.dump(layout, f, indent=2)
print(f"Migrated {PATH} from {cw}x{ch} → 320x240")
print("Now run:  git stash && git pull origin main && git stash pop")
print("If stash pop conflicts on work_layout.json, use your migrated version:")
print("  git checkout --ours work_layout.json && git add work_layout.json")
