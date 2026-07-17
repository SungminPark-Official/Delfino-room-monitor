from __future__ import annotations

import json
import os
import re
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


# 객실명에 반드시 포함되어야 하는 문구입니다.
REQUIRED_TERMS = [
    "골드",
    "클린",
    "설악마운틴뷰",
    "침대",
]


# 동일한 객실 카드 안에 이 문구가 있으면 예약 불가 후보로 봅니다.
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


# 활성화된 버튼에 이 문구가 있을 때 예약 가능 신호로 봅니다.
AVAILABLE_BUTTON_TERMS = [
    "예약",
    "객실 선택",
    "객실선택",
    "선택하기",
    "구매",
    "결제",
]


# 페이지 전체에 이 문구가 있으면 접근 제한 가능성이 있다고 판단합니다.
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
        file.write(f"- 필수 조건: `{', '.join(REQUIRED_TERMS)}`\n")

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
    - {prefix}-viewport.png
    - {prefix}-full.png
    - {prefix}.html
    - {prefix}-inner-text.txt
    - {prefix}-text-content.txt
    - {prefix}-scroll-snapshots.txt
    - {prefix}-embedded-data.txt
    - {prefix}-diagnostic.json
    """

    # ------------------------------------------------------------------
    # 1. 현재 viewport 스크린샷
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 2. 전체 페이지 스크린샷
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 3. 전체 HTML
    # ------------------------------------------------------------------

    html = ""

    try:
        html = page.content()

        (
            ARTIFACT_DIR / f"{prefix}.html"
        ).write_text(
            html,
            encoding="utf-8",
        )

        print(f"Saved: {prefix}.html")
    except Exception as exc:
        print(f"HTML save failed: {exc}")

    # ------------------------------------------------------------------
    # 4. 현재 화면에서 보이는 텍스트: innerText
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 5. 숨겨진 DOM 요소까지 포함한 텍스트: textContent
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 6. 스크롤 각 지점에서 보이는 텍스트 누적
    # ------------------------------------------------------------------

    try:
        scroll_result = page.evaluate(
            """
            async () => {
              const delay = (ms) =>
                new Promise((resolve) =>
                  setTimeout(resolve, ms)
                );

              const normalize = (value) =>
                (value || '')
                  .replace(/\\s+/g, ' ')
                  .trim();

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

              /*
               * 전체 문서 스크롤.
               * 끝에서 한 번 읽는 것이 아니라 각 위치의 텍스트를 저장합니다.
               */
              let previousDocumentHeight = -1;

              for (let cycle = 0; cycle < 4; cycle += 1) {
                const documentHeight =
                  getDocumentHeight();

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

                const newHeight =
                  getDocumentHeight();

                if (
                  newHeight === previousDocumentHeight
                ) {
                  break;
                }

                previousDocumentHeight =
                  newHeight;
              }

              /*
               * overflow:auto 또는 overflow:scroll인
               * 내부 스크롤 컨테이너도 순회합니다.
               */
              const scrollContainers =
                Array.from(
                  document.querySelectorAll('*')
                ).filter((element) => {
                  const style =
                    window.getComputedStyle(element);

                  const overflowY =
                    style.overflowY;

                  return (
                    ['auto', 'scroll'].includes(
                      overflowY
                    ) &&
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
                const element =
                  scrollContainers[index];

                const maxScroll =
                  element.scrollHeight -
                  element.clientHeight;

                const step = Math.max(
                  Math.floor(
                    element.clientHeight * 0.55
                  ),
                  250
                );

                containerDiagnostics.push({
                  index,
                  tag: element.tagName,
                  className:
                    typeof element.className === 'string'
                      ? element.className
                      : '',
                  clientHeight:
                    element.clientHeight,
                  scrollHeight:
                    element.scrollHeight,
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

                  /*
                   * 내부 스크롤로 바뀐 전체 화면도 같이 저장합니다.
                   */
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

              for (
                const element of scrollContainers
              ) {
                element.scrollTop = 0;
              }

              await delay(1000);

              return {
                snapshots,
                containerDiagnostics,
                finalDocumentHeight:
                  getDocumentHeight()
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
                        (
                            "textLength: "
                            f"{snapshot.get('textLength')}"
                        ),
                        "=" * 80,
                        snapshot.get("text", ""),
                    ]
                )
            )

        snapshot_text = "\n\n".join(
            snapshot_sections
        )

        (
            ARTIFACT_DIR
            / f"{prefix}-scroll-snapshots.txt"
        ).write_text(
            snapshot_text,
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
        print(
            f"Saved: {prefix}-scroll-containers.json"
        )

    except Exception as exc:
        print(f"Scroll snapshot save failed: {exc}")

    # ------------------------------------------------------------------
    # 7. HTML 및 script 데이터에서 목표 객실 주변 문맥 추출
    # ------------------------------------------------------------------

    try:
        embedded_result = page.evaluate(
            """
            ({ searchTerms }) => {
              const normalize = (value) =>
                (value || '')
                  .replace(/\\s+/g, ' ')
                  .trim();

              const results = [];

              const addContext = (
                source,
                content,
                term
              ) => {
                if (!content) {
                  return;
                }

                let startIndex = 0;
                let matchCount = 0;

                while (matchCount < 20) {
                  const index =
                    content.indexOf(
                      term,
                      startIndex
                    );

                  if (index < 0) {
                    break;
                  }

                  const contextStart =
                    Math.max(0, index - 500);

                  const contextEnd =
                    Math.min(
                      content.length,
                      index + term.length + 1000
                    );

                  results.push({
                    source,
                    term,
                    index,
                    context: normalize(
                      content.slice(
                        contextStart,
                        contextEnd
                      )
                    )
                  });

                  startIndex =
                    index + term.length;

                  matchCount += 1;
                }
              };

              /*
               * script 태그 안의 JSON이나 초기 상태 데이터 조사.
               */
              Array.from(
                document.querySelectorAll('script')
              ).forEach((script, index) => {
                const content =
                  script.textContent || '';

                for (const term of searchTerms) {
                  if (content.includes(term)) {
                    addContext(
                      `script-${index}` +
                      (
                        script.id
                          ? `#${script.id}`
                          : ''
                      ),
                      content,
                      term
                    );
                  }
                }
              });

              /*
               * 숨겨진 DOM 요소나 일반 DOM에서 목표 문구를 포함하는
               * 요소를 별도로 수집합니다.
               */
              const matchingElements =
                Array.from(
                  document.querySelectorAll('*')
                )
                  .filter((element) => {
                    const text =
                      element.textContent || '';

                    return searchTerms.some(
                      (term) =>
                        text.includes(term)
                    );
                  })
                  .sort((a, b) => {
                    const aLength =
                      (a.textContent || '').length;

                    const bLength =
                      (b.textContent || '').length;

                    return aLength - bLength;
                  })
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
                        typeof element.className ===
                        'string'
                          ? element.className
                          : '',
                      innerText:
                        normalize(
                          element.innerText
                        ),
                      textContent:
                        normalize(
                          element.textContent
                        ),
                      display:
                        style.display,
                      visibility:
                        style.visibility,
                      opacity:
                        style.opacity,
                      width:
                        rect.width,
                      height:
                        rect.height,
                      inViewport:
                        rect.bottom >= 0 &&
                        rect.top <= window.innerHeight
                    };
                  });

              return {
                scriptContexts: results,
                matchingElements
              };
            }
            """,
            {
                "searchTerms": [
                    "골드",
                    "클린",
                    "설악",
                    "설악마운틴뷰",
                    "파노라마뷰",
                    "침대",
                ]
            },
        )

        output_sections = [
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

        embedded_text = "\n".join(
            output_sections
        )

        (
            ARTIFACT_DIR
            / f"{prefix}-embedded-data.txt"
        ).write_text(
            embedded_text,
            encoding="utf-8",
        )

        print(
            f"Saved: {prefix}-embedded-data.txt"
        )

    except Exception as exc:
        print(f"Embedded data save failed: {exc}")

    # ------------------------------------------------------------------
    # 8. 페이지 종합 진단 정보
    # ------------------------------------------------------------------

    try:
        diagnostic = page.evaluate(
            """
            () => {
              const body = document.body;
              const root =
                document.documentElement;

              const bodyStyle = body
                ? window.getComputedStyle(body)
                : null;

              const scrollableElements =
                Array.from(
                  document.querySelectorAll('*')
                ).filter((element) => {
                  const style =
                    window.getComputedStyle(element);

                  return (
                    ['auto', 'scroll'].includes(
                      style.overflowY
                    ) &&
                    element.scrollHeight >
                      element.clientHeight + 100
                  );
                });

              const scripts =
                Array.from(
                  document.querySelectorAll('script')
                ).map((script, index) => ({
                  index,
                  id: script.id || '',
                  type:
                    script.getAttribute('type') || '',
                  src:
                    script.getAttribute('src') || '',
                  textLength:
                    script.textContent?.length ?? 0
                }));

              return {
                href: location.href,
                title: document.title,
                readyState:
                  document.readyState,
                visibilityState:
                  document.visibilityState,

                bodyInnerTextLength:
                  body?.innerText?.length ?? 0,

                bodyTextContentLength:
                  body?.textContent?.length ?? 0,

                bodyHtmlLength:
                  body?.innerHTML?.length ?? 0,

                bodyWidth:
                  body?.getBoundingClientRect()
                    .width ?? 0,

                bodyHeight:
                  body?.getBoundingClientRect()
                    .height ?? 0,

                scrollWidth:
                  root?.scrollWidth ?? 0,

                scrollHeight:
                  root?.scrollHeight ?? 0,

                viewportWidth:
                  window.innerWidth,

                viewportHeight:
                  window.innerHeight,

                scrollX:
                  window.scrollX,

                scrollY:
                  window.scrollY,

                devicePixelRatio:
                  window.devicePixelRatio,

                bodyDisplay:
                  bodyStyle?.display ?? null,

                bodyVisibility:
                  bodyStyle?.visibility ?? null,

                bodyOpacity:
                  bodyStyle?.opacity ?? null,

                bodyBackgroundColor:
                  bodyStyle?.backgroundColor ?? null,

                elementCount:
                  document.querySelectorAll('*')
                    .length,

                buttonCount:
                  document.querySelectorAll(
                    'button, a, [role="button"]'
                  ).length,

                iframeCount:
                  document.querySelectorAll(
                    'iframe'
                  ).length,

                scriptCount:
                  scripts.length,

                scrollableElementCount:
                  scrollableElements.length,

                scrollableElements:
                  scrollableElements
                    .slice(0, 30)
                    .map((element, index) => {
                      const style =
                        window.getComputedStyle(element);

                      return {
                        index,
                        tag:
                          element.tagName,
                        id:
                          element.id || '',
                        className:
                          typeof element.className ===
                          'string'
                            ? element.className
                            : '',
                        overflowY:
                          style.overflowY,
                        clientHeight:
                          element.clientHeight,
                        scrollHeight:
                          element.scrollHeight,
                        scrollTop:
                          element.scrollTop
                      };
                    }),

                scripts:
                  scripts.slice(0, 100),

                targetTerms: {
                  innerText: {
                    gold:
                      body?.innerText
                        ?.includes('골드') ??
                      false,
                    seorak:
                      body?.innerText
                        ?.includes('설악') ??
                      false,
                    seorakMountainView:
                      body?.innerText
                        ?.includes(
                          '설악마운틴뷰'
                        ) ??
                      false,
                    panorama:
                      body?.innerText
                        ?.includes(
                          '파노라마뷰'
                        ) ??
                      false
                  },

                  textContent: {
                    gold:
                      body?.textContent
                        ?.includes('골드') ??
                      false,
                    seorak:
                      body?.textContent
                        ?.includes('설악') ??
                      false,
                    seorakMountainView:
                      body?.textContent
                        ?.includes(
                          '설악마운틴뷰'
                        ) ??
                      false,
                    panorama:
                      body?.textContent
                        ?.includes(
                          '파노라마뷰'
                        ) ??
                      false
                  },

                  html: {
                    gold:
                      document.documentElement
                        .innerHTML
                        .includes('골드'),
                    seorak:
                      document.documentElement
                        .innerHTML
                        .includes('설악'),
                    seorakMountainView:
                      document.documentElement
                        .innerHTML
                        .includes(
                          '설악마운틴뷰'
                        ),
                    panorama:
                      document.documentElement
                        .innerHTML
                        .includes(
                          '파노라마뷰'
                        )
                  }
                }
              };
            }
            """
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

        print(
            f"Saved: {prefix}-diagnostic.json"
        )

    except Exception as exc:
        print(f"Diagnostic save failed: {exc}")

# ---------------------------------------------------------------------------
# 렌더링 보조
# ---------------------------------------------------------------------------

def force_desktop_render(page: Page) -> None:
    """
    데스크톱 페이지를 단계적으로 스크롤하여
    lazy rendering 및 lazy loading을 유도합니다.
    """
    page.evaluate(
        """
        async () => {
          const delay = (ms) =>
            new Promise((resolve) => setTimeout(resolve, ms));

          const getDocumentHeight = () =>
            Math.max(
              document.body?.scrollHeight ?? 0,
              document.documentElement?.scrollHeight ?? 0
            );

          let previousHeight = 0;

          for (let cycle = 0; cycle < 3; cycle += 1) {
            const documentHeight = getDocumentHeight();
            const viewportHeight = window.innerHeight;

            for (
              let position = 0;
              position < documentHeight;
              position += Math.max(
                Math.floor(viewportHeight * 0.7),
                500
              )
            ) {
              window.scrollTo({
                top: position,
                left: 0,
                behavior: 'instant'
              });

              await delay(500);
            }

            await delay(1000);

            const newHeight = getDocumentHeight();

            if (newHeight === previousHeight) {
              break;
            }

            previousHeight = newHeight;
          }

          window.scrollTo({
            top: 0,
            left: 0,
            behavior: 'instant'
          });

          await delay(1500);
        }
        """
    )


def wait_for_render(page: Page) -> None:
    """주요 로딩 상태를 기다리고 렌더링을 유도합니다."""

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

    # React/Next.js 앱이 초기 렌더링을 마칠 시간을 줍니다.
    page.wait_for_timeout(10_000)

    force_desktop_render(page)

    page.wait_for_timeout(3_000)


# ---------------------------------------------------------------------------
# 객실 상태 탐지
# ---------------------------------------------------------------------------

def inspect_target_room(page: Page) -> dict[str, Any] | None:
    """
    목표 객실명 요소를 찾고 상위 DOM을 단계적으로 추적합니다.

    반환 상태:
    - AVAILABLE
    - UNAVAILABLE
    - UNKNOWN
    """

    return page.evaluate(
        """
        ({
          requiredTerms,
          unavailableTerms,
          availableButtonTerms
        }) => {
          const normalize = (value) =>
            (value || '')
              .replace(/\\s+/g, ' ')
              .trim();

          const uniqueByTextAndTag = (items) => {
            const seen = new Set();

            return items.filter((item) => {
              const key =
                `${item.tag}|${item.text}|${item.disabled}`;

              if (seen.has(key)) {
                return false;
              }

              seen.add(key);
              return true;
            });
          };

          const allElements = Array.from(
            document.querySelectorAll(
              'div, span, p, strong, b, h1, h2, h3, h4, h5, li, article, section'
            )
          );

          const roomNameElements = allElements
            .filter((element) => {
              const text = normalize(element.innerText);

              if (!text || text.length > 250) {
                return false;
              }

              return requiredTerms.every(
                (term) => text.includes(term)
              );
            })
            .sort((a, b) => {
              const aLength = normalize(a.innerText).length;
              const bLength = normalize(b.innerText).length;

              return aLength - bLength;
            });

          if (roomNameElements.length === 0) {
            return null;
          }

          const inspectedAncestors = [];

          for (const roomNameElement of roomNameElements) {
            let current = roomNameElement;

            for (
              let depth = 0;
              depth <= 12 && current;
              depth += 1
            ) {
              const text = normalize(current.innerText);

              if (!text) {
                current = current.parentElement;
                continue;
              }

              if (text.length > 6000) {
                break;
              }

              const controls = uniqueByTextAndTag(
                Array.from(
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
                    text: normalize(control.innerText),
                    disabled:
                      control.disabled === true ||
                      control.hasAttribute('disabled') ||
                      control.getAttribute('aria-disabled') === 'true',
                    href:
                      control.getAttribute('href'),
                    visible:
                      style.display !== 'none' &&
                      style.visibility !== 'hidden' &&
                      style.opacity !== '0' &&
                      rect.width > 0 &&
                      rect.height > 0
                  };
                })
              );

              const unavailableMatches =
                unavailableTerms.filter(
                  (term) => text.includes(term)
                );

              const priceMatches =
                text.match(
                  /(?:\\d{1,3}(?:,\\d{3})+|\\d+)\\s*원/g
                ) || [];

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

              const unavailableControls =
                controls.filter((control) => {
                  const unavailableControlText =
                    unavailableTerms.some(
                      (term) =>
                        control.text.includes(term)
                    );

                  return (
                    control.disabled ||
                    unavailableControlText
                  );
                });

              const hasUsefulContext =
                controls.length > 0 ||
                priceMatches.length > 0 ||
                unavailableMatches.length > 0;

              inspectedAncestors.push({
                depth,
                tag: current.tagName,
                textLength: text.length,
                text,
                controls,
                priceMatches,
                unavailableMatches,
                availableControlCount:
                  availableControls.length,
                unavailableControlCount:
                  unavailableControls.length,
                hasUsefulContext
              });

              if (hasUsefulContext) {
                let status = 'UNKNOWN';
                let reason =
                  '객실명은 찾았지만 예약 가능 여부를 확정할 신호가 부족합니다.';

                if (
                  unavailableMatches.length > 0
                ) {
                  status = 'UNAVAILABLE';
                  reason =
                    '객실 카드 안에서 예약 불가 문구가 발견됐습니다.';
                } else if (
                  availableControls.length > 0
                ) {
                  status = 'AVAILABLE';
                  reason =
                    '객실 카드 안에서 활성화된 예약 관련 버튼이 발견됐습니다.';
                } else if (
                  unavailableControls.length > 0 &&
                  availableControls.length === 0
                ) {
                  status = 'UNAVAILABLE';
                  reason =
                    '객실 카드 안의 예약 관련 버튼이 비활성화돼 있습니다.';
                }

                return {
                  status,
                  reason,
                  roomNameText:
                    normalize(roomNameElement.innerText),
                  depth,
                  tag: current.tagName,
                  text,
                  controls,
                  priceMatches,
                  unavailableMatches,
                  inspectedAncestors
                };
              }

              current = current.parentElement;
            }
          }

          return {
            status: 'UNKNOWN',
            reason:
              '목표 객실명은 발견했지만 상태 문구, 가격 또는 버튼이 있는 상위 카드를 찾지 못했습니다.',
            roomNameText:
              normalize(roomNameElements[0].innerText),
            inspectedAncestors
          };
        }
        """,
        {
            "requiredTerms": REQUIRED_TERMS,
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
    print(f"Required terms: {REQUIRED_TERMS}")
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

        # 브라우저 내부 오류를 GitHub Actions 로그에 출력합니다.
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

            print(f"Current URL after navigation: {page.url}")

            if response is not None:
                print(f"Initial HTTP status: {response.status}")

                if response.status in {403, 429}:
                    save_debug_files(page, "blocked")

                    write_github_summary(
                        "BLOCKED",
                        f"초기 요청이 HTTP {response.status}로 차단됐습니다.",
                    )

                    return 2

            wait_for_render(page)

            body_text = page.locator("body").inner_text(
                timeout=20_000,
            )

            normalized_body = normalize_text(body_text)

            print(f"Page title: {page.title()}")
            print(f"Body text length: {len(body_text)}")
            print(
                "Body text preview: "
                f"{normalized_body[:1000]}"
            )

            # 진단 단계에서는 매번 저장합니다.
            save_debug_files(page, "page-render")

            blocked_detected = any(
                term.lower() in normalized_body.lower()
                for term in BLOCKED_TERMS
            )

            if blocked_detected:
                print("RESULT: BLOCKED")
                print(
                    "A blocking or CAPTCHA message "
                    "was detected."
                )

                save_debug_files(page, "blocked")

                write_github_summary(
                    "BLOCKED",
                    "페이지에서 접근 제한 또는 CAPTCHA 문구가 발견됐습니다.",
                    normalized_body[:2000],
                )

                return 2

            room_status = inspect_target_room(page)

            if room_status is None:
                print("RESULT: NOT_FOUND")
                print(
                    "The target room name was not found "
                    "in the rendered page."
                )

                save_debug_files(page, "room-not-found")

                write_github_summary(
                    "NOT_FOUND",
                    "렌더링된 페이지에서 목표 객실명을 찾지 못했습니다.",
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

            status = room_status.get("status", "UNKNOWN")
            reason = room_status.get(
                "reason",
                "판정 사유가 없습니다.",
            )
            room_text = room_status.get("text", "")
            room_name_text = room_status.get(
                "roomNameText",
                "",
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
                    room_text[:3000] or room_name_text,
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
                    room_text[:3000] or room_name_text,
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
                    room_text[:3000] or room_name_text,
                )

                send_telegram(
                    "🚨 야놀자 예약 가능 객실이 발견됐습니다.\n\n"
                    f"조건: {', '.join(REQUIRED_TERMS)}\n"
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
            print(f"RESULT: ERROR")
            print(f"Page timeout: {exc}")

            save_debug_files(page, "timeout")

            write_github_summary(
                "ERROR",
                "페이지 로딩 중 시간 초과가 발생했습니다.",
                str(exc),
            )

            return 1

        except Exception as exc:
            print("RESULT: ERROR")
            print(
                f"Unexpected error: "
                f"{type(exc).__name__}: {exc}"
            )

            save_debug_files(page, "error")

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