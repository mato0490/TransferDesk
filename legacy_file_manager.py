from __future__ import annotations

import filecmp
import hashlib
import json
import os
import plistlib
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Callable

from PIL import Image, UnidentifiedImageError

import themes_config as tc
import translations as i18n
import network_transfer as nt
import webrtc_transfer as wt
from transferdesk_version import __version__


APP_TITLE = "File Manager"
APP_VERSION = __version__
CHUNK_SIZE = 4 * 1024 * 1024
PROFILE_FILENAME = "profiles.json"
HISTORY_FILENAME = "history.json"
MAX_HISTORY_ENTRIES = 200

EXTENSION_DB = {
    "Images": [
        ".jpg", ".jpeg", ".png", ".raw", ".arw", ".cr2", ".nef",
        ".dng", ".svg", ".gif", ".bmp", ".webp", ".heic",
    ],
    "Videos": [
        ".mp4", ".mov", ".mkv", ".avi", ".mts", ".m4v", ".flv",
        ".webm", ".wmv", ".3gp",
    ],
    "Audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"],
    "Documents": [
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
        ".txt", ".md", ".csv", ".rtf",
    ],
    "Archives": [".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".bz2"],
    "Code / Data": [
        ".py", ".js", ".html", ".css", ".json", ".xml", ".sql",
        ".c", ".cpp", ".java", ".exe", ".bin",
    ],
}
ALL_EXTENSIONS = sorted({ext for values in EXTENSION_DB.values() for ext in values})


class TransferError(Exception):
    """Configuration error suitable for display to the user."""


class TransferCancelled(Exception):
    """Intentional cancellation of the current operation."""


@dataclass(frozen=True)
class TransferOptions:
    source: Path
    destination: Path
    extensions: tuple[str, ...] = ()
    date_mode: str = "latest"
    date_start: date | None = None
    date_end: date | None = None
    create_folder: bool = True
    folder_name_mode: str = "auto"
    custom_folder_name: str = "File_Backup"
    delete_source: bool = False
    preserve_tree: bool = False
    verify_checksum: bool = True
    conflict_policy: str = "rename"
    organization_mode: str = "none"
    language: str = "en"


@dataclass(frozen=True)
class FileCandidate:
    path: Path
    modified: date
    size: int
    captured: date | None = None


@dataclass(frozen=True)
class TransferPlanItem:
    source: Path
    destination: Path
    size: int
    action: str
    reason: str = ""


@dataclass
class TransferPlan:
    items: list[TransferPlanItem] = field(default_factory=list)
    target_dir: Path | None = None
    required_bytes: int = 0
    free_bytes: int = 0

    @property
    def enough_space(self) -> bool:
        return self.required_bytes <= self.free_bytes

    @property
    def actionable_items(self) -> list[TransferPlanItem]:
        return [
            item for item in self.items
            if item.action not in {"skip_identical", "skip_conflict"}
        ]


@dataclass
class TransferResult:
    selected: int = 0
    copied: int = 0
    skipped: int = 0
    renamed: int = 0
    replaced: int = 0
    verified: int = 0
    conflict_skipped: int = 0
    deleted: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    target_dir: Path | None = None


@dataclass(frozen=True)
class DuplicateGroup:
    digest: str
    size: int
    original: Path
    duplicates: tuple[Path, ...]

    @property
    def files(self) -> tuple[Path, ...]:
        return (self.original, *self.duplicates)


@dataclass
class DuplicateScanResult:
    groups: list[DuplicateGroup] = field(default_factory=list)
    files_scanned: int = 0

    @property
    def duplicate_files(self) -> int:
        return sum(len(group.duplicates) for group in self.groups)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(group.size * len(group.duplicates) for group in self.groups)


EventCallback = Callable[[str, dict], None]


def enable_windows_liquid_glass(root: tk.Tk) -> None:
    """Enable the safest Windows 11 backdrop and title-bar glass effects."""
    if os.name != "nt":
        return
    try:
        import ctypes

        root.update_idletasks()
        hwnd = root.winfo_id()
        dwm = ctypes.windll.dwmapi

        def set_attribute(attribute: int, value: int) -> None:
            data = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)
            )

        set_attribute(20, 1)   # DWMWA_USE_IMMERSIVE_DARK_MODE
        set_attribute(38, 2)   # DWMWA_SYSTEMBACKDROP_TYPE: Mica
        set_attribute(33, 3)   # DWMWA_WINDOW_CORNER_PREFERENCE: small radius
        set_attribute(34, 0x00160B07)  # dark caption, COLORREF byte order
        set_attribute(35, 0x00664734)  # subtle blue glass border
        set_attribute(36, 0x00FFF7F5)  # near-white title text
    except (AttributeError, OSError, tk.TclError):
        # Older Windows versions keep the same palette without native Mica.
        return


def application_data_dir() -> Path:
    """Return a writable per-user directory in source and frozen builds."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TransferDesk"
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "TransferDesk"
    return Path.home() / ".auto_sd_file_manager"


def sha256_file(path: Path, cancel_event: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise TransferCancelled
            chunk = file_handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def extension_category(path: Path) -> str:
    suffix = path.suffix.casefold()
    for category, extensions in EXTENSION_DB.items():
        if suffix in extensions:
            return category.replace(" / ", "_").replace(" ", "_")
    return "Other"


def format_display_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def format_path_date(value: date) -> str:
    return value.strftime("%d-%m-%Y")


def format_history_timestamp(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return parsed.strftime("%d/%m/%Y %H:%M:%S")


def photo_taken_date(path: Path) -> date | None:
    """Read the original capture date from common EXIF fields when available."""
    if path.suffix.casefold() not in {".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".png"}:
        return None
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            raw_value = exif.get(36867) or exif.get(36868) or exif.get(306)
        if not raw_value:
            return None
        return datetime.strptime(str(raw_value)[:19], "%Y:%m:%d %H:%M:%S").date()
    except (OSError, ValueError, TypeError, UnidentifiedImageError):
        return None


class JsonStore:
    """Small atomic JSON store used for profiles and operation history."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, default: object) -> object:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            return default

    def save(self, value: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)


class ProfileStore:
    def __init__(self, path: Path | None = None) -> None:
        self.store = JsonStore(path or application_data_dir() / PROFILE_FILENAME)

    def all(self) -> dict[str, dict[str, object]]:
        value = self.store.load({})
        return value if isinstance(value, dict) else {}

    def save(self, name: str, settings: dict[str, object]) -> None:
        cleaned = name.strip()
        if not cleaned:
            raise TransferError("The profile name is empty.")
        profiles = self.all()
        profiles[cleaned] = settings
        self.store.save(profiles)

    def delete(self, name: str) -> None:
        profiles = self.all()
        profiles.pop(name, None)
        self.store.save(profiles)


class HistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.store = JsonStore(path or application_data_dir() / HISTORY_FILENAME)

    def entries(self) -> list[dict[str, object]]:
        value = self.store.load([])
        return value if isinstance(value, list) else []

    def append(self, entry: dict[str, object]) -> None:
        entries = self.entries()
        entries.insert(0, entry)
        self.store.save(entries[:MAX_HISTORY_ENTRIES])

    def export_text(self, destination: Path, language: str = "en") -> None:
        lines: list[str] = []
        for entry in self.entries():
            counts = i18n.translate(
                language, "history_counts", copied=entry.get("copied", 0),
                skipped=entry.get("skipped", 0), errors=entry.get("errors", 0),
            )
            lines.append(
                f"{format_history_timestamp(entry.get('timestamp', ''))} | "
                f"{i18n.translate(language, str(entry.get('status', '')).casefold())} | "
                f"{entry.get('source', '')} -> {entry.get('destination', '')} | {counts}"
            )
        destination.write_text("\n".join(lines), encoding="utf-8")


def removable_drives() -> list[Path]:
    """Return removable volumes on Windows and macOS."""
    if os.name == "nt":
        try:
            import ctypes

            mask = ctypes.windll.kernel32.GetLogicalDrives()
            drives: list[Path] = []
            for index in range(26):
                if mask & (1 << index):
                    root = f"{chr(65 + index)}:\\"
                    if ctypes.windll.kernel32.GetDriveTypeW(root) == 2:
                        drives.append(Path(root))
            return drives
        except (AttributeError, OSError):
            return []
    if sys.platform == "darwin":
        volumes = Path("/Volumes")
        try:
            candidates = list(volumes.iterdir())
        except OSError:
            return []
        removable: list[Path] = []
        for volume in candidates:
            try:
                completed = subprocess.run(
                    ["/usr/sbin/diskutil", "info", "-plist", str(volume)],
                    capture_output=True, timeout=5, check=False,
                )
                info = plistlib.loads(completed.stdout) if completed.returncode == 0 else {}
                if info.get("RemovableMedia") or info.get("Ejectable"):
                    removable.append(volume)
            except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
                continue
        return removable
    return []


def removable_root(path: Path) -> Path | None:
    try:
        resolved = Path(path).resolve()
    except OSError:
        resolved = Path(path)
    for drive in removable_drives():
        if os.name == "nt":
            if drive.drive.casefold() == resolved.drive.casefold():
                return drive
        else:
            try:
                resolved.relative_to(drive.resolve())
                return drive
            except (OSError, ValueError):
                continue
    return None


def removable_drive_key(path: Path) -> str:
    """Return a stable comparison key for Windows drive letters or macOS volumes."""
    if os.name == "nt":
        return path.drive.casefold()
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path).casefold()


