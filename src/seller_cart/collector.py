from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from patchright.async_api import async_playwright, Page, Route


BASE_URL = "https://477477.ru"

SECTIONS = {
    "kanalizaciya": "/catalog/kanalizaciya",
    "truby-i-fitingi": "/catalog/truby-i-fitingi",
    "fitingi": "/catalog/truby-i-fitingi/podrazdel/nerzhaveyushchaya-stal?sort=price_asc",
}

PROFILE_DIR = Path(".patchright-profile")

YANDEX_BROWSER = (
    Path(os.environ["LOCALAPPDATA"])
    / "Yandex"
    / "YandexBrowser"
    / "Application"
    / "browser.exe"
)

MIN_DELAY = 2.0
MAX_DELAY = 4.0

NAVIGATION_TIMEOUT = 30_000
MAX_RETRIES = 3


CATEGORY_CARDS_JS = r"""
() => {
    const cards = [...document.querySelectorAll('a.product-list-card')].map(card => {
        const badges = [
            ...card.querySelectorAll('.badges .badge')
        ]
            .map(x => x.textContent?.trim())
            .filter(Boolean);

        const characteristics = {};

        const paragraphs = [...card.querySelectorAll('p')]
            .map(x => x.innerText?.trim())
            .filter(Boolean);

        const fields = [
            ...badges,
            ...paragraphs.join(' ').split(/(?<=\.)\s+/)
        ];

        let article = null;

        for (const field of fields) {
            const pos = field.indexOf(':');

            if (pos <= 0)
                continue;

            const key = field.slice(0, pos).trim();

            const value = field
                .slice(pos + 1)
                .trim()
                .replace(/[.;,]+$/, '');

            if (!key || !value)
                continue;

            characteristics[key] = value;

            if (key.toLowerCase() === 'артикул')
                article = value;
        }

        const href = card.getAttribute('href');

        return {
            url: href
                ? new URL(href, location.origin).href
                : null,

            name:
                card.querySelector('h3')?.innerText?.trim()
                || null,

            manufacturer:
                card.querySelector('.brand-line')?.innerText?.trim()
                || null,

            article,

            category:
                characteristics['Группа']
                ?? characteristics['Тип']
                ?? null,

            characteristics:
                Object.keys(characteristics).length
                    ? characteristics
                    : null,

            raw_badges: badges,
        };
    });

    const next =
        document.querySelector(
            'nav.catalog-pagination a[rel="next"]'
        );

    return {
        cards,

        next_page:
            next?.href
            || null,

        title:
            document.title,

        page_label:
            document.querySelector('.section-head .meta')
                ?.innerText
                ?.trim()
            || null,
    };
}
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def sleep_between_requests() -> None:
    delay = random.uniform(MIN_DELAY, MAX_DELAY)
    print(f"[sleep] {delay:.2f}s")
    await asyncio.sleep(delay)


async def resource_filter(route: Route) -> None:
    """
    Каталогу для парсинга не нужны тяжёлые ресурсы.
    JS/document/XHR оставляем.
    """

    resource_type = route.request.resource_type

    if resource_type in {
        "image",
        "media",
        "font",
    }:
        await route.abort()
        return

    await route.continue_()


def valid_catalog_url(url: str, section: str) -> bool:
    parsed = urlparse(url)

    if parsed.netloc != "477477.ru":
        return False

    root = urlparse(SECTIONS[section]).path

    return (
        parsed.path == root
        or parsed.path.startswith(root + "/")
    )


def normalize_card(
    card: dict,
    section: str,
    collected_at: str,
) -> dict | None:

    url = card.get("url")
    name = card.get("name")

    if not url or not name:
        return None

    parsed = urlparse(url)

    expected = urlparse(SECTIONS[section]).path.split("/podrazdel/", 1)[0] + "/"

    if (
        parsed.scheme != "https"
        or parsed.netloc != "477477.ru"
        or not parsed.path.startswith(expected)
    ):
        return None

    characteristics = card.get("characteristics")

    article = (
        card.get("article")
        or (characteristics or {}).get("Артикул")
    )

    return {
        "url": url,
        "name": name,
        "manufacturer": card.get("manufacturer"),
        "article": article,

        "category":
            card.get("category")
            or (characteristics or {}).get("Группа")
            or (characteristics or {}).get("Тип"),

        "characteristics": characteristics,
        "raw_badges": card.get("raw_badges") or [],
        "collected_at": collected_at,
    }


async def navigate(
    page: Page,
    url: str,
) -> tuple[bool, int | None]:

    for attempt in range(1, MAX_RETRIES + 1):

        print(
            f"[goto] {url} "
            f"(attempt {attempt}/{MAX_RETRIES})"
        )

        try:
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT,
            )

            status = response.status if response else None

            print(f"[http] {status}")

            if status == 200:
                return True, status

            # Сервер явно просит остановиться.
            if status in (403, 429):
                return False, status

            if status and 400 <= status < 500:
                return False, status

        except Exception as exc:
            print(
                f"[error] navigation: "
                f"{type(exc).__name__}: {exc}"
            )

        if attempt < MAX_RETRIES:
            delay = random.uniform(4, 8)

            print(
                f"[retry] sleeping "
                f"{delay:.1f}s"
            )

            await asyncio.sleep(delay)

    return False, None


def save_json(
    output: Path,
    records: list[dict],
    report: dict,
) -> None:

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            records,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report_path = (
        output.parent
        / "collection_report.json"
    )

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


async def collect(
    output: Path,
    limit: int = 60,
    section: str = "kanalizaciya",
    max_seconds: int = 7200,
) -> dict:

    if section not in SECTIONS:
        raise ValueError(
            f"Unknown section: {section}"
        )

    limit = max(
        1,
        min(limit, 1000),
    )

    started = time.monotonic()
    started_at = utc_now()

    records: list[dict] = []

    seen_urls: set[str] = set()
    visited_pages: set[str] = set()

    errors: list[str] = []

    stats = {
        "requests": 0,
        "documents": 0,
        "xhr": 0,
        "fetch": 0,
        "blocked_resources": 0,
    }

    access_blocked = False

    async with async_playwright() as p:

        #
        # persistent_context намного удобнее:
        # cookies/localStorage между запусками сохраняются.
        #
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),

            executable_path=str(YANDEX_BROWSER),

            headless=False,
            no_viewport=True,

            args=[
                "--start-maximized",
            ],
        )

        page = (
            context.pages[0]
            if context.pages
            else await context.new_page()
        )

        #
        # Режем тяжёлые ресурсы.
        #
        async def route_handler(route: Route):
            resource_type = (
                route.request.resource_type
            )

            if resource_type in {
                "image",
                "font",
                "media",
            }:
                stats["blocked_resources"] += 1
                await route.abort()
                return

            await route.continue_()

        await page.route(
            "**/*",
            route_handler,
        )

        #
        # Считаем реальные browser requests.
        #
        def on_request(request):
            stats["requests"] += 1

            resource_type = (
                request.resource_type
            )

            if resource_type in stats:
                stats[resource_type] += 1

        page.on(
            "request",
            on_request,
        )

        next_url: str | None = urljoin(
            BASE_URL,
            SECTIONS[section],
        )

        page_number = 0

        while (
            next_url
            and len(records) < limit
            and time.monotonic() - started
                < max_seconds
        ):

            if next_url in visited_pages:
                errors.append(
                    f"Pagination loop: {next_url}"
                )
                break

            if not valid_catalog_url(
                next_url,
                section,
            ):
                errors.append(
                    f"URL outside section: "
                    f"{next_url}"
                )
                break

            visited_pages.add(next_url)

            page_number += 1

            print()
            print("=" * 70)
            print(
                f"PAGE {page_number}: "
                f"{next_url}"
            )
            print("=" * 70)

            ok, status = await navigate(
                page,
                next_url,
            )

            if not ok:

                errors.append(
                    f"{next_url}: HTTP {status}"
                )

                if status in (403, 429):
                    access_blocked = True
                    print(
                        "[stop] Server returned "
                        f"{status}; stopping."
                    )

                break

            #
            # Иногда DOM строится после
            # DOMContentLoaded.
            #
            try:
                await page.wait_for_selector(
                    "a.product-list-card",
                    timeout=10_000,
                )

            except Exception:
                print(
                    "[warn] product cards "
                    "did not appear in 10s"
                )

            listing = await page.evaluate(
                CATEGORY_CARDS_JS
            )

            cards = listing.get(
                "cards",
                [],
            )

            print(
                f"[page] cards={len(cards)}"
            )

            if not cards:
                errors.append(
                    f"{next_url}: "
                    "product cards not found"
                )
                break

            added = 0

            for card in cards:

                item = normalize_card(
                    card,
                    section,
                    started_at,
                )

                if item is None:
                    continue

                url = item["url"]

                if url in seen_urls:
                    continue

                seen_urls.add(url)
                records.append(item)

                added += 1

                print(
                    f"[+] {len(records):04d} "
                    f"{item['name']}"
                )

                if len(records) >= limit:
                    break

            print(
                f"[page] added={added} "
                f"total={len(records)} "
                f"requests={stats['requests']}"
            )

            #
            # Сохраняем результат после
            # КАЖДОЙ страницы.
            #
            temporary_report = {
                "source": BASE_URL,
                "section": section,
                "started_at": started_at,
                "parsed": len(records),
                "visited_listing_pages":
                    len(visited_pages),
                "requests": stats,
                "errors": errors,
                "access_blocked":
                    access_blocked,
            }

            save_json(
                output,
                records,
                temporary_report,
            )

            if len(records) >= limit:
                break

            next_url = listing.get(
                "next_page"
            )

            if next_url:
                next_url = urljoin(
                    BASE_URL,
                    next_url,
                )

            if next_url:
                await sleep_between_requests()

        await context.close()

    report = {
        "source": BASE_URL,
        "section": section,
        "section_path": SECTIONS[section],

        "started_at": started_at,

        "parsed": len(records),

        "unique_urls": len(
            seen_urls
        ),

        "visited_listing_pages": len(
            visited_pages
        ),

        "limit": limit,

        "requests": stats,

        "access_blocked":
            access_blocked,

        "errors": errors,

        "elapsed_seconds": round(
            time.monotonic() - started,
            2,
        ),
    }

    save_json(
        output,
        records,
        report,
    )

    print()
    print("=" * 70)
    print("DONE")
    print("=" * 70)

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )

    return report


if __name__ == "__main__":

    asyncio.run(
        collect(
            Path(
                "data/fitingi_live.json"
            ),
            limit=60,
            section="fitingi",
        )
    )
