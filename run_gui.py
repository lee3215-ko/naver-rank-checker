"""GUI entry point."""

from naver_rank_checker.runtime import ensure_single_instance, notify_already_running


def main() -> None:
    if not ensure_single_instance("NaverRankChecker"):
        notify_already_running(
            "Naver Rank Checker",
            "Naver Rank Checker가 이미 실행 중입니다.",
        )
        return

    from naver_rank_checker.gui import NaverRankApp

    NaverRankApp().mainloop()


if __name__ == "__main__":
    main()
