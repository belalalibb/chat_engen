"""D0/D3 architecture guard: chat_engine never imports upstream apps/ or ui/."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "chat_engine"
FORBIDDEN_ROOTS = ("apps", "ui")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)
    return found


def test_no_forbidden_imports_statically() -> None:
    offenders: list[str] = []
    for py in PKG.rglob("*.py"):
        for mod in _imports(py):
            root = mod.split(".")[0]
            if root in FORBIDDEN_ROOTS:
                offenders.append(f"{py.relative_to(PKG.parent)} imports {mod}")
    assert not offenders, "\n".join(offenders)


def test_apps_not_loaded_at_runtime() -> None:
    for m in list(sys.modules):
        if m == "apps" or m.startswith("apps."):
            del sys.modules[m]
    import chat_engine.composition.runtime  # noqa: F401  (imports the whole graph)

    loaded = [m for m in sys.modules if m == "apps" or m.startswith("apps.")]
    assert not loaded, f"upstream app layer leaked into chat_engine: {loaded}"
