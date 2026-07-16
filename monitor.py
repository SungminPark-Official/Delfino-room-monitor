from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import (
    Browser,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


TARGET_URL = os.environ["TARGET_URL"]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ARTIFACT_DIR = Path("artifacts")
ARTIFACT_DIR.mkdir(exist_ok=True)

REQUIRED_TERMS = [
    "골드",
    "클린",
    "설악마운틴뷰",
    "침대",
]

UNAVAILABLE_TERMS = [
    "판매완료",
    "예약마감",
    "예약 마감",
    "매진",
    "객실없음",
    "객실 없음",
    "예약불가",
    "예약 불가",
    "상품 준비중",
]

BLOCKED_TERMS = [
    "비정상적인 접근",
    "접근이 제한",
    "접근이 차단",
    "captcha",
    "로봇이 아닙니다",
    "too many requests",
]


def now_kst_string() -> str:
    """Return the current time expressed as Korea Standard Time."""
    from datetime import timedelta

    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S KST")


def send_telegram(message: str) -> None:
    """Send a Telegram message when Telegram configuration exists."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets are not configured. Message follows:")
        print(message)
        return

    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    response.raise_for_status()


def block_heavy_resources(page: Page) -> None:
    """Reduce unnecessary traffic while preserving scripts and API requests."""

    def route_handler(route) -> None:
        resource_type = route.request.resource_type

        if resource_type in {"image", "media", "font"}:
            route.abort()
        else:
            route.continue_()

    page.route("**/*", route_handler)


def save_debug_files(page: Page, prefix: str) -> None:
    """Save diagnostic files for positive detections and failures."""
    try:
        page.screenshot(
            path=str(ARTIFACT_DIR / f"{prefix}.png"),
            full_page=True,
        )
    except Exception as exc:
        print(f"Screenshot failed: {exc}")

    try:
        html = page.content()
        (ARTIFACT_DIR / f"{prefix}.html").write_text(
            html,
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"HTML save failed: {exc}")


def find_candidate(page: Page) -> dict | None:
    """
    Find a relatively small DOM element containing all requested terms.

    This is intentionally a pilot heuristic. After inspecting the real page
    structure, replace it with a selector targeting the exact room card.
    """
    return page.evaluate(
        """
        ({ requiredTerms, unavailableTerms }) => {
          const elements = Array.from(
            document.querySelectorAll(
              'article, li, section, div, a, button'
            )
          );

          const candidates = elements
            .map((element) => {
              const text = (element.innerText || '')
                .replace(/\\s+/g, ' ')
                .trim();

              return {
                tag: element.tagName,
                text,
                length: text.length
              };
            })
            .filter(({ text, length }) => {
              if (length < 10 || length > 1200) {
                return false;
              }

              const hasAllRequiredTerms =
                requiredTerms.every((term) => text.includes(term));

              const hasUnavailableTerm =
                unavailableTerms.some((term) => text.includes(term));

              return hasAllRequiredTerms && !hasUnavailableTerm;
            })
            .sort((a, b) => a.length - b.length);

          return candidates.length > 0 ? candidates[0] : null;
        }
        """,
        {
            "requiredTerms": REQUIRED_TERMS,
            "unavailableTerms": UNAVAILABLE_TERMS,
        },
    )


def open_browser() -> tuple[Browser, Page]:
    raise RuntimeError("This function is only present for type documentation.")


def main() -> int:
    print(f"Check started: {now_kst_string()}")
    print(f"Required terms: {REQUIRED_TERMS}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
        )

        context = browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={
                "width": 1440,
                "height": 1400,
            },
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()
        block_heavy_resources(page)

        try:
            response = page.goto(
                TARGET_URL,
                wait_until="domcontentloaded",
                timeout=60_000,
            )

            if response is not None:
                print(f"Initial HTTP status: {response.status}")

                if response.status in {403, 429}:
                    save_debug_files(page, "blocked")
                    print("Access was blocked. Monitoring stopped.")
                    return 2

            # 동적 객실 데이터가 렌더링될 시간을 줍니다.
            page.wait_for_timeout(8_000)

            body_text = page.locator("body").inner_text(timeout=20_000)
            normalized_body = " ".join(body_text.split())

            blocked_detected = any(
                term.lower() in normalized_body.lower()
                for term in BLOCKED_TERMS
            )

            if blocked_detected:
                save_debug_files(page, "blocked")
                print("A blocking or CAPTCHA message was detected.")
                return 2

            candidate = find_candidate(page)

            if candidate is None:
                print("No matching available room candidate was found.")
                print(f"Page title: {page.title()}")
                return 0

            print("Matching candidate found:")
            print(json.dumps(candidate, ensure_ascii=False, indent=2))

            save_debug_files(page, "room-found")

            send_telegram(
                "🚨 야놀자 객실 후보가 발견됐습니다.\n\n"
                f"조건: {', '.join(REQUIRED_TERMS)}\n"
                f"확인 시각: {now_kst_string()}\n\n"
                f"화면 문구:\n{candidate['text'][:700]}\n\n"
                f"{TARGET_URL}"
            )

            return 0

        except PlaywrightTimeoutError as exc:
            print(f"Page timeout: {exc}")
            save_debug_files(page, "timeout")
            return 1

        except Exception as exc:
            print(f"Unexpected error: {type(exc).__name__}: {exc}")
            save_debug_files(page, "error")
            return 1

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())