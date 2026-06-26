"""시작 시 원격 version.json과 비교해 새 버전을 알려 줍니다."""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


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
