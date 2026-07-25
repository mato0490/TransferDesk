"""Universal encrypted TransferDesk transfers using WebRTC ICE/STUN/TURN.

The rendezvous service only exchanges signed WebRTC descriptions. File bytes
travel through the end-to-end encrypted WebRTC data channel, directly whenever
ICE can establish a peer-to-peer route and through TURN as a last resort.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import string
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable


WEBRTC_PROTOCOL_VERSION = 2
# 64 KiB stays below the conservative WebRTC/SCTP message-size limit while
# halving the Python and UI overhead compared with the former 32 KiB chunks.
WEBRTC_CHUNK_SIZE = 64 * 1024
# Keep enough data queued to fill high-bandwidth, high-latency Internet paths.
# This is a per-transfer cap, not memory proportional to the file size.
MAX_BUFFERED_BYTES = 16 * 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 0.1
MAX_SIGNAL_BYTES = 256 * 1024
DEFAULT_STUN_URL = "stun:stun.l.google.com:19302"
CLOUDFLARE_HOST_SUFFIXES = (
    "cloudflare.com",
    "cloudflare.net",
    "pages.dev",
    "trycloudflare.com",
    "workers.dev",
)
ROOM_LIFETIME_SECONDS = 15 * 60
MANUAL_SIGNAL_VERSION = 1
MANUAL_SIGNAL_PREFIX = "TRANSFERDESK-MANUAL-1."
MAX_MANUAL_SIGNAL_CHARS = (MAX_SIGNAL_BYTES * 4 // 3) + 256
EventCallback = Callable[[str, dict], None]


class UniversalTransferError(Exception):
    """A user-facing WebRTC or rendezvous error."""


class UniversalTransferCancelled(Exception):
    """The user cancelled the universal transfer."""


@dataclass(frozen=True)
class IceServer:
    urls: tuple[str, ...]
    username: str | None = None
    credential: str | None = None


@dataclass(frozen=True)
class RendezvousSession:
    room_id: str
    token: str
    expires_at: int
    ice_servers: tuple[IceServer, ...]


@dataclass
class UniversalTransferResult:
    files: int = 0
    bytes_transferred: int = 0
    paths: list[Path] = field(default_factory=list)


def normalize_rendezvous_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UniversalTransferError("Adresse du service de connexion invalide.")
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in CLOUDFLARE_HOST_SUFFIXES
    ):
        raise UniversalTransferError("Les services Cloudflare ne sont pas pris en charge.")
    return url


def default_rendezvous_url() -> str:
    """Return the configured service URL without embedding any secret."""
    environment_value = os.environ.get("TRANSFERDESK_RENDEZVOUS_URL", "").strip()
    if environment_value:
        return environment_value.rstrip("/")
    roots = [Path.cwd(), Path(__file__).resolve().parent]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            roots.insert(0, Path(bundle_root))
    for root in roots:
        for filename in ("transferdesk-network.json",):
            configuration = root / filename
            if not configuration.is_file():
                continue
            try:
                value = str(json.loads(configuration.read_text(encoding="utf-8"))["rendezvous_url"])
                return normalize_rendezvous_url(value)
            except (OSError, KeyError, TypeError, json.JSONDecodeError, UniversalTransferError):
                continue
    return ""


def generate_auth_secret() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(16))
    return "-".join(raw[index:index + 4] for index in range(0, len(raw), 4))


def connection_code(room_id: str, auth_secret: str) -> str:
    return f"{room_id.strip().upper()}.{auth_secret.strip().upper()}"


def parse_connection_code(value: str) -> tuple[str, str]:
    compact = "".join(value.strip().upper().split())
    try:
        room_id, secret = compact.split(".", 1)
    except ValueError as exc:
        raise UniversalTransferError("Code de connexion Internet invalide.") from exc
    allowed = set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789-")
    if not 6 <= len(room_id) <= 16 or not 16 <= len(secret) <= 24:
        raise UniversalTransferError("Code de connexion Internet invalide.")
    if any(char not in allowed for char in room_id + secret):
        raise UniversalTransferError("Code de connexion Internet invalide.")
    return room_id, secret


def _description_signature(auth_secret: str, kind: str, sdp: str) -> str:
    key = hashlib.sha256(auth_secret.encode("ascii")).digest()
    return hmac.new(key, f"{kind}\n{sdp}".encode("utf-8"), hashlib.sha256).hexdigest()


def _signed_description(auth_secret: str, kind: str, sdp: str) -> dict[str, str]:
    return {
        "type": kind,
        "sdp": sdp,
        "signature": _description_signature(auth_secret, kind, sdp),
    }


def _verify_description(auth_secret: str, payload: dict, expected_kind: str) -> str:
    kind = str(payload.get("type", ""))
    sdp = str(payload.get("sdp", ""))
    signature = str(payload.get("signature", ""))
    if kind != expected_kind or not sdp or len(sdp.encode("utf-8")) > MAX_SIGNAL_BYTES:
        raise UniversalTransferError("Description de connexion invalide.")
    expected = _description_signature(auth_secret, kind, sdp)
    if not hmac.compare_digest(signature, expected):
        raise UniversalTransferError("La connexion Internet n'a pas pu être authentifiée.")
    return sdp


def _encode_manual_signal(
    kind: str,
    auth_secret: str,
    sdp: str,
    expires_at: int,
    *,
    include_secret: bool,
) -> str:
    if kind not in {"offer", "answer"}:
        raise ValueError("unsupported manual signal kind")
    data: dict[str, object] = {
        "version": MANUAL_SIGNAL_VERSION,
        "kind": kind,
        "expiresAt": int(expires_at),
        "description": _signed_description(auth_secret, kind, sdp),
    }
    if include_secret:
        data["secret"] = auth_secret
    encoded = base64.urlsafe_b64encode(
        json.dumps(data, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    if len(encoded) + len(MANUAL_SIGNAL_PREFIX) > MAX_MANUAL_SIGNAL_CHARS:
        raise UniversalTransferError("Les informations de connexion manuelle sont trop grandes.")
    return MANUAL_SIGNAL_PREFIX + encoded


def _decode_manual_signal(value: str, expected_kind: str) -> dict:
    compact = "".join(str(value).split())
    if not compact.startswith(MANUAL_SIGNAL_PREFIX) or len(compact) > MAX_MANUAL_SIGNAL_CHARS:
        raise UniversalTransferError("Informations de connexion manuelle invalides.")
    raw = compact[len(MANUAL_SIGNAL_PREFIX):]
    try:
        decoded = base64.b64decode(
            raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True
        )
        if len(decoded) > MAX_SIGNAL_BYTES:
            raise ValueError("manual signal too large")
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UniversalTransferError("Informations de connexion manuelle invalides.") from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != MANUAL_SIGNAL_VERSION
        or data.get("kind") != expected_kind
        or not isinstance(data.get("description"), dict)
    ):
        raise UniversalTransferError("Informations de connexion manuelle invalides.")
    try:
        expires_at = int(data["expiresAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UniversalTransferError("Informations de connexion manuelle invalides.") from exc
    if expires_at < int(time.time()):
        raise UniversalTransferError("Les informations de connexion manuelle ont expiré.")
    data["expiresAt"] = expires_at
    return data


def encode_manual_offer(auth_secret: str, sdp: str, expires_at: int) -> str:
    """Encode a signed WebRTC offer for copy/paste signaling."""
    return _encode_manual_signal(
        "offer", auth_secret, sdp, expires_at, include_secret=True
    )


def decode_manual_offer(value: str) -> tuple[str, str, int]:
    """Decode and authenticate a copy/pasted WebRTC offer."""
    data = _decode_manual_signal(value, "offer")
    secret = str(data.get("secret", "")).upper()
    allowed = set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789-")
    if not 16 <= len(secret) <= 24 or any(char not in allowed for char in secret):
        raise UniversalTransferError("Informations de connexion manuelle invalides.")
    sdp = _verify_description(secret, data["description"], "offer")
    return secret, sdp, int(data["expiresAt"])


def encode_manual_answer(auth_secret: str, sdp: str, expires_at: int) -> str:
    """Encode a signed WebRTC answer for copy/paste signaling."""
    return _encode_manual_signal(
        "answer", auth_secret, sdp, expires_at, include_secret=False
    )


def decode_manual_answer(value: str, auth_secret: str) -> tuple[str, int]:
    """Decode and authenticate a copy/pasted WebRTC answer."""
    data = _decode_manual_signal(value, "answer")
    sdp = _verify_description(auth_secret, data["description"], "answer")
    return sdp, int(data["expiresAt"])


def _parse_ice_servers(raw_servers: object) -> tuple[IceServer, ...]:
    servers: list[IceServer] = []
    if isinstance(raw_servers, list):
        for raw in raw_servers:
            if not isinstance(raw, dict):
                continue
            raw_urls = raw.get("urls")
            if isinstance(raw_urls, str):
                urls = (raw_urls,)
            elif isinstance(raw_urls, list):
                urls = tuple(str(url) for url in raw_urls if isinstance(url, str))
            else:
                urls = ()
            # Port 53 is an alternate route that often causes a long timeout.
            urls = tuple(url for url in urls if not url.split("?", 1)[0].endswith(":53"))
            if urls:
                servers.append(IceServer(
                    urls=urls,
                    username=str(raw["username"]) if raw.get("username") else None,
                    credential=str(raw["credential"]) if raw.get("credential") else None,
                ))
    if not servers:
        servers.append(IceServer((DEFAULT_STUN_URL,)))
    return tuple(servers)


def _connection_timeout_message(servers: tuple[IceServer, ...]) -> str:
    message = (
        "Aucun chemin P2P n’a pu être établi. Vérifie que les deux PC utilisent "
        "la même version d’TransferDesk, autorise TransferDesk dans le pare-feu Windows et "
        "désactive l’isolation des appareils du réseau Wi-Fi."
    )
    has_turn = any(
        url.casefold().startswith(("turn:", "turns:"))
        for server in servers
        for url in server.urls
    )
    if not has_turn:
        message += " Le service de connexion ne fournit actuellement aucun relais TURN."
    return message


class RendezvousClient:
    """Small HTTP client for an TransferDesk-compatible rendezvous service."""

    def __init__(self, base_url: str, timeout: float = 12.0) -> None:
        self.base_url = normalize_rendezvous_url(base_url)
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        payload: dict | None = None,
        pending_ok: bool = False,
    ) -> dict | None:
        body = None
        headers = {"Accept": "application/json", "User-Agent": "TransferDesk/8"}
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.base_url + path, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status == 204:
                    return None
                raw = response.read(MAX_SIGNAL_BYTES + 1)
                if len(raw) > MAX_SIGNAL_BYTES:
                    raise UniversalTransferError("Réponse du service de connexion trop grande.")
                decoded = json.loads(raw.decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise ValueError("response is not an object")
                return decoded
        except urllib.error.HTTPError as exc:
            if pending_ok and exc.code == 204:
                return None
            try:
                message = json.loads(exc.read(8192).decode("utf-8")).get("error")
            except Exception:
                message = None
            if exc.code == 404:
                message = message or "Code introuvable ou expiré."
            elif exc.code == 409:
                message = message or "Ce code est déjà utilisé."
            elif exc.code == 429:
                message = message or "Trop de tentatives. Réessaie dans un instant."
            raise UniversalTransferError(message or f"Service de connexion indisponible ({exc.code}).") from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
            raise UniversalTransferError(f"Service de connexion inaccessible : {exc}") from exc

    @staticmethod
    def _session(data: dict) -> RendezvousSession:
        try:
            room_id = str(data["roomId"]).upper()
            token = str(data["token"])
            expires_at = int(data["expiresAt"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UniversalTransferError("Réponse invalide du service de connexion.") from exc
        if not room_id or not token:
            raise UniversalTransferError("Réponse invalide du service de connexion.")
        return RendezvousSession(room_id, token, expires_at, _parse_ice_servers(data.get("iceServers")))

    def create_room(self, device_name: str) -> RendezvousSession:
        data = self._request("POST", "/v1/rooms", payload={"deviceName": device_name})
        if data is None:
            raise UniversalTransferError("Le service n'a pas créé le code.")
        return self._session(data)

    def join_room(self, room_id: str, device_name: str) -> RendezvousSession:
        room = urllib.parse.quote(room_id, safe="")
        data = self._request("POST", f"/v1/rooms/{room}/join", payload={"deviceName": device_name})
        if data is None:
            raise UniversalTransferError("Le service n'a pas rejoint la connexion.")
        return self._session(data)

    def put_description(self, session: RendezvousSession, kind: str, payload: dict) -> None:
        room = urllib.parse.quote(session.room_id, safe="")
        self._request("PUT", f"/v1/rooms/{room}/{kind}", token=session.token, payload=payload)

    def wait_description(
        self,
        session: RendezvousSession,
        kind: str,
        cancel_event: threading.Event,
        timeout: float,
    ) -> dict:
        room = urllib.parse.quote(session.room_id, safe="")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel_event.is_set():
                raise UniversalTransferCancelled
            data = self._request(
                "GET", f"/v1/rooms/{room}/{kind}", token=session.token, pending_ok=True
            )
            if data is not None:
                return data
            cancel_event.wait(0.45)
        raise UniversalTransferError("Délai dépassé en attendant l'autre PC.")


def _safe_name(value: object) -> str:
    name = str(value)
    if not name or name in {".", ".."} or Path(name).name != name or "\x00" in name:
        raise UniversalTransferError("Nom de fichier dangereux reçu.")
    return name


def _available_path(destination: Path, name: str) -> Path:
    candidate = destination / name
    if not candidate.exists():
        return candidate
    original = Path(name)
    number = 2
    while True:
        candidate = destination / f"{original.stem} ({number}){original.suffix}"
        if not candidate.exists():
            return candidate
        number += 1


def _sha256(path: Path, cancel_event: threading.Event) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            if cancel_event.is_set():
                raise UniversalTransferCancelled
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


class _ChannelInbox:
    def __init__(self, channel, cancel_event: threading.Event) -> None:
        self.channel = channel
        self.cancel_event = cancel_event
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        self.closed = asyncio.Event()
        channel.on("message", self.messages.put_nowait)
        channel.on("close", self.closed.set)

    async def receive(self, timeout: float = 60.0) -> object:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if self.cancel_event.is_set():
                raise UniversalTransferCancelled
            if self.closed.is_set() and self.messages.empty():
                raise UniversalTransferError("La connexion avec l'autre PC a été fermée.")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise UniversalTransferError("L'autre PC ne répond plus.")
            try:
                return await asyncio.wait_for(self.messages.get(), min(0.25, remaining))
            except asyncio.TimeoutError:
                continue

    async def receive_control(self, expected: str, timeout: float = 60.0) -> dict:
        message = await self.receive(timeout)
        if not isinstance(message, str):
            raise UniversalTransferError("Message de contrôle invalide.")
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            raise UniversalTransferError("Message de contrôle invalide.") from exc
        if not isinstance(payload, dict) or payload.get("t") != expected:
            if isinstance(payload, dict) and payload.get("t") == "error":
                raise UniversalTransferError(str(payload.get("message", "Erreur distante.")))
            raise UniversalTransferError(f"Message inattendu (attendu : {expected}).")
        return payload

    async def send_control(self, kind: str, **data: object) -> None:
        await self.wait_writable()
        self.channel.send(json.dumps({"t": kind, **data}, separators=(",", ":")))

    async def send_bytes(self, data: bytes) -> None:
        await self.wait_writable()
        self.channel.send(data)

    async def wait_writable(self) -> None:
        while self.channel.bufferedAmount > MAX_BUFFERED_BYTES:
            if self.cancel_event.is_set():
                raise UniversalTransferCancelled
            if self.closed.is_set():
                raise UniversalTransferError("La connexion avec l'autre PC a été fermée.")
            # A long polling interval creates a saw-tooth throughput pattern on
            # fast links once the SCTP send buffer reaches its high-water mark.
            await asyncio.sleep(0.002)

    async def drain(self, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while self.channel.bufferedAmount:
            if self.cancel_event.is_set():
                raise UniversalTransferCancelled
            if asyncio.get_running_loop().time() >= deadline:
                return
            await asyncio.sleep(0.01)


class _ProgressReporter:
    """Throttle expensive UI callbacks without reducing transfer throughput."""

    def __init__(self, callback: EventCallback, total: int, file_count: int) -> None:
        self.callback = callback
        self.total = total
        self.file_count = file_count
        self.started = time.monotonic()
        self.last_report = 0.0

    def report(self, completed: int, file_index: int, name: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and completed < self.total and now - self.last_report < PROGRESS_INTERVAL_SECONDS:
            return
        self.last_report = now
        elapsed = max(now - self.started, 0.001)
        self.callback("progress", {
            "completed": completed,
            "total": self.total,
            "speed": completed / elapsed,
            "file_index": file_index,
            "total_files": self.file_count,
            "name": name,
        })


async def _wait_channel_open(channel, cancel_event: threading.Event, timeout: float = 45.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while channel.readyState != "open":
        if cancel_event.is_set():
            raise UniversalTransferCancelled
        if channel.readyState == "closed":
            raise UniversalTransferError("Impossible d'ouvrir le canal chiffré.")
        if asyncio.get_running_loop().time() >= deadline:
            raise UniversalTransferError("Délai dépassé pendant la connexion Internet.")
        await asyncio.sleep(0.05)


async def _wait_future_with_cancel(
    future: asyncio.Future,
    cancel_event: threading.Event,
    timeout: float,
):
    deadline = asyncio.get_running_loop().time() + timeout
    while not future.done():
        if cancel_event.is_set():
            raise UniversalTransferCancelled
        if asyncio.get_running_loop().time() >= deadline:
            raise asyncio.TimeoutError
        await asyncio.sleep(0.05)
    return future.result()


async def _wait_ice_gathering(pc, cancel_event: threading.Event, timeout: float = 25.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while pc.iceGatheringState != "complete":
        if cancel_event.is_set():
            raise UniversalTransferCancelled
        if asyncio.get_running_loop().time() >= deadline:
            raise UniversalTransferError("La recherche d'un chemin Internet a expiré.")
        await asyncio.sleep(0.05)


async def _close_peer(pc) -> None:
    try:
        await asyncio.wait_for(pc.close(), 5.0)
    except (asyncio.TimeoutError, RuntimeError):
        pass


def _rtc_configuration(servers: tuple[IceServer, ...]):
    from aiortc import RTCConfiguration, RTCIceServer

    return RTCConfiguration(iceServers=[
        RTCIceServer(urls=list(server.urls), username=server.username, credential=server.credential)
        for server in servers
    ])


async def _send_files(
    channel,
    paths: Iterable[Path],
    cancel_event: threading.Event,
    callback: EventCallback,
) -> UniversalTransferResult:
    selected: list[tuple[Path, str, int]] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            raise UniversalTransferError(f"Fichier introuvable : {path}")
        name = _safe_name(path.name)
        if name.casefold() in seen:
            raise UniversalTransferError(f"Deux fichiers portent le même nom : {name}")
        seen.add(name.casefold())
        size = path.stat().st_size
        selected.append((path, name, size))
    if not selected:
        raise UniversalTransferError("Sélectionne au moins un fichier.")

    inbox = _ChannelInbox(channel, cancel_event)
    # Hash while sending instead of reading every file once before transfer.
    manifest = [{"name": name, "size": size} for _, name, size in selected]
    await inbox.send_control("offer", version=WEBRTC_PROTOCOL_VERSION, files=manifest)
    await inbox.receive_control("ready")
    total = sum(item[2] for item in selected)
    completed = 0
    progress = _ProgressReporter(callback, total, len(selected))
    result = UniversalTransferResult()
    for index, (path, name, size) in enumerate(selected):
        await inbox.send_control("file", index=index)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                if cancel_event.is_set():
                    raise UniversalTransferCancelled
                chunk = handle.read(WEBRTC_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                await inbox.send_bytes(chunk)
                completed += len(chunk)
                progress.report(completed, index + 1, name)
        progress.report(completed, index + 1, name, force=True)
        await inbox.send_control("file_end", index=index, sha256=digest.hexdigest())
        await inbox.receive_control("file_ok")
        result.files += 1
        result.bytes_transferred += size
    await inbox.send_control("complete")
    await inbox.receive_control("complete_ok")
    callback("complete", {"files": result.files, "total": result.bytes_transferred})
    return result


async def _receive_files(
    channel,
    destination: Path,
    cancel_event: threading.Event,
    callback: EventCallback,
) -> UniversalTransferResult:
    inbox = channel if isinstance(channel, _ChannelInbox) else _ChannelInbox(channel, cancel_event)
    offer = await inbox.receive_control("offer")
    if offer.get("version") != WEBRTC_PROTOCOL_VERSION:
        await inbox.send_control("error", message="Version TransferDesk incompatible.")
        raise UniversalTransferError("Version TransferDesk incompatible.")
    raw_files = offer.get("files")
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > 10_000:
        raise UniversalTransferError("Liste de fichiers invalide.")
    manifest: list[tuple[str, int]] = []
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise UniversalTransferError("Fichier proposé invalide.")
        name = _safe_name(raw.get("name"))
        size = int(raw.get("size", -1))
        if size < 0:
            raise UniversalTransferError(f"Métadonnées invalides pour {name}.")
        manifest.append((name, size))
    total = sum(size for _, size in manifest)
    callback("offer", {"peer": "Internet", "files": len(manifest), "total": total})
    await inbox.send_control("ready")

    destination.mkdir(parents=True, exist_ok=True)
    result = UniversalTransferResult()
    completed = 0
    progress = _ProgressReporter(callback, total, len(manifest))
    for index, (name, expected_size) in enumerate(manifest):
        control = await inbox.receive_control("file")
        if int(control.get("index", -1)) != index:
            raise UniversalTransferError("Ordre des fichiers invalide.")
        final_path = _available_path(destination, name)
        temporary = destination / f".{final_path.name}.transferdesk-part-{secrets.token_hex(5)}"
        digest = hashlib.sha256()
        received = 0
        try:
            with temporary.open("xb") as handle:
                while True:
                    message = await inbox.receive(timeout=120.0)
                    if isinstance(message, bytes):
                        if received + len(message) > expected_size:
                            raise UniversalTransferError(f"Trop de données reçues pour {name}.")
                        handle.write(message)
                        digest.update(message)
                        received += len(message)
                        completed += len(message)
                        progress.report(completed, index + 1, name)
                        continue
                    if not isinstance(message, str):
                        raise UniversalTransferError("Données de fichier invalides.")
                    try:
                        end = json.loads(message)
                    except json.JSONDecodeError as exc:
                        raise UniversalTransferError("Fin de fichier invalide.") from exc
                    if not isinstance(end, dict) or end.get("t") != "file_end" or int(end.get("index", -1)) != index:
                        raise UniversalTransferError("Fin de fichier invalide.")
                    expected_digest = str(end.get("sha256", "")).lower()
                    if len(expected_digest) != 64 or any(
                        char not in string.hexdigits for char in expected_digest
                    ):
                        raise UniversalTransferError("Empreinte de fichier invalide.")
                    break
            progress.report(completed, index + 1, name, force=True)
            if received != expected_size or digest.hexdigest() != expected_digest:
                raise UniversalTransferError(f"Vérification d'intégrité échouée pour {name}.")
            temporary.replace(final_path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        result.files += 1
        result.bytes_transferred += received
        result.paths.append(final_path)
        callback("file_received", {"name": final_path.name})
        await inbox.send_control("file_ok", index=index)
    await inbox.receive_control("complete")
    await inbox.send_control("complete_ok")
    await inbox.drain()
    callback("complete", {"files": result.files, "total": result.bytes_transferred})
    return result


class ManualSender:
    """Send files after a two-step copy/paste WebRTC negotiation."""

    def __init__(
        self,
        cancel_event: threading.Event | None = None,
        callback: EventCallback | None = None,
        ice_servers: tuple[IceServer, ...] | None = None,
    ) -> None:
        self.cancel_event = cancel_event or threading.Event()
        self.callback = callback or (lambda _event, _data: None)
        self.ice_servers = (
            _parse_ice_servers(None) if ice_servers is None else ice_servers
        )
        self.auth_secret = generate_auth_secret()
        self.expires_at = int(time.time()) + ROOM_LIFETIME_SECONDS
        self._answer_ready = threading.Event()
        self._answer_lock = threading.Lock()
        self._answer_sdp = ""

    def accept_answer(self, value: str) -> None:
        answer_sdp, _expires_at = decode_manual_answer(value, self.auth_secret)
        with self._answer_lock:
            if self._answer_ready.is_set():
                raise UniversalTransferError("Une réponse manuelle a déjà été importée.")
            self._answer_sdp = answer_sdp
            self._answer_ready.set()

    def _wait_for_answer(self) -> str:
        while not self._answer_ready.wait(0.2):
            if self.cancel_event.is_set():
                raise UniversalTransferCancelled
            if time.time() >= self.expires_at:
                raise UniversalTransferError("L’offre de connexion manuelle a expiré.")
        if self.cancel_event.is_set():
            raise UniversalTransferCancelled
        with self._answer_lock:
            return self._answer_sdp

    def send_files(self, paths: Iterable[Path]) -> UniversalTransferResult:
        selected = list(paths)
        return asyncio.run(self._send_files(selected))

    async def _send_files(self, paths: list[Path]) -> UniversalTransferResult:
        from aiortc import RTCPeerConnection, RTCSessionDescription

        pc = RTCPeerConnection(_rtc_configuration(self.ice_servers))
        channel = pc.createDataChannel("transferdesk-files", ordered=True)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            self.callback("route_state", {"state": pc.connectionState})

        try:
            await pc.setLocalDescription(await pc.createOffer())
            await _wait_ice_gathering(pc, self.cancel_event)
            assert pc.localDescription is not None
            payload = encode_manual_offer(
                self.auth_secret, pc.localDescription.sdp, self.expires_at
            )
            self.callback("manual_offer_ready", {
                "payload": payload,
                "minutes": max(1, (self.expires_at - int(time.time())) // 60),
            })
            answer_sdp = await asyncio.to_thread(self._wait_for_answer)
            self.callback("connecting", {"host": "ICE/STUN", "port": 0})
            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=answer_sdp, type="answer")
            )
            try:
                await _wait_channel_open(channel, self.cancel_event)
            except UniversalTransferError as exc:
                if "Délai dépassé" not in str(exc):
                    raise
                raise UniversalTransferError(
                    _connection_timeout_message(self.ice_servers)
                ) from exc
            self.callback("authenticated", {"peer": "PC distant · canal chiffré"})
            return await _send_files(
                channel, paths, self.cancel_event, self.callback
            )
        finally:
            await _close_peer(pc)


class ManualReceiver:
    """Receive files from a copy/pasted WebRTC offer."""

    def __init__(
        self,
        destination: Path,
        offer_value: str,
        cancel_event: threading.Event | None = None,
        callback: EventCallback | None = None,
        ice_servers: tuple[IceServer, ...] | None = None,
    ) -> None:
        self.destination = Path(destination)
        self.auth_secret, self.offer_sdp, self.expires_at = decode_manual_offer(
            offer_value
        )
        self.cancel_event = cancel_event or threading.Event()
        self.callback = callback or (lambda _event, _data: None)
        self.ice_servers = (
            _parse_ice_servers(None) if ice_servers is None else ice_servers
        )

    def receive_once(self) -> UniversalTransferResult:
        return asyncio.run(self._receive_once())

    async def _receive_once(self) -> UniversalTransferResult:
        from aiortc import RTCPeerConnection, RTCSessionDescription

        pc = RTCPeerConnection(_rtc_configuration(self.ice_servers))
        channel_future: asyncio.Future = asyncio.get_running_loop().create_future()

        @pc.on("datachannel")
        def on_datachannel(channel) -> None:
            if not channel_future.done():
                channel_future.set_result(_ChannelInbox(channel, self.cancel_event))

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            self.callback("route_state", {"state": pc.connectionState})

        try:
            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=self.offer_sdp, type="offer")
            )
            await pc.setLocalDescription(await pc.createAnswer())
            await _wait_ice_gathering(pc, self.cancel_event)
            assert pc.localDescription is not None
            payload = encode_manual_answer(
                self.auth_secret, pc.localDescription.sdp, self.expires_at
            )
            self.callback("manual_answer_ready", {
                "payload": payload,
                "minutes": max(1, (self.expires_at - int(time.time())) // 60),
            })
            timeout = max(1.0, self.expires_at - time.time())
            inbox = await _wait_future_with_cancel(
                channel_future, self.cancel_event, timeout
            )
            try:
                await _wait_channel_open(inbox.channel, self.cancel_event)
            except UniversalTransferError as exc:
                if "Délai dépassé" not in str(exc):
                    raise
                raise UniversalTransferError(
                    _connection_timeout_message(self.ice_servers)
                ) from exc
            self.callback("authenticated", {"peer": "PC distant · canal chiffré"})
            return await _receive_files(
                inbox, self.destination, self.cancel_event, self.callback
            )
        except asyncio.TimeoutError as exc:
            raise UniversalTransferError(
                _connection_timeout_message(self.ice_servers)
            ) from exc
        finally:
            await _close_peer(pc)


class UniversalReceiver:
    def __init__(
        self,
        destination: Path,
        rendezvous_url: str,
        device_name: str,
        cancel_event: threading.Event | None = None,
        callback: EventCallback | None = None,
    ) -> None:
        self.destination = Path(destination)
        self.client = RendezvousClient(rendezvous_url)
        self.device_name = device_name
        self.cancel_event = cancel_event or threading.Event()
        self.callback = callback or (lambda _event, _data: None)

    def receive_once(self) -> UniversalTransferResult:
        return asyncio.run(self._receive_once())

    async def _receive_once(self) -> UniversalTransferResult:
        from aiortc import RTCPeerConnection, RTCSessionDescription

        auth_secret = generate_auth_secret()
        session = await asyncio.to_thread(self.client.create_room, self.device_name)
        code = connection_code(session.room_id, auth_secret)
        self.callback("room_ready", {
            "code": code,
            "minutes": max(1, (session.expires_at - int(time.time())) // 60),
        })
        pc = RTCPeerConnection(_rtc_configuration(session.ice_servers))
        channel_future: asyncio.Future = asyncio.get_running_loop().create_future()

        @pc.on("datachannel")
        def on_datachannel(channel) -> None:
            if not channel_future.done():
                # Attach the message handler immediately. The sender can transmit
                # as soon as SCTP opens, before the awaiting coroutine resumes.
                channel_future.set_result(_ChannelInbox(channel, self.cancel_event))

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            self.callback("route_state", {"state": pc.connectionState})

        try:
            raw_offer = await asyncio.to_thread(
                self.client.wait_description, session, "offer", self.cancel_event,
                max(1.0, session.expires_at - time.time()),
            )
            offer_sdp = _verify_description(auth_secret, raw_offer, "offer")
            self.callback("connecting", {"host": "ICE/STUN/TURN", "port": 0})
            await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
            await pc.setLocalDescription(await pc.createAnswer())
            await _wait_ice_gathering(pc, self.cancel_event)
            assert pc.localDescription is not None
            answer = _signed_description(auth_secret, "answer", pc.localDescription.sdp)
            await asyncio.to_thread(self.client.put_description, session, "answer", answer)
            inbox = await asyncio.wait_for(channel_future, 45.0)
            try:
                await _wait_channel_open(inbox.channel, self.cancel_event)
            except UniversalTransferError as exc:
                if "Délai dépassé" not in str(exc):
                    raise
                raise UniversalTransferError(
                    _connection_timeout_message(session.ice_servers)
                ) from exc
            self.callback("authenticated", {"peer": "PC distant · canal chiffré"})
            return await _receive_files(
                inbox, self.destination, self.cancel_event, self.callback
            )
        except asyncio.TimeoutError as exc:
            raise UniversalTransferError(
                _connection_timeout_message(session.ice_servers)
            ) from exc
        finally:
            await _close_peer(pc)


class UniversalSender:
    def __init__(
        self,
        connection_code_value: str,
        rendezvous_url: str,
        device_name: str,
        cancel_event: threading.Event | None = None,
        callback: EventCallback | None = None,
    ) -> None:
        self.room_id, self.auth_secret = parse_connection_code(connection_code_value)
        self.client = RendezvousClient(rendezvous_url)
        self.device_name = device_name
        self.cancel_event = cancel_event or threading.Event()
        self.callback = callback or (lambda _event, _data: None)

    def send_files(self, paths: Iterable[Path]) -> UniversalTransferResult:
        selected = list(paths)
        return asyncio.run(self._send_files(selected))

    async def _send_files(self, paths: list[Path]) -> UniversalTransferResult:
        from aiortc import RTCPeerConnection, RTCSessionDescription

        session = await asyncio.to_thread(self.client.join_room, self.room_id, self.device_name)
        pc = RTCPeerConnection(_rtc_configuration(session.ice_servers))
        channel = pc.createDataChannel("transferdesk-files", ordered=True)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            self.callback("route_state", {"state": pc.connectionState})

        try:
            self.callback("connecting", {"host": "ICE/STUN/TURN", "port": 0})
            await pc.setLocalDescription(await pc.createOffer())
            await _wait_ice_gathering(pc, self.cancel_event)
            assert pc.localDescription is not None
            offer = _signed_description(self.auth_secret, "offer", pc.localDescription.sdp)
            await asyncio.to_thread(self.client.put_description, session, "offer", offer)
            raw_answer = await asyncio.to_thread(
                self.client.wait_description, session, "answer", self.cancel_event, 60.0
            )
            answer_sdp = _verify_description(self.auth_secret, raw_answer, "answer")
            await pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))
            try:
                await _wait_channel_open(channel, self.cancel_event)
            except UniversalTransferError as exc:
                if "Délai dépassé" not in str(exc):
                    raise
                raise UniversalTransferError(
                    _connection_timeout_message(session.ice_servers)
                ) from exc
            self.callback("authenticated", {"peer": "PC distant · canal chiffré"})
            return await _send_files(channel, paths, self.cancel_event, self.callback)
        finally:
            await _close_peer(pc)
