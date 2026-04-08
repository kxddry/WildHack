"""
E2E browser tests for the WildHack Dashboard (Next.js).
Tests all 5 pages: Readiness, Overview, Forecasts, Dispatch, Quality.

Usage:
    pytest tests/e2e/test_dashboard_e2e.py -v --headed  (with browser window)
    pytest tests/e2e/test_dashboard_e2e.py -v            (headless)

Requires:
    - docker-compose up (all services running)
    - pip install playwright pytest
    - playwright install chromium
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect, sync_playwright

BASE_URL = "http://localhost:4000"

# Timeout for navigation and assertions (ms)
NAV_TIMEOUT = 15_000
LOAD_TIMEOUT = 10_000


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.set_default_timeout(NAV_TIMEOUT)
    yield pg
    pg.close()
    ctx.close()


def _wait_for_content_loaded(page: Page) -> None:
    """Wait until skeleton loaders (animate-pulse) disappear."""
    page.wait_for_timeout(500)
    try:
        page.wait_for_selector(".animate-pulse", state="hidden", timeout=LOAD_TIMEOUT)
    except Exception:
        pass  # Page may not have skeletons


def _nav_links(page: Page) -> list[str]:
    """Return text content of all sidebar nav links."""
    return [el.text_content().strip() for el in page.query_selector_all("nav a")]


# ── 1. Readiness Page ──────────────────────────────────────────────


@pytest.mark.e2e
class TestReadinessPage:
    def test_page_loads(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/readiness")
        _wait_for_content_loaded(page)
        expect(page.locator("h1")).to_contain_text("System Readiness")

    def test_sidebar_has_all_nav_links(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/readiness")
        _wait_for_content_loaded(page)
        links = _nav_links(page)
        for name in ("Readiness", "Overview", "Forecasts", "Dispatch", "Quality"):
            assert name in links, f"Missing nav link: {name}"

    def test_readiness_link_is_active(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/readiness")
        _wait_for_content_loaded(page)
        active = page.locator("nav a.bg-sidebar-accent")
        expect(active).to_contain_text("Readiness")

    def test_health_check_cards_render(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/readiness")
        _wait_for_content_loaded(page)
        # Should have multiple check cards (grid items)
        cards = page.locator(".rounded-xl.border").all()
        assert len(cards) >= 3, f"Expected at least 3 check cards, got {len(cards)}"

    def test_no_error_banner(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/readiness")
        _wait_for_content_loaded(page)
        # No visible error banners
        errors = page.locator("[role='alert']").all()
        for err in errors:
            text = err.text_content() or ""
            assert "error" not in text.lower() or "fail" not in text.lower(), (
                f"Unexpected error banner: {text}"
            )


# ── 2. Overview Page ───────────────────────────────────────────────


@pytest.mark.e2e
class TestOverviewPage:
    def test_page_loads(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/overview")
        _wait_for_content_loaded(page)
        expect(page.locator("h1")).to_contain_text("Overview")

    def test_overview_link_active(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/overview")
        _wait_for_content_loaded(page)
        active = page.locator("nav a.bg-sidebar-accent")
        expect(active).to_contain_text("Overview")

    def test_kpi_metrics_present(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/overview")
        _wait_for_content_loaded(page)
        # Look for metric-like elements (cards with numbers)
        main = page.locator("main")
        text = main.text_content() or ""
        # Overview should show warehouse-related info
        assert len(text) > 50, "Main content area appears empty"

    def test_warehouse_table_or_chart(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/overview")
        _wait_for_content_loaded(page)
        # Look for table or chart elements
        has_table = page.locator("table").count() > 0
        has_chart = page.locator("canvas, svg.recharts-surface, [class*='chart']").count() > 0
        assert has_table or has_chart, "Expected a table or chart on Overview page"


# ── 3. Forecasts Page ──────────────────────────────────────────────


@pytest.mark.e2e
class TestForecastsPage:
    def test_page_loads(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/forecasts")
        _wait_for_content_loaded(page)
        expect(page.locator("h1")).to_contain_text("Forecast")

    def test_forecasts_link_active(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/forecasts")
        _wait_for_content_loaded(page)
        active = page.locator("nav a.bg-sidebar-accent")
        expect(active).to_contain_text("Forecasts")

    def test_warehouse_selector_exists(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/forecasts")
        _wait_for_content_loaded(page)
        # Look for a select/combobox for warehouse selection
        selectors = page.locator("select, [role='combobox'], button[role='combobox']")
        assert selectors.count() > 0, "Expected a warehouse selector on Forecasts page"

    def test_forecast_content_renders(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/forecasts")
        _wait_for_content_loaded(page)
        main = page.locator("main")
        text = main.text_content() or ""
        assert len(text) > 50, "Forecasts page content appears empty"


# ── 4. Dispatch Page ───────────────────────────────────────────────


@pytest.mark.e2e
class TestDispatchPage:
    def test_page_loads(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/dispatch")
        _wait_for_content_loaded(page)
        expect(page.locator("h1")).to_contain_text("Dispatch")

    def test_dispatch_link_active(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/dispatch")
        _wait_for_content_loaded(page)
        active = page.locator("nav a.bg-sidebar-accent")
        expect(active).to_contain_text("Dispatch")

    def test_dispatch_controls_exist(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/dispatch")
        _wait_for_content_loaded(page)
        # Should have a Run Dispatch button or similar action trigger
        buttons = page.locator("button")
        button_texts = [b.text_content().strip().lower() for b in buttons.all()]
        has_dispatch_button = any("dispatch" in t or "run" in t for t in button_texts)
        has_selector = page.locator(
            "select, [role='combobox'], button[role='combobox']"
        ).count() > 0
        assert has_dispatch_button or has_selector, (
            f"Expected dispatch controls, found buttons: {button_texts}"
        )

    def test_transport_requests_table(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/dispatch")
        _wait_for_content_loaded(page)
        # Table with transport requests or empty state
        main = page.locator("main")
        text = main.text_content() or ""
        has_table = page.locator("table").count() > 0
        has_content = len(text) > 100
        assert has_table or has_content, "Dispatch page has no table or content"


# ── 5. Quality Page ────────────────────────────────────────────────


@pytest.mark.e2e
class TestQualityPage:
    def test_page_loads(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/quality")
        _wait_for_content_loaded(page)
        expect(page.locator("h1")).to_contain_text("Quality")

    def test_quality_link_active(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/quality")
        _wait_for_content_loaded(page)
        active = page.locator("nav a.bg-sidebar-accent")
        expect(active).to_contain_text("Quality")

    def test_model_info_section(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/quality")
        _wait_for_content_loaded(page)
        main = page.locator("main")
        text = main.text_content() or ""
        # Should mention model-related terms
        text_lower = text.lower()
        has_model_info = any(
            term in text_lower for term in ("model", "wape", "accuracy", "version", "quality")
        )
        assert has_model_info, f"Quality page missing model info. Content: {text[:200]}"

    def test_warehouse_route_selectors(self, page: Page) -> None:
        page.goto(f"{BASE_URL}/quality")
        _wait_for_content_loaded(page)
        selectors = page.locator("select, [role='combobox'], button[role='combobox']")
        # Quality page should have warehouse and/or route selectors
        assert selectors.count() >= 1, "Expected at least 1 selector on Quality page"


# ── 6. Cross-page navigation ──────────────────────────────────────


@pytest.mark.e2e
class TestNavigation:
    def test_navigate_all_pages_via_sidebar(self, page: Page) -> None:
        """Click through all 5 pages via sidebar and verify each loads."""
        page.goto(f"{BASE_URL}/readiness")
        _wait_for_content_loaded(page)

        pages_map = {
            "Overview": "Overview",
            "Forecasts": "Forecast",
            "Dispatch": "Dispatch",
            "Quality": "Quality",
            "Readiness": "Readiness",
        }

        for link_text, expected_h1 in pages_map.items():
            nav_link = page.locator(f"nav a:has-text('{link_text}')")
            nav_link.click()
            _wait_for_content_loaded(page)
            expect(page.locator("h1")).to_contain_text(expected_h1)

    def test_no_console_errors(self, page: Page) -> None:
        """Navigate through all pages and check for JS console errors."""
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        for path in ("/readiness", "/overview", "/forecasts", "/dispatch", "/quality"):
            page.goto(f"{BASE_URL}{path}")
            _wait_for_content_loaded(page)

        critical_errors = [e for e in errors if "hydration" not in e.lower()]
        assert len(critical_errors) == 0, f"JS console errors found: {critical_errors}"

    def test_default_redirect(self, page: Page) -> None:
        """Root URL should redirect to /readiness or show content."""
        page.goto(BASE_URL)
        _wait_for_content_loaded(page)
        # Should either redirect to readiness or show main content
        url = page.url
        has_content = len(page.locator("main").text_content() or "") > 20
        assert "/readiness" in url or has_content, (
            f"Root URL did not redirect properly. URL: {url}"
        )
