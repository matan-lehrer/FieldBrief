"""Report PDF: a short, Playwright-rendered HTML->PDF of the synthesis
highlights (reports/render.py's render_pdf_html — not the full web report;
see its docstring for why). Reports are immutable, so a rendered PDF never
goes stale.

Needs Playwright's Chromium system shared libraries installed
(`playwright install-deps chromium`, needs root) — verified working once
those were installed.
"""

from playwright.async_api import async_playwright


async def render_report_pdf(html: str) -> bytes:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="load")
            return await page.pdf(format="A4", print_background=True)
        finally:
            await browser.close()
