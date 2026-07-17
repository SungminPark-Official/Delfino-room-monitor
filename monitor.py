from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

TARGET_URL = os.environ["TARGET_URL"]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TARGET_ROOM_NAME = "골드-클린/설악마운틴뷰/침대"

ARTIFACT_DIR = Path("artifacts")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


UNAVAILABLE_TERMS = [
    "판매완료",
    "판매 완료",
    "예약마감",
    "예약 마감",
    "매진",
    "객실없음",
    "객실 없음",
    "예약불가",
    "예약 불가",
    "상품 준비중",
    "판매 종료",
    "판매종료",
    "남은 객실 없음",
]


AVAILABLE_BUTTON_TERMS = [
    "예약하기",
    "객실 선택",
    "객실선택",
    "선택하기",
    "구매하기",
    "결제하기",
]


BLOCKED_TERMS = [
    "비정상적인 접근",
    "접근이 제한",
    "접근이 차단",
    "captcha",
    "로봇이 아닙니다",
    "too many requests",
    "access denied",
    "temporarily blocked",
]


# ---------------------------------------------------------------------------
# 공통 유틸리티
# ---------------------------------------------------------------------------

def now_kst_string() -> str:
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S KST")


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def write_github_summary(
    status: str,
    description: str,
    details: str = "",
) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")

    if not summary_path:
        return

    icon_by_status = {
        "AVAILABLE": "🚨",
        "UNAVAILABLE": "❌",
        "UNKNOWN": "⚠️",
        "NOT_FOUND": "🔍",
        "BLOCKED": "⛔",
        "ERROR": "💥",
    }

    icon = icon_by_status.get(status, "ℹ️")

    with open(summary_path, "a", encoding="utf-8") as file:
        file.write(f"## {icon} 결과: {status}\n\n")
        file.write(f"{description}\n\n")
        file.write(f"- 확인 시각: `{now_kst_string()}`\n")
        file.write(f"- 대상 객실: `{TARGET_ROOM_NAME}`\n")

        if details:
            file.write("\n```text\n")
            file.write(details[:2000])
            file.write("\n```\n")

        file.write(f"\n[야놀자 페이지 열기]({TARGET_URL})\n\n")
        file.write("---\n\n")


