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
# 환경 설정
# ---------------------------------------------------------------------------

TARGET_URL = os.environ["TARGET_URL"]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ARTIFACT_DIR = Path("artifacts")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


# 정확히 찾으려는 객실명입니다.
TARGET_ROOM_NAME = "골드-클린/설악마운틴뷰/침대"


# GitHub Summary에 표시할 조건입니다.
REQUIRED_TERMS = [
    "골드",
    "클린",
    "설악마운틴뷰",
    "침대",
]


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
    """현재 한국 시간을 문자열로 반환합니다."""
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S KST")


def normalize_text(value: str) -> str:
    """연속된 공백과 줄바꿈을 하나의 공백으로 정리합니다."""
    return " ".join(value.split())


def write_github_summary(
    status: str,
    description: str,
    details: str = "",
) -> None:
    """GitHub Actions 실행 화면의 Summary에 결과를 기록합니다."""
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
            file.write("\n### 감지된 정보\n\n")
            file.write("```text\n")
            file.write(details[:3000])
            file.write("\n```\n")

        file.write(f"\n[야놀자 페이지 열기]({TARGET_URL})\n")


def send_telegram(message: str) -> None:
    """텔레그램 설정이 있으면 메시지를 보내고, 없으면 로그에만 출력합니다."""
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


# ---------------------------------------------------------------------------
# 진단 자료 저장
# ---------------------------------------------------------------------------

