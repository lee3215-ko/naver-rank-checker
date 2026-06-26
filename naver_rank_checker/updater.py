"""시작 시 원격 version.json과 비교해 새 버전을 알려 줍니다."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    notes: str


_RAW_GITHUB_RE = re.compile(
    r"^https://raw\.githubusercontent\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<branch>[^/]+)/(?P<path>.+)$"
)


def parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in version.strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or (0,))


def is_newer(remote_version: str, local_version: str) -> bool:
    return parse_version(remote_version) > parse_version(local_version)


def _github_api_url(raw_url: str) -> str | None:
    match = _RAW_GITHUB_RE.match(raw_url.strip())
    if match is None:
        return None
    owner = match.group("owner")
    repo = match.group("repo")
    branch = match.group("branch")
    path = match.group("path")
    return (
        f"https://api.github.com/repos/{owner}/{repo}/contents/"
        f"{urllib.parse.quote(path)}?ref={urllib.parse.quote(branch)}"
    )


def _fetch_via_github_api(api_url: str, current_version: str) -> dict | None:
    request = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": f"NaverRankChecker/{current_version}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        meta = json.loads(response.read().decode("utf-8"))
    content = base64.b64decode(meta["content"]).decode("utf-8")
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("version.json must be a JSON object")
    return payload


def _fetch_via_raw_url(raw_url: str, current_version: str) -> dict:
    parsed = urllib.parse.urlparse(raw_url.strip())
    query = urllib.parse.parse_qs(parsed.query)
    query["_"] = [str(int(time.time()))]
    busted_url = parsed._replace(
        query=urllib.parse.urlencode(query, doseq=True)
    ).geturl()
    request = urllib.request.Request(
        busted_url,
        headers={
            "User-Agent": f"NaverRankChecker/{current_version}",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("version.json must be a JSON object")
    return payload


def fetch_version_payload(version_url: str, current_version: str) -> dict | None:
    url = version_url.strip()
    if not url:
        return None

    api_url = _github_api_url(url)
    if api_url:
        try:
            return _fetch_via_github_api(api_url, current_version)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, KeyError):
            pass

    try:
        return _fetch_via_raw_url(url, current_version)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def check_for_update(version_url: str, current_version: str) -> UpdateInfo | None:
    payload = fetch_version_payload(version_url, current_version)
    if payload is None:
        return None

    remote_version = str(payload.get("version", "")).strip()
    if not remote_version or not is_newer(remote_version, current_version):
        return None

    return UpdateInfo(
        version=remote_version,
        url=str(payload.get("url", "")).strip(),
        notes=str(payload.get("notes", "")).strip(),
    )


def can_auto_update() -> bool:
    return getattr(sys, "frozen", False) and sys.platform == "win32"


def get_install_dir() -> Path:
    if can_auto_update():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ProgressCallback = Callable[[int, int], None]


def download_file(
    url: str,
    dest: Path,
    *,
    current_version: str,
    on_progress: ProgressCallback | None = None,
) -> None:
    request = urllib.request.Request(
        url.strip(),
        headers={"User-Agent": f"NaverRankChecker/{current_version}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length", 0) or 0)
        downloaded = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if on_progress is not None:
                    on_progress(downloaded, total)


def _write_update_batch(batch_path: Path) -> None:
    batch_path.write_text(
        r"""@echo off
setlocal EnableExtensions
set "ZIP=%~1"
set "INSTALL=%~2"
set "EXE=%~3"
set "STAGING=%TEMP%\NaverRankChecker_update_%RANDOM%"

:wait
timeout /t 2 /nobreak >nul
tasklist /FI "IMAGENAME eq NaverRankChecker.exe" 2>nul | find /I "NaverRankChecker.exe" >nul
if not errorlevel 1 goto wait

powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath $env:ZIP -DestinationPath $env:STAGING -Force"
if errorlevel 1 goto fail

robocopy "%STAGING%\NaverRankChecker" "%INSTALL%" /E /IS /IT /R:3 /W:1 >nul
if errorlevel 8 goto fail

rd /s /q "%STAGING%" 2>nul
del /f /q "%ZIP%" 2>nul
start "" "%EXE%"
endlocal
del "%~f0"
exit /b 0

:fail
rd /s /q "%STAGING%" 2>nul
msg * "업데이트에 실패했습니다. 브라우저에서 zip을 받아 수동으로 덮어써 주세요."
endlocal
del "%~f0"
exit /b 1
""",
        encoding="utf-8",
    )


def schedule_apply_update(zip_path: Path, install_dir: Path | None = None) -> None:
    if not can_auto_update():
        raise RuntimeError("자동 업데이트는 배포용 exe에서만 사용할 수 있습니다.")

    target_dir = install_dir or get_install_dir()
    exe_path = target_dir / "NaverRankChecker.exe"
    batch_path = Path(tempfile.gettempdir()) / f"NaverRankChecker_update_{os.getpid()}.bat"
    _write_update_batch(batch_path)

    creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        ["cmd.exe", "/c", str(batch_path), str(zip_path), str(target_dir), str(exe_path)],
        creationflags=creationflags,
        close_fds=True,
    )

