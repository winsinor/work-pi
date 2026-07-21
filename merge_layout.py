#!/usr/bin/env python3
"""Merge new pages/line_positions entries from repo layout into install dir layout.

Adds only keys that are absent — never overwrites existing user-edited positions.
Called by the deploy script: merge_layout.py <repo_layout> <install_layout>
"""
import json
import os
import sys

if len(sys.argv) != 3:
    print("Usage: merge_layout.py <repo_layout> <install_layout>", file=sys.stderr)
    sys.exit(1)

repo_path, inst_path = sys.argv[1], sys.argv[2]

if not os.path.exists(inst_path):
    sys.exit(0)

with open(repo_path) as f:
    repo = json.load(f)
with open(inst_path) as f:
    inst = json.load(f)

changed = False
for section in ("pages", "line_positions"):
    for key, val in repo.get(section, {}).items():
        if key not in inst.get(section, {}):
            inst.setdefault(section, {})[key] = val
            changed = True
            print(f"  Added {section}.{key}")

if changed:
    tmp = inst_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(inst, f, indent=2)
    os.replace(tmp, inst_path)
    print("  work_layout.json updated.")
else:
    print("  work_layout.json already up to date.")
