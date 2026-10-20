#!/usr/bin/env python3
"""CE-24/A — import LiteLLM's community price/capability table as a *secondary* snapshot.

Source: https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json
(MIT). We keep only the fields chat_engine reads, so the pinned copy stays small and
diff-reviewable. The engine never depends on the ``litellm`` package — this is data only.

Precedence inside the engine (see ``PricingBook`` / ``CatalogService``):
    manual override > models.dev snapshot > litellm snapshot
Catalog facts from this file become *proposals* only (never auto-confirmed).

Usage:
    python scripts/import_litellm_catalog.py                 # fetch + write snapshot
    python scripts/import_litellm_catalog.py --from FILE     # offline: convert a local copy
    python scripts/import_litellm_catalog.py --check         # print stats of the pinned file

stdlib only (urllib) — no new dependency.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SOURCE_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "compat" / "catalogs" / "litellm_prices_snapshot.json"

#: modes we care about (chat engine + embeddings op). Image/audio/video rows are dropped.
KEEP_MODES = {"chat", "responses", "embedding", "completion"}
#: the only fields we persist (names kept as upstream for traceability)
KEEP_FIELDS = (
    "litellm_provider",
    "mode",
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "max_input_tokens",
    "max_output_tokens",
    "supports_function_calling",
    "supports_vision",
    "supports_response_schema",
    "supports_reasoning",
    "supports_tool_choice",
    "output_vector_size",
)


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def convert(raw: dict[str, object], *, fetched_at: str, source: str) -> dict[str, object]:
    models: dict[str, dict[str, object]] = {}
    for key, row in sorted(raw.items()):
        if key == "sample_spec" or not isinstance(row, dict):
            continue
        mode = row.get("mode")
        if mode not in KEEP_MODES:
            continue
        out: dict[str, object] = {}
        for f in KEEP_FIELDS:
            v = row.get(f)
            if v is None:
                continue
            if f.endswith("_cost") or f.endswith("_per_token"):
                n = _num(v)
                if n is None:
                    continue
                # upstream is USD per token → store USD per 1M (what PricingRecord uses);
                # field names are kept as upstream so rows stay greppable against the source
                out[f] = round(n * 1_000_000, 6)
            else:
                out[f] = v
        if not out.get("litellm_provider"):
            continue
        models[key] = out
    return {
        "source": source,
        "license": "MIT (BerriAI/litellm)",
        "fetched_at": fetched_at,
        "unit": "USD per 1M tokens (converted from upstream per-token)",
        "total": len(models),
        "models": models,
    }


def fetch(url: str = SOURCE_URL) -> dict[str, object]:
    req = urllib.request.Request(url, headers={"User-Agent": "chat_engine-import/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("unexpected payload (not an object)")
    return data


def stats(path: Path = OUT) -> dict[str, object]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    models = doc.get("models") or {}
    by_mode: dict[str, int] = {}
    priced = 0
    for m in models.values():
        by_mode[str(m.get("mode"))] = by_mode.get(str(m.get("mode")), 0) + 1
        if "input_cost_per_token" in m and "output_cost_per_token" in m:
            priced += 1
    return {
        "path": str(path),
        "fetched_at": doc.get("fetched_at"),
        "total": len(models),
        "priced": priced,
        "by_mode": by_mode,
        "bytes": path.stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--from", dest="src", help="local copy of the upstream JSON (offline)")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check", action="store_true", help="print stats of the pinned snapshot")
    a = ap.parse_args(argv)
    if a.check:
        print(json.dumps(stats(Path(a.out)), indent=2))
        return 0
    if a.src:
        raw = json.loads(Path(a.src).read_text(encoding="utf-8"))
        source = f"file:{Path(a.src).name}"
    else:
        raw = fetch()
        source = SOURCE_URL
    doc = convert(raw, fetched_at=datetime.now(UTC).isoformat(timespec="seconds"), source=source)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(stats(out), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
