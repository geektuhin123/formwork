"""Playwright flows for the console at 1440 px and 390 px, against the real API, a real Postgres and the offline
FakeProvider: define the target format (weak-description warning), add sources, run, select a cell, switch source
tabs, approve, edit, resolve a conflict, clear the queue, export.

    uv run pytest apps/web/e2e -m e2e -s      (needs: cd apps/web && npm ci && npm run build)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
SHOTS = Path(__file__).parent / "screenshots"
pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture(scope="module")
def stack():
    if not (ROOT / "apps/web/.next").exists():
        pytest.skip("web app is not built (cd apps/web && npm ci && npm run build)")
    import dev_stack

    try:
        s = dev_stack.start()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    yield s
    s.stop()


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    from formwork_testkit import synth

    d = tmp_path_factory.mktemp("sources")
    orders = [synth.make_order(3), synth.make_order(4)]  # a short shipment and a quantity conflict
    (d / "po_register.xlsx").write_bytes(synth.po_register_xlsx(orders))
    for o in orders:
        (d / f"invoice_{o.po_number}.pdf").write_bytes(synth.invoice_pdf(o.invoice))
        (d / f"grn_{o.po_number}_scan.pdf").write_bytes(synth.scan_pdf(synth.grn_pdf(o), seed=5))
    return sorted(str(p) for p in d.iterdir())


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_define_run_review_export(stack, sources, width, height):
    from formwork_testkit.schemas import reconciliation_json_schema
    from playwright.sync_api import expect, sync_playwright

    SHOTS.mkdir(exist_ok=True)
    tag = f"{width}"
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": width, "height": height}).new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 1. Target format: JSON Schema -> fields, with the weak-description warning
        page.goto(f"{stack.web_url}/new")
        page.get_by_role("button", name="JSON Schema").click()
        page.get_by_label("JSON Schema").fill(json.dumps(reconciliation_json_schema()))
        page.get_by_role("button", name="Compile to fields").click()
        expect(page.locator('tr[data-field="po_number"]')).to_be_visible()
        expect(page.get_by_text("Descriptions look specific")).to_be_visible()
        desc = page.get_by_label("Description of grn_number")
        original = desc.input_value()
        desc.fill("GRN number")
        expect(page.get_by_test_id("weak-count")).to_contain_text("1 weak description")
        expect(page.locator('tr[data-field="grn_number"]')).to_contain_text("Weak description (2 words)")
        page.screenshot(path=str(SHOTS / f"schema-weak-{tag}.png"))
        desc.fill(original)
        expect(page.get_by_test_id("weak-count")).to_have_count(0)

        # 2. Sources -> run
        page.get_by_role("button", name="Continue to sources").click()
        page.get_by_test_id("source-input").set_input_files(sources)
        expect(page.locator(".src")).to_have_count(5, timeout=30_000)
        page.locator("#thr0").fill(
            "0.98"
        )  # a strict threshold sends most single-source fields to review: a real queue to clear
        page.get_by_test_id("start-job").click()
        page.wait_for_url("**/jobs/job_*", timeout=30_000)
        expect(page.get_by_role("heading", name="Review")).to_be_visible(timeout=120_000)
        expect(page.get_by_test_id("inspector")).to_be_visible()
        page.screenshot(path=str(SHOTS / f"review-{tag}.png"), full_page=width < 500)

        # 3. Select a cell: the evidence region is highlighted; a scanned source uses the page image, a digital PDF uses PDF.js
        page.locator('[data-testid="cell"][data-field="grn_number"]').first.click()
        expect(page.get_by_test_id("viewer")).to_have_attribute("data-kind", "image")
        expect(page.get_by_test_id("highlight")).to_be_visible()
        page.locator('[data-testid="cell"][data-field="invoice_number"]').first.click()
        expect(page.get_by_test_id("viewer")).to_have_attribute("data-kind", "pdf")
        expect(page.locator('canvas[data-rendered="pdfjs"]')).to_be_visible(timeout=30_000)
        box = page.get_by_test_id("highlight").bounding_box()
        assert box and box["width"] > 20 and box["height"] > 5

        # 3b. Lineage graph: toggle on, a field node drives the same inspector, record tabs switch records
        page.get_by_role("button", name="Lineage").click()
        expect(page.get_by_test_id("lineage-graph")).to_be_visible()
        page.screenshot(path=str(SHOTS / f"lineage-{tag}.png"))
        page.locator('[data-testid="cell"][data-field="po_number"]').first.click()
        expect(page.get_by_test_id("insp-value")).to_be_visible()
        expect(page.locator(".ln-edge.winner.on")).not_to_have_count(0)
        tabs = page.get_by_test_id("lineage-record-tab")
        if tabs.count() > 1:
            other_key = tabs.nth(1).inner_text()
            tabs.nth(1).click()
            expect(page.locator(".insp .fname").first).to_contain_text(other_key)
        page.get_by_role("button", name="Grid").click()

        # 4. Switch source tabs: po_number is cited to the sheet (row highlighted) and to both PDFs
        page.locator('[data-testid="cell"][data-field="po_number"]').first.click()
        tabs = page.get_by_test_id("source-tab")
        expect(tabs).to_have_count(3)
        kinds = set()
        for i in range(3):
            tabs.nth(i).click()
            expect(page.get_by_test_id("highlight")).to_be_visible(timeout=30_000)
            kinds.add(page.get_by_test_id("viewer").get_attribute("data-kind"))
        assert kinds == {"sheet", "pdf", "image"}

        # 5. Resolve the conflict by picking the competing value
        page.get_by_role("button", name="Conflicts").click()
        page.locator('[data-testid="cell"][data-status="conflict"]').first.click()
        expect(page.get_by_test_id("insp-status")).to_contain_text("Sources disagree")
        other = page.locator(".alt b").first.inner_text()
        page.get_by_test_id("use-alt").first.click()
        expect(page.locator('[data-testid="cell"][data-status="conflict"]')).to_have_count(0, timeout=15_000)
        page.get_by_role("button", name="All fields").click()
        assert page.locator('[data-testid="cell"][data-field="ordered_qty"]', has_text=other).count() >= 1

        # 6. Edit a value (the short-shipment quantity fails its rule and cannot simply be approved)
        page.locator('[data-testid="cell"][data-field="received_qty"][data-status="review"]').first.click()
        page.get_by_test_id("edit").click()
        page.get_by_test_id("edit-input").fill("215")
        page.get_by_test_id("save-edit").click()
        expect(page.locator('[data-testid="cell"][data-field="received_qty"]', has_text="215")).to_have_count(
            1, timeout=15_000
        )

        # 7. Clear the rest of the queue with Approve / Next issue, timing it
        started = time.monotonic()
        cleared = 0
        for _ in range(80):
            if page.get_by_test_id("done-banner").count():
                break
            if page.get_by_test_id("approve").count():
                page.get_by_test_id("approve").click()
            else:  # failed validation: needs a value
                page.get_by_test_id("edit").click()
                page.get_by_test_id("save-edit").click()
            cleared += 1
            page.wait_for_timeout(350)
        if not page.get_by_test_id("done-banner").count():
            print(
                "STUCK",
                cleared,
                page.get_by_test_id("count-review").inner_text(),
                page.locator(".alert").all_inner_texts(),
                page.get_by_test_id("insp-status").inner_text(),
                page.locator(".insp .fname").first.inner_text(),
            )
        expect(page.get_by_test_id("done-banner")).to_be_visible(timeout=15_000)
        print(
            f"\n[{tag}px] cleared {cleared} more review items in {time.monotonic() - started:.1f}s (scripted clicks)"
        )
        page.screenshot(path=str(SHOTS / f"review-done-{tag}.png"))

        # 8. Export
        page.get_by_role("button", name="Go to export").click()
        expect(page.get_by_text("Every field is approved")).to_be_visible(timeout=15_000)
        expect(page.get_by_test_id("export-preview")).to_contain_text("PO-2026-0203")
        page.get_by_role("button", name="CSV").click()
        expect(page.get_by_test_id("export-preview")).to_contain_text("po_number,vendor_name")
        with page.expect_download() as dl:
            page.get_by_test_id("download").click()
        assert dl.value.suggested_filename.endswith(".csv")
        page.screenshot(path=str(SHOTS / f"export-{tag}.png"))

        # no horizontal page overflow at this width, and the job list shows the finished job
        wide = page.evaluate(
            "[...document.querySelectorAll('main *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1 && !e.closest('.scroll-x, .grid-wrap, .lineage, pre, .viewer, .steps')).map(e => e.className || e.tagName).slice(0, 5)"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), wide
        page.goto(stack.web_url)
        expect(page.get_by_text("Completed").first).to_be_visible(timeout=15_000)
        browser.close()
    assert not [e for e in errors if "favicon" not in e], errors
