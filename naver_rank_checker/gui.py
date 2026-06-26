from __future__ import annotations

import threading
import tempfile
import time
import tkinter as tk
import urllib.error
import webbrowser
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from tkinter import messagebox

from .checker import IndexStatus, find_rank
from .constants import APP_VERSION, DEFAULT_DELAY_SECONDS, DEFAULT_MAX_PAGES, RESULTS_PER_PAGE, UPDATE_VERSION_URL
from .rank_search import clear_search_cache, normalize_site
from .storage import (
    EntryStore,
    SavedEntry,
    compute_rank_change,
    format_date,
    format_datetime,
    format_rank,
)
from .updater import UpdateInfo, can_auto_update, check_for_update, download_file, schedule_apply_update

ACCENT = "#03C75A"
ACCENT_HOVER = "#02B350"
SURFACE = "#161B22"
SURFACE_ALT = "#1C2128"
HEADER_BG = "#21262D"
BORDER = "#30363D"
TEXT_MUTED = "#8B949E"
RANK_TOP = "#3FB950"
RANK_MID = "#58A6FF"
RANK_NONE = "#F85149"
RANK_WARN = "#D29922"
ROW_HL = "#1F3D2E"


class TableScrollArea(ctk.CTkFrame):
    """창 이동·리사이즈 시 불필요한 스크롤 재계산을 줄이는 테이블 스크롤 영역."""

    def __init__(self, master, *, fg_color: str = SURFACE, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self._frozen = False
        self._scroll_job: str | None = None
        self._wheel_bound = False

        self._canvas = tk.Canvas(
            self,
            highlightthickness=0,
            bd=0,
            bg=fg_color,
            borderwidth=0,
        )
        self._scrollbar = ctk.CTkScrollbar(
            self,
            orientation="vertical",
            command=self._canvas.yview,
        )
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self.body = ctk.CTkFrame(self._canvas, fg_color=fg_color, corner_radius=0)
        self._body_window = self._canvas.create_window((0, 0), window=self.body, anchor="nw")
        self._canvas_width = 0

        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._scrollbar.grid(row=0, column=1, sticky="ns")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.body.bind("<Configure>", self._on_body_configure, add="+")
        self._canvas.bind("<Configure>", self._on_canvas_configure, add="+")
        self.bind("<Enter>", self._bind_wheel, add="+")
        self.bind("<Leave>", self._unbind_wheel, add="+")

    def _on_body_configure(self, event: tk.Event) -> None:
        if event.widget is self.body:
            self._schedule_scrollregion()

    def _schedule_scrollregion(self) -> None:
        if self._frozen:
            return
        if self._scroll_job is not None:
            self.after_cancel(self._scroll_job)
        self._scroll_job = self.after(100, self._apply_scrollregion)

    def _apply_scrollregion(self) -> None:
        self._scroll_job = None
        if self._frozen:
            return
        bbox = self._canvas.bbox("all")
        if bbox:
            self._canvas.configure(scrollregion=bbox)

    def _on_canvas_configure(self, event: tk.Event) -> None:
        if self._frozen or event.width <= 1:
            return
        if event.width == getattr(self, "_canvas_width", 0):
            return
        self._canvas_width = event.width
        self._canvas.itemconfigure(self._body_window, width=event.width)

    def set_frozen(self, frozen: bool) -> None:
        if self._frozen == frozen:
            return
        self._frozen = frozen
        if frozen:
            return
        width = self._canvas.winfo_width()
        if width > 1:
            self._canvas_width = width
            self._canvas.itemconfigure(self._body_window, width=width)
        self._apply_scrollregion()

    def _on_mousewheel(self, event: tk.Event) -> None:
        if self._frozen:
            return
        delta = -int(event.delta / 120) if event.delta else 0
        if delta:
            self._canvas.yview_scroll(delta, "units")

    def _bind_wheel(self, _event: tk.Event | None = None) -> None:
        if self._wheel_bound:
            return
        self._wheel_bound = True
        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")

    def _unbind_wheel(self, _event: tk.Event | None = None) -> None:
        self.after_idle(self._maybe_unbind_wheel)

    def _maybe_unbind_wheel(self) -> None:
        if not self._wheel_bound:
            return
        x, y = self.winfo_pointerxy()
        widget = self.winfo_containing(x, y)
        if widget is not None and self._contains_widget(widget):
            return
        self._wheel_bound = False
        self.unbind_all("<MouseWheel>")

    def _contains_widget(self, widget: tk.Misc) -> bool:
        current: tk.Misc | None = widget
        while current is not None:
            if current in (self, self.body, self._canvas):
                return True
            current = current.master
        return False

    def destroy(self) -> None:
        if self._scroll_job is not None:
            self.after_cancel(self._scroll_job)
        if self._wheel_bound:
            self.unbind_all("<MouseWheel>")
        super().destroy()


def parse_keywords(text: str) -> list[str]:
    """줄바꿈·쉼표·세미콜론으로 구분된 키워드 목록을 파싱합니다."""
    keywords: list[str] = []
    seen: set[str] = set()
    for line in text.replace(",", "\n").replace(";", "\n").splitlines():
        keyword = line.strip()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        keywords.append(keyword)
    return keywords


def create_selectable_site_field(
    parent: ctk.CTkFrame, site: str, bg: str, *, font: ctk.CTkFont | None = None
) -> ctk.CTkLabel:
    """더블클릭 시 URL을 클립보드에 복사하는 사이트 표시."""
    label = ctk.CTkLabel(
        parent,
        text=site,
        font=font or ctk.CTkFont(size=11),
        fg_color=bg,
        corner_radius=4,
        anchor="w",
        cursor="xterm",
    )

    def copy_site(_event: tk.Event | None = None) -> None:
        root = label.winfo_toplevel()
        root.clipboard_clear()
        root.clipboard_append(site)
        root.update_idletasks()

    label.bind("<Double-Button-1>", copy_site)
    return label


class NaverRankApp(ctk.CTk):
    _TABLE_BATCH_SIZE = 8

    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")
        super().__init__()

        self.title(f"Naver Rank Checker v{APP_VERSION}")
        self.geometry("1360x860")
        self.minsize(1100, 640)

        self.store = EntryStore()
        self._cancel_event = False
        self._worker: threading.Thread | None = None
        self._searching_id: str | None = None
        self._row_widgets: dict[str, dict] = {}
        self._fonts: dict[tuple[int, str], ctk.CTkFont] = {}
        self._save_after_id: str | None = None
        self._status_after_id: str | None = None
        self._filter_after_id: str | None = None
        self._empty_panel: ctk.CTkLabel | None = None
        self._updating_select_all = False
        self._select_all_var = ctk.BooleanVar(value=False)
        self._closing = False
        self._table_batch_token = 0
        self._last_root_size: tuple[int, int] = (0, 0)
        self._resize_after_id: str | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_top_bar()
        self._build_toolbar()
        self._build_table()
        self._build_status_bar()
        self._update_table_metadata()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Configure>", self._on_root_configure, add="+")
        self.after(1, self._deferred_startup)

        self.url_entry.bind("<Return>", lambda _e: self.keyword_text.focus())

    def _prewarm_fonts(self) -> None:
        for size, weight in ((10, "normal"), (11, "normal"), (11, "bold"), (12, "normal"), (12, "bold"), (14, "normal"), (18, "bold")):
            self._font(size, weight)

    def _deferred_startup(self) -> None:
        if self._closing:
            return
        self._prewarm_fonts()
        self._refresh_table(defer_rows=True)
        self.after(2500, self._check_for_updates)

    def _on_root_configure(self, event: tk.Event) -> None:
        if self._closing or event.widget is not self or event.width < 100 or event.height < 100:
            return
        size = (event.width, event.height)
        if size == self._last_root_size:
            return
        self._last_root_size = size
        self.table_scroll.set_frozen(True)
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(120, self._end_root_resize)

    def _end_root_resize(self) -> None:
        self._resize_after_id = None
        if self._closing:
            return
        self.table_scroll.set_frozen(False)

    def _build_top_bar(self) -> None:
        top = ctk.CTkFrame(self, fg_color=HEADER_BG, corner_radius=0, height=96)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            top,
            text="Naver Rank",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=ACCENT,
        ).grid(row=0, column=0, rowspan=2, padx=(20, 16), pady=16, sticky="w")

        reg = ctk.CTkFrame(top, fg_color="transparent")
        reg.grid(row=0, column=1, rowspan=2, sticky="ew", pady=12)
        reg.grid_columnconfigure(2, weight=2)
        reg.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(reg, text="사이트 주소", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.url_entry = ctk.CTkEntry(
            reg,
            placeholder_text="https://example.com",
            height=36,
            corner_radius=6,
        )
        self.url_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 12))

        ctk.CTkLabel(reg, text="키워드 (여러 개)", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        self.keyword_text = ctk.CTkTextbox(
            reg,
            height=72,
            corner_radius=6,
            wrap="word",
            font=ctk.CTkFont(size=12),
        )
        self.keyword_text.grid(row=1, column=2, sticky="nsew", padx=(0, 12))
        self.keyword_text.insert("1.0", "")
        self.keyword_text.bind("<Control-Return>", lambda _e: self._register_entry())

        self.register_btn = ctk.CTkButton(
            reg,
            text="등록하기",
            width=100,
            height=36,
            corner_radius=6,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._register_entry,
        )
        self.register_btn.grid(row=1, column=3, sticky="e")

        self.last_update_label = ctk.CTkLabel(
            top,
            text="마지막 업데이트: -",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        )
        self.last_update_label.grid(row=0, column=2, rowspan=2, padx=20, sticky="e")

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=52)
        bar.grid(row=1, column=0, sticky="ew")
        bar.grid_columnconfigure(7, weight=1)

        self.run_all_btn = ctk.CTkButton(
            bar,
            text="전체 재검사",
            width=110,
            height=34,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._start_search_all,
        )
        self.run_all_btn.grid(row=0, column=0, padx=(16, 6), pady=9)

        self.stop_btn = ctk.CTkButton(
            bar,
            text="중지",
            width=64,
            height=34,
            fg_color="#21262D",
            hover_color="#30363D",
            border_width=1,
            border_color=BORDER,
            state="disabled",
            command=self._request_cancel,
        )
        self.stop_btn.grid(row=0, column=1, padx=6, pady=9)

        ctk.CTkButton(
            bar,
            text="전체 선택",
            width=80,
            height=34,
            fg_color="#21262D",
            hover_color="#30363D",
            command=lambda: self._set_all_checked(True),
        ).grid(row=0, column=2, padx=6, pady=9)

        ctk.CTkButton(
            bar,
            text="선택 해제",
            width=80,
            height=34,
            fg_color="#21262D",
            hover_color="#30363D",
            command=lambda: self._set_all_checked(False),
        ).grid(row=0, column=3, padx=6, pady=9)

        self.delete_selected_btn = ctk.CTkButton(
            bar,
            text="선택 삭제",
            width=88,
            height=34,
            fg_color="#3D1F20",
            hover_color="#5C2B2B",
            border_width=1,
            border_color="#8B3A3A",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._delete_selected_entries,
        )
        self.delete_selected_btn.grid(row=0, column=4, padx=6, pady=9)

        settings = ctk.CTkFrame(bar, fg_color="transparent")
        settings.grid(row=0, column=5, padx=12, pady=9)

        ctk.CTkLabel(settings, text="페이지", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(
            side="left", padx=(0, 4)
        )
        self.pages_var = ctk.StringVar(value=str(DEFAULT_MAX_PAGES))
        ctk.CTkOptionMenu(
            settings,
            values=[str(v) for v in (5, 10, 15, 20, 30)],
            variable=self.pages_var,
            width=68,
            height=30,
            fg_color=ACCENT,
            button_color=ACCENT_HOVER,
        ).pack(side="left", padx=(0, 12))

        ctk.CTkLabel(settings, text="간격", font=ctk.CTkFont(size=11), text_color=TEXT_MUTED).pack(
            side="left", padx=(0, 4)
        )
        self.delay_label = ctk.CTkLabel(
            settings,
            text=f"{DEFAULT_DELAY_SECONDS:.0f}초",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT,
            width=36,
        )
        self.delay_label.pack(side="left", padx=(0, 4))
        self.delay_slider = ctk.CTkSlider(
            settings,
            from_=1.0,
            to=6.0,
            number_of_steps=50,
            width=100,
            command=self._on_delay_change,
        )
        self.delay_slider.set(DEFAULT_DELAY_SECONDS)
        self.delay_slider.pack(side="left")

        filter_frame = ctk.CTkFrame(bar, fg_color="transparent")
        filter_frame.grid(row=0, column=6, padx=(8, 12), pady=9, sticky="w")

        ctk.CTkLabel(
            filter_frame,
            text="목록 검색",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        ).pack(side="left", padx=(12, 4))
        self.site_filter_entry = ctk.CTkEntry(
            filter_frame,
            placeholder_text="사이트 주소",
            width=180,
            height=30,
        )
        self.site_filter_entry.pack(side="left", padx=(0, 4))
        self.site_filter_entry.bind("<KeyRelease>", lambda _e: self._apply_site_filter())
        ctk.CTkButton(
            filter_frame,
            text="×",
            width=28,
            height=30,
            fg_color="#21262D",
            hover_color="#30363D",
            command=self._clear_site_filter,
        ).pack(side="left")

        self.count_label = ctk.CTkLabel(
            bar,
            text="등록 0건",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED,
        )
        self.count_label.grid(row=0, column=8, padx=16, pady=9, sticky="e")

    def _build_table(self) -> None:
        wrap = ctk.CTkFrame(self, fg_color="#0D1117", corner_radius=0)
        wrap.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 4))
        wrap.grid_rowconfigure(1, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self._col_specs = [
            ("no", "#", 36),
            ("chk", "", 32),
            ("created", "등록일", 96),
            ("site", "사이트", 200),
            ("domain", "도메인", 120),
            ("keyword", "키워드", 130),
            ("initial", "최초", 52),
            ("prev", "이전", 52),
            ("curr", "현재", 60),
            ("change", "변동", 64),
            ("searched", "마지막 검사", 118),
            ("index", "인덱싱", 88),
            ("actions", "", 148),
        ]

        header = ctk.CTkFrame(wrap, fg_color=HEADER_BG, corner_radius=0, height=38)
        header.grid(row=0, column=0, sticky="ew")
        for idx, (_key, title, width) in enumerate(self._col_specs):
            header.grid_columnconfigure(idx, minsize=width, weight=1 if _key == "site" else 0)
            if _key == "chk":
                ctk.CTkCheckBox(
                    header,
                    text="",
                    width=20,
                    checkbox_width=16,
                    checkbox_height=16,
                    variable=self._select_all_var,
                    command=self._on_select_all_header,
                ).grid(row=0, column=idx, padx=6, pady=8, sticky="w")
                continue
            ctk.CTkLabel(
                header,
                text=title,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=TEXT_MUTED,
                anchor="w",
            ).grid(row=0, column=idx, sticky="ew", padx=6, pady=8)

        self.table_scroll = TableScrollArea(wrap, fg_color=SURFACE)
        self.table_scroll.grid(row=1, column=0, sticky="nsew")
        self.table_body = self.table_scroll.body
        for idx, (_key, _title, width) in enumerate(self._col_specs):
            self.table_body.grid_columnconfigure(
                idx, minsize=width, weight=1 if _key == "site" else 0
            )

    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=48)
        bar.grid(row=3, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            bar,
            text="사이트 주소 1개 + 키워드 여러 개(한 줄에 하나) 입력 후 등록 · Ctrl+Enter (네이버 통합검색 기준)",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=16, pady=8)

        self.progress = ctk.CTkProgressBar(bar, width=200, height=8, progress_color=ACCENT)
        self.progress.grid(row=0, column=1, padx=16, pady=8, sticky="e")
        self.progress.set(0)

    def _font(self, size: int = 11, weight: str = "normal") -> ctk.CTkFont:
        key = (size, weight)
        if key not in self._fonts:
            self._fonts[key] = ctk.CTkFont(size=size, weight=weight)
        return self._fonts[key]

    def _schedule_save(self) -> None:
        if self._save_after_id is not None:
            self.after_cancel(self._save_after_id)
        self._save_after_id = self.after(400, self._flush_save)

    def _flush_save(self) -> None:
        self._save_after_id = None
        self.store.save()

    def _set_status(self, text: str, color: str = TEXT_MUTED) -> None:
        self.status_label.configure(text=text, text_color=color)

    def _set_status_throttled(self, text: str, color: str = TEXT_MUTED) -> None:
        if self._status_after_id is not None:
            self.after_cancel(self._status_after_id)

        def apply() -> None:
            self._status_after_id = None
            self._set_status(text, color)

        self._status_after_id = self.after(120, apply)

    def _check_for_updates(self) -> None:
        if not UPDATE_VERSION_URL.strip() or self._closing:
            return

        def worker() -> None:
            info = check_for_update(UPDATE_VERSION_URL, APP_VERSION)
            if info is not None and not self._closing:
                self.after(0, lambda: self._show_update_dialog(info))

        threading.Thread(target=worker, daemon=True).start()

    def _show_update_dialog(self, info: UpdateInfo) -> None:
        if self._closing or not self.winfo_exists():
            return
        message = (
            f"새 버전 {info.version}이 있습니다.\n"
            f"(현재 버전: {APP_VERSION})"
        )
        if info.notes:
            message += f"\n\n{info.notes}"

        if can_auto_update() and info.url:
            message += (
                "\n\n「예」를 누르면 프로그램이 자동으로 업데이트된 뒤 다시 실행됩니다.\n"
                "「아니오」를 누르면 브라우저에서 직접 받을 수 있습니다."
            )
            choice = messagebox.askyesnocancel("업데이트 안내", message)
            if choice is True:
                self._start_auto_update(info)
            elif choice is False and info.url:
                webbrowser.open(info.url)
            return

        message += (
            "\n\nzip 파일을 받아 기존 폴더에 덮어쓴 뒤 실행하면 업데이트됩니다.\n"
            "브라우저에서 「일반적으로 다운로드되지 않음」 경고가 뜨면 "
            "「…」→ 유지 를 선택하세요.\n\n"
            "지금 다운로드 페이지를 열까요?"
        )
        if messagebox.askyesno("업데이트 안내", message):
            if info.url:
                webbrowser.open(info.url)
            else:
                messagebox.showinfo(
                    "업데이트",
                    "다운로드 주소가 없습니다.\n배포 페이지에서 새 파일을 받아 주세요.",
                )

    def _start_auto_update(self, info: UpdateInfo) -> None:
        if not info.url:
            messagebox.showwarning("업데이트", "다운로드 주소가 없습니다.")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("업데이트 중")
        dialog.geometry("360x140")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        status = ctk.CTkLabel(dialog, text="다운로드 준비 중...")
        status.pack(padx=20, pady=(24, 8))
        progress = ctk.CTkProgressBar(dialog, width=300)
        progress.pack(padx=20, pady=8)
        progress.set(0)

        def close_dialog() -> None:
            if dialog.winfo_exists():
                dialog.grab_release()
                dialog.destroy()

        def on_progress(done: int, total: int) -> None:
            if total > 0:
                fraction = min(done / total, 1.0)
                self.after(0, lambda: progress.set(fraction))
                percent = int(fraction * 100)
                self.after(0, lambda: status.configure(text=f"다운로드 중... {percent}%"))
            else:
                self.after(0, lambda: status.configure(text="다운로드 중..."))

        def worker() -> None:
            zip_path = Path(tempfile.gettempdir()) / f"NaverRankChecker-{info.version}.zip"
            try:
                download_file(
                    info.url,
                    zip_path,
                    current_version=APP_VERSION,
                    on_progress=on_progress,
                )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                self.after(0, close_dialog)
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "업데이트 실패",
                        f"다운로드에 실패했습니다.\n{exc}\n\n브라우저에서 직접 받아 주세요.",
                    ),
                )
                return

            def finish() -> None:
                close_dialog()
                try:
                    schedule_apply_update(zip_path)
                except RuntimeError as exc:
                    messagebox.showerror("업데이트 실패", str(exc))
                    return
                self._closing = True
                self.quit()
                self.destroy()

            self.after(0, lambda: status.configure(text="설치 준비 중... 잠시 후 다시 실행됩니다."))
            self.after(400, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _on_delay_change(self, value: float) -> None:
        self.delay_label.configure(text=f"{value:.0f}초")

    def _rank_display(self, rank: int | None, rank_kind: str | None = None) -> str:
        if rank is None:
            return "-"
        if rank > 30:
            page = (rank - 1) // RESULTS_PER_PAGE + 1
            text = f"{rank} ({page}p)"
        else:
            text = str(rank)
        if rank_kind == "main":
            return f"{text}[메인섹션]"
        return text

    def _rank_color(self, rank: int | None) -> str:
        if rank is None:
            return TEXT_MUTED
        if rank <= 3:
            return RANK_TOP
        if rank <= 10:
            return RANK_MID
        if rank <= 30:
            return RANK_WARN
        return TEXT_MUTED

    def _change_display(self, entry: SavedEntry) -> tuple[str, str]:
        change = compute_rank_change(entry.previous_rank, entry.last_rank)
        if entry.last_rank is None and entry.indexed is False:
            return "미노출", RANK_NONE
        if entry.last_rank is None and entry.indexed is True:
            return "미노출", RANK_WARN
        color = RANK_TOP if change.delta and change.delta > 0 else RANK_NONE if change.delta and change.delta < 0 else TEXT_MUTED
        if change.label in ("신규", "이탈"):
            color = RANK_TOP if change.label == "신규" else RANK_NONE
        return change.label, color

    def _index_display(self, entry: SavedEntry) -> tuple[str, str]:
        if entry.indexed is True:
            return "O", RANK_TOP
        if entry.indexed is False:
            return "X", RANK_NONE
        return "-", TEXT_MUTED

    def _site_filter_text(self) -> str:
        return self.site_filter_entry.get().strip()

    def _matches_site_filter(self, entry: SavedEntry, query: str) -> bool:
        if not query:
            return True
        try:
            needle = normalize_site(query)
        except ValueError:
            needle = query.lower().removeprefix("www.")
        try:
            haystack = normalize_site(entry.site)
        except ValueError:
            haystack = entry.site.lower()
        needle_host = needle.split("/")[0]
        host = haystack.split("/")[0]
        site_lower = entry.site.lower()
        return (
            needle in haystack
            or needle in site_lower
            or host == needle_host
            or needle_host in host
            or host.startswith(needle_host)
        )

    def _filtered_entries(self) -> list[SavedEntry]:
        query = self._site_filter_text()
        entries = self.store.list_entries()
        if not query:
            return entries
        return [entry for entry in entries if self._matches_site_filter(entry, query)]

    def _update_count_label(self, shown: int, total: int) -> None:
        if self._site_filter_text() and shown != total:
            self.count_label.configure(text=f"표시 {shown}건 / 전체 {total}건")
        else:
            self.count_label.configure(text=f"등록 {total}건")

    def _apply_site_filter(self) -> None:
        if self._filter_after_id is not None:
            self.after_cancel(self._filter_after_id)
        self._filter_after_id = self.after(180, self._apply_site_filter_now)

    def _apply_site_filter_now(self) -> None:
        self._filter_after_id = None
        self._sync_filter_visibility()

    def _clear_site_filter(self) -> None:
        if self._filter_after_id is not None:
            self.after_cancel(self._filter_after_id)
            self._filter_after_id = None
        self.site_filter_entry.delete(0, "end")
        self._sync_filter_visibility()

    def _hide_empty_panel(self) -> None:
        if self._empty_panel is not None:
            self._empty_panel.grid_remove()

    def _show_empty_panel(self, text: str) -> None:
        if self._empty_panel is None:
            self._empty_panel = ctk.CTkLabel(
                self.table_body,
                text=text,
                font=self._font(14),
                text_color=TEXT_MUTED,
                justify="center",
            )
        else:
            self._empty_panel.configure(text=text)
        self._empty_panel.grid(
            row=0, column=0, columnspan=len(self._col_specs), pady=80, sticky="ew"
        )

    def _update_table_metadata(self) -> None:
        all_entries = self.store.list_entries()
        entries = self._filtered_entries()
        self._update_count_label(len(entries), len(all_entries))

        latest = max(
            (entry.last_searched_at for entry in all_entries if entry.last_searched_at),
            default=None,
        )
        if latest:
            self.last_update_label.configure(text=f"마지막 업데이트: {format_date(latest)}")
        else:
            self.last_update_label.configure(text="마지막 업데이트: -")

    def _sync_filter_visibility(self, *, defer_rows: bool = False) -> None:
        all_entries = self.store.list_entries()
        entries = self._filtered_entries()
        visible_ids = {entry.id for entry in entries}
        self._update_count_label(len(entries), len(all_entries))

        if not all_entries:
            self._table_batch_token += 1
            for widgets in self._row_widgets.values():
                widgets["row"].grid_remove()
            self._show_empty_panel(
                "등록된 사이트·키워드가 없습니다.\n"
                "사이트 1개와 키워드(한 줄에 하나)를 입력 후 「등록하기」를 누르세요."
            )
            return

        if not entries:
            self._table_batch_token += 1
            for widgets in self._row_widgets.values():
                widgets["row"].grid_remove()
            self._show_empty_panel(f"「{self._site_filter_text()}」에 해당하는 사이트가 없습니다.")
            return

        self._hide_empty_panel()

        for entry_id, widgets in self._row_widgets.items():
            if entry_id not in visible_ids:
                widgets["row"].grid_remove()

        pending: list[tuple[int, SavedEntry]] = []
        for row_idx, entry in enumerate(entries):
            widgets = self._row_widgets.get(entry.id)
            if widgets is None:
                pending.append((row_idx, entry))
            else:
                widgets["row"].grid()
                self._sync_row(entry, row_idx, relayout=True)

        for entry_id in list(self._row_widgets):
            if entry_id not in {entry.id for entry in all_entries}:
                self._row_widgets[entry_id]["row"].destroy()
                del self._row_widgets[entry_id]

        if pending and defer_rows and len(pending) > self._TABLE_BATCH_SIZE:
            self._table_batch_token += 1
            token = self._table_batch_token
            self._build_table_rows_batched(pending, token)
        else:
            for row_idx, entry in pending:
                self._build_table_row(row_idx, entry)
            self._update_select_all_header()

    def _build_table_rows_batched(
        self, pending: list[tuple[int, SavedEntry]], token: int, start: int = 0
    ) -> None:
        if self._closing or token != self._table_batch_token:
            return
        end = min(start + self._TABLE_BATCH_SIZE, len(pending))
        for row_idx, entry in pending[start:end]:
            if entry.id not in self._row_widgets:
                self._build_table_row(row_idx, entry)
        if end < len(pending):
            self.after(1, lambda: self._build_table_rows_batched(pending, token, end))
            return
        self._update_select_all_header()

    def _relayout_rows(self) -> None:
        for row_idx, entry in enumerate(self._filtered_entries()):
            widgets = self._row_widgets.get(entry.id)
            if widgets is None:
                continue
            if not widgets["row"].winfo_ismapped():
                continue
            self._sync_row(entry, row_idx, relayout=True)

    def _remove_row(self, entry_id: str) -> None:
        widgets = self._row_widgets.pop(entry_id, None)
        if widgets:
            widgets["row"].destroy()

        all_entries = self.store.list_entries()
        if not all_entries:
            self._sync_filter_visibility()
            return

        entries = self._filtered_entries()
        if not entries:
            self._sync_filter_visibility()
            return

        self._hide_empty_panel()
        self._relayout_rows()
        self._update_table_metadata()

    def _refresh_table(self, *, rebuild: bool = False, defer_rows: bool = False) -> None:
        if rebuild:
            self._table_batch_token += 1
            for widget in self.table_body.winfo_children():
                if widget is not self._empty_panel:
                    widget.destroy()
            self._row_widgets.clear()
            if self._empty_panel is not None:
                self._empty_panel.destroy()
                self._empty_panel = None

        self._sync_filter_visibility(defer_rows=defer_rows)
        self._update_table_metadata()

    def _row_bg(self, row_idx: int, searching: bool) -> str:
        if searching:
            return ROW_HL
        return SURFACE_ALT if row_idx % 2 == 0 else SURFACE

    def _sync_row(self, entry: SavedEntry, row_idx: int, *, relayout: bool = False) -> None:
        widgets = self._row_widgets.get(entry.id)
        if widgets is None:
            self._build_table_row(row_idx, entry)
            return

        searching = entry.id == self._searching_id
        stored_idx = widgets.get("grid_idx")
        needs_layout = relayout or stored_idx != row_idx

        if needs_layout:
            bg = self._row_bg(row_idx, searching)
            widgets["row"].configure(fg_color=bg)
            widgets["row"].grid(row=row_idx, column=0, columnspan=len(self._col_specs), sticky="ew")
            widgets["grid_idx"] = row_idx
            widgets["no"].configure(text=str(row_idx + 1))
            site_entry = widgets.get("site_entry")
            if site_entry is not None:
                site_entry.configure(fg_color=bg)
        elif searching or widgets.get("was_searching"):
            bg = self._row_bg(row_idx, searching)
            widgets["row"].configure(fg_color=bg)
            site_entry = widgets.get("site_entry")
            if site_entry is not None:
                site_entry.configure(fg_color=bg)
        widgets["was_searching"] = searching

        widgets["initial"].configure(text=self._rank_display(entry.initial_rank, entry.rank_kind))
        widgets["prev"].configure(text=self._rank_display(entry.previous_rank))

        curr_color = ACCENT if searching else self._rank_color(entry.last_rank)
        if entry.last_rank:
            curr_text = self._rank_display(entry.last_rank, entry.rank_kind)
        else:
            curr_text = "검사중" if searching else "-"
        widgets["curr"].configure(text=curr_text, text_color=curr_color)

        change_text, change_color = self._change_display(entry)
        widgets["change"].configure(text=change_text, text_color=change_color)
        widgets["searched"].configure(text=format_datetime(entry.last_searched_at))

        idx_text, idx_color = self._index_display(entry)
        widgets["index"].configure(text=idx_text, text_color=idx_color)

    def _highlight_searching_row(self, entry_id: str | None) -> None:
        previous = self._searching_id
        self._searching_id = entry_id
        for eid in (previous, entry_id):
            if not eid:
                continue
            widgets = self._row_widgets.get(eid)
            if widgets is None or not widgets["row"].winfo_ismapped():
                continue
            entry = self.store.get_entry(eid)
            if entry:
                self._sync_row(entry, self._row_index(eid), relayout=False)

    def _build_table_row(self, row_idx: int, entry: SavedEntry) -> None:
        searching = entry.id == self._searching_id
        bg = self._row_bg(row_idx, searching)

        row = ctk.CTkFrame(self.table_body, fg_color=bg, corner_radius=0, height=40)
        row.grid(row=row_idx, column=0, columnspan=len(self._col_specs), sticky="ew", pady=0)

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=0, pady=4)
        for idx, (_key, _title, width) in enumerate(self._col_specs):
            inner.grid_columnconfigure(idx, minsize=width, weight=1 if _key == "site" else 0)

        no_label = ctk.CTkLabel(
            inner, text=str(row_idx + 1), font=self._font(), text_color=TEXT_MUTED, anchor="w"
        )
        no_label.grid(row=0, column=0, padx=6, sticky="w")

        check_var = ctk.BooleanVar(value=entry.checked)

        def on_toggle(eid=entry.id, var=check_var) -> None:
            self.store.set_checked(eid, var.get(), save=False)
            self._schedule_save()
            self._update_select_all_header()

        ctk.CTkCheckBox(
            inner,
            text="",
            width=20,
            checkbox_width=16,
            checkbox_height=16,
            variable=check_var,
            command=on_toggle,
        ).grid(row=0, column=1, padx=2)

        ctk.CTkLabel(
            inner, text=format_date(entry.created_at), font=self._font(), anchor="w"
        ).grid(row=0, column=2, padx=4, sticky="w")

        site_entry = create_selectable_site_field(inner, entry.site, bg, font=self._font(11))
        site_entry.grid(row=0, column=3, padx=4, sticky="ew")

        ctk.CTkLabel(
            inner, text=entry.site_label(), font=self._font(), text_color=TEXT_MUTED, anchor="w"
        ).grid(row=0, column=4, padx=4, sticky="w")

        ctk.CTkLabel(
            inner, text=entry.keyword, font=self._font(weight="bold"), anchor="w"
        ).grid(row=0, column=5, padx=4, sticky="w")

        initial_label = ctk.CTkLabel(
            inner,
            text=self._rank_display(entry.initial_rank, entry.rank_kind),
            font=self._font(),
            anchor="w",
        )
        initial_label.grid(row=0, column=6, padx=4, sticky="w")

        prev_label = ctk.CTkLabel(
            inner,
            text=self._rank_display(entry.previous_rank),
            font=self._font(),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        prev_label.grid(row=0, column=7, padx=4, sticky="w")

        curr_color = self._rank_color(entry.last_rank)
        if searching:
            curr_color = ACCENT
        curr_text = (
            self._rank_display(entry.last_rank, entry.rank_kind)
            if entry.last_rank
            else ("검사중" if searching else "-")
        )
        curr_label = ctk.CTkLabel(
            inner,
            text=curr_text,
            font=self._font(12, "bold"),
            text_color=curr_color,
            anchor="w",
        )
        curr_label.grid(row=0, column=8, padx=4, sticky="w")

        change_text, change_color = self._change_display(entry)
        change_label = ctk.CTkLabel(
            inner,
            text=change_text,
            font=self._font(weight="bold"),
            text_color=change_color,
            anchor="w",
        )
        change_label.grid(row=0, column=9, padx=4, sticky="w")

        searched_label = ctk.CTkLabel(
            inner,
            text=format_datetime(entry.last_searched_at),
            font=self._font(10),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        searched_label.grid(row=0, column=10, padx=4, sticky="w")

        idx_text, idx_color = self._index_display(entry)
        index_label = ctk.CTkLabel(
            inner,
            text=idx_text,
            font=self._font(weight="bold"),
            text_color=idx_color,
            anchor="w",
        )
        index_label.grid(row=0, column=11, padx=4, sticky="w")

        actions = ctk.CTkFrame(inner, fg_color="transparent")
        actions.grid(row=0, column=12, padx=4, sticky="e")

        ctk.CTkButton(
            actions,
            text="재검사",
            width=58,
            height=28,
            corner_radius=6,
            font=self._font(),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=lambda eid=entry.id: self._start_search_single(eid),
        ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            actions,
            text="삭제",
            width=48,
            height=28,
            corner_radius=6,
            font=self._font(),
            fg_color="#21262D",
            hover_color="#30363D",
            command=lambda eid=entry.id: self._delete_entry(eid),
        ).pack(side="left")

        self._row_widgets[entry.id] = {
            "row": row,
            "grid_idx": row_idx,
            "was_searching": searching,
            "no": no_label,
            "initial": initial_label,
            "prev": prev_label,
            "curr": curr_label,
            "change": change_label,
            "searched": searched_label,
            "index": index_label,
            "site_entry": site_entry,
            "check_var": check_var,
        }

    def _get_keyword_text(self) -> str:
        return self.keyword_text.get("1.0", "end").strip()

    def _clear_keyword_text(self) -> None:
        self.keyword_text.delete("1.0", "end")

    def _register_entry(self) -> None:
        site = self.url_entry.get().strip()
        keywords = parse_keywords(self._get_keyword_text())
        if not site:
            self.status_label.configure(text="사이트 주소를 입력해 주세요.", text_color=RANK_NONE)
            return
        if not keywords:
            self.status_label.configure(
                text="키워드를 한 줄에 하나씩 입력해 주세요. (쉼표로 구분해도 됩니다)",
                text_color=RANK_NONE,
            )
            return

        created, new_count, duplicate_count = self.store.register(site, keywords)
        self._clear_keyword_text()
        query = self._site_filter_text()
        for entry in created:
            if entry.id in self._row_widgets:
                continue
            if query and not self._matches_site_filter(entry, query):
                continue
            self._build_table_row(self._row_index(entry.id), entry)
        self._hide_empty_panel()
        self._update_table_metadata()

        if len(keywords) == 1:
            detail = f"「{keywords[0]}」"
        else:
            detail = f"{len(keywords)}개 키워드"

        if duplicate_count:
            self.status_label.configure(
                text=f"{detail} 등록 · 신규 {new_count}건 · 중복 {duplicate_count}건 · 자동 저장됨",
                text_color=RANK_TOP,
            )
        else:
            self.status_label.configure(
                text=f"{detail} 등록 완료 · {new_count}건 반영 · 자동 저장됨",
                text_color=RANK_TOP,
            )
        self.keyword_text.focus()

    def _delete_entry(self, entry_id: str) -> None:
        entry = self.store.get_entry(entry_id)
        if entry is None:
            return
        if not messagebox.askyesno("삭제", f"'{entry.keyword}' ({entry.site_label()}) 항목을 삭제할까요?"):
            return
        self.store.delete_entry(entry_id)
        self._remove_row(entry_id)
        self._set_status("항목이 삭제되었습니다.", TEXT_MUTED)

    def _delete_selected_entries(self) -> None:
        if self._worker and self._worker.is_alive():
            self.status_label.configure(
                text="검사 중에는 삭제할 수 없습니다.",
                text_color=RANK_WARN,
            )
            return

        selected = self.store.selected_entries()
        if not selected:
            self.status_label.configure(
                text="삭제할 항목을 체크해 주세요.",
                text_color=RANK_WARN,
            )
            return

        count = len(selected)
        if count == 1:
            entry = selected[0]
            message = f"'{entry.keyword}' ({entry.site_label()}) 항목을 삭제할까요?"
        else:
            preview = "\n".join(
                f"· {entry.keyword} ({entry.site})" for entry in selected[:5]
            )
            if count > 5:
                preview += f"\n· 외 {count - 5}건"
            message = f"선택한 {count}개 항목을 삭제할까요?\n\n{preview}"

        if not messagebox.askyesno("선택 삭제", message):
            return

        entry_ids = [entry.id for entry in selected]
        removed = self.store.delete_entries(entry_ids)
        for entry_id in entry_ids:
            widgets = self._row_widgets.pop(entry_id, None)
            if widgets:
                widgets["row"].destroy()

        self._sync_filter_visibility()
        self._set_status(f"선택한 {removed}건을 삭제했습니다.", TEXT_MUTED)

    def _on_select_all_header(self) -> None:
        if self._updating_select_all:
            return
        self._set_all_checked(self._select_all_var.get())

    def _update_select_all_header(self) -> None:
        entries = self._filtered_entries()
        all_checked = bool(entries) and all(entry.checked for entry in entries)
        self._updating_select_all = True
        self._select_all_var.set(all_checked)
        self._updating_select_all = False

    def _set_all_checked(self, checked: bool) -> None:
        targets = self._filtered_entries()
        for entry in targets:
            self.store.set_checked(entry.id, checked, save=False)
            widgets = self._row_widgets.get(entry.id)
            if widgets:
                widgets["check_var"].set(checked)
        self._schedule_save()
        self._update_select_all_header()

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.run_all_btn.configure(state=state)
        self.delete_selected_btn.configure(state=state)
        self.delay_slider.configure(state=state)
        self.stop_btn.configure(state="normal" if running else "disabled")

    def _request_cancel(self) -> None:
        self._cancel_event = True
        self.status_label.configure(text="중지 요청됨...")

    def _should_cancel(self) -> bool:
        return self._cancel_event

    def _start_search_single(self, entry_id: str) -> None:
        entry = self.store.get_entry(entry_id)
        if entry is None:
            return
        self._run_search_thread([entry])

    def _start_search_all(self) -> None:
        targets = self.store.checked_entries()
        if not targets:
            self.status_label.configure(text="등록된 항목이 없습니다.", text_color=RANK_NONE)
            return
        self._run_search_thread(targets)

    def _run_search_thread(self, targets: list[SavedEntry]) -> None:
        if self._worker and self._worker.is_alive():
            self.status_label.configure(text="이미 검사가 진행 중입니다.", text_color=RANK_WARN)
            return

        self._cancel_event = False
        self._set_running(True)
        self.progress.set(0)
        self.status_label.configure(text=f"{len(targets)}개 항목 검사 시작...", text_color=TEXT_MUTED)

        self._worker = threading.Thread(target=self._run_search, args=(targets,), daemon=True)
        self._worker.start()

    def _run_search(self, targets: list[SavedEntry]) -> None:
        max_pages = int(self.pages_var.get())
        delay = float(self.delay_slider.get())
        total = len(targets)
        clear_search_cache()
        index_cache: dict[str, IndexStatus] = {}

        for index, entry in enumerate(targets):
            if self._should_cancel():
                break

            entry_id = entry.id
            self.after(0, lambda eid=entry_id: self._highlight_searching_row(eid))
            self.after(
                0,
                lambda i=index, kw=entry.keyword: self._update_status(i, total, kw),
            )

            def on_page(_kw: str, page: int, max_p: int, kw=entry.keyword) -> None:
                self.after(
                    0,
                    lambda p=page, mp=max_p, keyword=kw: self._set_status_throttled(
                        f"「{keyword}」 {p}/{mp} 페이지 검색 중..."
                    ),
                )

            try:
                result = find_rank(
                    entry.keyword,
                    entry.site,
                    max_pages=max_pages,
                    delay_seconds=delay,
                    should_cancel=self._should_cancel,
                    on_page=on_page,
                    index_cache=index_cache,
                )
            except RuntimeError as exc:
                self.after(0, lambda msg=str(exc): self._on_search_error(msg))
                return

            if result.cancelled:
                break

            self.store.apply_result(entry_id, result, save=False)
            self.after(0, lambda eid=entry_id: self._sync_row_result(eid))

            if index + 1 < total and delay > 0:
                time.sleep(delay)

        self.store.save()
        clear_search_cache()
        self.after(0, lambda: self._finish_search(total))

    def _row_index(self, entry_id: str) -> int:
        for idx, entry in enumerate(self._filtered_entries()):
            if entry.id == entry_id:
                return idx
        return 0

    def _sync_row_result(self, entry_id: str) -> None:
        entry = self.store.get_entry(entry_id)
        if entry is None:
            return
        widgets = self._row_widgets.get(entry_id)
        if widgets is None or not widgets["row"].winfo_ismapped():
            return
        self._sync_row(entry, self._row_index(entry_id), relayout=False)
        self._update_table_metadata()

    def _finish_search(self, total: int) -> None:
        previous = self._searching_id
        self._searching_id = None
        if previous:
            widgets = self._row_widgets.get(previous)
            if widgets and widgets["row"].winfo_ismapped():
                entry = self.store.get_entry(previous)
                if entry:
                    self._sync_row(entry, self._row_index(previous), relayout=False)
        self._update_table_metadata()
        self._on_search_done(total)

    def _update_status(self, index: int, total: int, keyword: str) -> None:
        self.progress.set(index / total if total else 0)
        self.status_label.configure(
            text=f"「{keyword}」 검사 중 ({index + 1}/{total})",
            text_color=TEXT_MUTED,
        )

    def _on_search_done(self, total: int) -> None:
        self.progress.set(1.0)
        self._set_running(False)

        if self._cancel_event:
            self.status_label.configure(text="검사가 중지되었습니다.", text_color=TEXT_MUTED)
        else:
            entries = self.store.list_entries()
            found = sum(1 for e in entries if e.last_rank is not None)
            self.status_label.configure(
                text=f"검사 완료 · {total}건 처리 · 순위 발견 {found}건 · 자동 저장됨",
                text_color=RANK_TOP,
            )
            now = datetime.now().strftime("%Y-%m-%d")
            self.last_update_label.configure(text=f"마지막 업데이트: {now}")

    def _on_search_error(self, message: str) -> None:
        self._searching_id = None
        self._update_table_metadata()
        self.store.save()
        self._set_running(False)
        self._set_status(message, RANK_NONE)

    def _cancel_pending_timers(self) -> None:
        for attr in (
            "_save_after_id",
            "_status_after_id",
            "_filter_after_id",
            "_resize_after_id",
        ):
            after_id = getattr(self, attr, None)
            if after_id is not None:
                self.after_cancel(after_id)
                setattr(self, attr, None)

        scroll_job = getattr(self.table_scroll, "_scroll_job", None)
        if scroll_job is not None:
            self.after_cancel(scroll_job)
            self.table_scroll._scroll_job = None

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._cancel_event = True
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.unbind("<Configure>")
        self._cancel_pending_timers()
        self.withdraw()
        try:
            self.store.save()
        except OSError:
            pass
        self.destroy()


def main() -> None:
    NaverRankApp().mainloop()


if __name__ == "__main__":
    main()
