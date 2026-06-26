from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .checker import RankResult, find_ranks
from .constants import DEFAULT_DELAY_SECONDS, DEFAULT_MAX_PAGES, RESULTS_PER_PAGE


def load_keywords(args: argparse.Namespace) -> list[str]:
    keywords: list[str] = list(args.keyword or [])
    if args.file:
        path = Path(args.file)
        if not path.exists():
            raise SystemExit(f"키워드 파일을 찾을 수 없습니다: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                keywords.append(line)
    return keywords


def format_result(result: RankResult) -> str:
    if result.main_section_rank is not None:
        unified_part = f" · 통합 {result.rank}위" if result.rank is not None else ""
        url = result.main_section_matched_url or result.matched_url or ""
        return f"[{result.keyword}] {result.main_section_rank}[메인섹션]{unified_part} ({url})"
    if result.rank is None:
        index_part = ""
        if result.indexed is True:
            index_part = f" · site: {result.index_message or '인덱싱됨'}"
        elif result.indexed is False:
            index_part = f" · site: {result.index_message or '미인덱싱'}"
        if result.pages_searched == 0 and result.indexed is False:
            return f"[{result.keyword}] 순위 검색 생략 (미인덱싱){index_part}"
        return (
            f"[{result.keyword}] 순위 없음 "
            f"({result.pages_searched}페이지 / 최대 {result.pages_searched * RESULTS_PER_PAGE}위까지 검색)"
            f"{index_part}"
        )
    return f"[{result.keyword}] {result.rank}위 ({result.matched_url})"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="naver-rank-checker",
        description="네이버 통합검색에서 내 사이트 순위를 확인합니다.",
    )
    parser.add_argument(
        "-u",
        "--url",
        help="확인할 사이트 주소 (예: https://example.com 또는 example.com)",
    )
    parser.add_argument(
        "-k",
        "--keyword",
        action="append",
        help="검색 키워드 (여러 번 지정 가능)",
    )
    parser.add_argument(
        "-f",
        "--file",
        help="키워드 목록 파일 (한 줄에 하나, #으로 주석 가능)",
    )
    parser.add_argument(
        "-p",
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"검색할 최대 페이지 수 (기본: {DEFAULT_MAX_PAGES}, 페이지당 15개 결과)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"요청 간 대기 시간(초, 기본: {DEFAULT_DELAY_SECONDS})",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="GUI 모드로 실행",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.gui:
        from .gui import main as gui_main

        gui_main()
        return 0

    keywords = load_keywords(args)
    if not keywords:
        parser.error("키워드를 하나 이상 입력해 주세요. (-k 또는 -f 사용)")

    if not args.url:
        parser.error("사이트 주소를 입력해 주세요. (-u 사용)")

    print(f"사이트: {args.url}")
    print(f"키워드 {len(keywords)}개 검사 중...\n")

    try:
        results = find_ranks(
            keywords,
            args.url,
            max_pages=args.max_pages,
            delay_seconds=args.delay,
        )
    except RuntimeError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    for result in results:
        print(format_result(result))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