def send_telegram(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram is not configured.")
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


# ---------------------------------------------------------------------------
# 최소 진단 파일
# ---------------------------------------------------------------------------

def save_minimal_debug(
    page: Page,
    prefix: str,
) -> None:
    """
    AVAILABLE, UNKNOWN, BLOCKED, ERROR에서만 호출합니다.

    저장 파일:
    - 현재 화면 PNG
    - 현재 HTML
    - 현재 보이는 텍스트
    """

    try:
        page.screenshot(
            path=str(
                ARTIFACT_DIR / f"{prefix}-viewport.png"
            ),
            full_page=False,
            animations="disabled",
            caret="hide",
            timeout=15_000,
        )
    except Exception as exc:
        print(f"Screenshot failed: {exc}")

    try:
        (
            ARTIFACT_DIR / f"{prefix}.html"
        ).write_text(
            page.content(),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"HTML save failed: {exc}")

    try:
        body_text = page.locator("body").inner_text(
            timeout=10_000,
        )

        (
            ARTIFACT_DIR / f"{prefix}.txt"
        ).write_text(
            body_text,
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"Text save failed: {exc}")


# ---------------------------------------------------------------------------
# 렌더링 및 객실 탐색
# ---------------------------------------------------------------------------

def wait_for_initial_render(page: Page) -> None:
    try:
        page.wait_for_load_state(
            "load",
            timeout=15_000,
        )
    except PlaywrightTimeoutError:
        print("Load event timeout; continuing.")

    # React 초기 렌더링을 위한 최소 대기
    page.wait_for_timeout(3_000)


def scroll_until_target_room(page: Page) -> bool:
    """
    정확한 대상 객실이 화면에 나타날 때까지만 스크롤합니다.

    발견한 요소에는 data-yanolja-target-room 속성을 추가합니다.
    """

    return page.evaluate(
        """
        async ({ targetRoomName }) => {
          const delay = (ms) =>
            new Promise((resolve) => setTimeout(resolve, ms));

          const normalize = (value) =>
            (value || '')
              .replace(/\\s+/g, ' ')
              .trim();

          const isVisible = (element) => {
            const style =
              window.getComputedStyle(element);

            const rect =
              element.getBoundingClientRect();

            return (
              style.display !== 'none' &&
              style.visibility !== 'hidden' &&
              style.opacity !== '0' &&
              rect.width > 0 &&
              rect.height > 0
            );
          };

          const findTargetElement = () => {
            document
              .querySelectorAll(
                '[data-yanolja-target-room="true"]'
              )
              .forEach((element) => {
                element.removeAttribute(
                  'data-yanolja-target-room'
                );
              });

            const candidates = Array.from(
              document.querySelectorAll(
                'div, span, p, strong, b, ' +
                'h1, h2, h3, h4, h5'
              )
            )
              .filter((element) => {
                if (!isVisible(element)) {
                  return false;
                }

                const text =
                  normalize(element.innerText);

                return (
                  text.includes(targetRoomName) &&
                  text.length <= 500
                );
              })
              .sort((a, b) =>
                normalize(a.innerText).length -
                normalize(b.innerText).length
              );

            if (candidates.length === 0) {
              return false;
            }

            const target = candidates[0];

            target.setAttribute(
              'data-yanolja-target-room',
              'true'
            );

            target.scrollIntoView({
              block: 'center',
              inline: 'nearest',
              behavior: 'instant'
            });

            return true;
          };

          if (findTargetElement()) {
            return true;
          }

          /*
           * 전체 문서 스크롤
           */
          let previousHeight = -1;

          for (let cycle = 0; cycle < 3; cycle += 1) {
            const documentHeight = Math.max(
              document.body?.scrollHeight ?? 0,
              document.documentElement?.scrollHeight ?? 0
            );

            const step = Math.max(
              Math.floor(window.innerHeight * 0.5),
              400
            );

            for (
              let position = 0;
              position <= documentHeight;
              position += step
            ) {
              window.scrollTo({
                top: position,
                left: 0,
                behavior: 'instant'
              });

              await delay(350);

              if (findTargetElement()) {
                await delay(250);
                return true;
              }
            }

            const newHeight = Math.max(
              document.body?.scrollHeight ?? 0,
              document.documentElement?.scrollHeight ?? 0
            );

            if (newHeight === previousHeight) {
              break;
            }

            previousHeight = newHeight;
          }

          /*
           * 내부 스크롤 컨테이너 탐색
           */
          const scrollContainers = Array.from(
            document.querySelectorAll('*')
          ).filter((element) => {
            const style =
              window.getComputedStyle(element);

            return (
              ['auto', 'scroll'].includes(style.overflowY) &&
              element.scrollHeight >
                element.clientHeight + 100 &&
              element.clientHeight > 100
            );
          });

          for (const element of scrollContainers) {
            const maxScroll =
              element.scrollHeight -
              element.clientHeight;

            const step = Math.max(
              Math.floor(element.clientHeight * 0.5),
              300
            );

            for (
              let position = 0;
              position <= maxScroll;
              position += step
            ) {
              element.scrollTop = position;

              await delay(350);

              if (findTargetElement()) {
                await delay(250);
                return true;
              }
            }
          }

          return false;
        }
        """,
        {
            "targetRoomName": TARGET_ROOM_NAME,
        },
    )


# ---------------------------------------------------------------------------
# 예약 가능 여부 판정
# ---------------------------------------------------------------------------

def inspect_target_room(
    page: Page,
) -> dict[str, Any] | None:
    """
    대상 객실 하나의 카드만 탐색합니다.

    활성화된 '예약하기'가 있으면 AVAILABLE을 최우선 판정합니다.
    """

    return page.evaluate(
        """
        ({
          targetRoomName,
          unavailableTerms,
          availableButtonTerms
        }) => {
          const normalize = (value) =>
            (value || '')
              .replace(/\\s+/g, ' ')
              .trim();

          const countOccurrences = (
            text,
            searchText
          ) => text.split(searchText).length - 1;

          const targetElement = document.querySelector(
            '[data-yanolja-target-room="true"]'
          );

          if (!targetElement) {
            return null;
          }

          let current = targetElement;

          for (
            let depth = 0;
            depth <= 14 && current;
            depth += 1
          ) {
            const text =
              normalize(current.innerText);

            if (!text) {
              current = current.parentElement;
              continue;
            }

            /*
             * 대상 객실명이 여러 번 나타나면
             * 여러 객실을 포함한 상위 목록입니다.
             */
            if (
              countOccurrences(
                text,
                targetRoomName
              ) > 1
            ) {
              break;
            }

            if (text.length > 2500) {
              break;
            }

            const controls = Array.from(
              current.querySelectorAll(
                'button, a, [role="button"]'
              )
            ).map((control) => {
              const style =
                window.getComputedStyle(control);

              const rect =
                control.getBoundingClientRect();

              return {
                text:
                  normalize(control.innerText),

                disabled:
                  control.disabled === true ||
                  control.hasAttribute('disabled') ||
                  control.getAttribute(
                    'aria-disabled'
                  ) === 'true',

                visible:
                  style.display !== 'none' &&
                  style.visibility !== 'hidden' &&
                  style.opacity !== '0' &&
                  rect.width > 0 &&
                  rect.height > 0
              };
            });

            const availableControls =
              controls.filter((control) => {
                if (
                  control.disabled ||
                  !control.visible
                ) {
                  return false;
                }

                return availableButtonTerms.some(
                  (term) =>
                    control.text.includes(term)
                );
              });

            const unavailableMatches =
              unavailableTerms.filter(
                (term) => text.includes(term)
              );

            const priceMatches =
              text.match(
                /(?:\\d{1,3}(?:,\\d{3})+|\\d+)\\s*원/g
              ) || [];

            /*
             * 예약하기 버튼이 가장 강한 긍정 신호입니다.
             */
            if (availableControls.length > 0) {
              return {
                status: 'AVAILABLE',
                reason:
                  '목표 객실 카드에서 활성화된 예약하기 버튼을 확인했습니다.',
                roomNameText: targetRoomName,
                text,
                priceMatches
              };
            }

            if (unavailableMatches.length > 0) {
              return {
                status: 'UNAVAILABLE',
                reason:
                  '목표 객실 카드에서 예약마감 문구를 확인했습니다.',
                roomNameText: targetRoomName,
                text,
                priceMatches,
                unavailableMatches
              };
            }

            current = current.parentElement;
          }

          return {
            status: 'UNKNOWN',
            reason:
              '목표 객실은 찾았지만 예약하기 또는 예약마감 신호를 찾지 못했습니다.',
            roomNameText: targetRoomName
          };
        }
        """,
        {
            "targetRoomName": TARGET_ROOM_NAME,
            "unavailableTerms": UNAVAILABLE_TERMS,
            "availableButtonTerms": AVAILABLE_BUTTON_TERMS,
        },
    )


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 72)
    print(f"Check started: {now_kst_string()}")
    print(f"Target room: {TARGET_ROOM_NAME}")
    print("=" * 72)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            channel="chromium",
            args=[
                "--disable-dev-shm-usage",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--window-size=1920,1080",
            ],
        )

        context = browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={
                "width": 1920,
                "height": 1080,
            },
            screen={
                "width": 1920,
                "height": 1080,
            },
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            java_script_enabled=True,
            color_scheme="light",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        try:
            response = page.goto(
                TARGET_URL,
                wait_until="domcontentloaded",
                timeout=45_000,
            )

            if response is not None:
                print(
                    f"Initial HTTP status: "
                    f"{response.status}"
                )

                if response.status in {403, 429}:
                    save_minimal_debug(
                        page,
                        "blocked",
                    )

                    write_github_summary(
                        "BLOCKED",
                        (
                            "초기 요청이 "
                            f"HTTP {response.status}로 "
                            "차단됐습니다."
                        ),
                    )

                    return 2

            wait_for_initial_render(page)

            body_text = page.locator(
                "body"
            ).inner_text(
                timeout=15_000,
            )

            normalized_body = normalize_text(
                body_text
            )

            blocked_detected = any(
                term.lower()
                in normalized_body.lower()
                for term in BLOCKED_TERMS
            )

            if blocked_detected:
                save_minimal_debug(
                    page,
                    "blocked",
                )

                write_github_summary(
                    "BLOCKED",
                    (
                        "접근 제한 또는 CAPTCHA "
                        "문구를 확인했습니다."
                    ),
                    normalized_body[:1000],
                )

                return 2

            target_rendered = (
                scroll_until_target_room(page)
            )

            print(
                "Target room rendered: "
                f"{target_rendered}"
            )

            if not target_rendered:
                save_minimal_debug(
                    page,
                    "not-found",
                )

                write_github_summary(
                    "NOT_FOUND",
                    (
                        "렌더링된 페이지에서 "
                        "목표 객실을 찾지 못했습니다."
                    ),
                )

                return 3

            room_status = inspect_target_room(
                page
            )

            if room_status is None:
                save_minimal_debug(
                    page,
                    "not-found",
                )

                write_github_summary(
                    "NOT_FOUND",
                    (
                        "목표 객실 표시 요소가 "
                        "판정 시점에 사라졌습니다."
                    ),
                )

                return 3

            print(
                json.dumps(
                    room_status,
                    ensure_ascii=False,
                    indent=2,
                )
            )

            status = room_status.get(
                "status",
                "UNKNOWN",
            )

            reason = room_status.get(
                "reason",
                "판정 사유가 없습니다.",
            )

            room_text = room_status.get(
                "text",
                "",
            )

            if status == "UNAVAILABLE":
                print("RESULT: UNAVAILABLE")
                print(reason)

                write_github_summary(
                    "UNAVAILABLE",
                    reason,
                )

                return 0

            if status == "AVAILABLE":
                print("RESULT: AVAILABLE")
                print(reason)

                save_minimal_debug(
                    page,
                    "room-available",
                )

                write_github_summary(
                    "AVAILABLE",
                    reason,
                    room_text,
                )

                send_telegram(
                    "🚨 야놀자 예약 가능 객실이 발견됐습니다.\n\n"
                    f"대상 객실: {TARGET_ROOM_NAME}\n"
                    f"확인 시각: {now_kst_string()}\n\n"
                    f"{TARGET_URL}"
                )

                # Workflow 반복을 즉시 중단하기 위한 코드
                return 10

            save_minimal_debug(
                page,
                "room-unknown",
            )

            write_github_summary(
                "UNKNOWN",
                reason,
                room_text,
            )

            return 3

        except PlaywrightTimeoutError as exc:
            print(f"Page timeout: {exc}")

            save_minimal_debug(
                page,
                "timeout",
            )

            write_github_summary(
                "ERROR",
                "페이지 로딩 시간이 초과됐습니다.",
                str(exc),
            )

            return 1

        except Exception as exc:
            print(
                f"Unexpected error: "
                f"{type(exc).__name__}: {exc}"
            )

            save_minimal_debug(
                page,
                "error",
            )

            write_github_summary(
                "ERROR",
                "모니터 실행 중 예외가 발생했습니다.",
                f"{type(exc).__name__}: {exc}",
            )

            return 1

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())