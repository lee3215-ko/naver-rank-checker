from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from .constants import (
    DEFAULT_DELAY_SECONDS,
    DEFAULT_MAX_PAGES,
    MAIN_SECTION_AREA,
    MAIN_SECTION_BLOCK_SCAN,
    RESULTS_PER_PAGE,
    SEARCH_SSC,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://search.naver.com/",
}

# 통합검색 메인 타이틀 링크
TITLE_LINK_RE = re.compile(
    r'<a nocr="1" href="(https?://[^"]+)" class="[^"]*bw6s5j6PgZwBpJOh[^"]*"[^>]+data-heatmap-target="\.link"',
    re.IGNORECASE,
)

# [메인] 섹션(urB_coR) 웹 결과 블록 — 블록당 대표 URL 1개
MAIN_SECTION_BLOCK_RE = re.compile(
    rf'<div id="fdr-[^"]+"[^>]*data-meta-area="{MAIN_SECTION_AREA}"[^>]*data-meta-ssuid="web"[^>]*>',
    re.IGNORECASE,
)
MAIN_SECTION_PROFILE_LINK_RE = re.compile(
    r'href="(https?://[^"]+)"[^>]*class="[^"]*bw6s5j6PgZwBpJOh'
    r'|class="[^"]*bw6s5j6PgZwBpJOh[^"]*"[^>]*href="(https?://[^"]+)"',
    re.IGNORECASE,
)
JSON_DESK_HREF_RE = re.compile(r'"deviceType":"desk","href":"(https?://[^"]+)"')

_main_section_cache: dict[str, list[str]] = {}
_main_section_lock = threading.Lock()

CancelCallback = Callable[[], bool]
ProgressCallback = Callable[[str, int, int], None]


def _normalize_href(href: str) -> str:
    return href.replace("\\/", "/").replace("&amp;", "&")


def _profile_url_from_block(chunk: str) -> str | None:
    match = MAIN_SECTION_PROFILE_LINK_RE.search(chunk)
    if match:
        href = match.group(1) or match.group(2)
        return _normalize_href(href)
    json_match = JSON_DESK_HREF_RE.search(chunk)
    if json_match:
        return _normalize_href(json_match.group(1))
    return None


