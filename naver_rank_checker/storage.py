from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .checker import RankResult, normalize_site


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _data_file() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home())) / "NaverRankChecker"
    else:
        base = Path.home() / ".naver-rank-checker"
    base.mkdir(parents=True, exist_ok=True)
    return base / "entries.json"


def _legacy_file() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home())) / "NaverRankChecker"
    else:
        base = Path.home() / ".naver-rank-checker"
    return base / "profiles.json"


def format_datetime(value: str | None) -> str:
    if not value:
        return "미검사"
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def format_date(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return value[:10] if len(value) >= 10 else value


def short_site(site: str) -> str:
    try:
        return normalize_site(site).split("/")[0]
    except ValueError:
        return site


@dataclass
class SavedEntry:
    id: str
    site: str
    keyword: str
    created_at: str
    updated_at: str
    checked: bool = True
    last_searched_at: str | None = None
    last_rank: int | None = None
    previous_rank: int | None = None
    initial_rank: int | None = None
    rank_kind: str | None = None
    last_unified_rank: int | None = None
    last_main_section_rank: int | None = None
    last_matched_url: str | None = None
    indexed: bool | None = None
    index_message: str = ""
    index_sample_url: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SavedEntry:
        return cls(
            id=data["id"],
            site=data["site"],
            keyword=data["keyword"],
            created_at=data.get("created_at") or "",
            updated_at=data.get("updated_at") or "",
            checked=bool(data.get("checked", True)),
            last_searched_at=data.get("last_searched_at"),
            last_rank=data.get("last_rank"),
            previous_rank=data.get("previous_rank"),
            initial_rank=data.get("initial_rank"),
            rank_kind=data.get("rank_kind"),
            last_unified_rank=data.get("last_unified_rank"),
            last_main_section_rank=data.get("last_main_section_rank"),
            last_matched_url=data.get("last_matched_url"),
            indexed=data.get("indexed"),
            index_message=data.get("index_message") or "",
            index_sample_url=data.get("index_sample_url"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def site_label(self) -> str:
        return short_site(self.site)

    def rank_label(self) -> str:
        return f"{self.last_rank}위" if self.last_rank is not None else "-"

    def index_label(self) -> str:
        if self.indexed is True:
            return self.index_message or "인덱싱됨"
        if self.indexed is False:
            return self.index_message or "미인덱싱"
        return "-"


@dataclass(frozen=True)
class RankChange:
    previous_rank: int | None
    current_rank: int | None
    delta: int | None
    label: str
    detail: str


def compute_rank_change(previous: int | None, current: int | None) -> RankChange:
    if previous is None and current is None:
        return RankChange(None, None, None, "—", "이전 기록 없음")

    if previous is None and current is not None:
        return RankChange(None, current, None, "신규", f"신규 진입 · 현재 {current}위")

    if previous is not None and current is None:
        return RankChange(previous, None, None, "이탈", f"이전 {previous}위 → 순위 없음")

    assert previous is not None and current is not None
    delta = previous - current
    if delta > 0:
        return RankChange(
            previous,
            current,
            delta,
            f"▲ {delta}",
            f"{previous}위 → {current}위 ({delta}위 상승)",
        )
    if delta < 0:
        drop = abs(delta)
        return RankChange(
            previous,
            current,
            delta,
            f"▼ {drop}",
            f"{previous}위 → {current}위 ({drop}위 하락)",
        )
    return RankChange(previous, current, 0, "유지", f"{current}위 유지")


def format_rank(rank: int | None) -> str:
    return f"{rank}위" if rank is not None else "-"


class EntryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _data_file()
        self.entries: list[SavedEntry] = []
        self.load()

    def load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = [SavedEntry.from_dict(item) for item in data.get("entries", [])]
            return

        legacy = _legacy_file()
        if legacy.exists():
            self._migrate_legacy(legacy)
            self.save()
            return

        self.entries = []

    def _migrate_legacy(self, legacy_path: Path) -> None:
        data = json.loads(legacy_path.read_text(encoding="utf-8"))
        migrated: list[SavedEntry] = []
        for profile in data.get("profiles", []):
            site = profile.get("site", "")
            last_results = profile.get("last_results") or {}
            for keyword in profile.get("keywords") or []:
                saved = last_results.get(keyword) or {}
                now = profile.get("updated_at") or _now_iso()
                migrated.append(
                    SavedEntry(
                        id=str(uuid.uuid4()),
                        site=site,
                        keyword=keyword,
                        created_at=profile.get("created_at") or now,
                        updated_at=now,
                        checked=True,
                        last_searched_at=saved.get("searched_at") or profile.get("last_searched_at"),
                        last_rank=saved.get("rank"),
                        last_matched_url=saved.get("matched_url"),
                    )
                )
        self.entries = migrated

    def save(self) -> None:
        payload = {"entries": [entry.to_dict() for entry in self.entries]}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_entries(self) -> list[SavedEntry]:
        return list(self.entries)

    def get_entry(self, entry_id: str | None) -> SavedEntry | None:
        if not entry_id:
            return None
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    def find_entry(self, site: str, keyword: str) -> SavedEntry | None:
        site = site.strip()
        keyword = keyword.strip()
        for entry in self.entries:
            if entry.site.strip() == site and entry.keyword.strip() == keyword:
                return entry
        return None

    def register(self, site: str, keywords: list[str]) -> tuple[list[SavedEntry], int, int]:
        site = site.strip()
        now = _now_iso()
        created: list[SavedEntry] = []
        new_count = 0
        duplicate_count = 0

        for raw_keyword in keywords:
            keyword = raw_keyword.strip()
            if not keyword:
                continue

            existing = self.find_entry(site, keyword)
            if existing:
                existing.updated_at = now
                created.append(existing)
                duplicate_count += 1
                continue

            entry = SavedEntry(
                id=str(uuid.uuid4()),
                site=site,
                keyword=keyword,
                created_at=now,
                updated_at=now,
                checked=True,
            )
            self.entries.append(entry)
            created.append(entry)
            new_count += 1

        self.save()
        return created, new_count, duplicate_count

    def delete_entry(self, entry_id: str) -> None:
        self.entries = [entry for entry in self.entries if entry.id != entry_id]
        self.save()

    def delete_entries(self, entry_ids: list[str]) -> int:
        if not entry_ids:
            return 0
        remove = set(entry_ids)
        before = len(self.entries)
        self.entries = [entry for entry in self.entries if entry.id not in remove]
        removed = before - len(self.entries)
        if removed:
            self.save()
        return removed

    def selected_entries(self) -> list[SavedEntry]:
        return [entry for entry in self.entries if entry.checked]

    def set_checked(self, entry_id: str, checked: bool, *, save: bool = True) -> None:
        entry = self.get_entry(entry_id)
        if entry is None:
            return
        entry.checked = checked
        if save:
            self.save()

    def set_all_checked(self, checked: bool, *, save: bool = True) -> None:
        for entry in self.entries:
            entry.checked = checked
        if save:
            self.save()

    def apply_result(
        self, entry_id: str, result: RankResult, *, save: bool = True
    ) -> SavedEntry | None:
        entry = self.get_entry(entry_id)
        if entry is None or result.cancelled:
            return entry

        now = _now_iso()
        entry.previous_rank = entry.last_rank
        effective = result.effective_rank
        entry.last_rank = effective
        entry.rank_kind = result.rank_kind
        entry.last_unified_rank = result.rank
        entry.last_main_section_rank = result.main_section_rank
        if entry.initial_rank is None and effective is not None:
            entry.initial_rank = effective
        entry.last_matched_url = result.main_section_matched_url or result.matched_url
        entry.last_searched_at = now
        entry.updated_at = now
        entry.indexed = result.indexed
        entry.index_message = result.index_message
        entry.index_sample_url = result.index_sample_url
        if save:
            self.save()
        return entry

    def checked_entries(self) -> list[SavedEntry]:
        checked = [entry for entry in self.entries if entry.checked]
        return checked or list(self.entries)
