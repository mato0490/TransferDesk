from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


REPOSITORY = "mato0490/ultra-pro-files-manager"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
REQUEST_TIMEOUT = 20


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    tag: str
    page_url: str
    body: str
    asset: ReleaseAsset
    checksum_asset: ReleaseAsset


@dataclass(frozen=True)
class DownloadedUpdate:
    release: UpdateRelease
    archive_path: Path
    checksum: str


def normalize_version(value: str) -> tuple[int, ...]:
    text = str(value).strip()
    if text.startswith(("v", "V")):
        text = text[1:]
    match = re.fullmatch(r"(\d+(?:\.\d+){0,3})(?:[-+].*)?", text)
    if not match:
        raise ValueError(f"Version invalide: {value}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(remote: str, local: str) -> bool:
    remote_parts = normalize_version(remote)
    local_parts = normalize_version(local)
    size = max(len(remote_parts), len(local_parts))
    remote_parts = (*remote_parts, *(0 for _ in range(size - len(remote_parts))))
    local_parts = (*local_parts, *(0 for _ in range(size - len(local_parts))))
    return remote_parts > local_parts


def current_platform_key() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return platform.system().casefold() or sys.platform.casefold()


def expected_asset_name(version: str, platform_key: str | None = None) -> str:
    key = platform_key or current_platform_key()
    return f"TransferDesk-{key}-v{version}.zip"


def fetch_latest_release(
    local_version: str,
    *,
    api_url: str = API_URL,
    platform_key: str | None = None,
    opener: Callable[[str], dict[str, Any]] | None = None,
) -> UpdateRelease | None:
    data = opener(api_url) if opener else _read_json(api_url)
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("La derniere publication GitHub n'a pas de tag.")
    version = tag[1:] if tag.lower().startswith("v") else tag
    if not is_newer_version(version, local_version):
        return None
    assets = [
        ReleaseAsset(
            name=str(asset.get("name") or ""),
            download_url=str(asset.get("browser_download_url") or ""),
            size=int(asset.get("size") or 0),
        )
        for asset in data.get("assets", [])
    ]
    wanted = expected_asset_name(version, platform_key)
    asset = next((item for item in assets if item.name == wanted and item.download_url), None)
    checksum = next((item for item in assets if item.name == f"{wanted}.sha256" and item.download_url), None)
    if asset is None or checksum is None:
        raise UpdateError(f"Aucun paquet compatible trouve pour {wanted}.")
    return UpdateRelease(
        version=version,
        tag=tag,
        page_url=str(data.get("html_url") or RELEASES_URL),
        body=str(data.get("body") or ""),
        asset=asset,
        checksum_asset=checksum,
    )


def download_update(
    release: UpdateRelease,
    destination_dir: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> DownloadedUpdate:
    destination_dir.mkdir(parents=True, exist_ok=True)
    archive_path = destination_dir / release.asset.name
    checksum_text = _read_text(release.checksum_asset.download_url)
    expected = parse_sha256(checksum_text, release.asset.name)
    _download_file(release.asset.download_url, archive_path, release.asset.size, progress)
    actual = sha256_file(archive_path)
    if actual.casefold() != expected.casefold():
        try:
            archive_path.unlink()
        except OSError:
            pass
        raise UpdateError("La verification SHA-256 de la mise a jour a echoue.")
    return DownloadedUpdate(release=release, archive_path=archive_path, checksum=actual)


def parse_sha256(text: str, expected_name: str | None = None) -> str:
    for line in str(text).splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        digest = parts[0].strip()
        if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            if expected_name is None or len(parts) == 1 or parts[-1].lstrip("*") == expected_name:
                return digest.lower()
    raise UpdateError("Le fichier de checksum SHA-256 est invalide.")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def install_downloaded_update(downloaded: DownloadedUpdate, *, relaunch: bool = True) -> str:
    archive = downloaded.archive_path
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(archive)])
        return "open"
    if not sys.platform.startswith("win"):
        raise UpdateError("Installation automatique disponible uniquement sur Windows et macOS.")
    if not getattr(sys, "frozen", False):
        subprocess.Popen(["explorer", "/select,", str(archive)])
        return "open"
    app_dir = Path(sys.executable).resolve().parent
    parent = app_dir.parent
    helper = write_windows_updater_script(downloaded, app_dir, parent, Path(sys.executable).name, relaunch)
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            str(os.getpid()),
        ],
        cwd=str(parent),
        close_fds=True,
    )
    QCoreApplication = None
    try:
        from PySide6.QtCore import QCoreApplication as QCoreApplication
    except Exception:
        pass
    if QCoreApplication is not None and QCoreApplication.instance() is not None:
        QCoreApplication.instance().quit()
    return "restart"


def write_windows_updater_script(
    downloaded: DownloadedUpdate,
    app_dir: Path,
    install_parent: Path,
    executable_name: str,
    relaunch: bool,
) -> Path:
    script = Path(tempfile.gettempdir()) / f"transferdesk-updater-{int(time.time())}.ps1"
    script.write_text(
        "\n".join(
            [
                "param([int]$PidToWait)",
                "$ErrorActionPreference = 'Stop'",
                f"$Archive = {powershell_quote(str(downloaded.archive_path))}",
                f"$AppDir = {powershell_quote(str(app_dir))}",
                f"$InstallParent = {powershell_quote(str(install_parent))}",
                f"$ExeName = {powershell_quote(executable_name)}",
                f"$Relaunch = ${str(bool(relaunch)).lower()}",
                "try { Wait-Process -Id $PidToWait -Timeout 120 } catch { Start-Sleep -Seconds 3 }",
                "$ExtractDir = Join-Path $env:TEMP ('transferdesk-install-' + [guid]::NewGuid())",
                "New-Item -ItemType Directory -Path $ExtractDir | Out-Null",
                "Expand-Archive -LiteralPath $Archive -DestinationPath $ExtractDir -Force",
                "$Candidate = Get-ChildItem -LiteralPath $ExtractDir -Directory | Select-Object -First 1",
                "if ($null -eq $Candidate) { throw 'Archive de mise a jour invalide.' }",
                "$Backup = $AppDir + '.old-' + (Get-Date -Format 'yyyyMMddHHmmss')",
                "Move-Item -LiteralPath $AppDir -Destination $Backup",
                "Move-Item -LiteralPath $Candidate.FullName -Destination $AppDir",
                "Remove-Item -LiteralPath $ExtractDir -Recurse -Force",
                "if ($Relaunch) { Start-Process -FilePath (Join-Path $AppDir $ExeName) }",
            ]
        ),
        encoding="utf-8",
    )
    return script


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _read_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "TransferDesk-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Verification GitHub impossible: {exc}") from exc


def _read_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "TransferDesk-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.read().decode("utf-8")
    except (OSError, urllib.error.URLError, UnicodeDecodeError) as exc:
        raise UpdateError(f"Telechargement du checksum impossible: {exc}") from exc


def _download_file(
    url: str,
    destination: Path,
    expected_size: int,
    progress: Callable[[int, int], None] | None,
) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "TransferDesk-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response, destination.open("wb") as output:
            total = int(response.headers.get("Content-Length") or expected_size or 0)
            completed = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                completed += len(chunk)
                if progress:
                    progress(completed, total)
    except (OSError, urllib.error.URLError) as exc:
        try:
            destination.unlink()
        except OSError:
            pass
        raise UpdateError(f"Telechargement de la mise a jour impossible: {exc}") from exc
