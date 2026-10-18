"""Deep UI end-to-end run against a LIVE chat_engine (gateway-bound): exercises the flows a
user actually clicks, not just screen rendering. Complements ``smoke_playwright.py``.

    python tests/ui/e2e_playwright.py http://localhost:8080

Prints PASS/FAIL per flow, exit 1 on any FAIL. Writes evidence/ce20/ui_e2e_final.png.
"""

from __future__ import annotations

import contextlib
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
WAIT = 120_000
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}".rstrip(), flush=True)


def main() -> int:  # noqa: C901 - linear scenario
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        http5xx: list[str] = []
        page.on(
            "response",
            lambda r: http5xx.append(f"{r.status} {r.request.method} {r.url.replace(BASE, '')}")
            if r.status >= 500
            else None,
        )

        page.goto(f"{BASE}/#chat", wait_until="networkidle")
        chat = page.locator("ce-chat")
        composer = chat.locator("[data-testid=composer]")
        send = chat.locator("[data-testid=send]")

        def settled(n: int) -> None:
            # locators pierce Lit shadow roots; document.querySelector does not
            deadline = time.time() + WAIT / 1000
            while time.time() < deadline:
                if chat.locator(".msg[data-role=assistant]:not([data-status=streaming])").count() >= n:
                    return
                page.wait_for_timeout(500)

        # 1. model picker from /v1/openai/models; explicit gateway model via stream(buffer)
        picker = chat.locator("select").first
        picker.locator("option").nth(5).wait_for(state="attached", timeout=30_000)
        options = picker.evaluate("s => [...s.options].map(o => o.value)")
        check("model picker lists catalog models", len(options) > 5, f"n={len(options)}")
        target = "deepseek-v3.2" if "deepseek-v3.2" in options else next(o for o in options if o)
        picker.select_option(target)
        composer.fill("Reply with exactly: OK")
        send.click()
        settled(1)
        first = chat.locator(".msg[data-role=assistant]").first
        check("explicit model chat completes (stream buffer)", first.get_attribute("data-status") == "complete", f"{target} text={first.inner_text()[:40]!r}")

        # 2. history kept on second turn
        composer.fill("What did I ask you to reply with? One word.")
        send.click()
        settled(2)
        check("second turn appended (history kept)", chat.locator(".msg[data-role=assistant]").count() >= 2)

        # 3. cancel control + honest settle
        composer.fill("Write 300 words about the ocean.")
        send.click()
        cancel_seen = False
        with contextlib.suppress(Exception):
            chat.locator("[data-testid=cancel]").wait_for(timeout=5_000)
            cancel_seen = True
            chat.locator("[data-testid=cancel]").click()
        settled(3)
        last_status = chat.locator(".msg[data-role=assistant]").last.get_attribute("data-status")
        check("cancel control shown + bubble settles", cancel_seen and last_status in {"partial", "complete", "failed"}, f"status={last_status}")

        # 4. conversations screen lists it; reopen restores transcript
        page.click("[data-nav=conversations]")
        page.wait_for_selector("ce-conversations .card")
        page.wait_for_timeout(800)
        rows = page.locator("ce-conversations tr.click")
        check("conversation listed", rows.count() >= 1, f"rows={rows.count()}")
        rows.first.click()
        page.wait_for_selector("ce-chat .msg")
        page.wait_for_timeout(800)
        check("reopen restores transcript", page.locator("ce-chat .msg").count() >= 4, f"msgs={page.locator('ce-chat .msg').count()}")

        # 5. project -> new chat in project -> completes
        page.click("[data-nav=conversations]")
        page.wait_for_selector("ce-conversations .card")
        page.fill("ce-conversations input.grow", "PW Project")
        page.fill("ce-conversations textarea", "You are terse. Always answer in one short line.")
        page.click("ce-conversations button.primary")
        page.wait_for_timeout(800)
        proj = page.locator("ce-conversations tbody tr", has_text="PW Project")
        check("project created + listed", proj.count() >= 1)
        proj.first.locator("button").first.click()
        page.wait_for_selector("ce-chat [data-testid=composer]")
        composer.fill("Say OK")
        send.click()
        settled(1)
        check("chat inside project completes", chat.locator(".msg[data-role=assistant]").first.get_attribute("data-status") == "complete")

        # 6. sequential_handoff with explicit stages typed in the UI
        chat.locator("select").nth(1).select_option("sequential_handoff")
        chat.locator("input[placeholder='model-a, model-b']").fill("deepseek-v3.2, deepseek-v4-flash")
        composer.fill("One sentence about tea.")
        send.click()
        with contextlib.suppress(Exception):
            chat.locator("details").first.wait_for(timeout=WAIT)
        tr = chat.locator("details tbody tr").count()
        err = chat.locator(".err").inner_text() if chat.locator(".err").count() else ""
        check("sequential_handoff trace with 2 steps", tr >= 2, f"trace rows={tr} {err[:80]}")
        chat.locator("select").nth(1).select_option("single")

        # 7. providers: draft via UI, validate unreachable base_url honestly, archive
        page.click("[data-nav=providers]")
        page.wait_for_selector("ce-providers [data-testid=pk]")
        key = f"pw{int(time.time()) % 100000}"
        inputs = page.locator("ce-providers .grid input")
        page.fill("ce-providers [data-testid=pk]", key)
        inputs.nth(1).fill("PW Draft")
        page.fill("ce-providers [data-testid=base-url]", "http://127.0.0.1:9/v1")
        inputs.nth(3).fill("m1,m2")
        page.click("ce-providers button.primary")
        page.wait_for_timeout(1500)
        prow = page.locator(f"ce-providers tr[data-provider={key}]")
        perr = page.locator("ce-providers .err").inner_text() if page.locator("ce-providers .err").count() else ""
        check("provider draft created via UI", prow.count() == 1, perr[:100])
        if prow.count():
            prow.locator("button").first.click()  # validate
            page.wait_for_timeout(3000)
            check("validate unreachable base_url is honest", bool(prow.locator(".pill").inner_text()), f"state={prow.locator('.pill').inner_text()}")
            prow.locator("button.danger").click()  # archive
            page.wait_for_timeout(800)

        # 8. skills register via UI
        page.click("[data-nav=skills]")
        page.wait_for_selector("ce-skills [data-testid=skill-id]")
        page.fill("ce-skills [data-testid=skill-id]", "pw_e2e_skill")
        page.click("ce-skills [data-testid=register-skill]")
        page.wait_for_timeout(800)
        check("skill registered via UI", page.locator("ce-skills tr[data-skill=pw_e2e_skill]").count() == 1)

        # 9. theme / collapse / language controls
        nav_buttons = page.locator("nav button")
        before = page.get_attribute("html", "data-theme")
        nav_buttons.nth(1).click()
        page.wait_for_timeout(200)
        check("theme toggle switches", page.get_attribute("html", "data-theme") != before, f"{before} -> {page.get_attribute('html', 'data-theme')}")
        w0 = page.locator("nav").first.bounding_box()["width"]
        nav_buttons.nth(2).click()
        page.wait_for_timeout(400)
        w1 = page.locator("nav").first.bounding_box()["width"]
        check("sidebar collapses", w1 < w0, f"{w0:.0f} -> {w1:.0f}")
        nav_buttons.nth(0).click()
        page.wait_for_timeout(200)
        check("language toggle", page.get_attribute("html", "dir") == "ltr")

        # 10. catalog search, admission scopes, capabilities rows
        page.click("[data-nav=catalog]")
        page.wait_for_selector("ce-catalog [data-testid=catalog-viewport]")
        page.locator("ce-catalog input").first.fill("deepseek")
        page.wait_for_timeout(900)
        n = page.locator("ce-catalog .r").count()
        check("catalog search filters", 0 < n < 60, f"rows={n}")
        page.click("[data-nav=admission]")
        page.wait_for_timeout(800)
        gw_rows = page.locator("ce-admission td.mono", has_text="gw_").count()
        check("admission shows gateway provider scopes", gw_rows >= 1, f"gw scopes={gw_rows}")
        page.click("[data-nav=capabilities]")
        page.wait_for_timeout(800)
        check("capabilities screen has rows", page.locator("ce-capabilities tr").count() > 1)

        check("no page errors", not errors, " | ".join(errors)[:300])
        check("no 5xx from UI flows", not http5xx, " | ".join(http5xx)[:300])
        page.screenshot(path="evidence/ce20/ui_e2e_final.png")
        browser.close()
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
