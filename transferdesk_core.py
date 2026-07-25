from __future__ import annotations

import filecmp
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from PIL import Image, UnidentifiedImageError

import translations as i18n


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



