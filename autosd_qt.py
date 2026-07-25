from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
import time
import webbrowser
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

try:
    from PySide6.QtCore import Property, QObject, QThreadPool, QRunnable, QUrl, Signal, Slot
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
except ImportError as exc:  # pragma: no cover - useful packaged/startup diagnostic
    raise SystemExit("PySide6 est requis. Installez les dépendances avec: py -m pip install -r requirements.txt") from exc

import network_transfer as nt
import translations as i18n
import webrtc_transfer as wt
import autosd_updater as updater
import autosd_core as core
from autosd_version import __version__


class WorkerSignals(QObject):
    event = Signal(str, "QVariant")
    finished = Signal("QVariant")
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.finished.emit(self.operation())
        except BaseException as exc:
            self.signals.failed.emit(str(exc))


class AutoSDBridge(QObject):
    busyChanged = Signal()
    languageChanged = Signal()
    themeChanged = Signal()
    statusChanged = Signal()
    progressChanged = Signal()
    eventReceived = Signal(str, "QVariant")
    notification = Signal(str, str)
    profilesChanged = Signal()
    historyChanged = Signal()
    duplicatesChanged = Signal()
    devicesChanged = Signal()
    previewChanged = Signal()
    previewReady = Signal()
    p2pCodeChanged = Signal()
    p2pStateChanged = Signal()
    p2pStatsChanged = Signal()
    socketCodeChanged = Signal()
    manualPayloadChanged = Signal()
    lastErrorChanged = Signal()
    updateChanged = Signal()
    incomingLocalTransfer = Signal("QVariantMap")
    discoveryEvent = Signal(str, "QVariant")

    def __init__(self) -> None:
        super().__init__()
        self._busy = False
        self._language = "fr"
        self._theme = "light"
        self._status = self.tr("ready")
        self._progress = 0.0
        self._cancel = threading.Event()
        self._pool = QThreadPool.globalInstance()
        self._profiles = core.ProfileStore()
        self._history = core.HistoryStore()
        self._duplicate_engine = None
        self._duplicates: list[dict[str, Any]] = []
        self._preview_items: list[dict[str, Any]] = []
        self._preview_summary: dict[str, Any] = {}
        self._preview_options: Any | None = None
        self._preview_settings: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []
        self._device_objects: dict[str, nt.DiscoveredDevice] = {}
        self._pending_local_requests: set[str] = set()
        self._local_server: nt.LocalTransferServer | None = None
        self._rendezvous_url = wt.default_rendezvous_url()
        self._p2p_code = ""
        self._socket_code = ""
        self._p2p_state = "idle"
        self._p2p_stats: dict[str, Any] = {}
        self._manual_payload = ""
        self._manual_payload_kind = ""
        self._manual_sender: wt.ManualSender | None = None
        self._last_error = ""
        self._update_state = "idle"
        self._update_version = ""
        self._update_message = ""
        self._update_progress = 0.0
        self._update_download_url = ""
        self._update_release: updater.UpdateRelease | None = None
        self._downloaded_update: updater.DownloadedUpdate | None = None
        self._reset_p2p_stats()
        self._device_code = self._load_device_code()
        self.discoveryEvent.connect(self._handle_discovery_event)
        self._discovery_service = nt.DiscoveryService(
            callback=self._queue_discovery_event,
            device_name=os.environ.get("COMPUTERNAME", "AutoSD"),
            instance_id=self._device_code,
        )
        self._discovery_service.start()

    def tr(self, key: str, **values: Any) -> str:
        try:
            return i18n.translate(self._language, key, **values)
        except Exception:
            return key.replace("_", " ").capitalize()

    @Slot(str, result=str)
    def text(self, key: str) -> str:
        return self.tr(str(key))

    @Slot(str, "QVariantMap", result=str)
    def textWith(self, key: str, values: dict[str, Any]) -> str:
        return self.tr(str(key), **dict(values or {}))

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=languageChanged)
    def language(self) -> str:
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        if value in {"en", "fr", "he"} and value != self._language:
            self._language = value
            self.languageChanged.emit()

    @Property(bool, notify=languageChanged)
    def rtl(self) -> bool:
        return self._language == "he"

    @Property(str, notify=themeChanged)
    def theme(self) -> str:
        return self._theme

    @theme.setter
    def theme(self, value: str) -> None:
        value = "dark" if value == "dark" else "light"
        if value != self._theme:
            self._theme = value
            self.themeChanged.emit()

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=lastErrorChanged)
    def lastError(self) -> str:
        return self._last_error

    @Property(float, notify=progressChanged)
    def progress(self) -> float:
        return self._progress

    @Property("QVariantList", notify=profilesChanged)
    def profiles(self) -> list[str]:
        return sorted(self._profiles.all(), key=str.casefold)

    @Property("QVariantList", notify=historyChanged)
    def history(self) -> list[dict[str, Any]]:
        return self._history.entries()

    @Property("QVariantList", notify=duplicatesChanged)
    def duplicates(self) -> list[dict[str, Any]]:
        return self._duplicates

    @Property("QVariantList", notify=devicesChanged)
    def devices(self) -> list[dict[str, Any]]:
        return self._devices

    @Property("QVariantList", notify=previewChanged)
    def previewItems(self) -> list[dict[str, Any]]:
        return self._preview_items

    @Property("QVariantMap", notify=previewChanged)
    def previewSummary(self) -> dict[str, Any]:
        return self._preview_summary

    @Property(str, constant=True)
    def rendezvousUrl(self) -> str:
        return self._rendezvous_url

    @Property(str, notify=p2pCodeChanged)
    def p2pCode(self) -> str:
        return self._p2p_code

    @Property(str, notify=socketCodeChanged)
    def socketCode(self) -> str:
        return self._socket_code

    @Property(str, notify=p2pStateChanged)
    def p2pState(self) -> str:
        return self._p2p_state

    @Property("QVariantMap", notify=p2pStatsChanged)
    def p2pStats(self) -> dict[str, Any]:
        return dict(self._p2p_stats)

    @Property(str, notify=manualPayloadChanged)
    def manualPayload(self) -> str:
        return self._manual_payload

    @Property(str, notify=manualPayloadChanged)
    def manualPayloadKind(self) -> str:
        return self._manual_payload_kind

    @Property(str, constant=True)
    def deviceCode(self) -> str:
        return self._device_code[:8].upper()

    @Property(str, constant=True)
    def socketPairingCode(self) -> str:
        return self._socket_pairing_code()

    @Property(str, constant=True)
    def appVersion(self) -> str:
        return __version__

    @Property(str, notify=updateChanged)
    def updateState(self) -> str:
        return self._update_state

    @Property(str, notify=updateChanged)
    def updateVersion(self) -> str:
        return self._update_version

    @Property(str, notify=updateChanged)
    def updateMessage(self) -> str:
        return self._update_message

    @Property(float, notify=updateChanged)
    def updateProgress(self) -> float:
        return self._update_progress

    @Property(str, notify=updateChanged)
    def updateDownloadUrl(self) -> str:
        return self._update_download_url

    def _load_device_code(self) -> str:
        path = core.application_data_dir() / "device-code.txt"
        try:
            value = path.read_text(encoding="ascii").strip().lower()
        except OSError:
            value = ""
        if not re.fullmatch(r"[0-9a-f]{32}", value):
            value = secrets.token_hex(16)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value, encoding="ascii")
            except OSError:
                pass
        return value

    def _socket_pairing_code(self) -> str:
        return f"{int(self._device_code[:12], 16) % 1_000_000:06d}"

    def _set_socket_code(self, value: str = "") -> None:
        value = str(value).strip()
        if value != self._socket_code:
            self._socket_code = value
            self.socketCodeChanged.emit()

    def _format_socket_code(self, host: str, port: int) -> str:
        endpoint = str(host).strip()
        if int(port) != nt.DEFAULT_PORT:
            endpoint = f"{endpoint}:{int(port)}"
        return f"{self._socket_pairing_code()}@{endpoint}"

    def _parse_socket_code(self, value: str) -> tuple[str, int, str]:
        compact = "".join(str(value).strip().split())
        match = re.fullmatch(r"(\d{6})@([^:]+)(?::(\d{1,5}))?", compact)
        if not match:
            raise nt.NetworkTransferError(self.tr("socket_code_invalid"))
        code, host, raw_port = match.groups()
        port = int(raw_port or nt.DEFAULT_PORT)
        if not 1 <= port <= 65535:
            raise nt.NetworkTransferError(self.tr("socket_code_invalid"))
        return host, port, code

    def _socket_retry_delay(self, attempt: int) -> float:
        return min(10.0, 0.5 * max(1, int(attempt)))

    def _wait_socket_retry(self, attempt: int) -> None:
        if self._cancel.wait(self._socket_retry_delay(attempt)):
            raise nt.NetworkTransferCancelled

    def _set_p2p_state(self, value: str) -> None:
        if value != self._p2p_state:
            self._p2p_state = value
            self.p2pStateChanged.emit()

    def _reset_p2p_stats(self) -> None:
        self._p2p_stats = {
            "completed": 0,
            "total": 0,
            "percent": 0,
            "completedText": core.format_size(0),
            "totalText": core.format_size(0),
            "speedText": "",
            "fileIndex": 0,
            "totalFiles": 0,
            "name": "",
            "summary": self.tr("p2p_stats_idle"),
        }
        self.p2pStatsChanged.emit()

    def _update_p2p_stats(self, data: dict[str, Any], *, complete: bool = False) -> None:
        total = int(float(data.get("total", self._p2p_stats.get("total", 0)) or 0))
        completed = int(float(data.get("completed", total if complete else self._p2p_stats.get("completed", 0)) or 0))
        percent = (completed / total * 100.0) if total else (100.0 if complete else 0.0)
        speed = float(data.get("speed", 0) or 0)
        file_index = int(data.get("file_index", self._p2p_stats.get("fileIndex", 0)) or 0)
        total_files = int(data.get("total_files", data.get("files", self._p2p_stats.get("totalFiles", 0))) or 0)
        name = str(data.get("name", self._p2p_stats.get("name", "")) or "")
        summary_key = "p2p_stats_complete" if complete else "p2p_stats_progress"
        self._p2p_stats = {
            "completed": completed,
            "total": total,
            "percent": round(percent, 1),
            "completedText": core.format_size(completed),
            "totalText": core.format_size(total),
            "speedText": core.format_size(speed) + "/s" if speed > 0 else "",
            "fileIndex": file_index,
            "totalFiles": total_files,
            "name": name,
            "summary": self.tr(
                summary_key,
                percent=f"{percent:.1f}",
                completed=core.format_size(completed),
                total=core.format_size(total),
                file=file_index,
                files=total_files,
                name=name or "-",
            ),
        }
        self.p2pStatsChanged.emit()

    def _set_manual_payload(self, value: str = "", kind: str = "") -> None:
        value = str(value)
        kind = str(kind)
        if value != self._manual_payload or kind != self._manual_payload_kind:
            self._manual_payload = value
            self._manual_payload_kind = kind
            self.manualPayloadChanged.emit()

    def _set_busy(self, value: bool) -> None:
        if self._busy != value:
            self._busy = value
            self.busyChanged.emit()

    def _set_status(self, value: str) -> None:
        self._status = value
        self.statusChanged.emit()

    def _set_last_error(self, value: str) -> None:
        value = str(value).strip()
        if value != self._last_error:
            self._last_error = value
            self.lastErrorChanged.emit()

    def _set_update(
        self,
        *,
        state: str | None = None,
        version: str | None = None,
        message: str | None = None,
        progress: float | None = None,
        download_url: str | None = None,
    ) -> None:
        changed = False
        if state is not None and state != self._update_state:
            self._update_state = state
            changed = True
        if version is not None and version != self._update_version:
            self._update_version = version
            changed = True
        if message is not None and message != self._update_message:
            self._update_message = message
            changed = True
        if progress is not None:
            normalized = max(0.0, min(1.0, float(progress)))
            if normalized != self._update_progress:
                self._update_progress = normalized
                changed = True
        if download_url is not None and download_url != self._update_download_url:
            self._update_download_url = download_url
            changed = True
        if changed:
            self.updateChanged.emit()

    def _event(self, name: str, data: dict[str, Any]) -> None:
        clean = {key: str(value) if isinstance(value, Path) else value for key, value in data.items()}
        if name in {"progress", "duplicate_progress"}:
            total = float(clean.get("total", 0) or 0)
            self._progress = float(clean.get("completed", 0)) / total if total else 0.0
            self.progressChanged.emit()
        if "message" in clean:
            self._set_status(str(clean["message"]))
        self.eventReceived.emit(name, clean)

    def _queue_discovery_event(self, name: str, data: dict[str, Any]) -> None:
        self.discoveryEvent.emit(name, data)

    @Slot(str, "QVariant")
    def _handle_discovery_event(self, name: str, data: Any) -> None:
        clean = dict(data or {})
        if name == "transfer_request":
            request_id = str(clean.get("request_id", ""))
            if not request_id:
                return
            if self._busy:
                self._discovery_service.respond_to_request(
                    request_id, accepted=False, message=self.tr("operation_running")
                )
                return
            self._pending_local_requests.add(request_id)
            self.incomingLocalTransfer.emit(clean)
        elif name == "discovery_error":
            self._set_status(str(clean.get("message", self.tr("discovery_error"))))

    def _start(
        self,
        operation: Callable[[], Any],
        done: Callable[[Any], None] | None = None,
        failed: Callable[[str], None] | None = None,
    ) -> None:
        if self._busy:
            self.notification.emit("warning", self.tr("operation_running"))
            return
        self._cancel.clear()
        self._set_last_error("")
        self._progress = 0.0
        self.progressChanged.emit()
        self._set_busy(True)
        worker = Worker(operation)
        worker.signals.finished.connect(lambda value: self._finish(value, done))
        worker.signals.failed.connect(failed or self._fail)
        self._pool.start(worker)

    def _finish(self, value: Any, done: Callable[[Any], None] | None) -> None:
        self._set_busy(False)
        if done:
            done(value)

    def _fail(self, message: str) -> None:
        self._set_busy(False)
        message = str(message).strip() or self.tr("unknown_error")
        self._set_last_error(message)
        self._set_status(message)
        self.notification.emit("error", message)

    def _fail_p2p(self, message: str) -> None:
        self._manual_sender = None
        self._local_server = None
        if self._cancel.is_set():
            self._set_p2p_state("cancelled")
            message = message or self.tr("operation_cancelled")
        elif "expir" in message.casefold() or "délai" in message.casefold():
            self._set_p2p_state("expired")
        else:
            self._set_p2p_state("error")
        self._fail(message or self.tr("p2p_failed"))

    def _p2p_event(self, name: str, data: dict[str, Any]) -> None:
        state_by_event = {
            "room_ready": "waiting",
            "listening": "waiting",
            "manual_offer_ready": "manual_waiting",
            "manual_answer_ready": "manual_waiting",
            "connecting": "connecting",
            "authenticated": "transferring",
            "offer": "transferring",
            "resuming": "transferring",
            "progress": "transferring",
            "file_received": "transferring",
            "complete": "success",
        }
        if name == "route_state":
            route = str(data.get("state", "")).casefold()
            if route in {"connected", "completed"}:
                self._set_p2p_state("transferring")
            elif route in {"failed", "closed", "disconnected"}:
                self._set_p2p_state("error")
        elif name in state_by_event:
            self._set_p2p_state(state_by_event[name])
        if name == "room_ready":
            self._p2p_code = str(data.get("code", ""))
            self.p2pCodeChanged.emit()
        elif name == "listening":
            self._set_socket_code(self._format_socket_code(
                str(data.get("host", nt.local_ip_address())),
                int(data.get("port", nt.DEFAULT_PORT) or nt.DEFAULT_PORT),
            ))
            self._set_status(self.tr("socket_receive_ready"))
        elif name == "offer":
            self._update_p2p_stats(data)
        elif name == "resuming":
            self._update_p2p_stats(data)
        elif name == "progress":
            self._update_p2p_stats(data)
        elif name == "complete":
            self._update_p2p_stats(data, complete=True)
        elif name == "manual_offer_ready":
            self._set_manual_payload(str(data.get("payload", "")), "offer")
            self._set_status(self.tr("manual_offer_ready"))
        elif name == "manual_answer_ready":
            self._set_manual_payload(str(data.get("payload", "")), "answer")
            self._set_status(self.tr("manual_answer_ready"))
        self._event(name, data)

    def _warn(self, message: str) -> bool:
        self._set_status(message)
        self.notification.emit("warning", message)
        return False

    def _validate_transfer(self, settings: dict[str, Any]) -> bool:
        source_text = str(settings.get("source", "")).strip()
        destination_text = str(settings.get("destination", "")).strip()
        if not source_text:
            return self._warn(self.tr("choose_source"))
        source = Path(source_text)
        if not source.is_dir():
            return self._warn(self.tr("source_missing"))
        if not destination_text:
            return self._warn(self.tr("choose_destination"))
        destination = Path(destination_text)
        if destination.exists() and not destination.is_dir():
            return self._warn(self.tr("destination_missing"))
        try:
            if source.resolve() == destination.resolve():
                return self._warn(self.tr("folders_overlap"))
        except OSError:
            pass
        raw_extensions = str(settings.get("extensions", ""))
        for raw in raw_extensions.replace(";", ",").split(","):
            value = raw.strip().lower()
            if not value or value in {"*", "*.*"}:
                continue
            value = value.removeprefix("*").removeprefix(".")
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,15}", value):
                return self._warn(self.tr("invalid_extension", value=raw.strip()))
        return True

    @Slot()
    def cancel(self) -> None:
        self._cancel.set()
        if self._local_server is not None:
            self._local_server.close()
        if self._p2p_state in {"generating", "waiting", "connecting", "transferring"}:
            self._set_p2p_state("cancelled")
        self._set_status(self.tr("cancelling"))

    def _options(self, settings: dict[str, Any]):
        parse = lambda value: core.parse_iso_date(value, "date", self._language) if value else None
        return core.TransferOptions(
            source=Path(settings.get("source", "")), destination=Path(settings.get("destination", "")),
            extensions=core.normalize_extensions(settings.get("extensions", "")),
            date_mode=settings.get("dateMode", "all"), date_start=parse(settings.get("dateStart", "")),
            date_end=parse(settings.get("dateEnd", "")), create_folder=settings.get("createFolder", True),
            folder_name_mode=settings.get("folderNameMode", "auto"),
            custom_folder_name=settings.get("folderName", "File_Backup"),
            delete_source=settings.get("deleteSource", False), preserve_tree=settings.get("preserveTree", False),
            verify_checksum=settings.get("verify", True), conflict_policy=settings.get("conflictPolicy", "rename"),
            organization_mode=settings.get("organization", "none"), language=self._language,
        )

    @Slot("QVariantMap")
    def previewTransfer(self, settings: dict[str, Any]) -> None:
        if not self._validate_transfer(settings):
            return
        options = self._options(settings)
        engine = core.TransferEngine(options, self._cancel, self._event)

        def done(plan: Any) -> None:
            if not plan.items:
                self._preview_items = []
                self._preview_summary = {}
                self.previewChanged.emit()
                self._warn(self.tr("no_files_match"))
                return
            self._preview_options = options
            self._preview_settings = dict(settings)
            self._preview_items = [{
                "source": str(item.source),
                "destination": str(item.destination),
                "size": item.size,
                "sizeText": core.format_size(item.size),
                "action": item.action,
                "reason": item.reason,
            } for item in plan.items]
            self._preview_summary = {
                "files": len(plan.items),
                "bytes": plan.required_bytes,
                "requiredText": core.format_size(plan.required_bytes),
                "freeText": core.format_size(plan.free_bytes),
                "enoughSpace": plan.enough_space,
                "target": str(plan.target_dir or ""),
            }
            self.previewChanged.emit()
            self.previewReady.emit()
            self._set_status(self.tr("preview_ready_count", count=len(plan.items)))

        self._set_status(self.tr("building_preview"))
        self._start(engine.preview, done)

    @Slot("QVariantMap")
    def startTransfer(self, settings: dict[str, Any]) -> None:
        if not self._validate_transfer(settings):
            return
        engine = core.TransferEngine(self._options(settings), self._cancel, self._event)
        self._start_transfer_engine(engine, settings)

    @Slot("QVariantList")
    def startPreviewedTransfer(self, decisions: list[dict[str, Any]]) -> None:
        if self._preview_options is None or not self._preview_items:
            self._warn(self.tr("preview_required"))
            return
        selected: set[Path] = set()
        overrides: dict[Path, str] = {}
        allowed_actions = {"rename", "skip", "replace", "newer"}
        for decision in decisions:
            source_text = str(decision.get("source", "")).strip()
            if not source_text or not bool(decision.get("selected", False)):
                continue
            source = Path(source_text)
            selected.add(source)
            action = str(decision.get("action", ""))
            if action in allowed_actions:
                overrides[source] = action
        if not selected:
            self._warn(self.tr("select_one_file"))
            return
        engine = core.TransferEngine(
            self._preview_options,
            self._cancel,
            self._event,
            selected_paths=selected,
            conflict_overrides=overrides,
        )
        settings = dict(self._preview_settings)
        self._preview_items = []
        self._preview_summary = {}
        self._preview_options = None
        self._preview_settings = {}
        self.previewChanged.emit()
        self._start_transfer_engine(engine, settings)

    def _start_transfer_engine(self, engine: Any, settings: dict[str, Any]) -> None:
        def done(result: Any) -> None:
            entry = {"timestamp": datetime.now().isoformat(timespec="seconds"), "status": "cancelled" if result.cancelled else "complete",
                     "source": settings.get("source", ""), "destination": str(result.target_dir or settings.get("destination", "")),
                     "copied": result.copied, "skipped": result.skipped, "errors": len(result.errors)}
            self._history.append(entry); self.historyChanged.emit()
            self._set_status(self.tr("operation_cancelled") if result.cancelled else self.tr("transfer_complete"))
        self._start(engine.run, done)

    @Slot(str)
    def scanDuplicates(self, folder: str) -> None:
        root = Path(str(folder).strip())
        if not str(folder).strip():
            self._warn(self.tr("choose_scan_folder"))
            return
        if not root.is_dir():
            self._warn(self.tr("scan_folder_missing"))
            return
        self._duplicate_engine = core.DuplicateEngine(root, self._cancel, self._event, language=self._language)
        def done(result: Any) -> None:
            self._duplicates = [{"digest": g.digest, "size": g.size, "original": str(g.original),
                                 "duplicates": [str(p) for p in g.duplicates]} for g in result.groups]
            self.duplicatesChanged.emit()
        self._start(self._duplicate_engine.scan, done)

    @Slot("QVariantList", str)
    def applyDuplicateAction(self, paths: list[str], action: str) -> None:
        if not self._duplicate_engine:
            self.notification.emit("warning", self.tr("duplicate_scanner_not_ready")); return
        self._start(lambda: self._duplicate_engine.apply_action([Path(p) for p in paths], action),
                    lambda _: self.scanDuplicates(str(self._duplicate_engine.root_folder)))

    @Slot(str, "QVariantMap")
    def saveProfile(self, name: str, settings: dict[str, Any]) -> None:
        try:
            self._profiles.save(name, dict(settings)); self.profilesChanged.emit()
        except Exception as exc: self._fail(str(exc))

    @Slot(str, result="QVariantMap")
    def loadProfile(self, name: str) -> dict[str, Any]:
        return dict(self._profiles.all().get(name, {}))

    @Slot(str)
    def deleteProfile(self, name: str) -> None:
        self._profiles.delete(name); self.profilesChanged.emit()

    @Slot(str, result=bool)
    def exportHistory(self, path: str) -> bool:
        try: self._history.export_text(Path(path), self._language); return True
        except Exception as exc: self._fail(str(exc)); return False

    @Slot(str)
    def eject(self, path: str) -> None:
        self._start(lambda: core.eject_removable_drive(Path(path), self._language), lambda _: self.notification.emit("info", self.tr("ejected")))

    @Slot()
    def scanDevices(self) -> None:
        def done(items: Any) -> None:
            self._device_objects = {d.device_id: d for d in items}
            self._devices = [{
                "id": d.device_id,
                "name": d.name,
                "host": d.host,
                "discoveryPort": d.discovery_port,
                "ready": d.ready,
                "label": f"{d.name} · {d.host}" + (
                    f" · {self.tr('device_ready_suffix')}" if d.ready else ""
                ),
            } for d in items]
            self.devicesChanged.emit()
            self._set_status(
                self.tr("devices_found_count", count=len(items)) if items
                else self.tr("no_devices_found")
            )
        self._start(lambda: nt.discover_devices(
            sender_id=self._discovery_service.instance_id,
            duration=2.5,
            cancel_event=self._cancel,
        ), done)

    @Slot(str, "QVariantList")
    def sendLocal(self, device_id: str, files: list[str]) -> None:
        device = self._device_objects.get(str(device_id))
        if device is None:
            self._warn(self.tr("select_device"))
            return
        paths = [Path(str(value).strip()) for value in files if str(value).strip()]
        if not paths:
            self._warn(self.tr("choose_send_file"))
            return
        missing = next((path for path in paths if not path.is_file() and not path.is_dir()), None)
        if missing is not None:
            self._warn(self.tr("missing_send_item", path=missing))
            return

        def operation() -> Any:
            invitation = nt.request_transfer(
                device=device,
                sender_id=self._discovery_service.instance_id,
                sender_name=self._discovery_service.device_name,
                files=paths,
                cancel_event=self._cancel,
            )
            client = nt.LocalTransferClient(
                invitation.host,
                invitation.port,
                invitation.pairing_code,
                self._cancel,
                self._event,
            )
            return client.send_files(paths)

        self._set_status(self.tr("local_request_sent", name=device.name))
        self._start(operation, lambda result: self._local_transfer_done(
            result, "send", "; ".join(str(path) for path in paths), device.name
        ))

    @Slot(str, bool, str)
    def respondLocalRequest(self, request_id: str, accepted: bool, destination: str) -> None:
        request_id = str(request_id)
        if request_id not in self._pending_local_requests:
            self._warn(self.tr("local_request_expired"))
            return
        self._pending_local_requests.discard(request_id)
        if not accepted:
            self._discovery_service.respond_to_request(
                request_id, accepted=False, message=self.tr("transfer_refused")
            )
            return
        target = Path(str(destination).strip())
        if not str(destination).strip() or not target.is_dir():
            self._discovery_service.respond_to_request(
                request_id, accepted=False, message=self.tr("invalid_receive_folder")
            )
            self._warn(self.tr("choose_existing_receive_folder"))
            return
        if self._busy:
            self._discovery_service.respond_to_request(
                request_id, accepted=False, message=self.tr("operation_running")
            )
            return

        code = nt.generate_pairing_code()
        server = nt.LocalTransferServer(
            destination=target,
            pairing_code=code,
            port=0,
            cancel_event=self._cancel,
            callback=self._event,
        )
        self._local_server = server

        def announce_when_ready() -> None:
            server.ready_event.wait(4.0)
            ready = server.bound_port is not None and not self._cancel.is_set()
            self._discovery_service.respond_to_request(
                request_id,
                accepted=ready,
                transfer_port=server.bound_port or 0,
                pairing_code=code if ready else "",
                message="" if ready else self.tr("receiver_start_failed"),
            )

        def operation() -> Any:
            self._discovery_service.set_ready(True)
            threading.Thread(target=announce_when_ready, daemon=True).start()
            try:
                return server.serve_once()
            finally:
                self._discovery_service.set_ready(False)
                self._local_server = None

        self._set_status(self.tr("local_receive_preparing"))
        self._start(
            operation,
            lambda result: self._local_transfer_done(result, "receive", "Réseau local", str(target)),
            self._local_transfer_failed,
        )

    def _append_network_history(self, kind: str, source: str, destination: str, result: Any) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "status": "complete",
            "source": source,
            "destination": destination,
            "copied": int(getattr(result, "files", 0) or 0),
            "skipped": 0,
            "errors": 0,
            "bytes": int(getattr(result, "bytes_transferred", 0) or 0),
            "kind": kind,
        }
        self._history.append(entry)
        self.historyChanged.emit()

    def _local_transfer_done(self, result: Any, mode: str, source: str = "", destination: str = "") -> None:
        self._append_network_history("local", source, destination, result)
        key = "local_done_receive" if mode == "receive" else "local_done_send"
        self._set_status(self.tr(key, count=result.files))
        self.notification.emit("info", self.tr("local_transfer_complete"))

    def _local_transfer_failed(self, message: str) -> None:
        self._discovery_service.set_ready(False)
        self._local_server = None
        self._fail(message)

    @Slot()
    def shutdown(self) -> None:
        self._cancel.set()
        if self._local_server is not None:
            self._local_server.close()
        self._discovery_service.stop()

    @Slot(str, str)
    def receiveP2P(self, destination: str, rendezvous_url: str) -> None:
        if self._busy:
            self._warn(self.tr("operation_running"))
            return
        destination_text = str(destination).strip()
        if not destination_text:
            self._warn(self.tr("choose_receive_folder_warning"))
            return
        target = Path(destination_text)
        if target.exists() and not target.is_dir():
            self._warn(self.tr("invalid_receive_folder_warning"))
            return
        try:
            url = wt.normalize_rendezvous_url(rendezvous_url)
        except Exception as exc:
            self._warn(str(exc))
            return
        self._p2p_code = ""
        self.p2pCodeChanged.emit()
        self._set_manual_payload()
        self._reset_p2p_stats()
        self._set_p2p_state("generating")
        receiver = wt.UniversalReceiver(target, url, os.environ.get("COMPUTERNAME", "AutoSD"), self._cancel, self._p2p_event)
        self._start(
            receiver.receive_once,
            done=lambda result: self._p2p_done(result, "receive", "P2P Internet", str(target)),
            failed=self._fail_p2p,
        )

    @Slot(str, str, "QVariantList")
    def sendP2P(self, code: str, rendezvous_url: str, files: list[str]) -> None:
        if self._busy:
            self._warn(self.tr("operation_running"))
            return
        try:
            url = wt.normalize_rendezvous_url(rendezvous_url)
            wt.parse_connection_code(str(code))
        except Exception as exc:
            self._warn(str(exc))
            return
        paths = [Path(str(value).strip()) for value in files if str(value).strip()]
        if not paths:
            self._warn(self.tr("choose_send_file"))
            return
        missing = next((path for path in paths if not path.is_file()), None)
        if missing is not None:
            self._warn(f"Fichier introuvable : {missing}")
            return
        self._set_manual_payload()
        self._reset_p2p_stats()
        self._set_p2p_state("connecting")
        sender = wt.UniversalSender(code, url, os.environ.get("COMPUTERNAME", "AutoSD"), self._cancel, self._p2p_event)
        self._start(
            lambda: sender.send_files(paths),
            done=lambda result: self._p2p_done(result, "send", "; ".join(str(path) for path in paths), "P2P Internet"),
            failed=self._fail_p2p,
        )

    def _p2p_done(self, result: Any, mode: str, source: str, destination: str) -> None:
        self._manual_sender = None
        self._local_server = None
        self._set_p2p_state("success")
        self._update_p2p_stats({"files": getattr(result, "files", 0), "total": getattr(result, "bytes_transferred", 0)}, complete=True)
        self._append_network_history("p2p", source, destination, result)
        key = "p2p_done_receive" if mode == "receive" else "p2p_done_send"
        self._set_status(self.tr(key, count=getattr(result, "files", 0)))
        self.notification.emit("info", self.tr("p2p_transfer_complete"))

    @Slot(str)
    def startSocketReceive(self, destination: str) -> None:
        if self._busy:
            self._warn(self.tr("operation_running"))
            return
        destination_text = str(destination).strip()
        if not destination_text:
            self._warn(self.tr("choose_receive_folder_warning"))
            return
        target = Path(destination_text)
        if target.exists() and not target.is_dir():
            self._warn(self.tr("invalid_receive_folder_warning"))
            return
        self._p2p_code = ""
        self.p2pCodeChanged.emit()
        self._set_manual_payload()
        self._set_socket_code("")
        self._reset_p2p_stats()
        self._set_p2p_state("generating")
        def make_server(port: int) -> nt.LocalTransferServer:
            server = nt.LocalTransferServer(
                destination=target,
                pairing_code=self._socket_pairing_code(),
                port=port,
                cancel_event=self._cancel,
                callback=self._p2p_event,
                timeout=30.0,
            )
            self._local_server = server
            return server

        self._local_server = make_server(nt.DEFAULT_PORT)

        def serve_one(port: int) -> Any:
            server = make_server(port)
            try:
                return server.serve_once()
            except nt.NetworkTransferError as exc:
                if "Address already in use" not in str(exc) and "Only one usage" not in str(exc):
                    raise
                fallback = make_server(0)
                return fallback.serve_once()

        def operation() -> Any:
            port = nt.DEFAULT_PORT
            attempt = 1
            while True:
                if self._cancel.is_set():
                    raise nt.NetworkTransferCancelled
                if attempt > 1:
                    self._set_status(self.tr("socket_waiting_retry"))
                    self._wait_socket_retry(attempt)
                try:
                    return serve_one(port)
                except nt.NetworkTransferCancelled:
                    raise
                except nt.NetworkTransferError as exc:
                    bound_port = self._local_server.bound_port if self._local_server is not None else None
                    port = int(bound_port or port)
                    attempt += 1

        self._start(
            operation,
            done=lambda result: self._p2p_done(result, "receive", "Socket direct", str(target)),
            failed=self._fail_p2p,
        )

    @Slot(str, "QVariantList")
    def sendSocket(self, code: str, files: list[str]) -> None:
        if self._busy:
            self._warn(self.tr("operation_running"))
            return
        try:
            host, port, pairing_code = self._parse_socket_code(code)
        except Exception as exc:
            self._warn(str(exc))
            return
        paths = [Path(str(value).strip()) for value in files if str(value).strip()]
        if not paths:
            self._warn(self.tr("choose_send_file"))
            return
        missing = next((path for path in paths if not path.is_file() and not path.is_dir()), None)
        if missing is not None:
            self._warn(self.tr("missing_send_item", path=missing))
            return
        self._p2p_code = ""
        self.p2pCodeChanged.emit()
        self._set_manual_payload()
        self._set_socket_code("")
        self._reset_p2p_stats()
        self._set_p2p_state("connecting")
        def operation() -> Any:
            attempt = 1
            while True:
                if self._cancel.is_set():
                    raise nt.NetworkTransferCancelled
                if attempt > 1:
                    self._p2p_event("connecting", {})
                    self._set_status(self.tr("socket_retrying"))
                    self._wait_socket_retry(attempt)
                client = nt.LocalTransferClient(
                    host,
                    port,
                    pairing_code,
                    self._cancel,
                    self._p2p_event,
                    timeout=30.0,
                )
                try:
                    return client.send_files(paths)
                except nt.NetworkTransferCancelled:
                    raise
                except nt.NetworkTransferError:
                    attempt += 1

        self._start(
            operation,
            done=lambda result: self._p2p_done(result, "send", "; ".join(str(path) for path in paths), f"Socket {host}"),
            failed=self._fail_p2p,
        )

    def _manual_failed(self, message: str) -> None:
        self._manual_sender = None
        self._fail_p2p(message)

    @Slot("QVariantList")
    def startManualSend(self, files: list[str]) -> None:
        if self._busy:
            self._warn(self.tr("operation_running"))
            return
        paths = [Path(str(value).strip()) for value in files if str(value).strip()]
        if not paths:
            self._warn(self.tr("choose_send_file"))
            return
        missing = next((path for path in paths if not path.is_file()), None)
        if missing is not None:
            self._warn(self.tr("missing_send_item", path=missing))
            return
        self._p2p_code = ""
        self.p2pCodeChanged.emit()
        self._set_socket_code("")
        self._set_manual_payload()
        self._reset_p2p_stats()
        self._set_p2p_state("manual_generating")
        sender = wt.ManualSender(self._cancel, self._p2p_event)
        self._manual_sender = sender
        self._start(
            lambda: sender.send_files(paths),
            done=lambda result: self._p2p_done(result, "send", "; ".join(str(path) for path in paths), "P2P manuel"),
            failed=self._manual_failed,
        )

    @Slot(str, str)
    def startManualReceive(self, destination: str, offer: str) -> None:
        if self._busy:
            self._warn(self.tr("operation_running"))
            return
        destination_text = str(destination).strip()
        if not destination_text:
            self._warn(self.tr("choose_receive_folder_warning"))
            return
        target = Path(destination_text)
        if target.exists() and not target.is_dir():
            self._warn(self.tr("invalid_receive_folder_warning"))
            return
        try:
            receiver = wt.ManualReceiver(
                target, str(offer), self._cancel, self._p2p_event
            )
        except Exception as exc:
            self._warn(str(exc))
            return
        self._p2p_code = ""
        self.p2pCodeChanged.emit()
        self._manual_sender = None
        self._set_manual_payload()
        self._reset_p2p_stats()
        self._set_p2p_state("manual_generating")
        self._start(
            receiver.receive_once,
            done=lambda result: self._p2p_done(result, "receive", "P2P manuel", str(target)),
            failed=self._manual_failed,
        )

    @Slot(str)
    def submitManualAnswer(self, answer: str) -> None:
        sender = self._manual_sender
        if sender is None or not self._busy:
            self._warn(self.tr("manual_no_sender"))
            return
        try:
            sender.accept_answer(str(answer))
        except Exception as exc:
            message = str(exc).strip() or self.tr("unknown_error")
            self._set_last_error(message)
            self._set_status(message)
            self.notification.emit("error", message)
            return
        self._set_p2p_state("connecting")
        self._set_status(self.tr("manual_answer_imported"))

    @Slot()
    def copyManualPayload(self) -> None:
        if not self._manual_payload:
            self._warn(self.tr("manual_nothing_to_copy"))
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            self.notification.emit("error", self.tr("clipboard_unavailable"))
            return
        clipboard.setText(self._manual_payload)
        self.notification.emit("info", self.tr("manual_copied"))

    @Slot()
    def clearManualP2P(self) -> None:
        if self._busy:
            self._warn(self.tr("operation_running"))
            return
        self._p2p_code = ""
        self.p2pCodeChanged.emit()
        self._set_manual_payload()
        self._set_p2p_state("idle")
        self._reset_p2p_stats()
        self._set_status(self.tr("manual_cleared"))

    @Slot()
    def checkForUpdates(self) -> None:
        if self._busy:
            self._warn(self.tr("operation_running"))
            return
        self._update_release = None
        self._downloaded_update = None
        self._set_update(
            state="checking",
            version="",
            message=self.tr("update_checking"),
            progress=0.0,
            download_url="",
        )

        def done(release: updater.UpdateRelease | None) -> None:
            if release is None:
                self._set_update(state="up-to-date", message=self.tr("update_up_to_date", version=__version__))
                self.notification.emit("info", self._update_message)
                return
            self._update_release = release
            self._set_update(
                state="available",
                version=release.version,
                message=self.tr(
                    "update_available",
                    version=release.version,
                    size=core.format_size(release.asset.size),
                ),
                download_url=release.asset.download_url,
            )

        def failed(message: str) -> None:
            self._set_busy(False)
            fallback = os.environ.get("AUTOSD_UPDATE_URL", "").strip() or updater.RELEASES_URL
            self._set_update(
                state="failed",
                message=self.tr("update_check_failed", error=str(message).strip() or self.tr("unknown_error")),
                download_url=fallback,
            )
            self.notification.emit("error", self._update_message)

        self._start(lambda: updater.fetch_latest_release(__version__), done=done, failed=failed)

    @Slot()
    def confirmDownloadUpdate(self) -> None:
        if self._update_release is None:
            self.notification.emit("warning", self.tr("update_not_available"))
            return
        self.notification.emit("info", self.tr("update_download_confirm_required", version=self._update_release.version))

    @Slot()
    def downloadUpdate(self) -> None:
        if self._busy:
            self._warn(self.tr("operation_running"))
            return
        if self._update_release is None:
            self.notification.emit("warning", self.tr("update_not_available"))
            return
        release = self._update_release
        destination = core.application_data_dir() / "updates" / release.tag
        self._set_update(
            state="downloading",
            version=release.version,
            message=self.tr("update_downloading", version=release.version),
            progress=0.0,
            download_url=release.asset.download_url,
        )
        self._set_busy(True)
        self._set_last_error("")
        holder: dict[str, Worker] = {}

        def progress(completed: int, total: int) -> None:
            worker = holder.get("worker")
            if worker is not None:
                worker.signals.event.emit("progress", {"completed": completed, "total": total})

        def operation() -> updater.DownloadedUpdate:
            return updater.download_update(release, destination, progress=progress)

        worker = Worker(operation)
        holder["worker"] = worker
        worker.signals.event.connect(self._update_download_event)
        worker.signals.finished.connect(self._download_update_done)
        worker.signals.failed.connect(self._download_update_failed)
        self._pool.start(worker)

    @Slot()
    def confirmInstallUpdate(self) -> None:
        if self._downloaded_update is None:
            self.notification.emit("warning", self.tr("update_not_ready"))
            return
        self.notification.emit("info", self.tr("update_install_confirm_required", version=self._downloaded_update.release.version))

    @Slot()
    def installDownloadedUpdate(self) -> None:
        if self._downloaded_update is None:
            self.notification.emit("warning", self.tr("update_not_ready"))
            return
        try:
            result = updater.install_downloaded_update(self._downloaded_update)
        except Exception as exc:
            message = str(exc).strip() or self.tr("unknown_error")
            self._set_update(state="failed", message=message)
            self._fail(message)
            return
        self._set_update(state="installing", message=self.tr("update_installing"))
        if result == "open":
            self.notification.emit("info", self.tr("update_archive_opened"))

    @Slot()
    def openUpdatePage(self) -> None:
        url = self._update_download_url if self._update_download_url.startswith("http") else ""
        if self._update_release is not None:
            url = self._update_release.page_url
        url = url or os.environ.get("AUTOSD_UPDATE_URL", "").strip() or updater.RELEASES_URL
        try:
            webbrowser.open(url)
            self.notification.emit("info", self.tr("update_opened"))
        except Exception as exc:
            self._fail(str(exc))

    @Slot(str, "QVariant")
    def _update_download_event(self, name: str, data: Any) -> None:
        if name != "progress":
            return
        clean = dict(data or {})
        total = float(clean.get("total", 0) or 0)
        completed = float(clean.get("completed", 0) or 0)
        progress = completed / total if total else 0.0
        self._set_update(
            progress=progress,
            message=self.tr(
                "update_download_progress",
                completed=core.format_size(completed),
                total=core.format_size(total),
            ),
        )

    @Slot("QVariant")
    def _download_update_done(self, value: Any) -> None:
        self._set_busy(False)
        self._downloaded_update = value
        release = value.release
        self._set_update(
            state="ready",
            version=release.version,
            message=self.tr("update_ready", version=release.version),
            progress=1.0,
            download_url=release.asset.download_url,
        )

    @Slot(str)
    def _download_update_failed(self, message: str) -> None:
        self._set_busy(False)
        clean = str(message).strip() or self.tr("unknown_error")
        self._set_last_error(clean)
        self._set_update(state="failed", message=clean)
        self.notification.emit("error", clean)

    @Slot(str)
    def copyToClipboard(self, text: str) -> None:
        value = str(text).strip()
        if not value:
            self.notification.emit("warning", self.tr("no_code_to_copy"))
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            self.notification.emit("error", "Le presse-papiers est indisponible.")
            return
        clipboard.setText(value)
        self.notification.emit("info", self.tr("code_copied"))


def main() -> int:
    if "--self-test-network" in sys.argv:
        identity = nt.create_tls_identity("AutoSD packaged self-test")
        try:
            invitation = nt.InternetInvitation("127.0.0.1", 48722, "123456", identity.fingerprint,
                                               int(datetime.now().timestamp()) + 60, "Self-test")
            if nt.InternetInvitation.decode(invitation.encode()).tls_fingerprint != identity.fingerprint:
                raise RuntimeError("Échec de l'auto-test P2P v2")
            secret = wt.generate_auth_secret()
            if wt.parse_connection_code(wt.connection_code("ABCD-EFGH", secret)) != ("ABCD-EFGH", secret):
                raise RuntimeError("Échec de l'auto-test du code P2P")
        finally:
            identity.close()
        return 0
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    app = QGuiApplication(sys.argv)
    app.setApplicationName("AutoSD File Manager")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AutoSD")
    engine = QQmlApplicationEngine()
    bridge = AutoSDBridge()
    app.aboutToQuit.connect(bridge.shutdown)
    engine.rootContext().setContextProperty("backend", bridge)
    qml = Path(__file__).with_name("qml") / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects(): return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