def save_debug_files(page: Page, prefix: str) -> None:
    """
    현재 페이지 상태를 다각도로 저장합니다.

    생성 파일:
    - viewport PNG
    - full-page PNG
    - HTML
    - innerText
    - textContent
    - 스크롤별 텍스트
    - embedded data
    - diagnostic JSON
    """

    try:
        page.screenshot(
            path=str(ARTIFACT_DIR / f"{prefix}-viewport.png"),
            full_page=False,
            animations="disabled",
            caret="hide",
            timeout=30_000,
        )
        print(f"Saved: {prefix}-viewport.png")
    except Exception as exc:
        print(f"Viewport screenshot failed: {exc}")

    try:
        page.screenshot(
            path=str(ARTIFACT_DIR / f"{prefix}-full.png"),
            full_page=True,
            animations="disabled",
            caret="hide",
            timeout=30_000,
        )
        print(f"Saved: {prefix}-full.png")
    except Exception as exc:
        print(f"Full-page screenshot failed: {exc}")

    try:
        html = page.content()

        (ARTIFACT_DIR / f"{prefix}.html").write_text(
            html,
            encoding="utf-8",
        )

        print(f"Saved: {prefix}.html")
    except Exception as exc:
        print(f"HTML save failed: {exc}")

    try:
        inner_text = page.locator("body").inner_text(
            timeout=10_000,
        )

        (
            ARTIFACT_DIR / f"{prefix}-inner-text.txt"
        ).write_text(
            inner_text,
            encoding="utf-8",
        )

        print(
            f"Saved: {prefix}-inner-text.txt "
            f"({len(inner_text)} characters)"
        )
    except Exception as exc:
        print(f"Body innerText save failed: {exc}")

    try:
        text_content = (
            page.locator("body").text_content(
                timeout=10_000,
            )
            or ""
        )

        (
            ARTIFACT_DIR / f"{prefix}-text-content.txt"
        ).write_text(
            text_content,
            encoding="utf-8",
        )

        print(
            f"Saved: {prefix}-text-content.txt "
            f"({len(text_content)} characters)"
        )
    except Exception as exc:
        print(f"Body textContent save failed: {exc}")

    try:
        scroll_result = page.evaluate(
            """
            async () => {
              const delay = (ms) =>
                new Promise((resolve) => setTimeout(resolve, ms));

              const normalize = (value) =>
                (value || '').replace(/\\s+/g, ' ').trim();

              const snapshots = [];
              const seenTexts = new Set();

              const saveSnapshot = (
                source,
                position,
                element = null
              ) => {
                const text = normalize(
                  element
                    ? element.innerText
                    : document.body?.innerText
                );

                if (!text || seenTexts.has(text)) {
                  return;
                }

                seenTexts.add(text);

                snapshots.push({
                  source,
                  position,
                  textLength: text.length,
                  text
                });
              };

              const getDocumentHeight = () =>
                Math.max(
                  document.body?.scrollHeight ?? 0,
                  document.documentElement?.scrollHeight ?? 0
                );

              let previousDocumentHeight = -1;

              for (let cycle = 0; cycle < 4; cycle += 1) {
                const documentHeight = getDocumentHeight();

                const step = Math.max(
                  Math.floor(window.innerHeight * 0.55),
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

                  await delay(650);

                  saveSnapshot(
                    'window',
                    window.scrollY
                  );
                }

                window.scrollTo({
                  top: documentHeight,
                  left: 0,
                  behavior: 'instant'
                });

                await delay(1200);

                saveSnapshot(
                  'window-bottom',
                  window.scrollY
                );

                const newHeight = getDocumentHeight();

                if (newHeight === previousDocumentHeight) {
                  break;
                }

                previousDocumentHeight = newHeight;
              }

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

              const containerDiagnostics = [];

              for (
                let index = 0;
                index < scrollContainers.length;
                index += 1
              ) {
                const element = scrollContainers[index];

                const maxScroll =
                  element.scrollHeight -
                  element.clientHeight;

                const step = Math.max(
                  Math.floor(element.clientHeight * 0.55),
                  250
                );

                containerDiagnostics.push({
                  index,
                  tag: element.tagName,
                  className:
                    typeof element.className === 'string'
                      ? element.className
                      : '',
                  clientHeight: element.clientHeight,
                  scrollHeight: element.scrollHeight,
                  maxScroll
                });

                for (
                  let position = 0;
                  position <= maxScroll;
                  position += step
                ) {
                  element.scrollTop = position;

                  await delay(650);

                  saveSnapshot(
                    `container-${index}`,
                    element.scrollTop,
                    element
                  );

                  saveSnapshot(
                    `body-after-container-${index}`,
                    element.scrollTop
                  );
                }

                element.scrollTop = maxScroll;
                await delay(900);

                saveSnapshot(
                  `container-${index}-bottom`,
                  element.scrollTop,
                  element
                );
              }

              window.scrollTo({
                top: 0,
                left: 0,
                behavior: 'instant'
              });

              for (const element of scrollContainers) {
                element.scrollTop = 0;
              }

              await delay(1000);

              return {
                snapshots,
                containerDiagnostics,
                finalDocumentHeight: getDocumentHeight()
              };
            }
            """
        )

        snapshot_sections = []

        for index, snapshot in enumerate(
            scroll_result.get("snapshots", [])
        ):
            snapshot_sections.append(
                "\n".join(
                    [
                        "=" * 80,
                        f"SNAPSHOT {index + 1}",
                        f"source: {snapshot.get('source')}",
                        f"position: {snapshot.get('position')}",
                        f"textLength: {snapshot.get('textLength')}",
                        "=" * 80,
                        snapshot.get("text", ""),
                    ]
                )
            )

        (
            ARTIFACT_DIR
            / f"{prefix}-scroll-snapshots.txt"
        ).write_text(
            "\n\n".join(snapshot_sections),
            encoding="utf-8",
        )

        (
            ARTIFACT_DIR
            / f"{prefix}-scroll-containers.json"
        ).write_text(
            json.dumps(
                scroll_result,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            f"Saved: {prefix}-scroll-snapshots.txt "
            f"({len(scroll_result.get('snapshots', []))} snapshots)"
        )

    except Exception as exc:
        print(f"Scroll snapshot save failed: {exc}")

    try:
        embedded_result = page.evaluate(
            """
            ({ searchTerms }) => {
              const normalize = (value) =>
                (value || '').replace(/\\s+/g, ' ').trim();

              const scriptContexts = [];

              Array.from(
                document.querySelectorAll('script')
              ).forEach((script, scriptIndex) => {
                const content = script.textContent || '';

                for (const term of searchTerms) {
                  let startIndex = 0;
                  let matchCount = 0;

                  while (matchCount < 20) {
                    const index =
                      content.indexOf(term, startIndex);

                    if (index < 0) {
                      break;
                    }

                    scriptContexts.push({
                      source:
                        `script-${scriptIndex}` +
                        (script.id ? `#${script.id}` : ''),
                      term,
                      index,
                      context: normalize(
                        content.slice(
                          Math.max(0, index - 500),
                          Math.min(
                            content.length,
                            index + term.length + 1000
                          )
                        )
                      )
                    });

                    startIndex = index + term.length;
                    matchCount += 1;
                  }
                }
              });

              const matchingElements =
                Array.from(
                  document.querySelectorAll('*')
                )
                  .filter((element) => {
                    const text =
                      element.textContent || '';

                    return searchTerms.some(
                      (term) => text.includes(term)
                    );
                  })
                  .sort((a, b) =>
                    (a.textContent || '').length -
                    (b.textContent || '').length
                  )
                  .slice(0, 100)
                  .map((element) => {
                    const style =
                      window.getComputedStyle(element);

                    const rect =
                      element.getBoundingClientRect();

                    return {
                      tag: element.tagName,
                      id: element.id || '',
                      className:
                        typeof element.className === 'string'
                          ? element.className
                          : '',
                      innerText:
                        normalize(element.innerText),
                      textContent:
                        normalize(element.textContent),
                      display: style.display,
                      visibility: style.visibility,
                      opacity: style.opacity,
                      width: rect.width,
                      height: rect.height,
                      inViewport:
                        rect.bottom >= 0 &&
                        rect.top <= window.innerHeight
                    };
                  });

              return {
                scriptContexts,
                matchingElements
              };
            }
            """,
            {
                "searchTerms": [
                    TARGET_ROOM_NAME,
                    "골드",
                    "설악마운틴뷰",
                    "파노라마뷰",
                    "예약하기",
                    "예약마감",
                ]
            },
        )

        (
            ARTIFACT_DIR
            / f"{prefix}-embedded-data.txt"
        ).write_text(
            "\n".join(
                [
                    "SCRIPT / EMBEDDED DATA CONTEXTS",
                    "=" * 80,
                    json.dumps(
                        embedded_result.get(
                            "scriptContexts",
                            [],
                        ),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "",
                    "",
                    "MATCHING DOM ELEMENTS",
                    "=" * 80,
                    json.dumps(
                        embedded_result.get(
                            "matchingElements",
                            [],
                        ),
                        ensure_ascii=False,
                        indent=2,
                    ),
                ]
            ),
            encoding="utf-8",
        )

        print(f"Saved: {prefix}-embedded-data.txt")

    except Exception as exc:
        print(f"Embedded data save failed: {exc}")

    try:
        diagnostic = page.evaluate(
            """
            ({ targetRoomName }) => {
              const body = document.body;
              const root = document.documentElement;

              return {
                href: location.href,
                title: document.title,
                readyState: document.readyState,
                visibilityState: document.visibilityState,

                bodyInnerTextLength:
                  body?.innerText?.length ?? 0,

                bodyTextContentLength:
                  body?.textContent?.length ?? 0,

                bodyHtmlLength:
                  body?.innerHTML?.length ?? 0,

                bodyWidth:
                  body?.getBoundingClientRect().width ?? 0,

                bodyHeight:
                  body?.getBoundingClientRect().height ?? 0,

                scrollWidth:
                  root?.scrollWidth ?? 0,

                scrollHeight:
                  root?.scrollHeight ?? 0,

                viewportWidth:
                  window.innerWidth,

                viewportHeight:
                  window.innerHeight,

                targetRoom: {
                  innerText:
                    body?.innerText
                      ?.includes(targetRoomName) ??
                    false,

                  textContent:
                    body?.textContent
                      ?.includes(targetRoomName) ??
                    false,

                  html:
                    document.documentElement
                      .innerHTML
                      .includes(targetRoomName)
                }
              };
            }
            """,
            {
                "targetRoomName": TARGET_ROOM_NAME,
            },
        )

        (
            ARTIFACT_DIR
            / f"{prefix}-diagnostic.json"
        ).write_text(
            json.dumps(
                diagnostic,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(f"Saved: {prefix}-diagnostic.json")

    except Exception as exc:
        print(f"Diagnostic save failed: {exc}")


# ---------------------------------------------------------------------------
# 렌더링 보조
# ---------------------------------------------------------------------------

def wait_for_render(page: Page) -> None:
    """초기 페이지 로딩만 기다립니다."""

    try:
        page.wait_for_load_state(
            "load",
            timeout=30_000,
        )
        print("Load event completed.")
    except PlaywrightTimeoutError:
        print("Load event timeout; continuing.")

    try:
        page.wait_for_load_state(
            "networkidle",
            timeout=20_000,
        )
        print("Network became idle.")
    except PlaywrightTimeoutError:
        print("Network idle timeout; continuing.")

    page.wait_for_timeout(10_000)


def scroll_until_target_room(page: Page) -> bool:
    """
    정확한 목표 객실명이 실제 화면에 나타날 때까지 스크롤합니다.

    목표 요소를 찾으면 data-yanolja-target-room 속성을 추가하고,
    이후 판정 함수가 같은 요소에서 출발하도록 합니다.
    """

    return page.evaluate(
        """
        async ({ targetRoomName }) => {
          const delay = (ms) =>
            new Promise((resolve) => setTimeout(resolve, ms));

          const normalize = (value) =>
            (value || '').replace(/\\s+/g, ' ').trim();

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
                'div, span, p, strong, b, h1, h2, h3, h4, h5'
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

          let previousHeight = -1;

          for (let cycle = 0; cycle < 4; cycle += 1) {
            const documentHeight = Math.max(
              document.body?.scrollHeight ?? 0,
              document.documentElement?.scrollHeight ?? 0
            );

            const step = Math.max(
              Math.floor(window.innerHeight * 0.4),
              300
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

              await delay(700);

              if (findTargetElement()) {
                await delay(500);
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
              Math.floor(element.clientHeight * 0.4),
              200
            );

            for (
              let position = 0;
              position <= maxScroll;
              position += step
            ) {
              element.scrollTop = position;

              await delay(700);

              if (findTargetElement()) {
                await delay(500);
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
# 객실 상태 탐지
# ---------------------------------------------------------------------------

def inspect_target_room(page: Page) -> dict[str, Any] | None:
    """
    정확한 목표 객실 요소에서 시작해 해당 판매 카드만 판정합니다.

    활성화된 '예약하기'가 있으면 AVAILABLE을 최우선으로 판정합니다.
    """

    return page.evaluate(
        """
        ({
          targetRoomName,
          unavailableTerms,
          availableButtonTerms
        }) => {
          const normalize = (value) =>
            (value || '').replace(/\\s+/g, ' ').trim();

          const countOccurrences = (
            text,
            searchText
          ) => {
            if (!searchText) {
              return 0;
            }

            return text.split(searchText).length - 1;
          };

          const targetElement = document.querySelector(
            '[data-yanolja-target-room="true"]'
          );

          if (!targetElement) {
            return null;
          }

          const inspectedAncestors = [];
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
             * 목표 객실명이 두 번 이상 들어가면 여러 판매 카드를
             * 포함한 상위 목록일 가능성이 높으므로 사용하지 않습니다.
             */
            const targetRoomOccurrenceCount =
              countOccurrences(
                text,
                targetRoomName
              );

            if (targetRoomOccurrenceCount > 1) {
              break;
            }

            /*
             * 지나치게 큰 상위 목록으로 올라가는 것을 막습니다.
             */
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
                tag: control.tagName,
                text:
                  normalize(control.innerText),
                disabled:
                  control.disabled === true ||
                  control.hasAttribute('disabled') ||
                  control.getAttribute(
                    'aria-disabled'
                  ) === 'true',
                href:
                  control.getAttribute('href'),
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

            const hasAvailabilitySignal =
              availableControls.length > 0 ||
              unavailableMatches.length > 0;

            inspectedAncestors.push({
              depth,
              tag: current.tagName,
              textLength: text.length,
              targetRoomOccurrenceCount,
              text,
              controls,
              availableControls,
              unavailableMatches,
              priceMatches,
              hasAvailabilitySignal
            });

            if (hasAvailabilitySignal) {
              /*
               * '예약하기'가 있으면 예약마감 문구보다 우선합니다.
               *
               * 다만 목표 객실 하나만 포함하는 가장 작은 카드만
               * 조사하므로 다른 객실의 예약마감이 섞이지 않습니다.
               */
              if (availableControls.length > 0) {
                return {
                  status: 'AVAILABLE',
                  reason:
                    '목표 객실 카드 안에서 활성화된 예약하기 버튼이 발견됐습니다.',
                  roomNameText: targetRoomName,
                  depth,
                  tag: current.tagName,
                  text,
                  controls,
                  priceMatches,
                  unavailableMatches,
                  inspectedAncestors
                };
              }

              if (unavailableMatches.length > 0) {
                return {
                  status: 'UNAVAILABLE',
                  reason:
                    '목표 객실 카드 안에 예약마감 또는 예약 불가 문구가 있습니다.',
                  roomNameText: targetRoomName,
                  depth,
                  tag: current.tagName,
                  text,
                  controls,
                  priceMatches,
                  unavailableMatches,
                  inspectedAncestors
                };
              }
            }

            current = current.parentElement;
          }

          return {
            status: 'UNKNOWN',
            reason:
              '목표 객실은 발견했지만 해당 객실 하나의 예약하기 또는 예약마감 신호를 찾지 못했습니다.',
            roomNameText: targetRoomName,
            inspectedAncestors
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
    print("=" * 80)
    print(f"Check started: {now_kst_string()}")
    print(f"Target URL: {TARGET_URL}")
    print(f"Target room: {TARGET_ROOM_NAME}")
    print("=" * 80)

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
            reduced_motion="no-preference",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        page.on(
            "console",
            lambda message: print(
                f"BROWSER CONSOLE [{message.type}]: "
                f"{message.text}"
            ),
        )

        page.on(
            "pageerror",
            lambda error: print(
                f"BROWSER PAGE ERROR: {error}"
            ),
        )

        page.on(
            "requestfailed",
            lambda request: print(
                "REQUEST FAILED: "
                f"{request.resource_type} "
                f"{request.url} "
                f"{request.failure}"
            ),
        )

        page.on(
            "response",
            lambda response: (
                print(
                    f"HTTP {response.status}: "
                    f"{response.url}"
                )
                if response.status >= 400
                else None
            ),
        )

        try:
            response = page.goto(
                TARGET_URL,
                wait_until="domcontentloaded",
                timeout=60_000,
            )

            print(
                f"Current URL after navigation: "
                f"{page.url}"
            )

            if response is not None:
                print(
                    f"Initial HTTP status: "
                    f"{response.status}"
                )

                if response.status in {403, 429}:
                    save_debug_files(
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

            wait_for_render(page)

            target_rendered = (
                scroll_until_target_room(page)
            )

            print(
                "Target room rendered during scrolling: "
                f"{target_rendered}"
            )

            page.wait_for_timeout(1_500)

            body_text = page.locator(
                "body"
            ).inner_text(
                timeout=20_000,
            )

            normalized_body = normalize_text(
                body_text
            )

            print(f"Page title: {page.title()}")
            print(
                f"Body text length: "
                f"{len(body_text)}"
            )
            print(
                "Body text preview: "
                f"{normalized_body[:1000]}"
            )

            blocked_detected = any(
                term.lower()
                in normalized_body.lower()
                for term in BLOCKED_TERMS
            )

            if blocked_detected:
                print("RESULT: BLOCKED")

                save_debug_files(
                    page,
                    "blocked",
                )

                write_github_summary(
                    "BLOCKED",
                    (
                        "페이지에서 접근 제한 또는 "
                        "CAPTCHA 문구가 발견됐습니다."
                    ),
                    normalized_body[:2000],
                )

                return 2

            room_status = inspect_target_room(
                page
            )

            save_debug_files(
                page,
                "page-render",
            )

            if room_status is None:
                print("RESULT: NOT_FOUND")
                print(
                    "The target room name was "
                    "not found in the rendered page."
                )

                save_debug_files(
                    page,
                    "room-not-found",
                )

                write_github_summary(
                    "NOT_FOUND",
                    (
                        "렌더링된 페이지에서 "
                        "목표 객실명을 찾지 못했습니다."
                    ),
                    normalized_body[:2000],
                )

                return 0

            print("Room status result:")
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

            room_name_text = room_status.get(
                "roomNameText",
                TARGET_ROOM_NAME,
            )

            if status == "UNAVAILABLE":
                print("RESULT: UNAVAILABLE")
                print(reason)

                save_debug_files(
                    page,
                    "room-unavailable",
                )

                write_github_summary(
                    "UNAVAILABLE",
                    reason,
                    room_text[:3000]
                    or room_name_text,
                )

                return 0

            if status == "UNKNOWN":
                print("RESULT: UNKNOWN")
                print(reason)

                save_debug_files(
                    page,
                    "room-unknown",
                )

                write_github_summary(
                    "UNKNOWN",
                    reason,
                    room_text[:3000]
                    or room_name_text,
                )

                return 0

            if status == "AVAILABLE":
                print("RESULT: AVAILABLE")
                print(reason)

                save_debug_files(
                    page,
                    "room-available",
                )

                write_github_summary(
                    "AVAILABLE",
                    reason,
                    room_text[:3000]
                    or room_name_text,
                )

                send_telegram(
                    "🚨 야놀자 예약 가능 객실이 발견됐습니다.\n\n"
                    f"대상 객실: {TARGET_ROOM_NAME}\n"
                    f"확인 시각: {now_kst_string()}\n\n"
                    f"판정 사유: {reason}\n\n"
                    f"객실 카드:\n"
                    f"{room_text[:1000] or room_name_text}\n\n"
                    f"{TARGET_URL}"
                )

                return 0

            raise RuntimeError(
                f"Unexpected room status: {status}"
            )

        except PlaywrightTimeoutError as exc:
            print("RESULT: ERROR")
            print(f"Page timeout: {exc}")

            save_debug_files(
                page,
                "timeout",
            )

            write_github_summary(
                "ERROR",
                (
                    "페이지 로딩 중 시간 초과가 "
                    "발생했습니다."
                ),
                str(exc),
            )

            return 1

        except Exception as exc:
            print("RESULT: ERROR")
            print(
                f"Unexpected error: "
                f"{type(exc).__name__}: {exc}"
            )

            save_debug_files(
                page,
                "error",
            )

            write_github_summary(
                "ERROR",
                (
                    "모니터 실행 중 예외가 "
                    "발생했습니다."
                ),
                (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            )

            return 1

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())