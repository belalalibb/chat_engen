#!/usr/bin/env python3
"""Release-gate secret scan (D8). Exit 1 on any match outside .git/.venv."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9\-_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----"),
]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    files = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"], cwd=root, capture_output=True, text=True
    ).stdout.split()
    hits: list[str] = []
    for rel in files:
        p = root / rel
        if not p.is_file() or p.suffix in {".png", ".jpg", ".ico", ".woff", ".woff2"}:
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for pat in PATTERNS:
                if pat.search(line):
                    hits.append(f"{rel}:{i}: {pat.pattern}")
    if hits:
        print("SECRET SCAN FAILED:\n" + "\n".join(hits))
        return 1
    print(f"secret scan clean ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
