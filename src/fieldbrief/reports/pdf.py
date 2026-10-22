"""Report PDF: same Jinja2 HTML as the web view, print stylesheet already
baked in (@media print), rendered by Playwright (design.md §9). Reports are
immutable, so a rendered PDF never goes stale.

Not executable in this sandbox: Playwright's Chromium needs system shared
libraries (libnspr4.so and others) this environment has no root access to
install (`playwright install --with-deps` requires sudo). The browser
binary itself downloaded fine; only the OS-level deps are missing. Verify
on a machine with Playwright's system deps installed, or inside the
project's own containers once stage 8 adds a Dockerfile for the app.
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
