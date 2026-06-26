"""Site indexing check via Naver site: operator."""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .constants import DEFAULT_DELAY_SECONDS, DEFAULT_MAX_PAGES
from .rank_search import (
    CancelCallback,
    ProgressCallback,
    RankResult,
    fetch_search_page,
    find_rank as _find_rank,
    normalize_site,
    url_matches,
)

NO_RESULT_PATTERNS = (
    "검색 결과가 없습니다",
    "에 대한 검색결과가 없습니다",
    "결과가 없습니다",
)


@dataclass(frozen=True)
class IndexStatus:
    indexed: bool
    message: str
    sample_url: str | None = None
    result_count: int = 0


def site_domain(site: str) -> str:
    return normalize_site(site).split("/")[0]


def _html_has_no_results(html: str) -> bool:
    lowered = html.lower()
    return any(pattern in html for pattern in NO_RESULT_PATTERNS) or (
        "search_not_found" in lowered
    )


def site_search_query(site: str) -> str:
    value = site.strip().rstrip("/")
    if not value.startswith("http"):
        value = f"https://{value}"
    return f"site:{value}"


def check_site_indexed(site: str) -> IndexStatus:
    query = site_search_query(site)

    try:
        html = fetch_search_page(query, start=1)
    except urllib.error.URLError as exc:
        return IndexStatus(False, f"확인 실패 ({exc})", result_count=0)

    if _html_has_no_results(html):
        return IndexStatus(False, "미인덱싱", result_count=0)

    from .rank_search import extract_page_results

    page_results = extract_page_results(html)
    urls = [href for _, href in page_results]
    matching = [url for url in urls if url_matches(url, site)]
    domain = site_domain(site)
    domain_urls = [url for url in urls if site_domain(url) == domain]

    if matching:
        return IndexStatus(
            True,
            "인덱싱됨",
            sample_url=matching[0],
            result_count=len(domain_urls or urls),
        )

    if domain_urls:
        return IndexStatus(
            True,
            f"도메인 인덱싱 ({len(domain_urls)}건)",
            sample_url=domain_urls[0],
            result_count=len(domain_urls),
        )

    if urls:
        return IndexStatus(
            True,
            f"인덱싱됨 ({len(urls)}건)",
            sample_url=urls[0],
            result_count=len(urls),
        )

    return IndexStatus(False, "미인덱싱", result_count=0)


def _resolve_index_status(
    site: str,
    cache: dict[str, IndexStatus] | None = None,
) -> IndexStatus:
    key = normalize_site(site)
    if cache is not None:
        if key not in cache:
            cache[key] = check_site_indexed(site)
        return cache[key]
    return check_site_indexed(site)


def _result_with_index(result: RankResult, index_status: IndexStatus) -> RankResult:
    return RankResult(
        keyword=result.keyword,
        site=result.site,
        rank=result.rank,
        matched_url=result.matched_url,
        pages_searched=result.pages_searched,
        cancelled=result.cancelled,
        indexed=index_status.indexed,
        index_message=index_status.message,
        index_sample_url=index_status.sample_url,
        main_section_rank=result.main_section_rank,
        main_section_matched_url=result.main_section_matched_url,
    )


def find_rank(
    keyword: str,
    site: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    should_cancel: CancelCallback | None = None,
    on_page: ProgressCallback | None = None,
    check_index_when_missing: bool = True,
    index_cache: dict[str, IndexStatus] | None = None,
) -> RankResult:
    index_status: IndexStatus | None = None

    if check_index_when_missing:
        if should_cancel and should_cancel():
            return RankResult(
                keyword=keyword,
                site=site,
                rank=None,
                matched_url=None,
                pages_searched=0,
                cancelled=True,
            )

        index_status = _resolve_index_status(site, index_cache)

        if not index_status.indexed and index_status.message == "미인덱싱":
            return RankResult(
                keyword=keyword,
                site=site,
                rank=None,
                matched_url=None,
                pages_searched=0,
                indexed=False,
                index_message=index_status.message,
                index_sample_url=index_status.sample_url,
            )

        if delay_seconds > 0:
            time.sleep(delay_seconds)

    result = _find_rank(
        keyword,
        site,
        max_pages=max_pages,
        delay_seconds=delay_seconds,
        should_cancel=should_cancel,
        on_page=on_page,
    )

    if index_status is not None:
        return _result_with_index(result, index_status)
    return result


def find_ranks(
    keywords: list[str],
    site: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    should_cancel: CancelCallback | None = None,
    on_keyword_start=None,
    on_page: ProgressCallback | None = None,
    check_index_when_missing: bool = True,
) -> list[RankResult]:
    cleaned = [keyword.strip() for keyword in keywords if keyword.strip()]
    results: list[RankResult] = []
    total = len(cleaned)
    index_cache: dict[str, IndexStatus] = {}

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
                check_index_when_missing=check_index_when_missing,
                index_cache=index_cache,
            )
        )

        if results[-1].cancelled:
            break

        if index + 1 < total and delay_seconds > 0:
            time.sleep(delay_seconds)

    return results
