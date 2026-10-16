#!/usr/bin/env python3
"""ui_no_copy_guard (CE-13, D9): ui_standalone must not contain any file copied from the
reference UI (general_ai_core/ui). Compares sha256 of every source file AND flags
identical basenames for .ts/.css/.html beyond a generic allowlist.

Usage: scripts/ui_no_copy_guard.py [--ref /path/to/general_ai_core/ui]
Exit 0 = clean. Exit 1 = a copy was detected. Exit 0 with a note when ref is absent.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OURS = ROOT / "ui_standalone"
GENERIC = {"index.html", "main.ts", "vite.config.ts", "tsconfig.json", "package.json", "index.ts", "client.ts", "base.ts", "tokens.css", ".gitignore", "README.md"}
EXTS = {".ts", ".tsx", ".js", ".css", ".html", ".svg", ".json"}


def files(root: Path):
    for p in root.rglob("*"):
        if p.is_file() and "node_modules" not in p.parts and "dist" not in p.parts and p.suffix in EXTS:
            yield p


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default=os.environ.get("CE_REF_UI", "/home/user/ref/general_ai_core/ui"))
    args = ap.parse_args()
    ref = Path(args.ref)
    if not ref.is_dir():
        print(f"ui_no_copy_guard: reference UI not found at {ref} — hash comparison skipped (NOTE)")
        return 0
    ref_hashes = {sha(p): p for p in files(ref)}
    ref_names = {p.name for p in files(ref)}
    bad = 0
    for p in files(OURS):
        h = sha(p)
        if h in ref_hashes:
            print(f"COPY (identical bytes): {p.relative_to(ROOT)} == {ref_hashes[h]}")
            bad += 1
        elif p.name in ref_names and p.name not in GENERIC:
            print(f"NAME COLLISION (review): {p.relative_to(ROOT)} shares basename with reference UI")
            bad += 1
    checked = sum(1 for _ in files(OURS))
    print(f"ui_no_copy_guard: checked {checked} files against {len(ref_hashes)} reference files → {'FAIL' if bad else 'CLEAN'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