def _extract_json_desk_urls(html: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for href in JSON_DESK_HREF_RE.findall(html):
        link = _normalize_href(href)
        if link in seen:
            continue
        seen.add(link)
        urls.append(link)
    return urls


@dataclass(frozen=True)
class RankResult:
    keyword: str
    site: str
    rank: int | None
    matched_url: str | None
    pages_searched: int
    cancelled: bool = False
    indexed: bool | None = None
    index_message: str = ""
    index_sample_url: str | None = None
    main_section_rank: int | None = None
    main_section_matched_url: str | None = None

    @property
    def effective_rank(self) -> int | None:
        if self.main_section_rank is not None:
            return self.main_section_rank
        return self.rank

    @property
    def rank_kind(self) -> str | None:
        if self.main_section_rank is not None:
            return "main"
        if self.rank is not None:
            return "unified"
        return None


def clear_search_cache() -> None:
    """배치 검사 시작 시 호출 — 키워드별 [메인] 섹션 캐시 초기화."""
    with _main_section_lock:
        _main_section_cache.clear()


def normalize_site(value: str) -> str:
    value = value.strip().lower()
    if not value:
        raise ValueError("사이트 주소를 입력해 주세요.")

    if "://" not in value:
        value = f"https://{value}"

    parsed = urllib.parse.urlparse(value)
    host = parsed.netloc or parsed.path.split("/")[0]
    host = host.removeprefix("www.")
    path = parsed.path.rstrip("/") if parsed.netloc else ""
    return host + path


def url_matches(result_url: str, target: str) -> bool:
    result = normalize_site(result_url)
    target_norm = normalize_site(target)

    result_host = result.split("/")[0]
    target_host = target_norm.split("/")[0]
    if result_host != target_host:
        return False

    result_path = result[len(result_host) :].lstrip("/")
    target_path = target_norm[len(target_host) :].lstrip("/")
    if not target_path:
        return True
    if not result_path:
        return True
    return result_path.startswith(target_path) or target_path.startswith(result_path)


def _fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_search_page(keyword: str, start: int = 1) -> str:
    params = {
        "query": keyword,
        "ssc": SEARCH_SSC,
        "start": str(start),
    }
    url = f"https://search.naver.com/search.naver?{urllib.parse.urlencode(params)}"
    return _fetch_url(url)


def fetch_main_section_page(keyword: str) -> str:
    """브라우저 통합검색과 동일한 nexearch HTML — [메인] urB_coR 블록 포함."""
    params = {
        "where": "nexearch",
        "query": keyword,
        "sm": "top_hty",
        "ie": "utf8",
    }
    url = f"https://search.naver.com/search.naver?{urllib.parse.urlencode(params)}"
    return _fetch_url(url)


def extract_main_section_results(html: str) -> list[str]:
    """[메인] 섹션(urB_coR) 웹 블록 순서대로 대표 URL 목록."""
    urls: list[str] = []
    for match in MAIN_SECTION_BLOCK_RE.finditer(html):
        chunk = html[match.start() : match.start() + MAIN_SECTION_BLOCK_SCAN]
        href = _profile_url_from_block(chunk)
        if href:
            urls.append(href)
    return urls


def _main_section_urls_for_keyword(keyword: str) -> list[str]:
    with _main_section_lock:
        cached = _main_section_cache.get(keyword)
        if cached is not None:
            return cached

    html = fetch_main_section_page(keyword)
    urls = extract_main_section_results(html)
    if not urls:
        return urls

    with _main_section_lock:
        _main_section_cache[keyword] = urls
    return urls


def find_main_section_rank(keyword: str, site: str) -> tuple[int | None, str | None]:
    for rank, result_url in enumerate(_main_section_urls_for_keyword(keyword), start=1):
        if url_matches(result_url, site):
            return rank, result_url
    return None, None


def extract_page_results(html: str) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    previous = None
    local_rank = 0
    for link in TITLE_LINK_RE.findall(html):
        link = _normalize_href(link)
        if link == previous:
            continue
        previous = link
        local_rank += 1
        results.append((local_rank, link))
    if results:
        return results

    for local_rank, link in enumerate(_extract_json_desk_urls(html), start=1):
        results.append((local_rank, link))
    return results


def page_start_index(page_index: int) -> int:
    return page_index * RESULTS_PER_PAGE + 1


def global_rank(page_start: int, local_rank: int) -> int:
    return page_start + local_rank - 1


def build_search_url(keyword: str, page: int) -> str:
    start = page_start_index(page - 1)
    params = {"query": keyword, "ssc": SEARCH_SSC, "start": str(start)}
    return f"https://search.naver.com/search.naver?{urllib.parse.urlencode(params)}"


def find_rank(
    keyword: str,
    site: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    should_cancel: CancelCallback | None = None,
    on_page: ProgressCallback | None = None,
) -> RankResult:
    max_pages = max(1, max_pages)
    pages_searched = 0
    main_section_rank: int | None = None
    main_section_matched_url: str | None = None

    for page_index in range(max_pages):
        if should_cancel and should_cancel():
            return RankResult(
                keyword=keyword,
                site=site,
                rank=None,
                matched_url=None,
                pages_searched=pages_searched,
                cancelled=True,
                main_section_rank=main_section_rank,
                main_section_matched_url=main_section_matched_url,
            )

        page_start = page_start_index(page_index)
        pages_searched += 1

        if on_page:
            on_page(keyword, page_index + 1, max_pages)

        try:
            html = fetch_search_page(keyword, start=page_start)
            if page_index == 0:
                try:
                    main_section_rank, main_section_matched_url = find_main_section_rank(
                        keyword, site
                    )
                except urllib.error.URLError:
                    pass
        except urllib.error.URLError as exc:
            raise RuntimeError(f"네이버 검색 요청 실패: {exc}") from exc

        page_results = extract_page_results(html)
        if not page_results:
            break

        for local_rank, result_url in page_results:
            if url_matches(result_url, site):
                return RankResult(
                    keyword=keyword,
                    site=site,
                    rank=global_rank(page_start, local_rank),
                    matched_url=result_url,
                    pages_searched=pages_searched,
                    main_section_rank=main_section_rank,
                    main_section_matched_url=main_section_matched_url,
                )

        if page_index + 1 < max_pages and delay_seconds > 0:
            time.sleep(delay_seconds)

    return RankResult(
        keyword=keyword,
        site=site,
        rank=None,
        matched_url=None,
        pages_searched=pages_searched,
        main_section_rank=main_section_rank,
        main_section_matched_url=main_section_matched_url,
    )


def find_ranks(
    keywords: list[str],
    site: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    should_cancel: CancelCallback | None = None,
    on_keyword_start: Callable[[str, int, int], None] | None = None,
    on_page: ProgressCallback | None = None,
) -> list[RankResult]:
    clear_search_cache()
    cleaned = [keyword.strip() for keyword in keywords if keyword.strip()]
    results: list[RankResult] = []
    total = len(cleaned)

    for index, keyword in enumerate(cleaned):
        if should_cancel and should_cancel():
            break

        if on_keyword_start:
            on_keyword_start(keyword, index + 1, total)

        results.append(
            find_rank(
                keyword,
                site,
                max_pages=max_pages,
                delay_seconds=delay_seconds,
                should_cancel=should_cancel,
                on_page=on_page,
            )
        )

        if results[-1].cancelled:
            break

        if index + 1 < total and delay_seconds > 0:
            time.sleep(delay_seconds)

    clear_search_cache()
    return results
