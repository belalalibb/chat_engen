"""Playwright UI smoke (CE-13). Hermetic: drives the built UI served by chat_engine.

Run:  python tests/ui/smoke_playwright.py [base_url]   (default http://localhost:8080)
Needs: pip install playwright && python -m playwright install chromium ; server up with dist built.
Prints PASS/FAIL per check; exit 1 on any failure. Output is the evidence (evidence/ce13/).
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    results.append((name, ok, extra))
    print(f"{'PASS' if ok else 'FAIL'} {name} {extra}".rstrip())


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/#chat", wait_until="networkidle")
        check("app renders", page.locator("ce-app").count() == 1)
        check("rtl default", page.get_attribute("html", "dir") == "rtl")

        # chat: single strategy streams; local_echo lacks a stream port ⇒ the UI shows the
        # failed/refused bubble honestly; what we assert is the round trip + bubbles.
        chat = page.locator("ce-chat")
        chat.locator("[data-testid=composer]").fill("hello from playwright")
        chat.locator("[data-testid=send]").click()
        page.wait_for_timeout(2500)
        roles = chat.locator(".msg").evaluate_all("els => els.map(e => e.getAttribute('data-role'))")
        check("user + assistant bubbles", roles[:1] == ["user"] and len(roles) >= 2, str(roles))

        # orchestration via sync path shows a trace
        chat.locator("select").nth(1).select_option("cost_tiered")
        chat.locator("[data-testid=composer]").fill("cost tiered please")
        chat.locator("[data-testid=send]").click()
        page.wait_for_timeout(2500)
        check("trace rendered for strategy", chat.locator("details").count() >= 1)

        page.click("[data-nav=catalog]")
        page.wait_for_selector("ce-catalog [data-testid=catalog-viewport]")
        page.wait_for_timeout(800)
        rows = page.locator("ce-catalog .r").count()
        check("catalog virtualized rows", 0 < rows < 200, f"rows={rows}")

        page.click("[data-nav=skills]")
        page.wait_for_selector("ce-skills [data-testid=skill-id]")
        page.fill("ce-skills [data-testid=skill-id]", "pw_skill")
        page.click("ce-skills [data-testid=register-skill]")
        page.wait_for_timeout(800)
        check("skill registered + listed", page.locator("ce-skills tr[data-skill=pw_skill]").count() == 1)

        for nav in ("providers", "capabilities", "admission", "conversations"):
            page.click(f"[data-nav={nav}]")
            page.wait_for_timeout(600)
            check(f"{nav} screen renders", page.locator(f"ce-{nav} .card").count() >= 1)

        page.click("nav button")
        page.wait_for_timeout(200)
        check("lang toggle flips dir", page.get_attribute("html", "dir") == "ltr")
        check("no page errors", not errors, " | ".join(errors))
        browser.close()
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