def eject_removable_drive(path: Path, language: str = "en") -> None:
    """Ask the operating system to safely eject a verified removable drive."""
    drive = removable_root(path)
    if drive is None:
        raise TransferError(i18n.translate(language, "removable_not_selected"))
    if sys.platform == "darwin":
        completed = subprocess.run(
            ["/usr/sbin/diskutil", "eject", str(drive)],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or i18n.translate(language, "eject_refused")
            raise TransferError(detail)
        return
    if os.name != "nt":
        raise TransferError(i18n.translate(language, "eject_windows_only"))
    drive_name = drive.drive.rstrip("\\")
    command = (
        "$shell = New-Object -ComObject Shell.Application; "
        f"$item = $shell.Namespace(17).ParseName('{drive_name}'); "
        "if ($null -eq $item) { exit 2 }; $item.InvokeVerb('Eject')"
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=creation_flags,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or i18n.translate(language, "eject_refused")
        raise TransferError(detail)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if removable_root(drive) is None:
            return
        time.sleep(0.25)
    raise TransferError(i18n.translate(language, "eject_still_mounted"))


def normalize_extensions(raw_value: str) -> tuple[str, ...]:
    """Normalize ``jpg, *.PNG, .mov`` into unique lowercase suffixes."""
    result: list[str] = []
    for value in raw_value.replace(";", ",").split(","):
        extension = value.strip().lower()
        if not extension:
            continue
        if extension in {"*", "*.*"}:
            return ()
        extension = extension.removeprefix("*")
        if not extension.startswith("."):
            extension = f".{extension}"
        if extension != "." and extension not in result:
            result.append(extension)
    return tuple(result)


def format_size(value: float) -> str:
    size = max(0.0, float(value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def format_duration(seconds: float, language: str = "en") -> str:
    if seconds < 0 or seconds == float("inf"):
        return i18n.translate(language, "calculating")
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return i18n.translate(
            language, "duration_hours", hours=hours, minutes=minutes, seconds=secs
        )
    return i18n.translate(
        language, "duration_minutes", minutes=minutes, seconds=secs
    )


def parse_iso_date(value: str, label: str, language: str = "en") -> date:
    """Parse French display dates while accepting legacy ISO profile values."""
    cleaned = value.strip()
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    raise TransferError(i18n.translate(language, "date_format_error", label=label))


def paths_overlap(first: Path, second: Path) -> bool:
    """Return whether two folders are identical or nested."""
    try:
        first_resolved = first.resolve()
        second_resolved = second.resolve()
        return (
            first_resolved == second_resolved
            or first_resolved in second_resolved.parents
            or second_resolved in first_resolved.parents
        )
    except OSError:
        return False


def validate_folder_name(name: str, language: str = "en") -> str:
    cleaned = name.strip()
    invalid_chars = '<>:"/\\|?*'
    if not cleaned:
        raise TransferError(i18n.translate(language, "custom_name_empty"))
    if cleaned in {".", ".."} or any(char in cleaned for char in invalid_chars):
        raise TransferError(i18n.translate(language, "custom_name_invalid"))
    if cleaned.endswith((" ", ".")):
        raise TransferError(i18n.translate(language, "custom_name_ending"))
    return cleaned


class TransferEngine:
    """Tkinter-independent, previewable and verifiable transfer engine."""

    def __init__(
        self,
        options: TransferOptions,
        cancel_event: threading.Event | None = None,
        callback: EventCallback | None = None,
        selected_paths: set[Path] | None = None,
        conflict_overrides: dict[Path, str] | None = None,
    ) -> None:
        self.options = options
        self.cancel_event = cancel_event or threading.Event()
        self.callback = callback or (lambda _event, _data: None)
        self.selected_paths = (
            {path.resolve() for path in selected_paths} if selected_paths is not None else None
        )
        self.conflict_overrides = {
            path.resolve(): action for path, action in (conflict_overrides or {}).items()
        }

    def t(self, key: str, **values: object) -> str:
        return i18n.translate(self.options.language, key, **values)

    def emit(self, event: str, **data: object) -> None:
        self.callback(event, data)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise TransferCancelled

    def validate(self) -> None:
        source = self.options.source
        destination = self.options.destination
        if not source.is_dir():
            raise TransferError(self.t("source_missing"))
        if not destination.is_dir():
            raise TransferError(self.t("destination_missing"))
        if paths_overlap(source, destination):
            raise TransferError(self.t("folders_overlap"))
        if self.options.date_mode not in {"latest", "all", "specific", "range"}:
            raise TransferError(self.t("date_mode_invalid"))
        if self.options.conflict_policy not in {"rename", "skip", "replace", "newer", "ask"}:
            raise TransferError(self.t("conflict_policy_invalid"))
        if self.options.organization_mode not in {"none", "date", "year_month", "type"}:
            raise TransferError(self.t("organization_invalid"))
        if self.options.date_mode == "specific" and self.options.date_start is None:
            raise TransferError(self.t("date_missing"))
        if self.options.date_mode == "range":
            if self.options.date_start is None or self.options.date_end is None:
                raise TransferError(self.t("date_range_incomplete"))
            if self.options.date_start > self.options.date_end:
                raise TransferError(self.t("date_order_invalid"))
        if self.options.create_folder and self.options.folder_name_mode == "custom":
            validate_folder_name(self.options.custom_folder_name, self.options.language)

    def scan(self) -> list[FileCandidate]:
        self.emit("log", message=self.t("scanning_source"), level="info")
        candidates: list[FileCandidate] = []
        extensions = self.options.extensions

        def on_walk_error(error: OSError) -> None:
            self.emit("log", message=self.t("folder_skipped", error=error), level="error")

        for root, _directories, files in os.walk(self.options.source, onerror=on_walk_error):
            self.check_cancelled()
            for filename in files:
                self.check_cancelled()
                if extensions and not filename.lower().endswith(extensions):
                    continue
                file_path = Path(root) / filename
                try:
                    stat = file_path.stat()
                    candidates.append(
                        FileCandidate(
                            path=file_path,
                            modified=datetime.fromtimestamp(stat.st_mtime).date(),
                            size=stat.st_size,
                            captured=photo_taken_date(file_path),
                        )
                    )
                except OSError as exc:
                    self.emit("log", message=self.t("file_unreadable", name=file_path.name, error=exc), level="error")
        return candidates

    def filter_candidates(self, candidates: list[FileCandidate]) -> list[FileCandidate]:
        if not candidates:
            return []
        mode = self.options.date_mode
        if mode == "all":
            filtered = candidates
        elif mode == "latest":
            latest = max(candidate.modified for candidate in candidates)
            self.emit("log", message=self.t("latest_selected", date=format_display_date(latest)), level="info")
            filtered = [candidate for candidate in candidates if candidate.modified == latest]
        elif mode == "specific":
            filtered = [candidate for candidate in candidates if candidate.modified == self.options.date_start]
        else:
            filtered = [
                candidate for candidate in candidates
                if self.options.date_start <= candidate.modified <= self.options.date_end  # type: ignore[operator]
            ]
        if self.selected_paths is not None:
            filtered = [candidate for candidate in filtered if candidate.path.resolve() in self.selected_paths]
        return filtered

    def target_directory(self, candidates: list[FileCandidate], create: bool = False) -> Path:
        if not self.options.create_folder:
            target = self.options.destination
        elif self.options.folder_name_mode == "custom":
            target = self.options.destination / validate_folder_name(
                self.options.custom_folder_name, self.options.language
            )
        else:
            dates = sorted({candidate.modified for candidate in candidates})
            first = format_path_date(dates[0])
            last = format_path_date(dates[-1])
            target = self.options.destination / (first if first == last else f"{first}_au_{last}")
        if create:
            target.mkdir(parents=True, exist_ok=True)
        return target

    def relative_directory(self, candidate: FileCandidate) -> Path:
        relative = Path()
        if self.options.preserve_tree:
            relative = candidate.path.relative_to(self.options.source).parent
        organization = self.options.organization_mode
        organization_date = candidate.captured or candidate.modified
        if organization == "date":
            prefix = Path(format_path_date(organization_date))
        elif organization == "year_month":
            prefix = Path(str(organization_date.year), f"{organization_date.month:02d}")
        elif organization == "type":
            category = extension_category(candidate.path)
            category_keys = {
                "Images": "category_images", "Videos": "category_videos",
                "Audio": "category_audio", "Documents": "category_documents",
                "Archives": "category_archives", "Code_Data": "category_code_data",
                "Other": "category_other",
            }
            prefix = Path(self.t(category_keys.get(category, "category_other")))
        else:
            prefix = Path()
        return prefix / relative

    @staticmethod
    def path_key(path: Path) -> str:
        return os.path.normcase(os.path.abspath(path))

    def resolve_destination(
        self, source: Path, desired: Path, reserved: set[str]
    ) -> tuple[Path, str, str]:
        desired_key = self.path_key(desired)
        if not desired.exists() and desired_key not in reserved:
            return desired, "copy", self.t("new_file")
        if desired.exists() and desired.is_file() and filecmp.cmp(source, desired, shallow=False):
            return desired, "skip_identical", self.t("already_verified")

        planned_collision = desired_key in reserved and not desired.exists()
        policy = self.conflict_overrides.get(source.resolve(), self.options.conflict_policy)
        if policy == "ask":
            return desired, "ask_conflict", self.t("decision_required")
        if policy == "skip":
            return desired, "skip_conflict", self.t("conflict_skipped")
        if not planned_collision and policy == "replace":
            return desired, "replace", self.t("conflict_replace")
        if not planned_collision and policy == "newer":
            try:
                if source.stat().st_mtime > desired.stat().st_mtime:
                    return desired, "replace", self.t("source_newer")
            except OSError:
                pass
            return desired, "skip_conflict", self.t("destination_newer")

        stem, suffix = desired.stem, desired.suffix
        number = 2
        while True:
            self.check_cancelled()
            candidate = desired.with_name(f"{stem} ({number}){suffix}")
            candidate_key = self.path_key(candidate)
            if not candidate.exists() and candidate_key not in reserved:
                return candidate, "rename", self.t("conflict_rename")
            if candidate.exists() and candidate.is_file() and filecmp.cmp(source, candidate, shallow=False):
                return candidate, "skip_identical", self.t("renamed_verified")
            number += 1

    def build_plan(self, candidates: list[FileCandidate]) -> TransferPlan:
        if not candidates:
            return TransferPlan(free_bytes=shutil.disk_usage(self.options.destination).free)
        candidates.sort(key=lambda candidate: (candidate.modified, str(candidate.path).casefold()))
        target = self.target_directory(candidates)
        reserved: set[str] = set()
        items: list[TransferPlanItem] = []
        required = 0
        for candidate in candidates:
            desired = target / self.relative_directory(candidate) / candidate.path.name
            destination, action, reason = self.resolve_destination(candidate.path, desired, reserved)
            if action not in {"skip_identical", "skip_conflict"}:
                reserved.add(self.path_key(destination))
                required += candidate.size
            items.append(TransferPlanItem(candidate.path, destination, candidate.size, action, reason))
        return TransferPlan(
            items=items,
            target_dir=target,
            required_bytes=required,
            free_bytes=shutil.disk_usage(self.options.destination).free,
        )

    def preview(self) -> TransferPlan:
        self.validate()
        return self.build_plan(self.filter_candidates(self.scan()))

    def copy_file(
        self,
        source: Path,
        destination: Path,
        completed_before: int,
        total_bytes: int,
        started_at: float,
        file_index: int,
        total_files: int,
        replace: bool = False,
        verify: bool = False,
    ) -> int:
        copied = 0
        last_update = 0.0
        output_path = destination
        destination_created = False
        temporary_path: Path | None = None
        destination.parent.mkdir(parents=True, exist_ok=True)
        if replace:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".transfer", dir=destination.parent
            )
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            temporary_path.unlink()
            output_path = temporary_path
        try:
            with source.open("rb") as input_file, output_path.open("xb") as output_file:
                destination_created = True
                while True:
                    self.check_cancelled()
                    chunk = input_file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    output_file.write(chunk)
                    copied += len(chunk)
                    now = time.monotonic()
                    if now - last_update >= 0.08:
                        self.emit_progress(
                            completed_before + copied, total_bytes, started_at, file_index, total_files
                        )
                        last_update = now
            self.check_cancelled()
            shutil.copystat(source, output_path)
            if verify and sha256_file(source, self.cancel_event) != sha256_file(output_path, self.cancel_event):
                raise TransferError(self.t("sha_failed", name=source.name))
            if replace:
                os.replace(output_path, destination)
                temporary_path = None
            return copied
        except BaseException:
            if destination_created:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def emit_progress(
        self,
        completed_bytes: int,
        total_bytes: int,
        started_at: float,
        file_index: int,
        total_files: int,
    ) -> None:
        elapsed = max(time.monotonic() - started_at, 0.001)
        speed = completed_bytes / elapsed
        remaining = max(total_bytes - completed_bytes, 0)
        eta = remaining / speed if speed else float("inf")
        self.emit(
            "progress", completed=min(completed_bytes, total_bytes), total=total_bytes,
            speed=speed, remaining=remaining, eta=eta,
            file_index=file_index, total_files=total_files,
        )

    def run(self) -> TransferResult:
        result = TransferResult()
        verified_sources: list[Path] = []
        try:
            self.validate()
            candidates = self.filter_candidates(self.scan())
            self.check_cancelled()
            if not candidates:
                self.emit("log", message=self.t("no_files_match"), level="warning")
                return result
            plan = self.build_plan(candidates)
            if any(item.action == "ask_conflict" for item in plan.items):
                raise TransferError(self.t("conflict_unresolved"))
            if not plan.enough_space:
                raise TransferError(self.t(
                    "space_engine", required=format_size(plan.required_bytes),
                    free=format_size(plan.free_bytes),
                ))
            result.selected = len(plan.items)
            result.target_dir = plan.target_dir
            if plan.target_dir is not None:
                plan.target_dir.mkdir(parents=True, exist_ok=True)
            total_bytes = sum(item.size for item in plan.items)
            self.emit(
                "scan_complete", total_files=len(plan.items), total_bytes=total_bytes,
                target=str(result.target_dir),
            )
            self.emit(
                "log", message=self.t("files_to_process", count=len(plan.items), size=format_size(total_bytes)),
                level="success",
            )

            completed_bytes = 0
            started_at = time.monotonic()
            verify_copy = self.options.verify_checksum or self.options.delete_source
            for index, item in enumerate(plan.items, start=1):
                self.check_cancelled()
                try:
                    if item.action == "skip_identical":
                        result.skipped += 1
                        result.verified += 1
                        verified_sources.append(item.source)
                        self.emit(
                            "log", message=f"[{index}/{len(plan.items)}] {item.reason}: {item.destination}",
                            level="muted",
                        )
                    elif item.action == "skip_conflict":
                        result.skipped += 1
                        result.conflict_skipped += 1
                        self.emit(
                            "log", message=f"[{index}/{len(plan.items)}] {item.reason}: {item.destination}",
                            level="warning",
                        )
                    else:
                        if item.action == "rename":
                            result.renamed += 1
                        elif item.action == "replace":
                            result.replaced += 1
                        self.emit(
                            "log", message=self.t(
                                "copying_file", index=index, total=len(plan.items),
                                name=item.source.name, destination=item.destination,
                            ),
                            level="info",
                        )
                        self.copy_file(
                            item.source, item.destination, completed_bytes, total_bytes, started_at,
                            index, len(plan.items), replace=item.action == "replace", verify=verify_copy,
                        )
                        result.copied += 1
                        if verify_copy:
                            result.verified += 1
                            verified_sources.append(item.source)
                    completed_bytes += item.size
                except TransferCancelled:
                    raise
                except (OSError, PermissionError, TransferError) as exc:
                    message = self.t("failed_file", name=item.source.name, error=exc)
                    result.errors.append(message)
                    self.emit("log", message=message, level="error")
                    completed_bytes += item.size
                self.emit_progress(
                    completed_bytes, total_bytes, started_at, index, len(plan.items)
                )

            self.check_cancelled()
            if self.options.delete_source and verified_sources:
                self.emit("log", message=self.t("deleting_verified"), level="warning")
                for source in verified_sources:
                    self.check_cancelled()
                    try:
                        source.unlink()
                        result.deleted += 1
                    except OSError as exc:
                        message = self.t("delete_source_failed", name=source.name, error=exc)
                        result.errors.append(message)
                        self.emit("log", message=message, level="error")
            return result
        except TransferCancelled:
            result.cancelled = True
            self.emit("log", message=self.t("operation_cancelled"), level="warning")
            return result


class DuplicateEngine:
    """Find exact duplicates and safely act only on verified extra copies."""

    def __init__(
        self,
        root_folder: Path,
        cancel_event: threading.Event | None = None,
        callback: EventCallback | None = None,
        duplicates_folder_name: str | None = None,
        language: str = "en",
    ) -> None:
        self.root_folder = root_folder
        self.cancel_event = cancel_event or threading.Event()
        self.callback = callback or (lambda _event, _data: None)
        self.duplicates_folder = root_folder / (
            duplicates_folder_name or i18n.translate(language, "duplicates_folder")
        )
        self.last_result = DuplicateScanResult()
        self.language = language

    def t(self, key: str, **values: object) -> str:
        return i18n.translate(self.language, key, **values)

    def emit(self, event: str, **data: object) -> None:
        self.callback(event, data)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise TransferCancelled

    def validate(self) -> None:
        if not self.root_folder.is_dir():
            raise TransferError(self.t("scan_folder_missing"))

    def hash_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file_handle:
            while True:
                self.check_cancelled()
                chunk = file_handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def scan(self) -> DuplicateScanResult:
        self.validate()
        by_size: dict[int, list[Path]] = {}
        files_scanned = 0
        duplicates_resolved = self.duplicates_folder.resolve()

        for current_root, directories, filenames in os.walk(self.root_folder):
            self.check_cancelled()
            current = Path(current_root)
            directories[:] = [
                name for name in directories
                if (current / name).resolve() != duplicates_resolved
            ]
            for filename in filenames:
                self.check_cancelled()
                path = current / filename
                try:
                    size = path.stat().st_size
                except OSError as exc:
                    self.emit("duplicate_log", message=self.t("duplicate_skipped", path=path, error=exc), level="error")
                    continue
                by_size.setdefault(size, []).append(path)
                files_scanned += 1

        candidates = [path for paths in by_size.values() if len(paths) > 1 for path in paths]
        hashed = 0
        by_hash: dict[tuple[int, str], list[Path]] = {}
        for size, paths in by_size.items():
            if len(paths) < 2:
                continue
            for path in paths:
                self.check_cancelled()
                try:
                    digest = self.hash_file(path)
                except OSError as exc:
                    self.emit("duplicate_log", message=self.t("duplicate_unreadable", path=path, error=exc), level="error")
                    continue
                by_hash.setdefault((size, digest), []).append(path)
                hashed += 1
                self.emit("duplicate_progress", completed=hashed, total=len(candidates))

        groups: list[DuplicateGroup] = []
        for (size, digest), paths in by_hash.items():
            if len(paths) < 2:
                continue
            ordered = sorted(paths, key=lambda path: str(path).casefold())
            groups.append(DuplicateGroup(digest, size, ordered[0], tuple(ordered[1:])))
        groups.sort(key=lambda group: str(group.original).casefold())
        self.last_result = DuplicateScanResult(groups=groups, files_scanned=files_scanned)
        return self.last_result

    def verified_duplicate_paths(self) -> set[Path]:
        return {path.resolve() for group in self.last_result.groups for path in group.duplicates}

    def validate_action_path(self, path: Path) -> Path:
        resolved = path.resolve()
        for group in self.last_result.groups:
            duplicate_paths = {duplicate.resolve() for duplicate in group.duplicates}
            if resolved not in duplicate_paths:
                continue
            original = group.original.resolve()
            try:
                still_identical = (
                    resolved.is_file()
                    and original.is_file()
                    and filecmp.cmp(resolved, original, shallow=False)
                )
            except OSError:
                still_identical = False
            if not still_identical:
                raise TransferError(self.t("duplicate_changed", path=path))
            return resolved
        raise TransferError(self.t("duplicate_unverified", path=path))

    @staticmethod
    def available_destination(path: Path, target_dir: Path) -> Path:
        destination = target_dir / path.name
        if not destination.exists():
            return destination
        number = 2
        while True:
            candidate = target_dir / f"{path.stem} ({number}){path.suffix}"
            if not candidate.exists():
                return candidate
            number += 1

    def apply_action(self, paths: list[Path], action: str) -> dict[Path, Path | None]:
        if action not in {"delete", "move", "ignore"}:
            raise TransferError(self.t("duplicate_action_unknown", action=action))
        if action == "ignore":
            return {path: None for path in paths}
        if action == "move":
            self.duplicates_folder.mkdir(exist_ok=True)

        results: dict[Path, Path | None] = {}
        for path in paths:
            self.check_cancelled()
            source = self.validate_action_path(path)
            if action == "delete":
                source.unlink()
                results[path] = None
            else:
                destination = self.available_destination(source, self.duplicates_folder)
                shutil.move(str(source), str(destination))
                results[path] = destination
        return results


# L'interface historique conserve ses définitions pour compatibilité avec les
# anciens scripts, mais utilise les symboles du moteur extrait à l'exécution.
from transferdesk_core import (
    ALL_EXTENSIONS,
    DuplicateEngine,
    DuplicateGroup,
    DuplicateScanResult,
    FileCandidate,
    HistoryStore,
    ProfileStore,
    TransferCancelled,
    TransferEngine,
    TransferError,
    TransferOptions,
    TransferPlan,
    TransferPlanItem,
    TransferResult,
    application_data_dir,
    eject_removable_drive,
    extension_category,
    format_display_date,
    format_duration,
    format_path_date,
    format_size,
    normalize_extensions,
    parse_iso_date,
    photo_taken_date,
    removable_drive_key,
    removable_drives,
    removable_root,
    validate_folder_name,
)


class TransferDeskApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.geometry("1180x840")
        self.root.minsize(820, 700)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.style = ttk.Style(root)
        self.events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        self.duplicate_worker: threading.Thread | None = None
        self.duplicate_running = False
        self.duplicate_cancel_event = threading.Event()
        self.duplicate_engine: DuplicateEngine | None = None
        self.duplicate_items: dict[str, Path] = {}
        self.network_worker: threading.Thread | None = None
        self.network_running = False
        self.network_cancel_event = threading.Event()
        self.network_server: nt.LocalTransferServer | None = None
        self.network_server_tls = False
        self.network_send_files: list[Path] = []
        self.network_window: tk.Toplevel | None = None
        self.network_devices: dict[str, nt.DiscoveredDevice] = {}
        self.network_scanning = False
        self.network_reserved_request_id: str | None = None
        self.discovery_service = nt.DiscoveryService(callback=self.enqueue_discovery_event)
        self.palette = tc.get_palette(tc.THEME_PAR_DEFAUT)
        self.profile_store = ProfileStore()
        self.history_store = HistoryStore()
        self.current_options: TransferOptions | None = None
        self.last_completed_source: Path | None = None
        self.known_removable_drives = {removable_drive_key(drive) for drive in removable_drives()}

        today = format_display_date(date.today())
        self.language_code = "fr"
        self.language_var = tk.StringVar(value=i18n.LANGUAGE_NAMES[self.language_code])
        self.source_var = tk.StringVar()
        self.destination_var = tk.StringVar()
        self.extensions_var = tk.StringVar()
        self.extension_search_var = tk.StringVar()
        self.date_mode_var = tk.StringVar(value="latest")
        self.date_start_var = tk.StringVar(value=today)
        self.date_end_var = tk.StringVar(value=today)
        self.create_folder_var = tk.BooleanVar(value=True)
        self.folder_mode_var = tk.StringVar(value="auto")
        self.custom_folder_var = tk.StringVar(value="File_Backup")
        self.delete_source_var = tk.BooleanVar(value=False)
        self.preserve_tree_var = tk.BooleanVar(value=True)
        self.verify_checksum_var = tk.BooleanVar(value=True)
        self.conflict_policy_var = tk.StringVar(value="rename")
        self.organization_mode_var = tk.StringVar(value="none")
        self.conflict_display_var = tk.StringVar()
        self.organization_display_var = tk.StringVar()
        self.monitor_removable_var = tk.BooleanVar(value=True)
        self.profile_name_var = tk.StringVar()
        self.theme_var = tk.StringVar(value=tc.THEME_PAR_DEFAUT)
        self.status_var = tk.StringVar(value=self.t("ready"))
        self.duplicate_folder_var = tk.StringVar()
        self.duplicate_status_var = tk.StringVar(value=self.t("choose_scan_folder"))
        self.network_host_var = tk.StringVar()
        self.network_port_var = tk.StringVar(value=str(nt.DEFAULT_PORT))
        self.network_code_var = tk.StringVar(value=nt.generate_pairing_code())
        downloads = Path.home() / "Downloads"
        self.network_destination_var = tk.StringVar(
            value=str(downloads) if downloads.is_dir() else ""
        )
        self.internet_port_var = tk.StringVar(value="48722")
        self.internet_public_host_var = tk.StringVar()
        self.internet_rendezvous_var = tk.StringVar(value=wt.default_rendezvous_url())
        self.network_status_var = tk.StringVar(
            value=nt.network_text(self.language_code, "ready")
        )

        self.build_ui()
        self.apply_theme(self.theme_var.get())
        enable_windows_liquid_glass(self.root)
        self.update_extension_suggestions()
        self.update_date_controls()
        self.update_folder_controls()
        self.discovery_service.start()
        self.root.after(80, self.process_events)
        self.root.after(1500, self.poll_removable_drives)

    def t(self, key: str, **values: object) -> str:
        return i18n.translate(self.language_code, key, **values)

    def on_language_changed(self, _event: tk.Event | None = None) -> None:
        requested = i18n.LANGUAGE_CODES.get(self.language_var.get(), self.language_code)
        if requested == self.language_code:
            return
        if self.running or self.duplicate_running or self.network_running:
            self.language_var.set(i18n.LANGUAGE_NAMES[self.language_code])
            messagebox.showinfo(
                self.t("language"), self.t("wait_scan"), parent=self.root
            )
            return
        current_tab = self.notebook.index(self.notebook.select()) if hasattr(self, "notebook") else 0
        self.language_code = requested
        self.status_var.set(self.t("ready"))
        self.duplicate_status_var.set(self.t("choose_scan_folder"))
        self.duplicate_engine = None
        self.duplicate_items.clear()
        for child in self.root.winfo_children():
            child.destroy()
        self.build_ui()
        self.notebook.select(min(current_tab, len(self.notebook.tabs()) - 1))
        self.apply_theme(self.theme_var.get())
        self.update_extension_suggestions()
        self.update_date_controls()
        self.update_folder_controls()
        self.update_eject_state()

    def build_ui(self) -> None:
        self.root.title(f"{self.t('app_title')} — V{APP_VERSION}")
        self.header = ttk.Frame(self.root, style="Header.TFrame", padding=(24, 18, 24, 16))
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.columnconfigure(0, weight=1)
        text_sticky = "e" if self.language_code == "he" else "w"
        ttk.Label(self.header, text=self.t("app_title"), style="Title.TLabel").grid(row=0, column=0, sticky=text_sticky)
        ttk.Label(
            self.header,
            text=self.t("subtitle"),
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky=text_sticky)
        theme_box = ttk.Frame(self.header, style="Header.TFrame")
        theme_box.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Label(theme_box, text=self.t("theme"), style="Header.TLabel").pack(side="left", padx=(0, 8))
        self.theme_combo = ttk.Combobox(
            theme_box,
            textvariable=self.theme_var,
            values=tc.TOUS_LES_THEMES,
            state="readonly",
            width=12,
        )
        self.theme_combo.pack(side="left")
        self.theme_combo.bind("<<ComboboxSelected>>", self.on_theme_changed)
        ttk.Label(theme_box, text=self.t("language"), style="Header.TLabel").pack(
            side="left", padx=(16, 8)
        )
        self.language_combo = ttk.Combobox(
            theme_box,
            textvariable=self.language_var,
            values=tuple(i18n.LANGUAGE_NAMES.values()),
            state="readonly",
            width=10,
        )
        self.language_combo.pack(side="left")
        self.language_combo.bind("<<ComboboxSelected>>", self.on_language_changed)
        ttk.Button(
            theme_box,
            text=nt.network_text(self.language_code, "button"),
            command=self.open_network_window,
        ).pack(side="left", padx=(16, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=18, pady=(12, 0))
        self.transfer_tab = ttk.Frame(self.notebook, style="App.TFrame")
        self.duplicate_tab = ttk.Frame(self.notebook, style="App.TFrame")
        self.history_tab = ttk.Frame(self.notebook, style="App.TFrame")
        self.notebook.add(self.transfer_tab, text=f"  {self.t('tab_transfer')}  ")
        self.notebook.add(self.duplicate_tab, text=f"  {self.t('tab_duplicates')}  ")
        self.notebook.add(self.history_tab, text=f"  {self.t('tab_history')}  ")

        body = ttk.Frame(self.transfer_tab, style="App.TFrame")
        body.grid(row=0, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.transfer_tab.rowconfigure(0, weight=1)
        self.transfer_tab.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.content = ttk.Frame(self.canvas, style="App.TFrame", padding=(18, 14, 18, 12))
        self.canvas_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.columnconfigure(0, weight=1)
        self.content.bind("<Configure>", self.on_content_configure)
        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.canvas.bind("<Enter>", lambda _event: self.canvas.bind_all("<MouseWheel>", self.on_mousewheel))
        self.canvas.bind("<Leave>", lambda _event: self.canvas.unbind_all("<MouseWheel>"))

        self.build_paths_card()
        self.build_types_card()
        self.build_dates_card()
        self.build_options_card()
        self.build_progress_card()
        self.build_log_card()

        footer = ttk.Frame(self.transfer_tab, style="Footer.TFrame", padding=(20, 14))
        footer.grid(row=1, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, style="Footer.TLabel").grid(row=0, column=0, sticky="w")
        self.cancel_button = ttk.Button(
            footer,
            text=self.t("cancel"),
            style="Danger.TButton",
            command=self.cancel_transfer,
            state="disabled",
        )
        self.cancel_button.grid(row=0, column=1, padx=(8, 0))
        self.eject_button = ttk.Button(
            footer,
            text=self.t("safe_eject"),
            command=self.eject_source,
            state="disabled",
        )
        self.eject_button.grid(row=0, column=2, padx=(8, 0))
        self.start_button = ttk.Button(
            footer,
            text=self.t("preview_transfer"),
            style="Success.TButton",
            command=self.start_transfer,
        )
        self.start_button.grid(row=0, column=3, padx=(8, 0))
        self.build_duplicate_ui()
        self.build_history_ui()

    def build_duplicate_ui(self) -> None:
        page = self.duplicate_tab
        page.rowconfigure(1, weight=1)
        page.columnconfigure(0, weight=1)

        controls = ttk.LabelFrame(page, text=self.t("folder_to_scan"), style="Card.TLabelframe", padding=12)
        controls.grid(row=0, column=0, sticky="ew", padx=16, pady=14)
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text=self.t("folder"), style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.duplicate_folder_var).grid(row=0, column=1, sticky="ew", padx=10)
        ttk.Button(controls, text=self.t("browse"), command=self.select_duplicate_folder).grid(row=0, column=2)
        self.duplicate_scan_button = ttk.Button(
            controls, text=self.t("scan_duplicates"), style="Success.TButton", command=self.start_duplicate_scan
        )
        self.duplicate_scan_button.grid(row=0, column=3, padx=(10, 0))
        self.duplicate_cancel_button = ttk.Button(
            controls, text=self.t("cancel"), style="Danger.TButton", command=self.cancel_duplicate_scan, state="disabled"
        )
        self.duplicate_cancel_button.grid(row=0, column=4, padx=(8, 0))
        self.duplicate_progress = ttk.Progressbar(
            controls, maximum=100, style="App.Horizontal.TProgressbar"
        )
        self.duplicate_progress.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(12, 5))
        ttk.Label(controls, textvariable=self.duplicate_status_var, style="CardMuted.TLabel").grid(
            row=2, column=0, columnspan=5, sticky="w"
        )

        results = ttk.LabelFrame(page, text=self.t("verified_duplicates"), style="Card.TLabelframe", padding=12)
        results.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        results.rowconfigure(0, weight=1)
        results.columnconfigure(0, weight=1)
        columns = ("role", "size", "path", "status")
        self.duplicate_tree = ttk.Treeview(results, columns=columns, show="tree headings", selectmode="extended")
        self.duplicate_tree.heading("#0", text=self.t("group"))
        self.duplicate_tree.heading("role", text=self.t("type"))
        self.duplicate_tree.heading("size", text=self.t("size"))
        self.duplicate_tree.heading("path", text=self.t("file"))
        self.duplicate_tree.heading("status", text=self.t("status"))
        self.duplicate_tree.column("#0", width=95, stretch=False)
        self.duplicate_tree.column("role", width=105, stretch=False)
        self.duplicate_tree.column("size", width=95, stretch=False, anchor="e")
        self.duplicate_tree.column("path", width=520)
        self.duplicate_tree.column("status", width=100, stretch=False)
        tree_scroll = ttk.Scrollbar(results, orient="vertical", command=self.duplicate_tree.yview)
        self.duplicate_tree.configure(yscrollcommand=tree_scroll.set)
        self.duplicate_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")

        actions = ttk.Frame(page, style="Footer.TFrame", padding=(16, 10))
        actions.grid(row=2, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        ttk.Label(actions, text=self.t("selected_files"), style="Footer.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text=self.t("do_nothing"), command=lambda: self.apply_duplicate_action("ignore", False)).grid(row=0, column=1, padx=4)
        ttk.Button(actions, text=self.t("move_duplicates"), command=lambda: self.apply_duplicate_action("move", False)).grid(row=0, column=2, padx=4)
        ttk.Button(actions, text=self.t("delete"), style="Danger.TButton", command=lambda: self.apply_duplicate_action("delete", False)).grid(row=0, column=3, padx=4)
        ttk.Separator(actions).grid(row=1, column=0, columnspan=4, sticky="ew", pady=7)
        ttk.Label(actions, text=self.t("all_duplicates"), style="Footer.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Button(actions, text=self.t("do_nothing_all"), command=lambda: self.apply_duplicate_action("ignore", True)).grid(row=2, column=1, padx=4)
        ttk.Button(actions, text=self.t("move_all"), command=lambda: self.apply_duplicate_action("move", True)).grid(row=2, column=2, padx=4)
        ttk.Button(actions, text=self.t("delete_all"), style="Danger.TButton", command=lambda: self.apply_duplicate_action("delete", True)).grid(row=2, column=3, padx=4)

    def build_history_ui(self) -> None:
        page = self.history_tab
        page.rowconfigure(0, weight=1)
        page.columnconfigure(0, weight=1)
        frame = ttk.LabelFrame(page, text=self.t("transfer_history"), style="Card.TLabelframe", padding=12)
        frame.grid(row=0, column=0, sticky="nsew", padx=16, pady=14)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        columns = ("timestamp", "status", "source", "destination", "result")
        self.history_tree = ttk.Treeview(frame, columns=columns, show="headings")
        headings = {
            "timestamp": self.t("history_date"), "status": self.t("status"),
            "source": self.t("history_source"), "destination": self.t("history_destination"),
            "result": self.t("history_result"),
        }
        widths = {"timestamp": 145, "status": 90, "source": 230, "destination": 230, "result": 210}
        for column in columns:
            self.history_tree.heading(column, text=headings[column])
            self.history_tree.column(column, width=widths[column], stretch=column in {"source", "destination"})
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=scroll.set)
        self.history_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        buttons = ttk.Frame(page, style="Footer.TFrame", padding=(16, 10))
        buttons.grid(row=1, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        ttk.Label(buttons, text=self.t("history_limit"), style="Footer.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(buttons, text=self.t("refresh"), command=self.refresh_history_tree).grid(row=0, column=1, padx=4)
        ttk.Button(buttons, text=self.t("export_report"), command=self.export_history).grid(row=0, column=2, padx=4)
        self.refresh_history_tree()

    def select_duplicate_folder(self) -> None:
        selected = filedialog.askdirectory(title=self.t("choose_scan_title"))
        if selected:
            self.duplicate_folder_var.set(selected)

    def start_duplicate_scan(self) -> None:
        if self.duplicate_running:
            return
        folder_text = self.duplicate_folder_var.get().strip()
        if not folder_text:
            messagebox.showerror(self.t("invalid_folder"), self.t("choose_folder_error"), parent=self.root)
            return
        folder = Path(folder_text).expanduser()
        try:
            engine = DuplicateEngine(
                folder, self.duplicate_cancel_event, self.enqueue_event,
                language=self.language_code,
            )
            engine.validate()
        except TransferError as exc:
            messagebox.showerror(self.t("invalid_folder"), str(exc), parent=self.root)
            return

        self.duplicate_engine = engine
        self.duplicate_running = True
        self.duplicate_cancel_event.clear()
        self.duplicate_scan_button.configure(state="disabled")
        self.duplicate_cancel_button.configure(state="normal")
        self.duplicate_progress.configure(value=0)
        self.duplicate_status_var.set(self.t("scanning_duplicates"))
        self.duplicate_items.clear()
        for item in self.duplicate_tree.get_children():
            self.duplicate_tree.delete(item)
        self.duplicate_worker = threading.Thread(target=self.run_duplicate_scan, daemon=True)
        self.duplicate_worker.start()

    def run_duplicate_scan(self) -> None:
        try:
            if self.duplicate_engine is None:
                raise TransferError(self.t("duplicate_scanner_not_ready"))
            result = self.duplicate_engine.scan()
            self.enqueue_event("duplicate_complete", {"result": result})
        except TransferCancelled:
            self.enqueue_event("duplicate_cancelled", {})
        except Exception as exc:
            self.enqueue_event("duplicate_error", {"message": str(exc)})

    def cancel_duplicate_scan(self) -> None:
        if self.duplicate_running:
            self.duplicate_cancel_event.set()
            self.duplicate_cancel_button.configure(state="disabled")
            self.duplicate_status_var.set(self.t("cancelling_scan"))

    def reset_duplicate_actions(self) -> None:
        self.duplicate_running = False
        self.duplicate_scan_button.configure(state="normal")
        self.duplicate_cancel_button.configure(state="disabled")

    def populate_duplicate_tree(self, result: DuplicateScanResult) -> None:
        self.duplicate_items.clear()
        for item in self.duplicate_tree.get_children():
            self.duplicate_tree.delete(item)
        for index, group in enumerate(result.groups, start=1):
            parent = self.duplicate_tree.insert(
                "", "end", text=f"{self.t('group')} {index}", open=True,
                values=("", format_size(group.size), f"{len(group.files)} identical files", ""),
            )
            self.duplicate_tree.insert(
                parent, "end", text="", values=(self.t("original"), format_size(group.size), str(group.original), self.t("protected"))
            )
            for duplicate in group.duplicates:
                item = self.duplicate_tree.insert(
                    parent, "end", text="", values=(self.t("duplicate"), format_size(group.size), str(duplicate), self.t("pending"))
                )
                self.duplicate_items[item] = duplicate

    def duplicate_action_items(self, apply_to_all: bool) -> list[str]:
        candidates = list(self.duplicate_items) if apply_to_all else list(self.duplicate_tree.selection())
        return [
            item for item in candidates
            if item in self.duplicate_items
            and self.duplicate_tree.set(item, "status") not in {self.t("deleted"), self.t("moved")}
        ]

    def apply_duplicate_action(self, action: str, apply_to_all: bool) -> None:
        if self.duplicate_running:
            messagebox.showinfo(self.t("scan_in_progress"), self.t("wait_scan"), parent=self.root)
            return
        if self.duplicate_engine is None:
            messagebox.showinfo(self.t("no_scan_results"), self.t("scan_first"), parent=self.root)
            return
        items = self.duplicate_action_items(apply_to_all)
        if not items:
            messagebox.showinfo(
                self.t("no_duplicates_selected"),
                self.t("select_duplicates_help"),
                parent=self.root,
            )
            return
        if action == "delete" and not messagebox.askyesno(
            self.t("delete_duplicates_title"),
            self.t("delete_duplicates_question", count=len(items)),
            icon="warning",
            parent=self.root,
        ):
            return

        completed = 0
        errors: list[str] = []
        for item in items:
            path = self.duplicate_items[item]
            try:
                result = self.duplicate_engine.apply_action([path], action)
                if action == "delete":
                    self.duplicate_tree.set(item, "status", self.t("deleted"))
                elif action == "move":
                    destination = result[path]
                    self.duplicate_tree.set(item, "status", self.t("moved"))
                    if destination is not None:
                        self.duplicate_tree.set(item, "path", str(destination))
                else:
                    self.duplicate_tree.set(item, "status", self.t("ignored"))
                completed += 1
            except (OSError, TransferError) as exc:
                self.duplicate_tree.set(item, "status", self.t("error"))
                errors.append(f"{path}: {exc}")

        action_label = {"delete": self.t("deleted"), "move": self.t("moved"), "ignore": self.t("ignored")}[action]
        self.duplicate_status_var.set(
            f"{completed} {self.t('duplicate')} · {action_label} · {len(errors)} {self.t('error')}"
        )
        if errors:
            messagebox.showwarning(self.t("action_errors"), "\n".join(errors[:8]), parent=self.root)

    def create_card(self, title: str, row: int) -> ttk.LabelFrame:
        card = ttk.LabelFrame(self.content, text=title, style="Card.TLabelframe", padding=16)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        card.columnconfigure(0, weight=1)
        return card

    def build_paths_card(self) -> None:
        card = self.create_card(self.t("card_folders"), 0)
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text=self.t("profile"), style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.profile_combo = ttk.Combobox(card, textvariable=self.profile_name_var, width=30)
        self.profile_combo.grid(row=0, column=1, sticky="ew", padx=10, pady=4)
        ttk.Button(card, text=self.t("load"), command=self.load_profile).grid(row=0, column=2, pady=4)
        profile_buttons = ttk.Frame(card, style="Card.TFrame")
        profile_buttons.grid(row=0, column=3, sticky="e", padx=(6, 0))
        ttk.Button(profile_buttons, text=self.t("save"), command=self.save_profile).pack(side="left", padx=2)
        ttk.Button(profile_buttons, text=self.t("delete"), style="Link.TButton", command=self.delete_profile).pack(side="left", padx=2)
        ttk.Separator(card).grid(row=1, column=0, columnspan=4, sticky="ew", pady=5)

        ttk.Label(card, text=self.t("source"), style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.source_entry = ttk.Entry(card, textvariable=self.source_var)
        self.source_entry.grid(row=2, column=1, sticky="ew", padx=10, pady=4)
        ttk.Button(card, text=self.t("browse"), command=lambda: self.select_folder("source")).grid(row=2, column=2, pady=4)

        ttk.Label(card, text=self.t("destination"), style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        self.destination_entry = ttk.Entry(card, textvariable=self.destination_var)
        self.destination_entry.grid(row=3, column=1, sticky="ew", padx=10, pady=4)
        ttk.Button(card, text=self.t("browse"), command=lambda: self.select_folder("destination")).grid(row=3, column=2, pady=4)
        self.refresh_profiles()

    def build_types_card(self) -> None:
        card = self.create_card(self.t("card_types"), 1)
        ttk.Label(
            card,
            text=self.t("extensions_help"),
            style="CardMuted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.extensions_entry = ttk.Entry(card, textvariable=self.extensions_var)
        self.extensions_entry.grid(row=1, column=0, sticky="ew", pady=(5, 8))

        chooser = ttk.Frame(card, style="Card.TFrame")
        chooser.grid(row=2, column=0, sticky="ew")
        chooser.columnconfigure(0, weight=1)
        chooser.columnconfigure(1, weight=1)

        search_frame = ttk.Frame(chooser, style="Card.TFrame")
        search_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        ttk.Label(search_frame, text=self.t("search_double_click"), style="Card.TLabel").pack(anchor="w")
        search_entry = ttk.Entry(search_frame, textvariable=self.extension_search_var)
        search_entry.pack(fill="x", pady=(4, 3))
        search_entry.bind("<KeyRelease>", self.update_extension_suggestions)
        self.suggestion_list = tk.Listbox(
            search_frame,
            height=4,
            exportselection=False,
            activestyle="none",
            borderwidth=1,
        )
        self.suggestion_list.pack(fill="x")
        self.suggestion_list.bind("<Double-Button-1>", self.add_selected_extension)
        self.suggestion_list.bind("<Return>", self.add_selected_extension)

        presets = ttk.Frame(chooser, style="Card.TFrame")
        presets.grid(row=0, column=1, sticky="nsew")
        ttk.Label(presets, text=self.t("quick_selections"), style="Card.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        category_keys = {
            "Images": "category_images", "Videos": "category_videos", "Audio": "category_audio",
            "Documents": "category_documents", "Archives": "category_archives",
            "Code / Data": "category_code_data",
        }
        for index, category in enumerate(EXTENSION_DB):
            ttk.Button(
                presets,
                text=f"+ {self.t(category_keys[category])}",
                style="Small.TButton",
                command=lambda selected=category: self.add_category(selected),
            ).grid(row=1 + index // 3, column=index % 3, sticky="ew", padx=2, pady=3)
        for column in range(3):
            presets.columnconfigure(column, weight=1)
        ttk.Button(
            presets,
            text=self.t("clear_selection"),
            style="Link.TButton",
            command=lambda: self.extensions_var.set(""),
        ).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0))

    def build_dates_card(self) -> None:
        card = self.create_card(self.t("card_dates"), 2)
        options = ttk.Frame(card, style="Card.TFrame")
        options.grid(row=0, column=0, sticky="ew")
        ttk.Radiobutton(
            options,
            text=self.t("most_recent_day"),
            variable=self.date_mode_var,
            value="latest",
            command=self.update_date_controls,
            style="Card.TRadiobutton",
        ).grid(row=0, column=0, sticky="w", padx=(0, 15))
        ttk.Radiobutton(
            options,
            text=self.t("all_dates"),
            variable=self.date_mode_var,
            value="all",
            command=self.update_date_controls,
            style="Card.TRadiobutton",
        ).grid(row=0, column=1, sticky="w", padx=(0, 15))
        ttk.Radiobutton(
            options,
            text=self.t("specific_date"),
            variable=self.date_mode_var,
            value="specific",
            command=self.update_date_controls,
            style="Card.TRadiobutton",
        ).grid(row=0, column=2, sticky="w", padx=(0, 15))
        ttk.Radiobutton(
            options,
            text=self.t("date_range"),
            variable=self.date_mode_var,
            value="range",
            command=self.update_date_controls,
            style="Card.TRadiobutton",
        ).grid(row=0, column=3, sticky="w")

        dates = ttk.Frame(card, style="Card.TFrame")
        dates.grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Label(dates, text=self.t("from_date"), style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.date_start_entry = ttk.Entry(dates, textvariable=self.date_start_var, width=14)
        self.date_start_entry.grid(row=0, column=1, padx=(8, 18))
        ttk.Label(dates, text=self.t("to"), style="Card.TLabel").grid(row=0, column=2, sticky="w")
        self.date_end_entry = ttk.Entry(dates, textvariable=self.date_end_var, width=14)
        self.date_end_entry.grid(row=0, column=3, padx=(8, 0))
        ttk.Label(dates, text=self.t("date_format"), style="CardMuted.TLabel").grid(row=0, column=4, padx=(12, 0))

    def build_options_card(self) -> None:
        card = self.create_card(self.t("card_options"), 3)
        ttk.Checkbutton(
            card,
            text=self.t("create_subfolder"),
            variable=self.create_folder_var,
            command=self.update_folder_controls,
            style="Card.TCheckbutton",
        ).grid(row=0, column=0, sticky="w")
        folder_options = ttk.Frame(card, style="Card.TFrame")
        folder_options.grid(row=1, column=0, sticky="ew", padx=(22, 0), pady=(6, 8))
        self.auto_folder_radio = ttk.Radiobutton(
            folder_options,
            text=self.t("automatic_name"),
            variable=self.folder_mode_var,
            value="auto",
            command=self.update_folder_controls,
            style="Card.TRadiobutton",
        )
        self.auto_folder_radio.grid(row=0, column=0, sticky="w")
        self.custom_folder_radio = ttk.Radiobutton(
            folder_options,
            text=self.t("custom_name"),
            variable=self.folder_mode_var,
            value="custom",
            command=self.update_folder_controls,
            style="Card.TRadiobutton",
        )
        self.custom_folder_radio.grid(row=0, column=1, sticky="w", padx=(20, 8))
        self.custom_folder_entry = ttk.Entry(folder_options, textvariable=self.custom_folder_var, width=28)
        self.custom_folder_entry.grid(row=0, column=2, sticky="w")

        ttk.Checkbutton(
            card,
            text=self.t("preserve_tree"),
            variable=self.preserve_tree_var,
            style="Card.TCheckbutton",
        ).grid(row=2, column=0, sticky="w", pady=2)
        ttk.Checkbutton(
            card,
            text=self.t("verify_sha"),
            variable=self.verify_checksum_var,
            style="Card.TCheckbutton",
        ).grid(row=3, column=0, sticky="w", pady=2)
        ttk.Checkbutton(
            card,
            text=self.t("detect_cards"),
            variable=self.monitor_removable_var,
            style="Card.TCheckbutton",
        ).grid(row=4, column=0, sticky="w", pady=2)

        policies = ttk.Frame(card, style="Card.TFrame")
        policies.grid(row=5, column=0, sticky="ew", pady=(7, 2))
        self.sync_option_displays()
        ttk.Label(policies, text=self.t("name_conflicts"), style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.conflict_combo = ttk.Combobox(
            policies,
            textvariable=self.conflict_display_var,
            values=tuple(self.conflict_option_labels().values()),
            state="readonly",
            width=25,
        )
        self.conflict_combo.grid(row=0, column=1, padx=(8, 22))
        self.conflict_combo.bind("<<ComboboxSelected>>", self.on_conflict_option_changed)
        ttk.Label(policies, text=self.t("automatic_organization"), style="Card.TLabel").grid(row=0, column=2, sticky="w")
        self.organization_combo = ttk.Combobox(
            policies,
            textvariable=self.organization_display_var,
            values=tuple(self.organization_option_labels().values()),
            state="readonly",
            width=27,
        )
        self.organization_combo.grid(row=0, column=3, padx=(8, 0))
        self.organization_combo.bind("<<ComboboxSelected>>", self.on_organization_option_changed)

        ttk.Separator(card).grid(row=6, column=0, sticky="ew", pady=6)
        ttk.Checkbutton(
            card,
            text=self.t("delete_verified_source"),
            variable=self.delete_source_var,
            style="Danger.Card.TCheckbutton",
        ).grid(row=7, column=0, sticky="w")

    def build_progress_card(self) -> None:
        card = self.create_card(self.t("card_progress"), 4)
        self.progress = ttk.Progressbar(card, maximum=100, style="App.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=0, sticky="ew")
        stats = ttk.Frame(card, style="Card.TFrame")
        stats.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        stats.columnconfigure((0, 1, 2), weight=1)
        self.speed_label = ttk.Label(stats, text=self.t("speed", value="—"), style="Card.TLabel")
        self.speed_label.grid(row=0, column=0, sticky="w")
        self.remaining_label = ttk.Label(stats, text=self.t("remaining", value="—"), style="Card.TLabel")
        self.remaining_label.grid(row=0, column=1)
        self.eta_label = ttk.Label(stats, text=self.t("estimated_time", value="—"), style="Card.TLabel")
        self.eta_label.grid(row=0, column=2, sticky="e")

    def build_log_card(self) -> None:
        card = self.create_card(self.t("card_log"), 5)
        self.log = ScrolledText(
            card,
            height=8,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
            borderwidth=1,
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        card.rowconfigure(0, weight=1)

    def apply_theme(self, requested_theme: str) -> None:
        theme = requested_theme if requested_theme in tc.TOUS_LES_THEMES else tc.THEME_PAR_DEFAUT
        self.theme_var.set(theme)
        palette = tc.get_palette(theme)
        self.palette = palette
        liquid = theme == "liquid_glass"
        ui_font = "Segoe UI Variable Text" if liquid else "Segoe UI"
        display_font = "Segoe UI Variable Display" if liquid else "Segoe UI"

        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self.root.configure(background=palette["bg"])
        self.root.option_add("*Font", (ui_font, 9))
        self.style.configure("App.TFrame", background=palette["bg"])
        self.style.configure("Header.TFrame", background=palette["surface_alt"])
        self.style.configure("Footer.TFrame", background=palette["surface_alt"])
        self.style.configure("Card.TFrame", background=palette["surface"])
        self.style.configure("TLabel", background=palette["bg"], foreground=palette["fg"])
        self.style.configure("Header.TLabel", background=palette["surface_alt"], foreground=palette["fg"])
        self.style.configure("Footer.TLabel", background=palette["surface_alt"], foreground=palette["muted"])
        self.style.configure("Title.TLabel", background=palette["surface_alt"], foreground=palette["fg"] if liquid else palette["primary"], font=(display_font, 22, "bold"))
        self.style.configure("Subtitle.TLabel", background=palette["surface_alt"], foreground=palette["muted"], font=(ui_font, 10))
        self.style.configure("Card.TLabel", background=palette["surface"], foreground=palette["fg"])
        self.style.configure("CardMuted.TLabel", background=palette["surface"], foreground=palette["muted"])
        self.style.configure(
            "Card.TLabelframe",
            background=palette["surface"],
            bordercolor=palette["border"],
            lightcolor=palette["border"],
            darkcolor=palette["border"],
            relief="solid",
            borderwidth=1,
        )
        self.style.configure(
            "Card.TLabelframe.Label",
            background=palette["surface"],
            foreground=palette["primary"],
            font=(ui_font, 10, "bold"),
        )
        self.style.configure(
            "TEntry",
            fieldbackground=palette["entry_bg"],
            foreground=palette["entry_fg"],
            insertcolor=palette["entry_fg"],
            bordercolor=palette["border"],
            lightcolor=palette["border"],
            darkcolor=palette["border"],
            padding=(10, 8) if liquid else 6,
        )
        self.style.map(
            "TEntry",
            bordercolor=[("focus", palette["primary"])],
            lightcolor=[("focus", palette["primary"])],
            darkcolor=[("focus", palette["primary"])],
        )
        self.style.configure(
            "TCombobox",
            fieldbackground=palette["entry_bg"],
            foreground=palette["entry_fg"],
            background=palette["surface"],
            arrowcolor=palette["fg"],
            bordercolor=palette["border"],
            padding=(9, 7) if liquid else 5,
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", palette["entry_bg"])],
            foreground=[("readonly", palette["entry_fg"])],
            selectbackground=[("readonly", palette["entry_bg"])],
            selectforeground=[("readonly", palette["entry_fg"])],
        )
        self.style.configure(
            "TButton",
            background=palette["surface_alt"],
            foreground=palette["fg"],
            bordercolor=palette["border"],
            lightcolor=palette["border"],
            darkcolor=palette["border"],
            relief="flat",
            padding=(13, 8) if liquid else (10, 6),
            font=(ui_font, 9, "bold") if liquid else (ui_font, 9),
        )
        self.style.map(
            "TButton",
            background=[("active", palette["entry_bg"] if liquid else palette["primary"]), ("pressed", palette["primary"]), ("disabled", palette["surface"])],
            bordercolor=[("active", palette["primary"]), ("focus", palette["primary"])],
            foreground=[("disabled", palette["muted"])],
        )
        self.style.configure("Small.TButton", padding=(8, 5), font=(ui_font, 8, "bold"))
        self.style.configure("Link.TButton", background=palette["surface"], foreground=palette["danger"])
        self.style.map("Link.TButton", background=[("active", palette["surface_alt"])])
        self.style.configure("Success.TButton", background=palette["primary"] if liquid else palette["success"], foreground="#07101f" if liquid else "#ffffff", font=(ui_font, 10, "bold"), padding=(18, 10))
        self.style.map("Success.TButton", background=[("active", palette["primary"]), ("disabled", palette["surface"])])
        self.style.configure("Danger.TButton", background=palette["danger"], foreground="#ffffff", font=(ui_font, 9, "bold"), padding=(14, 9))
        self.style.map("Danger.TButton", background=[("active", palette["warning"]), ("disabled", palette["surface"])])
        for widget_style in ("Card.TCheckbutton", "Card.TRadiobutton"):
            self.style.configure(widget_style, background=palette["surface"], foreground=palette["fg"], indicatorcolor=palette["entry_bg"])
            self.style.map(widget_style, background=[("active", palette["surface"])], indicatorcolor=[("selected", palette["primary"])], foreground=[("disabled", palette["muted"])])
        self.style.configure("Danger.Card.TCheckbutton", background=palette["surface"], foreground=palette["danger"], indicatorcolor=palette["entry_bg"])
        self.style.map("Danger.Card.TCheckbutton", background=[("active", palette["surface"])], indicatorcolor=[("selected", palette["danger"])])
        self.style.configure(
            "App.Horizontal.TProgressbar",
            troughcolor=palette["surface_alt"],
            background=palette["primary"],
            lightcolor=palette["primary"],
            darkcolor=palette["primary"],
            bordercolor=palette["border"],
            thickness=10 if liquid else 14,
        )
        self.style.configure("TSeparator", background=palette["border"])
        self.style.configure("TNotebook", background=palette["bg"], borderwidth=0)
        self.style.configure(
            "TNotebook.Tab",
            background=palette["surface_alt"],
            foreground=palette["fg"],
            padding=(22, 11) if liquid else (16, 8),
            font=(ui_font, 10, "bold") if liquid else (ui_font, 9),
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", palette["surface"]), ("active", palette["entry_bg"] if liquid else palette["primary"])],
            foreground=[("selected", palette["primary"]), ("active", palette["fg"])],
        )
        self.style.configure(
            "Treeview",
            background=palette["entry_bg"],
            foreground=palette["entry_fg"],
            fieldbackground=palette["entry_bg"],
            bordercolor=palette["border"],
            rowheight=30 if liquid else 25,
            font=(ui_font, 9),
        )
        self.style.configure(
            "Treeview.Heading",
            background=palette["surface_alt"],
            foreground=palette["fg"],
            font=(ui_font, 9, "bold"),
            padding=(8, 7) if liquid else 4,
        )
        self.style.map(
            "Treeview",
            background=[("selected", palette["primary"])],
            foreground=[("selected", "#ffffff")],
        )
        self.canvas.configure(background=palette["bg"])
        self.suggestion_list.configure(
            background=palette["entry_bg"],
            foreground=palette["entry_fg"],
            selectbackground=palette["primary"],
            selectforeground="#ffffff",
            highlightbackground=palette["border"],
            highlightcolor=palette["primary"],
        )
        self.log.configure(
            background=palette["log_bg"],
            foreground=palette["log_fg"],
            insertbackground=palette["log_fg"],
            selectbackground=palette["primary"],
            selectforeground="#ffffff",
        )
        self.configure_log_tags()

    def configure_log_tags(self) -> None:
        self.log.tag_configure("info", foreground=self.palette["log_fg"])
        self.log.tag_configure("success", foreground=self.palette["success"])
        self.log.tag_configure("warning", foreground=self.palette["warning"])
        self.log.tag_configure("error", foreground=self.palette["danger"])
        self.log.tag_configure("muted", foreground=self.palette["muted"])

    def on_theme_changed(self, _event: tk.Event | None = None) -> None:
        self.apply_theme(self.theme_var.get())

    def on_content_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def select_folder(self, kind: str) -> None:
        selected = filedialog.askdirectory(
            title=self.t("choose_source_title" if kind == "source" else "choose_destination_title")
        )
        if selected:
            (self.source_var if kind == "source" else self.destination_var).set(selected)
            self.update_eject_state()

    def profile_settings(self) -> dict[str, object]:
        return {
            "source": self.source_var.get(),
            "destination": self.destination_var.get(),
            "extensions": self.extensions_var.get(),
            "date_mode": self.date_mode_var.get(),
            "date_start": self.date_start_var.get(),
            "date_end": self.date_end_var.get(),
            "create_folder": self.create_folder_var.get(),
            "folder_mode": self.folder_mode_var.get(),
            "custom_folder": self.custom_folder_var.get(),
            "delete_source": self.delete_source_var.get(),
            "preserve_tree": self.preserve_tree_var.get(),
            "verify_checksum": self.verify_checksum_var.get(),
            "conflict_policy": self.conflict_policy_var.get(),
            "organization_mode": self.organization_mode_var.get(),
            "monitor_removable": self.monitor_removable_var.get(),
        }

    def refresh_profiles(self) -> None:
        if hasattr(self, "profile_combo"):
            self.profile_combo.configure(values=tuple(sorted(self.profile_store.all(), key=str.casefold)))

    def save_profile(self) -> None:
        name = self.profile_name_var.get().strip()
        if not name:
            messagebox.showerror(
                self.t("profile_error"), self.t("profile_name_empty"), parent=self.root
            )
            return
        try:
            self.profile_store.save(name, self.profile_settings())
        except (OSError, TransferError) as exc:
            messagebox.showerror(self.t("profile_error"), str(exc), parent=self.root)
            return
        self.profile_name_var.set(name)
        self.refresh_profiles()
        self.status_var.set(self.t("profile_saved", name=name))

    def load_profile(self) -> None:
        name = self.profile_name_var.get().strip()
        settings = self.profile_store.all().get(name)
        if settings is None:
            messagebox.showerror(self.t("profile_not_found"), self.t("choose_saved_profile"), parent=self.root)
            return
        variables: dict[str, tk.Variable] = {
            "source": self.source_var, "destination": self.destination_var,
            "extensions": self.extensions_var, "date_mode": self.date_mode_var,
            "date_start": self.date_start_var, "date_end": self.date_end_var,
            "create_folder": self.create_folder_var, "folder_mode": self.folder_mode_var,
            "custom_folder": self.custom_folder_var, "delete_source": self.delete_source_var,
            "preserve_tree": self.preserve_tree_var, "verify_checksum": self.verify_checksum_var,
            "conflict_policy": self.conflict_policy_var,
            "organization_mode": self.organization_mode_var,
            "monitor_removable": self.monitor_removable_var,
        }
        for key, variable in variables.items():
            if key in settings:
                variable.set(settings[key])
        self.update_date_controls()
        self.update_folder_controls()
        self.sync_option_displays()
        self.update_eject_state()
        self.status_var.set(self.t("profile_loaded", name=name))

    def delete_profile(self) -> None:
        name = self.profile_name_var.get().strip()
        if not name or name not in self.profile_store.all():
            messagebox.showinfo(self.t("profile"), self.t("choose_profile_delete"), parent=self.root)
            return
        if not messagebox.askyesno(
            self.t("delete_profile"), self.t("delete_profile_question", name=name), parent=self.root
        ):
            return
        try:
            self.profile_store.delete(name)
        except OSError as exc:
            messagebox.showerror(self.t("profile_error"), str(exc), parent=self.root)
            return
        self.profile_name_var.set("")
        self.refresh_profiles()

    def refresh_history_tree(self) -> None:
        if not hasattr(self, "history_tree"):
            return
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        for entry in self.history_store.entries():
            result = self.t(
                "history_counts", copied=entry.get("copied", 0),
                skipped=entry.get("skipped", 0), errors=entry.get("errors", 0),
            )
            status_key = str(entry.get("status", "")).casefold()
            self.history_tree.insert(
                "", "end", values=(format_history_timestamp(entry.get("timestamp", "")), self.t(status_key),
                entry.get("source", ""), entry.get("destination", ""), result),
            )

    def export_history(self) -> None:
        selected = filedialog.asksaveasfilename(
            title=self.t("export_history_title"), defaultextension=".txt",
            filetypes=((self.t("text_report"), "*.txt"), (self.t("all_files"), "*.*")),
            initialfile=f"file-manager-history-{format_path_date(date.today())}.txt",
        )
        if not selected:
            return
        try:
            self.history_store.export_text(Path(selected), self.language_code)
        except OSError as exc:
            messagebox.showerror(self.t("export_error"), str(exc), parent=self.root)
            return
        messagebox.showinfo(
            self.t("history_exported"), self.t("report_saved", path=selected), parent=self.root
        )

    def record_history(self, status: str, result: TransferResult | None = None) -> None:
        options = self.current_options
        if options is None:
            return
        entry: dict[str, object] = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "source": str(options.source),
            "destination": str(result.target_dir if result and result.target_dir else options.destination),
            "copied": result.copied if result else 0,
            "skipped": result.skipped if result else 0,
            "replaced": result.replaced if result else 0,
            "verified": result.verified if result else 0,
            "deleted": result.deleted if result else 0,
            "errors": len(result.errors) if result else 1,
        }
        try:
            self.history_store.append(entry)
            self.refresh_history_tree()
        except OSError as exc:
            self.append_log(f"Could not save history: {exc}", "warning")

    def poll_removable_drives(self) -> None:
        if not self.root.winfo_exists():
            return
        current_paths = removable_drives()
        current = {removable_drive_key(drive) for drive in current_paths}
        new_drives = [
            drive for drive in current_paths
            if removable_drive_key(drive) not in self.known_removable_drives
        ]
        self.known_removable_drives = current
        if new_drives and self.monitor_removable_var.get() and not self.running:
            drive = new_drives[0]
            if messagebox.askyesno(
                self.t("card_detected"),
                self.t("card_detected_question", drive=drive),
                parent=self.root,
            ):
                self.source_var.set(str(drive))
                self.duplicate_folder_var.set(str(drive))
                self.status_var.set(self.t("removable_detected", drive=drive))
        self.update_eject_state()
        self.root.after(2000, self.poll_removable_drives)

    def update_eject_state(self) -> None:
        source_text = self.source_var.get().strip()
        removable = removable_root(Path(source_text)) if source_text else None
        state = "normal" if removable is not None and not self.running else "disabled"
        if hasattr(self, "eject_button"):
            self.eject_button.configure(state=state)

    def eject_source(self) -> None:
        if self.running:
            return
        source_text = self.source_var.get().strip()
        if not source_text:
            messagebox.showerror(self.t("safe_eject_title"), self.t("choose_removable"), parent=self.root)
            return
        self.eject_button.configure(state="disabled")
        self.status_var.set(self.t("requesting_eject"))
        threading.Thread(target=self.run_eject, args=(Path(source_text),), daemon=True).start()

    def run_eject(self, source: Path) -> None:
        try:
            eject_removable_drive(source, self.language_code)
            self.enqueue_event("eject_complete", {"source": str(source)})
        except Exception as exc:
            self.enqueue_event("eject_error", {"message": str(exc)})

    def update_extension_suggestions(self, _event: tk.Event | None = None) -> None:
        search = self.extension_search_var.get().strip().lower()
        self.suggestion_list.delete(0, tk.END)
        for extension in ALL_EXTENSIONS:
            if search in extension:
                self.suggestion_list.insert(tk.END, extension)

    def add_selected_extension(self, _event: tk.Event | None = None) -> None:
        selection = self.suggestion_list.curselection()
        if selection:
            self.add_extensions([self.suggestion_list.get(selection[0])])
            self.extension_search_var.set("")
            self.update_extension_suggestions()

    def add_category(self, category: str) -> None:
        self.add_extensions(EXTENSION_DB[category])

    def add_extensions(self, extensions: list[str]) -> None:
        current = list(normalize_extensions(self.extensions_var.get()))
        for extension in extensions:
            if extension not in current:
                current.append(extension)
        self.extensions_var.set(", ".join(current))

    def update_date_controls(self) -> None:
        mode = self.date_mode_var.get()
        self.date_start_entry.configure(state="normal" if mode in {"specific", "range"} else "disabled")
        self.date_end_entry.configure(state="normal" if mode == "range" else "disabled")

    def update_folder_controls(self) -> None:
        enabled = self.create_folder_var.get()
        radio_state = "normal" if enabled else "disabled"
        self.auto_folder_radio.configure(state=radio_state)
        self.custom_folder_radio.configure(state=radio_state)
        custom_enabled = enabled and self.folder_mode_var.get() == "custom"
        self.custom_folder_entry.configure(state="normal" if custom_enabled else "disabled")

    def conflict_option_labels(self) -> dict[str, str]:
        return {
            "rename": self.t("policy_rename"), "skip": self.t("policy_skip"),
            "replace": self.t("policy_replace"), "newer": self.t("policy_newer"),
            "ask": self.t("policy_ask"),
        }

    def organization_option_labels(self) -> dict[str, str]:
        return {
            "none": self.t("organization_none"), "date": self.t("organization_date"),
            "year_month": self.t("organization_year_month"),
            "type": self.t("organization_type"),
        }

    def sync_option_displays(self) -> None:
        self.conflict_display_var.set(
            self.conflict_option_labels().get(self.conflict_policy_var.get(), self.t("policy_rename"))
        )
        self.organization_display_var.set(
            self.organization_option_labels().get(
                self.organization_mode_var.get(), self.t("organization_none")
            )
        )

    def on_conflict_option_changed(self, _event: tk.Event | None = None) -> None:
        reverse = {label: code for code, label in self.conflict_option_labels().items()}
        self.conflict_policy_var.set(reverse.get(self.conflict_display_var.get(), "rename"))

    def on_organization_option_changed(self, _event: tk.Event | None = None) -> None:
        reverse = {label: code for code, label in self.organization_option_labels().items()}
        self.organization_mode_var.set(reverse.get(self.organization_display_var.get(), "none"))

    def collect_options(self) -> TransferOptions:
        source_text = self.source_var.get().strip()
        destination_text = self.destination_var.get().strip()
        if not source_text:
            raise TransferError(self.t("choose_source"))
        if not destination_text:
            raise TransferError(self.t("choose_destination"))

        mode = self.date_mode_var.get()
        start: date | None = None
        end: date | None = None
        if mode == "specific":
            start = parse_iso_date(self.date_start_var.get(), self.t("history_date"), self.language_code)
        elif mode == "range":
            start = parse_iso_date(self.date_start_var.get(), self.t("from_date"), self.language_code)
            end = parse_iso_date(self.date_end_var.get(), self.t("to"), self.language_code)
            if start > end:
                raise TransferError(self.t("date_order_invalid"))

        options = TransferOptions(
            source=Path(source_text).expanduser(),
            destination=Path(destination_text).expanduser(),
            extensions=normalize_extensions(self.extensions_var.get()),
            date_mode=mode,
            date_start=start,
            date_end=end,
            create_folder=self.create_folder_var.get(),
            folder_name_mode=self.folder_mode_var.get(),
            custom_folder_name=self.custom_folder_var.get(),
            delete_source=self.delete_source_var.get(),
            preserve_tree=self.preserve_tree_var.get(),
            verify_checksum=self.verify_checksum_var.get(),
            conflict_policy=self.conflict_policy_var.get(),
            organization_mode=self.organization_mode_var.get(),
            language=self.language_code,
        )
        TransferEngine(options).validate()
        return options

    def show_transfer_preview(self, plan: TransferPlan) -> set[Path] | None:
        window = tk.Toplevel(self.root)
        window.title(self.t("preview_title"))
        window.geometry("1150x650")
        window.minsize(760, 430)
        window.transient(self.root)
        window.grab_set()
        window.rowconfigure(1, weight=1)
        window.columnconfigure(0, weight=1)

        summary = self.t(
            "preview_summary", count=len(plan.items), required=format_size(plan.required_bytes),
            free=format_size(plan.free_bytes),
        )
        if not plan.enough_space:
            summary += self.t("preview_space_hint")
        ttk.Label(window, text=summary, padding=(12, 10)).grid(row=0, column=0, sticky="ew")
        frame = ttk.Frame(window, padding=(12, 0))
        frame.grid(row=1, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        columns = ("action", "size", "source", "destination", "reason")
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for column, title, width in (
            ("action", self.t("action"), 120), ("size", self.t("size"), 90),
            ("source", self.t("source"), 260), ("destination", self.t("destination"), 300),
            ("reason", self.t("details"), 250),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, stretch=column in {"source", "destination", "reason"})
        scroll_y = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scroll_x = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        item_paths: dict[str, TransferPlanItem] = {}
        action_labels = {
            "copy": self.t("new_file"), "rename": self.t("policy_rename"),
            "replace": self.t("policy_replace"), "skip_identical": self.t("already_verified"),
            "skip_conflict": self.t("policy_skip"), "ask_conflict": self.t("policy_ask"),
        }
        for item in plan.items:
            row = tree.insert(
                "", "end", values=(action_labels.get(item.action, item.action), format_size(item.size),
                str(item.source), str(item.destination), item.reason),
            )
            item_paths[row] = item
        tree.selection_set(*tree.get_children())

        result: list[set[Path] | None] = [None]

        def confirm() -> None:
            selected_rows = tree.selection()
            if not selected_rows:
                messagebox.showwarning(self.t("preview"), self.t("select_one_file"), parent=window)
                return
            selected_items = [item_paths[row] for row in selected_rows]
            result[0] = {item.source for item in selected_items}
            window.destroy()

        footer = ttk.Frame(window, padding=12)
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer, text=self.t("preview_selection_help")
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text=self.t("select_all"), command=lambda: tree.selection_set(*tree.get_children())).grid(row=0, column=1, padx=4)
        ttk.Button(
            footer, text=self.t("select_none"),
            command=lambda: tree.selection_remove(*tree.selection()),
        ).grid(row=0, column=2, padx=4)
        ttk.Button(footer, text=self.t("cancel"), command=window.destroy).grid(row=0, column=3, padx=4)
        ttk.Button(footer, text=self.t("start_selected"), style="Success.TButton", command=confirm).grid(row=0, column=4, padx=(8, 0))
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        self.root.wait_window(window)
        return result[0]

    def ask_conflict_resolution(self, item: TransferPlanItem) -> str | None:
        """Ask for one explicit decision and never silently choose for the user."""
        dialog = tk.Toplevel(self.root)
        dialog.title(self.t("conflict_title"))
        dialog.geometry("760x300")
        dialog.minsize(620, 260)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        ttk.Label(
            dialog,
            text=self.t("conflict_exists"),
            font=("Segoe UI", 12, "bold"),
            padding=(16, 14, 16, 8),
        ).grid(row=0, column=0, sticky="w")
        details = ttk.Frame(dialog, padding=(16, 4))
        details.grid(row=1, column=0, sticky="nsew")
        details.columnconfigure(1, weight=1)
        ttk.Label(details, text=f"{self.t('source')}:").grid(row=0, column=0, sticky="nw", padx=(0, 8), pady=4)
        ttk.Label(details, text=str(item.source), wraplength=610).grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(details, text=f"{self.t('destination')}:").grid(row=1, column=0, sticky="nw", padx=(0, 8), pady=4)
        ttk.Label(details, text=str(item.destination), wraplength=610).grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(details, text=f"{self.t('size')}: {format_size(item.size)}").grid(
            row=2, column=1, sticky="w", pady=4
        )

        answer: list[str | None] = [None]

        def choose(action: str) -> None:
            answer[0] = action
            dialog.destroy()

        buttons = ttk.Frame(dialog, padding=16)
        buttons.grid(row=2, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        ttk.Button(buttons, text=self.t("cancel_transfer"), command=dialog.destroy).grid(row=0, column=1, padx=4)
        ttk.Button(buttons, text=self.t("skip"), command=lambda: choose("skip")).grid(row=0, column=2, padx=4)
        ttk.Button(buttons, text=self.t("rename_copy"), command=lambda: choose("rename")).grid(row=0, column=3, padx=4)
        replace_state = "normal" if item.destination.exists() else "disabled"
        ttk.Button(
            buttons, text=self.t("replace"), style="Danger.TButton",
            command=lambda: choose("replace"), state=replace_state,
        ).grid(row=0, column=4, padx=(4, 0))
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        self.root.wait_window(dialog)
        return answer[0]

    def resolve_ask_conflicts(
        self, plan: TransferPlan, selected_paths: set[Path]
    ) -> dict[Path, str] | None:
        overrides: dict[Path, str] = {}
        conflicts = [
            item for item in plan.items
            if item.source in selected_paths and item.action == "ask_conflict"
        ]
        for index, item in enumerate(conflicts, start=1):
            self.status_var.set(self.t("resolving_conflict", index=index, total=len(conflicts)))
            decision = self.ask_conflict_resolution(item)
            if decision is None:
                return None
            overrides[item.source] = decision
        return overrides

    def start_transfer(self) -> None:
        if self.running:
            return
        try:
            options = self.collect_options()
        except TransferError as exc:
            messagebox.showerror(self.t("invalid_configuration"), str(exc), parent=self.root)
            return

        self.status_var.set(self.t("building_preview"))
        self.root.update_idletasks()
        try:
            plan = TransferEngine(options).preview()
        except (TransferError, OSError) as exc:
            self.status_var.set(self.t("preview_failed"))
            messagebox.showerror(self.t("preview_error"), str(exc), parent=self.root)
            return
        if not plan.items:
            self.status_var.set(self.t("no_matching_files"))
            messagebox.showinfo(self.t("preview"), self.t("no_files_match"), parent=self.root)
            return
        selected_paths = self.show_transfer_preview(plan)
        if selected_paths is None:
            self.status_var.set(self.t("ready"))
            return
        conflict_overrides = self.resolve_ask_conflicts(plan, selected_paths)
        if conflict_overrides is None:
            self.status_var.set(self.t("ready"))
            return
        try:
            final_plan = TransferEngine(
                options,
                selected_paths=selected_paths,
                conflict_overrides=conflict_overrides,
            ).preview()
        except (TransferError, OSError) as exc:
            self.status_var.set(self.t("preview_failed"))
            messagebox.showerror(self.t("preview_error"), str(exc), parent=self.root)
            return
        if not final_plan.enough_space:
            messagebox.showerror(
                self.t("not_enough_space"),
                self.t("not_enough_space_detail", required=format_size(final_plan.required_bytes),
                       free=format_size(final_plan.free_bytes)),
                parent=self.root,
            )
            self.status_var.set(self.t("not_enough_space"))
            return

        if options.delete_source and not messagebox.askyesno(
            self.t("confirm_deletion"),
            self.t("confirm_source_deletion"),
            icon="warning",
            parent=self.root,
        ):
            return

        self.current_options = options
        self.running = True
        self.cancel_event.clear()
        self.start_button.configure(state="disabled", text=self.t("transfer_in_progress"))
        self.cancel_button.configure(state="normal")
        self.eject_button.configure(state="disabled")
        self.progress.configure(value=0)
        self.speed_label.configure(text=self.t("speed", value="—"))
        self.remaining_label.configure(text=self.t("remaining", value="—"))
        self.eta_label.configure(text=self.t("estimated_time", value="—"))
        self.status_var.set(self.t("scanning"))
        self.clear_log()
        self.append_log(self.t("app_started", version=APP_VERSION), "success")

        self.worker = threading.Thread(
            target=self.run_transfer,
            args=(options, selected_paths, conflict_overrides),
            daemon=True,
        )
        self.worker.start()

    def run_transfer(
        self,
        options: TransferOptions,
        selected_paths: set[Path],
        conflict_overrides: dict[Path, str],
    ) -> None:
        try:
            engine = TransferEngine(
                options,
                self.cancel_event,
                self.enqueue_event,
                selected_paths=selected_paths,
                conflict_overrides=conflict_overrides,
            )
            result = engine.run()
            self.enqueue_event("finished", {"result": result})
        except TransferError as exc:
            self.enqueue_event("fatal", {"message": str(exc)})
        except Exception as exc:
            self.enqueue_event("fatal", {"message": self.t("unexpected_error", error=exc)})

    def enqueue_event(self, event: str, data: dict) -> None:
        self.events.put((event, data))

    def process_events(self) -> None:
        try:
            while True:
                event, data = self.events.get_nowait()
                self.handle_event(event, data)
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(80, self.process_events)

    def network_text(self, key: str, **values: object) -> str:
        return nt.network_text(self.language_code, key, **values)

    def network_ui_exists(self) -> bool:
        try:
            return self.network_window is not None and bool(self.network_window.winfo_exists())
        except tk.TclError:
            return False

    def open_network_window(self) -> None:
        if self.network_ui_exists():
            self.network_window.lift()  # type: ignore[union-attr]
            self.network_window.focus_force()  # type: ignore[union-attr]
            return

        window = tk.Toplevel(self.root)
        self.network_window = window
        window.title(self.network_text("title"))
        window.geometry("760x780")
        window.minsize(640, 650)
        window.transient(self.root)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        ttk.Label(
            window,
            text=self.network_text("private_lan"),
            padding=(16, 14, 16, 8),
            wraplength=660,
        ).grid(row=0, column=0, sticky="ew")

        tabs = ttk.Notebook(window)
        tabs.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 10))
        receive_tab = ttk.Frame(tabs, padding=16)
        send_tab = ttk.Frame(tabs, padding=16)
        internet_tab = ttk.Frame(tabs, padding=16)
        tabs.add(receive_tab, text=f"  {self.network_text('receive')}  ")
        tabs.add(send_tab, text=f"  {self.network_text('send')}  ")
        tabs.add(internet_tab, text=f"  {self.network_text('internet')}  ")
        receive_tab.columnconfigure(1, weight=1)
        send_tab.columnconfigure(1, weight=1)
        internet_tab.columnconfigure(1, weight=1)

        ttk.Label(receive_tab, text=self.network_text("destination")).grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(receive_tab, textvariable=self.network_destination_var).grid(
            row=0, column=1, sticky="ew", pady=6
        )
        ttk.Button(
            receive_tab,
            text=self.network_text("choose"),
            command=self.choose_network_destination,
        ).grid(row=0, column=2, padx=(8, 0), pady=6)
        ttk.Label(receive_tab, text=self.network_text("port")).grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(receive_tab, textvariable=self.network_port_var, width=12).grid(
            row=1, column=1, sticky="w", pady=6
        )
        ttk.Label(receive_tab, text=self.network_text("code")).grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(
            receive_tab,
            textvariable=self.network_code_var,
            width=12,
            font=("Segoe UI", 16, "bold"),
            justify="center",
        ).grid(row=2, column=1, sticky="w", pady=6)
        ttk.Button(
            receive_tab,
            text="↻",
            width=3,
            command=lambda: self.network_code_var.set(nt.generate_pairing_code()),
        ).grid(row=2, column=2, padx=(8, 0), pady=6)
        ttk.Label(
            receive_tab,
            text=f"IP locale : {nt.local_ip_address()}",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 8))
        self.network_receive_button = ttk.Button(
            receive_tab,
            text=self.network_text("start_receive"),
            style="Success.TButton",
            command=self.start_network_receive,
        )
        self.network_receive_button.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        ttk.Label(
            send_tab,
            text=self.network_text("nearby_pcs"),
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.network_scan_button = ttk.Button(
            send_tab, text=self.network_text("refresh"), command=self.scan_network_devices
        )
        self.network_scan_button.grid(row=0, column=2, sticky="e", pady=(0, 6))
        device_columns = ("name", "address", "status")
        self.network_device_tree = ttk.Treeview(
            send_tab, columns=device_columns, show="headings", height=5, selectmode="browse"
        )
        self.network_device_tree.heading("name", text="PC")
        self.network_device_tree.heading("address", text="IP")
        self.network_device_tree.heading("status", text="État")
        self.network_device_tree.column("name", width=190, stretch=True)
        self.network_device_tree.column("address", width=130, stretch=False)
        self.network_device_tree.column("status", width=140, stretch=False)
        self.network_device_tree.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        self.network_device_tree.bind("<<TreeviewSelect>>", self.on_network_device_selected)

        ttk.Separator(send_tab).grid(row=2, column=0, columnspan=3, sticky="ew", pady=6)
        ttk.Label(send_tab, text=self.network_text("manual_connection")).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(0, 2)
        )
        ttk.Label(send_tab, text=self.network_text("address")).grid(
            row=4, column=0, sticky="w", padx=(0, 10), pady=4
        )
        ttk.Entry(send_tab, textvariable=self.network_host_var).grid(
            row=4, column=1, columnspan=2, sticky="ew", pady=4
        )
        ttk.Label(send_tab, text=self.network_text("port")).grid(
            row=5, column=0, sticky="w", padx=(0, 10), pady=4
        )
        ttk.Entry(send_tab, textvariable=self.network_port_var, width=12).grid(
            row=5, column=1, sticky="w", pady=4
        )
        ttk.Label(send_tab, text=self.network_text("code")).grid(
            row=6, column=0, sticky="w", padx=(0, 10), pady=4
        )
        ttk.Entry(send_tab, textvariable=self.network_code_var, width=12, justify="center").grid(
            row=6, column=1, sticky="w", pady=4
        )
        ttk.Label(send_tab, text=self.network_text("files")).grid(
            row=7, column=0, sticky="w", padx=(0, 10), pady=6
        )
        self.network_files_label = ttk.Label(
            send_tab, text=self.network_text("no_files"), wraplength=390
        )
        self.network_files_label.grid(row=7, column=1, sticky="w", pady=6)
        ttk.Button(
            send_tab,
            text=self.network_text("select_files"),
            command=self.choose_network_files,
        ).grid(row=7, column=2, padx=(8, 0), pady=6)
        self.network_send_button = ttk.Button(
            send_tab,
            text=self.network_text("start_send"),
            style="Success.TButton",
            command=self.start_network_send,
        )
        self.network_send_button.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        ttk.Label(
            internet_tab,
            text=self.network_text("internet_intro"),
            wraplength=620,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(internet_tab, text=self.network_text("destination")).grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        ttk.Entry(internet_tab, textvariable=self.network_destination_var).grid(
            row=1, column=1, sticky="ew", pady=4
        )
        ttk.Button(
            internet_tab, text=self.network_text("choose"),
            command=self.choose_network_destination,
        ).grid(row=1, column=2, padx=(8, 0), pady=4)
        ttk.Label(internet_tab, text=self.network_text("rendezvous_service")).grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=4
        )
        self.internet_rendezvous_entry = ttk.Entry(
            internet_tab, textvariable=self.internet_rendezvous_var
        )
        self.internet_rendezvous_entry.grid(
            row=2, column=1, columnspan=2, sticky="ew", pady=4
        )
        ttk.Label(
            internet_tab,
            text=self.network_text("rendezvous_help"),
            wraplength=600,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.internet_create_button = ttk.Button(
            internet_tab,
            text=self.network_text("create_universal_code"),
            style="Success.TButton",
            command=self.create_universal_receiver,
        )
        self.internet_create_button.grid(row=4, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(internet_tab, text=self.network_text("universal_code_share")).grid(
            row=5, column=0, sticky="nw", padx=(0, 10), pady=4
        )
        self.internet_receive_code = ScrolledText(internet_tab, height=3, wrap="word")
        self.internet_receive_code.grid(row=5, column=1, sticky="ew", pady=4)
        ttk.Button(
            internet_tab, text=self.network_text("copy"),
            command=self.copy_internet_invitation,
        ).grid(row=5, column=2, padx=(8, 0), pady=4, sticky="n")
        ttk.Separator(internet_tab).grid(
            row=6, column=0, columnspan=3, sticky="ew", pady=8
        )
        ttk.Label(internet_tab, text=self.network_text("paste_universal_code")).grid(
            row=7, column=0, sticky="nw", padx=(0, 10), pady=4
        )
        self.internet_send_code = ScrolledText(internet_tab, height=3, wrap="word")
        self.internet_send_code.grid(row=7, column=1, columnspan=2, sticky="ew", pady=4)
        self.internet_files_label = ttk.Label(
            internet_tab, text=self.network_text("no_files"), wraplength=390
        )
        self.internet_files_label.grid(row=8, column=1, sticky="w", pady=4)
        ttk.Button(
            internet_tab, text=self.network_text("select_files"),
            command=self.choose_network_files,
        ).grid(row=8, column=2, padx=(8, 0), pady=4)
        self.internet_send_button = ttk.Button(
            internet_tab,
            text=self.network_text("send_internet"),
            style="Success.TButton",
            command=self.start_universal_send,
        )
        self.internet_send_button.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        footer = ttk.Frame(window, padding=(14, 0, 14, 14))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.network_status_var, wraplength=640).grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self.network_progress = ttk.Progressbar(footer, maximum=100)
        self.network_progress.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.network_cancel_button = ttk.Button(
            footer,
            text=self.network_text("cancel"),
            style="Danger.TButton",
            state="disabled",
            command=self.cancel_network_transfer,
        )
        self.network_cancel_button.grid(row=1, column=1, padx=(10, 0), pady=(0, 8))
        self.network_log = ScrolledText(footer, height=9, state="disabled", wrap="word")
        self.network_log.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.network_log.configure(
            background=self.palette["log_bg"], foreground=self.palette["log_fg"],
            insertbackground=self.palette["log_fg"], relief="flat",
        )
        window.protocol("WM_DELETE_WINDOW", self.close_network_window)
        self.update_network_controls()
        window.after(150, self.scan_network_devices)

    def close_network_window(self) -> None:
        if self.network_running:
            self.cancel_network_transfer()
        if self.network_ui_exists():
            self.network_window.destroy()  # type: ignore[union-attr]
        self.network_window = None

    def choose_network_destination(self) -> None:
        chosen = filedialog.askdirectory(
            title=self.network_text("destination"), parent=self.network_window or self.root
        )
        if chosen:
            self.network_destination_var.set(chosen)

    def choose_network_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title=self.network_text("select_files"), parent=self.network_window or self.root
        )
        if not selected:
            return
        self.network_send_files = [Path(path) for path in selected]
        total = sum(path.stat().st_size for path in self.network_send_files if path.is_file())
        if self.network_ui_exists():
            label = f"{len(self.network_send_files)} fichier(s) · {format_size(total)}"
            self.network_files_label.configure(text=label)
            self.internet_files_label.configure(text=label)

    def scan_network_devices(self) -> None:
        if self.network_scanning or self.network_running:
            return
        self.network_scanning = True
        self.network_status_var.set(self.network_text("scanning"))
        if self.network_ui_exists():
            self.network_scan_button.configure(state="disabled")
        threading.Thread(target=self.run_network_scan, daemon=True).start()

    def run_network_scan(self) -> None:
        try:
            devices = nt.discover_devices(
                sender_id=self.discovery_service.instance_id,
            )
            self.enqueue_event("network_devices_found", {"devices": devices})
        except nt.NetworkTransferCancelled:
            self.enqueue_event("network_scan_cancelled", {})
        except Exception as exc:
            self.enqueue_event("network_discovery_error", {"message": str(exc)})

    def populate_network_devices(self, devices: list[nt.DiscoveredDevice]) -> None:
        self.network_devices = {device.device_id: device for device in devices}
        if not self.network_ui_exists():
            return
        for item in self.network_device_tree.get_children():
            self.network_device_tree.delete(item)
        for device in devices:
            status = self.network_text("device_ready" if device.ready else "device_available")
            self.network_device_tree.insert(
                "", "end", iid=device.device_id,
                values=(device.name, device.host, status),
            )
        if devices:
            self.network_device_tree.selection_set(devices[0].device_id)
            self.network_device_tree.focus(devices[0].device_id)
            self.on_network_device_selected()

    def selected_network_device(self) -> nt.DiscoveredDevice | None:
        if not self.network_ui_exists():
            return None
        selected = self.network_device_tree.selection()
        if not selected:
            return None
        return self.network_devices.get(selected[0])

    def on_network_device_selected(self, _event: tk.Event | None = None) -> None:
        device = self.selected_network_device()
        if device is None:
            return
        self.network_host_var.set(device.host)
        self.network_status_var.set(f"{device.name} · {device.host}")

    def network_port(self) -> int:
        try:
            port = int(self.network_port_var.get().strip())
        except ValueError as exc:
            raise nt.NetworkTransferError(self.network_text("invalid_port")) from exc
        if not 1 <= port <= 65535:
            raise nt.NetworkTransferError(self.network_text("invalid_port"))
        return port

    def internet_port(self) -> int:
        try:
            port = int(self.internet_port_var.get().strip())
        except ValueError as exc:
            raise nt.NetworkTransferError(self.network_text("invalid_port")) from exc
        if not 1 <= port <= 65535:
            raise nt.NetworkTransferError(self.network_text("invalid_port"))
        return port

    @staticmethod
    def text_value(widget: ScrolledText) -> str:
        return widget.get("1.0", tk.END).strip()

    def copy_internet_invitation(self) -> None:
        code = self.text_value(self.internet_receive_code)
        if not code:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(code)

    def universal_service_url(self) -> str:
        value = self.internet_rendezvous_var.get()
        if not value.strip():
            raise wt.UniversalTransferError(self.network_text("missing_rendezvous"))
        return wt.normalize_rendezvous_url(value)

    def create_universal_receiver(self) -> None:
        destination = Path(self.network_destination_var.get().strip()).expanduser()
        if not destination.is_dir():
            self.show_network_error(self.network_text("pick_destination"))
            return
        try:
            service_url = self.universal_service_url()
        except wt.UniversalTransferError as exc:
            self.show_network_error(str(exc))
            return
        self.internet_receive_code.delete("1.0", tk.END)
        self.prepare_network_operation()
        self.network_status_var.set(self.network_text("creating_universal_code"))
        self.network_worker = threading.Thread(
            target=self.run_universal_receiver,
            args=(destination, service_url),
            daemon=True,
        )
        self.network_worker.start()

    def run_universal_receiver(self, destination: Path, service_url: str) -> None:
        try:
            receiver = wt.UniversalReceiver(
                destination=destination,
                rendezvous_url=service_url,
                device_name=self.discovery_service.device_name,
                cancel_event=self.network_cancel_event,
                callback=self.enqueue_network_engine_event,
            )
            result = receiver.receive_once()
            self.enqueue_event("network_done", {"result": result, "mode": "universal_receive"})
        except wt.UniversalTransferCancelled:
            self.enqueue_event("network_cancelled", {})
        except Exception as exc:
            self.enqueue_event("network_failed", {"message": str(exc)})

    def start_universal_send(self) -> None:
        if not self.network_send_files:
            self.show_network_error(self.network_text("pick_files"))
            return
        try:
            service_url = self.universal_service_url()
            sender = wt.UniversalSender(
                connection_code_value=self.text_value(self.internet_send_code),
                rendezvous_url=service_url,
                device_name=self.discovery_service.device_name,
                cancel_event=self.network_cancel_event,
                callback=self.enqueue_network_engine_event,
            )
        except wt.UniversalTransferError as exc:
            self.show_network_error(str(exc))
            return
        self.prepare_network_operation()
        files = list(self.network_send_files)
        self.network_worker = threading.Thread(
            target=self.run_universal_send,
            args=(sender, files),
            daemon=True,
        )
        self.network_worker.start()

    def run_universal_send(
        self, sender: wt.UniversalSender, files: list[Path]
    ) -> None:
        try:
            result = sender.send_files(files)
            self.enqueue_event("network_done", {"result": result, "mode": "universal_send"})
        except wt.UniversalTransferCancelled:
            self.enqueue_event("network_cancelled", {})
        except Exception as exc:
            self.enqueue_event("network_failed", {"message": str(exc)})

    def create_internet_invitation(self) -> None:
        destination = Path(self.network_destination_var.get().strip()).expanduser()
        if not destination.is_dir():
            self.show_network_error(self.network_text("pick_destination"))
            return
        try:
            port = self.internet_port()
        except nt.NetworkTransferError as exc:
            self.show_network_error(str(exc))
            return
        public_host = self.internet_public_host_var.get().strip()
        self.internet_receive_code.delete("1.0", tk.END)
        self.prepare_network_operation()
        self.network_status_var.set(self.network_text("upnp_mapping"))
        self.network_worker = threading.Thread(
            target=self.run_internet_receive_session,
            args=(destination, port, public_host),
            daemon=True,
        )
        self.network_worker.start()

    def run_internet_receive_session(
        self, destination: Path, port: int, public_host: str
    ) -> None:
        identity: nt.TLSIdentity | None = None
        mapping: nt.UPnPPortMapping | None = None
        server: nt.LocalTransferServer | None = None
        receiver: threading.Thread | None = None
        outcome: dict[str, object] = {}
        try:
            identity = nt.create_tls_identity(self.discovery_service.device_name)
            code = nt.generate_pairing_code()
            server_host = "::" if ":" in public_host else "0.0.0.0"
            server = nt.LocalTransferServer(
                destination=destination,
                pairing_code=code,
                host=server_host,
                port=port,
                cancel_event=self.network_cancel_event,
                callback=self.enqueue_network_engine_event,
                ssl_context=identity.context,
            )
            self.network_server = server
            self.network_server_tls = True

            def receive() -> None:
                try:
                    outcome["result"] = server.serve_once()
                except BaseException as exc:
                    outcome["error"] = exc

            receiver = threading.Thread(target=receive, daemon=True)
            receiver.start()
            server.ready_event.wait(5.0)
            if server.bound_port is None:
                error = outcome.get("error")
                if isinstance(error, BaseException):
                    raise error
                raise nt.NetworkTransferError("The Internet receiver could not start.")

            self.enqueue_event("network_internet_setup", {})
            if public_host:
                external_host = public_host
                external_port = port
            else:
                mapping = nt.create_upnp_mapping(server.bound_port, external_port=port)
                external_host = mapping.external_ip
                external_port = mapping.external_port
            expires_at = int(time.time()) + 15 * 60
            invitation = nt.InternetInvitation(
                host=external_host,
                port=external_port,
                pairing_code=code,
                tls_fingerprint=identity.fingerprint,
                expires_at=expires_at,
                device_name=self.discovery_service.device_name,
            )
            self.enqueue_event(
                "network_internet_invitation",
                {"code": invitation.encode(), "minutes": 15},
            )
            while receiver.is_alive():
                if self.network_cancel_event.is_set() or time.time() >= expires_at:
                    server.close()
                receiver.join(0.2)
            if "error" in outcome:
                error = outcome["error"]
                if isinstance(error, BaseException):
                    raise error
            result = outcome.get("result")
            if not isinstance(result, nt.NetworkTransferResult):
                raise nt.NetworkTransferError("The Internet transfer ended unexpectedly.")
            self.enqueue_event("network_done", {"result": result, "mode": "internet_receive"})
        except nt.NetworkTransferCancelled:
            self.enqueue_event("network_cancelled", {})
        except Exception as exc:
            if server is not None:
                server.close()
            if receiver is not None:
                receiver.join(2.0)
            self.enqueue_event("network_failed", {"message": str(exc)})
        finally:
            if mapping is not None:
                mapping.close()
            if identity is not None:
                identity.close()

    def start_internet_send(self) -> None:
        if not self.network_send_files:
            self.show_network_error(self.network_text("pick_files"))
            return
        try:
            invitation = nt.InternetInvitation.decode(
                self.text_value(self.internet_send_code)
            )
        except nt.NetworkTransferError as exc:
            self.show_network_error(str(exc))
            return
        self.prepare_network_operation()
        files = list(self.network_send_files)
        self.network_worker = threading.Thread(
            target=self.run_internet_send,
            args=(invitation, files),
            daemon=True,
        )
        self.network_worker.start()

    def run_internet_send(
        self, invitation: nt.InternetInvitation, files: list[Path]
    ) -> None:
        try:
            client = nt.LocalTransferClient(
                host=invitation.host,
                port=invitation.port,
                pairing_code=invitation.pairing_code,
                cancel_event=self.network_cancel_event,
                callback=self.enqueue_network_engine_event,
                tls_fingerprint=invitation.tls_fingerprint,
            )
            result = client.send_files(files)
            self.enqueue_event("network_done", {"result": result, "mode": "internet_send"})
        except nt.NetworkTransferCancelled:
            self.enqueue_event("network_cancelled", {})
        except Exception as exc:
            self.enqueue_event("network_failed", {"message": str(exc)})

    def show_network_error(self, message: str) -> None:
        self.network_status_var.set(self.network_text("error", message=message))
        messagebox.showerror(
            self.network_text("title"), message,
            parent=self.network_window if self.network_ui_exists() else self.root,
        )

    def prepare_network_operation(self) -> None:
        self.network_running = True
        self.network_cancel_event = threading.Event()
        self.network_progress.configure(value=0)
        self.network_log.configure(state="normal")
        self.network_log.delete("1.0", tk.END)
        self.network_log.configure(state="disabled")
        self.update_network_controls()

    def start_network_receive(self) -> None:
        destination = Path(self.network_destination_var.get().strip()).expanduser()
        if not destination.is_dir():
            self.show_network_error(self.network_text("pick_destination"))
            return
        try:
            self.begin_network_receive(destination, self.network_port())
        except (nt.NetworkTransferError, ValueError) as exc:
            self.show_network_error(str(exc))

    def begin_network_receive(
        self, destination: Path, port: int, pairing_code: str | None = None
    ) -> nt.LocalTransferServer:
        code = pairing_code or self.network_code_var.get()
        server = nt.LocalTransferServer(
            destination=destination,
            pairing_code=code,
            port=port,
            cancel_event=threading.Event(),
            callback=self.enqueue_network_engine_event,
        )
        self.network_code_var.set(code)
        self.prepare_network_operation()
        server.cancel_event = self.network_cancel_event
        self.network_server = server
        self.network_server_tls = False
        self.network_worker = threading.Thread(
            target=self.run_network_receive, args=(server,), daemon=True
        )
        self.network_worker.start()
        return server

    def start_network_send(self) -> None:
        if not self.network_send_files:
            self.show_network_error(self.network_text("pick_files"))
            return
        device = self.selected_network_device()
        if device is not None:
            self.prepare_network_operation()
            files = list(self.network_send_files)
            self.network_status_var.set(self.network_text("requesting", name=device.name))
            self.network_worker = threading.Thread(
                target=self.run_discovered_network_send,
                args=(device, files),
                daemon=True,
            )
            self.network_worker.start()
            return
        host = self.network_host_var.get().strip()
        if not host:
            self.show_network_error(self.network_text("address"))
            return
        try:
            client = nt.LocalTransferClient(
                host=host,
                port=self.network_port(),
                pairing_code=self.network_code_var.get(),
                cancel_event=threading.Event(),
                callback=self.enqueue_network_engine_event,
            )
        except (nt.NetworkTransferError, ValueError) as exc:
            self.show_network_error(str(exc))
            return
        self.prepare_network_operation()
        client.cancel_event = self.network_cancel_event
        self.network_server = None
        files = list(self.network_send_files)
        self.network_worker = threading.Thread(
            target=self.run_network_send, args=(client, files), daemon=True
        )
        self.network_worker.start()

    def run_discovered_network_send(
        self, device: nt.DiscoveredDevice, files: list[Path]
    ) -> None:
        try:
            invitation = nt.request_transfer(
                device=device,
                sender_id=self.discovery_service.instance_id,
                sender_name=self.discovery_service.device_name,
                files=files,
                cancel_event=self.network_cancel_event,
            )
            client = nt.LocalTransferClient(
                host=invitation.host,
                port=invitation.port,
                pairing_code=invitation.pairing_code,
                cancel_event=self.network_cancel_event,
                callback=self.enqueue_network_engine_event,
            )
            result = client.send_files(files)
            self.enqueue_event("network_done", {"result": result, "mode": "send"})
        except nt.NetworkTransferCancelled:
            self.enqueue_event("network_cancelled", {})
        except Exception as exc:
            self.enqueue_event("network_failed", {"message": str(exc)})

    def enqueue_network_engine_event(self, event: str, data: dict) -> None:
        if event not in {"complete", "cancelled", "error"}:
            self.enqueue_event(f"network_engine_{event}", data)

    def enqueue_discovery_event(self, event: str, data: dict) -> None:
        self.enqueue_event(f"network_{event}", data)

    def handle_incoming_transfer_request(self, data: dict) -> None:
        request_id = str(data["request_id"])
        if self.network_running:
            if (
                self.network_server is not None
                and not self.network_server_tls
                and self.network_server.bound_port is not None
                and self.network_reserved_request_id is None
            ):
                self.network_reserved_request_id = request_id
                self.discovery_service.respond_to_request(
                    request_id,
                    accepted=True,
                    transfer_port=self.network_server.bound_port,
                    pairing_code=self.network_code_var.get(),
                )
            else:
                self.discovery_service.respond_to_request(
                    request_id, accepted=False, message=self.network_text("busy")
                )
            return

        destination = Path(self.network_destination_var.get().strip()).expanduser()
        destination_text = str(destination) if destination.is_dir() else self.network_text("pick_destination")
        accepted = messagebox.askyesno(
            self.network_text("incoming_title"),
            self.network_text(
                "incoming_question",
                sender=data["sender_name"],
                files=data["files"],
                size=format_size(data["total"]),
                destination=destination_text,
            ),
            icon="question",
            parent=self.root,
        )
        if not accepted:
            self.discovery_service.respond_to_request(
                request_id, accepted=False, message=self.network_text("refused")
            )
            return
        if not destination.is_dir():
            chosen = filedialog.askdirectory(
                title=self.network_text("destination"), parent=self.root
            )
            if not chosen:
                self.discovery_service.respond_to_request(
                    request_id, accepted=False, message=self.network_text("refused")
                )
                return
            destination = Path(chosen)
            self.network_destination_var.set(chosen)

        self.open_network_window()
        code = nt.generate_pairing_code()
        self.network_reserved_request_id = request_id
        try:
            server = self.begin_network_receive(destination, port=0, pairing_code=code)
        except (nt.NetworkTransferError, OSError, ValueError) as exc:
            self.network_reserved_request_id = None
            self.discovery_service.respond_to_request(
                request_id, accepted=False, message=str(exc)
            )
            self.show_network_error(str(exc))
            return
        threading.Thread(
            target=self.answer_incoming_request_when_ready,
            args=(request_id, server, code),
            daemon=True,
        ).start()

    def answer_incoming_request_when_ready(
        self, request_id: str, server: nt.LocalTransferServer, code: str
    ) -> None:
        server.ready_event.wait(4.0)
        if server.bound_port is not None and not self.network_cancel_event.is_set():
            self.discovery_service.respond_to_request(
                request_id,
                accepted=True,
                transfer_port=server.bound_port,
                pairing_code=code,
            )
        else:
            self.discovery_service.respond_to_request(
                request_id, accepted=False, message=self.network_text("busy")
            )

    def run_network_receive(self, server: nt.LocalTransferServer) -> None:
        try:
            result = server.serve_once()
            self.enqueue_event("network_done", {"result": result, "mode": "receive"})
        except nt.NetworkTransferCancelled:
            self.enqueue_event("network_cancelled", {})
        except Exception as exc:
            self.enqueue_event("network_failed", {"message": str(exc)})

    def run_network_send(self, client: nt.LocalTransferClient, files: list[Path]) -> None:
        try:
            result = client.send_files(files)
            self.enqueue_event("network_done", {"result": result, "mode": "send"})
        except nt.NetworkTransferCancelled:
            self.enqueue_event("network_cancelled", {})
        except Exception as exc:
            self.enqueue_event("network_failed", {"message": str(exc)})

    def cancel_network_transfer(self) -> None:
        if not self.network_running:
            return
        self.network_cancel_event.set()
        if self.network_server is not None:
            self.network_server.close()
        self.network_status_var.set(self.network_text("cancelled"))
        if self.network_ui_exists():
            self.network_cancel_button.configure(state="disabled")

    def update_network_controls(self) -> None:
        if not self.network_ui_exists():
            return
        active = "disabled" if self.network_running else "normal"
        self.network_receive_button.configure(state=active)
        self.network_send_button.configure(state=active)
        self.network_scan_button.configure(
            state="disabled" if self.network_running or self.network_scanning else "normal"
        )
        self.internet_create_button.configure(state=active)
        self.internet_send_button.configure(state=active)
        self.network_cancel_button.configure(state="normal" if self.network_running else "disabled")

    def append_network_log(self, message: str) -> None:
        if not self.network_ui_exists():
            return
        self.network_log.configure(state="normal")
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.network_log.insert(tk.END, f"[{timestamp}] {message}\n")
        self.network_log.see(tk.END)
        self.network_log.configure(state="disabled")

    def finish_network_operation(self) -> None:
        self.network_running = False
        self.network_server = None
        self.network_server_tls = False
        self.network_reserved_request_id = None
        self.discovery_service.set_ready(False)
        self.update_network_controls()

    def handle_network_event(self, event: str, data: dict) -> None:
        if event == "transfer_request":
            self.handle_incoming_transfer_request(data)
            return
        if event == "devices_found":
            self.network_scanning = False
            devices: list[nt.DiscoveredDevice] = data["devices"]
            self.populate_network_devices(devices)
            self.update_network_controls()
            if not devices:
                self.network_status_var.set(self.network_text("no_devices"))
            return
        if event == "scan_cancelled":
            self.network_scanning = False
            self.update_network_controls()
            return
        if event == "discovery_error":
            self.network_scanning = False
            self.update_network_controls()
            message = self.network_text("discovery_error", message=data["message"])
        elif event == "internet_setup":
            message = self.network_text("upnp_mapping")
        elif event == "internet_invitation":
            message = self.network_text("invitation_ready", minutes=data["minutes"])
            if self.network_ui_exists():
                self.internet_receive_code.delete("1.0", tk.END)
                self.internet_receive_code.insert("1.0", str(data["code"]))
        elif event == "engine_room_ready":
            message = self.network_text("universal_code_ready", minutes=data["minutes"])
            if self.network_ui_exists():
                self.internet_receive_code.delete("1.0", tk.END)
                self.internet_receive_code.insert("1.0", str(data["code"]))
        elif event == "engine_listening":
            if not self.network_server_tls:
                self.discovery_service.set_ready(True)
            message = self.network_text(
                "listening", host=data["host"], port=data["port"],
                code=self.network_code_var.get(),
            )
        elif event == "engine_connecting":
            if int(data.get("port", 0)) == 0:
                message = self.network_text("automatic_route")
            else:
                message = self.network_text("connecting", host=data["host"], port=data["port"])
        elif event == "engine_route_state":
            if data.get("state") == "connected":
                message = self.network_text("route_connected")
            elif data.get("state") == "failed":
                message = self.network_text("route_failed")
            else:
                return
        elif event == "engine_authenticated":
            message = self.network_text("connected", peer=data["peer"])
        elif event == "engine_offer":
            message = self.network_text(
                "offer", files=data["files"], size=format_size(data["total"])
            )
        elif event == "engine_hashing":
            message = self.network_text("hashing", name=data["name"])
        elif event == "engine_file_received":
            message = self.network_text("file_received", name=data["name"])
        elif event == "engine_progress":
            total = int(data["total"])
            completed = int(data["completed"])
            percent = completed / total * 100 if total else 100
            message = self.network_text(
                "progress", percent=percent, name=data["name"],
                speed=format_size(data["speed"]),
            )
            if self.network_ui_exists():
                self.network_progress.configure(value=percent)
            self.network_status_var.set(message)
            return
        elif event == "done":
            result: nt.NetworkTransferResult = data["result"]
            message = self.network_text(
                "complete", files=result.files, size=format_size(result.bytes_transferred)
            )
            self.finish_network_operation()
            if self.network_ui_exists():
                self.network_progress.configure(value=100)
                messagebox.showinfo(self.network_text("title"), message, parent=self.network_window)
        elif event == "cancelled":
            message = self.network_text("cancelled")
            self.finish_network_operation()
        elif event == "failed":
            message = self.network_text("error", message=data["message"])
            self.finish_network_operation()
            if self.network_ui_exists():
                messagebox.showerror(
                    self.network_text("title"), str(data["message"]), parent=self.network_window
                )
        else:
            return
        self.network_status_var.set(message)
        self.append_network_log(message)

    def handle_event(self, event: str, data: dict) -> None:
        if event.startswith("network_"):
            self.handle_network_event(event.removeprefix("network_"), data)
        elif event == "duplicate_progress":
            total = int(data["total"])
            completed = int(data["completed"])
            self.duplicate_progress.configure(value=(completed / total * 100) if total else 100)
            self.duplicate_status_var.set(
                self.t("verifying_candidate", completed=completed, total=total)
            )
        elif event == "duplicate_log":
            self.duplicate_status_var.set(str(data["message"]))
        elif event == "duplicate_complete":
            result: DuplicateScanResult = data["result"]
            self.reset_duplicate_actions()
            self.duplicate_progress.configure(value=100)
            self.populate_duplicate_tree(result)
            self.duplicate_status_var.set(self.t(
                "scan_summary", files=result.files_scanned, duplicates=result.duplicate_files,
                groups=len(result.groups), size=format_size(result.reclaimable_bytes),
            ))
        elif event == "duplicate_cancelled":
            self.reset_duplicate_actions()
            self.duplicate_status_var.set(self.t("duplicate_scan_cancelled"))
        elif event == "duplicate_error":
            self.reset_duplicate_actions()
            self.duplicate_status_var.set(self.t("duplicate_scan_failed"))
            messagebox.showerror(self.t("duplicate_scan_error"), str(data["message"]), parent=self.root)
        elif event == "log":
            self.append_log(str(data["message"]), str(data.get("level", "info")))
        elif event == "scan_complete":
            self.status_var.set(self.t("transferring_to", target=data["target"]))
        elif event == "progress":
            total = int(data["total"])
            completed = int(data["completed"])
            percent = completed / total * 100 if total else 100
            self.progress.configure(value=percent)
            self.speed_label.configure(text=self.t("speed", value=f"{format_size(data['speed'])}/s"))
            self.remaining_label.configure(
                text=self.t(
                    "remaining_files", size=format_size(data["remaining"]),
                    count=data["total_files"] - data["file_index"],
                )
            )
            self.eta_label.configure(
                text=self.t("estimated_time", value=format_duration(data["eta"], self.language_code))
            )
        elif event == "finished":
            self.finish_transfer(data["result"])
        elif event == "eject_complete":
            self.status_var.set(self.t("eject_success"))
            self.source_var.set("")
            self.eject_button.configure(state="disabled")
            messagebox.showinfo(
                self.t("safe_eject_title"), self.t("eject_accepted"), parent=self.root
            )
        elif event == "eject_error":
            self.status_var.set(self.t("eject_failed"))
            self.update_eject_state()
            messagebox.showerror(self.t("safe_eject_title"), str(data["message"]), parent=self.root)
        elif event == "fatal":
            self.running = False
            self.reset_actions()
            self.status_var.set(self.t("error"))
            self.append_log(str(data["message"]), "error")
            self.record_history("Error")
            messagebox.showerror(self.t("error"), str(data["message"]), parent=self.root)

    def finish_transfer(self, result: TransferResult) -> None:
        self.running = False
        self.reset_actions()
        if self.current_options is not None:
            self.last_completed_source = self.current_options.source
        if result.cancelled:
            self.status_var.set(self.t("transfer_cancelled"))
            self.record_history("Cancelled", result)
            messagebox.showwarning(self.t("cancelled_title"), self.t("cancelled_safe"), parent=self.root)
            return

        if result.selected == 0:
            self.status_var.set(self.t("no_matching_files"))
            self.record_history("Empty", result)
            messagebox.showinfo(self.t("complete"), self.t("no_files_match"), parent=self.root)
            return

        self.progress.configure(value=100)
        summary = self.t(
            "transfer_summary", copied=result.copied, skipped=result.skipped,
            verified=result.verified, renamed=result.renamed, replaced=result.replaced,
            errors=len(result.errors),
        )
        if result.deleted:
            summary += self.t("deleted_source_count", count=result.deleted)
        self.append_log(
            self.t("complete_log", summary=summary),
            "success" if not result.errors else "warning",
        )
        self.status_var.set(summary)
        self.record_history("Warnings" if result.errors else "Complete", result)
        if result.errors:
            messagebox.showwarning(self.t("completed_errors"), summary, parent=self.root)
        else:
            messagebox.showinfo(self.t("transfer_complete"), summary, parent=self.root)

    def cancel_transfer(self) -> None:
        if self.running and not self.cancel_event.is_set():
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status_var.set(self.t("cancelling"))
            self.append_log(self.t("cancellation_requested"), "warning")

    def reset_actions(self) -> None:
        self.start_button.configure(state="normal", text=self.t("preview_transfer"))
        self.cancel_button.configure(state="disabled")
        self.update_eject_state()

    def append_log(self, message: str, level: str = "info") -> None:
        self.log.configure(state="normal")
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.insert(tk.END, f"[{timestamp}] {message}\n", level)
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.configure(state="disabled")

    def on_close(self) -> None:
        if (self.running or self.duplicate_running or self.network_running) and not messagebox.askyesno(
            self.t("quit_title"),
            self.t("quit_active"),
            icon="warning",
            parent=self.root,
        ):
            return
        self.cancel_event.set()
        self.duplicate_cancel_event.set()
        self.network_cancel_event.set()
        if self.network_server is not None:
            self.network_server.close()
        self.discovery_service.stop()
        self.root.destroy()


def main() -> None:
    if "--self-test-network" in sys.argv:
        identity = nt.create_tls_identity("TransferDesk packaged self-test")
        try:
            invitation = nt.InternetInvitation(
                host="127.0.0.1",
                port=48722,
                pairing_code="123456",
                tls_fingerprint=identity.fingerprint,
                expires_at=int(time.time()) + 60,
                device_name="Self-test",
            )
            if nt.InternetInvitation.decode(invitation.encode()).tls_fingerprint != identity.fingerprint:
                raise RuntimeError("Internet invitation self-test failed.")
            secret = wt.generate_auth_secret()
            code = wt.connection_code("ABCD-EFGH", secret)
            if wt.parse_connection_code(code) != ("ABCD-EFGH", secret):
                raise RuntimeError("Universal connection code self-test failed.")
            configuration = wt._rtc_configuration((wt.IceServer((wt.DEFAULT_STUN_URL,)),))
            if not configuration.iceServers:
                raise RuntimeError("WebRTC packaging self-test failed.")
        finally:
            identity.close()
        return
    root = tk.Tk()
    TransferDeskApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
