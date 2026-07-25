"""Authenticated file transfers between UltraPro instances on a local network.

The protocol deliberately uses only Python's standard library so it also works
in the packaged Windows application.  A short-lived pairing code authenticates
the peers, while signed metadata and SHA-256 digests protect file integrity.
Payload bytes are not encrypted in protocol version 1; it is intended for a
trusted private LAN.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import ssl
import struct
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import xml.sax.saxutils as saxutils
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable


PROTOCOL_VERSION = 1
DEFAULT_PORT = 48721
DISCOVERY_PORT = 48720
DISCOVERY_MAGIC = "ULTRAPRO_DISCOVERY_V1"
INTERNET_INVITATION_PREFIX = "ULTRAPRO1-"
NETWORK_CHUNK_SIZE = 1024 * 1024
MAX_CONTROL_FRAME = 1024 * 1024
MAX_DATAGRAM_SIZE = 64 * 1024
PBKDF2_ROUNDS = 120_000
RESUME_PART_SUFFIX = ".ultrapro-part"
EventCallback = Callable[[str, dict], None]


class NetworkTransferError(Exception):
    """A network or protocol error suitable for display to the user."""


class NetworkTransferCancelled(Exception):
    """The user intentionally cancelled the network operation."""


@dataclass(frozen=True)
class NetworkFile:
    path: Path
    name: str
    relative_path: str
    size: int
    digest: str


@dataclass
class NetworkTransferResult:
    files: int = 0
    bytes_transferred: int = 0
    paths: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class DiscoveredDevice:
    device_id: str
    name: str
    host: str
    discovery_port: int = DISCOVERY_PORT
    ready: bool = False


@dataclass(frozen=True)
class TransferInvitation:
    host: str
    port: int
    pairing_code: str


@dataclass(frozen=True)
class InternetInvitation:
    host: str
    port: int
    pairing_code: str
    tls_fingerprint: str
    expires_at: int
    device_name: str = "UltraPro PC"

    def encode(self) -> str:
        payload = json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "h": self.host,
                "p": self.port,
                "c": self.pairing_code,
                "f": self.tls_fingerprint,
                "e": self.expires_at,
                "n": self.device_name,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(zlib.compress(payload, level=9)).decode("ascii")
        return INTERNET_INVITATION_PREFIX + encoded.rstrip("=")

    @classmethod
    def decode(cls, raw_value: str, now: int | None = None) -> InternetInvitation:
        compact = "".join(raw_value.split())
        if not compact.startswith(INTERNET_INVITATION_PREFIX):
            raise NetworkTransferError("Invalid UltraPro Internet invitation.")
        encoded = compact.removeprefix(INTERNET_INVITATION_PREFIX)
        if not encoded or len(encoded) > 4096:
            raise NetworkTransferError("Invalid UltraPro Internet invitation.")
        encoded += "=" * (-len(encoded) % 4)
        try:
            compressed = base64.urlsafe_b64decode(encoded.encode("ascii"))
            decompressor = zlib.decompressobj()
            payload = decompressor.decompress(compressed, 8192)
            if decompressor.unconsumed_tail or not decompressor.eof:
                raise ValueError("Invitation payload is too large or incomplete.")
            data = json.loads(payload.decode("utf-8"))
            host = str(data["h"]).strip()
            port = int(data["p"])
            code = _validate_pairing_code(str(data["c"]))
            fingerprint = str(data["f"]).lower()
            expires_at = int(data["e"])
            device_name = str(data.get("n", "UltraPro PC"))
        except (
            KeyError, ValueError, TypeError, UnicodeError,
            binascii.Error, zlib.error, json.JSONDecodeError,
        ) as exc:
            raise NetworkTransferError("Invalid UltraPro Internet invitation.") from exc
        if data.get("v") != PROTOCOL_VERSION or not host or not 1 <= port <= 65535:
            raise NetworkTransferError("Invalid UltraPro Internet invitation.")
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise NetworkTransferError("Invalid TLS fingerprint in the invitation.")
        current_time = int(time.time()) if now is None else int(now)
        if expires_at <= current_time:
            raise NetworkTransferError("This UltraPro Internet invitation has expired.")
        return cls(host, port, code, fingerprint, expires_at, device_name)


@dataclass
class TLSIdentity:
    context: ssl.SSLContext
    fingerprint: str
    _temporary_directory: tempfile.TemporaryDirectory[str]

    def close(self) -> None:
        self._temporary_directory.cleanup()


def create_tls_identity(common_name: str = "UltraPro Internet Transfer") -> TLSIdentity:
    """Create a short-lived TLS 1.3 identity pinned by the invitation."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=2))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    temporary_directory = tempfile.TemporaryDirectory(prefix="ultrapro-tls-")
    certificate_path = Path(temporary_directory.name) / "certificate.pem"
    private_key_path = Path(temporary_directory.name) / "private-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private_key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(str(certificate_path), str(private_key_path))
    fingerprint = hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()
    return TLSIdentity(context, fingerprint, temporary_directory)


def generate_pairing_code() -> str:
    """Return a zero-padded, cryptographically random six-digit code."""
    return f"{secrets.randbelow(1_000_000):06d}"


def local_ip_address() -> str:
    """Best-effort LAN address, without sending any network traffic."""
    connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        connection.connect(("192.0.2.1", 9))
        return str(connection.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        connection.close()


def local_ipv4_addresses() -> set[str]:
    """Return IPv4 addresses assigned to this computer for self-discovery filtering."""
    addresses = {"127.0.0.1"}
    for hostname in {socket.gethostname(), socket.getfqdn()}:
        if not hostname:
            continue
        try:
            infos = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_DGRAM)
        except OSError:
            continue
        for _family, _kind, _protocol, _canonical, sockaddr in infos:
            addresses.add(str(sockaddr[0]))
    candidate = local_ip_address()
    try:
        if ipaddress.ip_address(candidate).version == 4:
            addresses.add(candidate)
    except ValueError:
        pass
    return addresses


def _validate_pairing_code(code: str) -> str:
    normalized = code.strip()
    if len(normalized) != 6 or not normalized.isdigit():
        raise NetworkTransferError("The pairing code must contain exactly 6 digits.")
    return normalized


def _derive_key(code: str, client_nonce: bytes, server_nonce: bytes) -> bytes:
    salt = b"UltraPro-LAN-v1" + client_nonce + server_nonce
    return hashlib.pbkdf2_hmac("sha256", code.encode("ascii"), salt, PBKDF2_ROUNDS)


def _proof(key: bytes, label: bytes, client_nonce: bytes, server_nonce: bytes) -> str:
    return hmac.new(key, label + client_nonce + server_nonce, hashlib.sha256).hexdigest()


def _manifest_signature(key: bytes, files: list[dict[str, object]]) -> str:
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, b"manifest\0" + payload, hashlib.sha256).hexdigest()


def _send_control(connection: socket.socket, message: dict[str, object]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_CONTROL_FRAME:
        raise NetworkTransferError("Control message is too large.")
    connection.sendall(struct.pack("!I", len(payload)) + payload)


def _recv_exact(
    connection: socket.socket,
    count: int,
    cancel_event: threading.Event,
) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        if cancel_event.is_set():
            raise NetworkTransferCancelled
        try:
            chunk = connection.recv(min(NETWORK_CHUNK_SIZE, count - len(chunks)))
        except socket.timeout:
            continue
        if not chunk:
            raise NetworkTransferError("The other computer closed the connection.")
        chunks.extend(chunk)
    return bytes(chunks)


def _recv_control(connection: socket.socket, cancel_event: threading.Event) -> dict[str, object]:
    length = struct.unpack("!I", _recv_exact(connection, 4, cancel_event))[0]
    if length <= 0 or length > MAX_CONTROL_FRAME:
        raise NetworkTransferError("Invalid control message length.")
    try:
        value = json.loads(_recv_exact(connection, length, cancel_event).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NetworkTransferError("Invalid control message.") from exc
    if not isinstance(value, dict):
        raise NetworkTransferError("Invalid control message type.")
    return value


def _expect(message: dict[str, object], expected_type: str) -> None:
    if message.get("type") == "error":
        raise NetworkTransferError(str(message.get("message", "Transfer refused.")))
    if message.get("type") != expected_type:
        raise NetworkTransferError(f"Expected {expected_type!r}, received {message.get('type')!r}.")


def _sha256(path: Path, cancel_event: threading.Event) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            if cancel_event.is_set():
                raise NetworkTransferCancelled
            chunk = handle.read(NETWORK_CHUNK_SIZE)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _safe_name(raw_name: object) -> str:
    name = str(raw_name)
    if not name or name in {".", ".."} or Path(name).name != name or "\x00" in name:
        raise NetworkTransferError("A file name in the offer is unsafe.")
    return name


def _safe_relative_path(raw_path: object) -> str:
    value = str(raw_path)
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise NetworkTransferError("A relative path in the offer is unsafe.")
    return path.as_posix()


def _expand_network_paths(paths: Iterable[Path]) -> list[tuple[Path, str]]:
    expanded: list[tuple[Path, str]] = []
    seen_paths: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file():
            candidates = [(path, _safe_name(path.name))]
        elif path.is_dir():
            root_name = _safe_name(path.name)
            candidates = [
                (
                    child,
                    PurePosixPath(root_name, *child.relative_to(path).parts).as_posix(),
                )
                for child in sorted(path.rglob("*"), key=lambda item: str(item).casefold())
                if child.is_file() and not child.is_symlink()
            ]
        else:
            raise NetworkTransferError(f"File or folder not found: {path}")
        for source, relative_path in candidates:
            relative_path = _safe_relative_path(relative_path)
            key = relative_path.casefold()
            if key in seen_paths:
                raise NetworkTransferError(
                    f"Two selected items produce the same path: {relative_path}"
                )
            seen_paths.add(key)
            expanded.append((source, relative_path))
            if len(expanded) > 10_000:
                raise NetworkTransferError("The selection contains too many files.")
    if not expanded:
        raise NetworkTransferError("Select at least one non-empty file or folder.")
    return expanded


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


def _partial_path_for(target: Path) -> Path:
    return target.with_name(f".{target.name}{RESUME_PART_SUFFIX}")


class LocalTransferClient:
    """Send a batch of files to one listening UltraPro instance."""

    def __init__(
        self,
        host: str,
        port: int,
        pairing_code: str,
        cancel_event: threading.Event | None = None,
        callback: EventCallback | None = None,
        timeout: float = 20.0,
        tls_fingerprint: str | None = None,
    ) -> None:
        self.host = host.strip()
        self.port = int(port)
        self.pairing_code = _validate_pairing_code(pairing_code)
        self.cancel_event = cancel_event or threading.Event()
        self.callback = callback or (lambda _event, _data: None)
        self.timeout = timeout
        self.tls_fingerprint = tls_fingerprint.lower() if tls_fingerprint else None
        if self.tls_fingerprint is not None and (
            len(self.tls_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in self.tls_fingerprint)
        ):
            raise NetworkTransferError("Invalid TLS fingerprint.")

    def emit(self, event: str, **data: object) -> None:
        self.callback(event, data)

    def _prepare_files(self, paths: Iterable[Path]) -> list[NetworkFile]:
        files: list[NetworkFile] = []
        for path, relative_path in _expand_network_paths(paths):
            if self.cancel_event.is_set():
                raise NetworkTransferCancelled
            name = _safe_name(path.name)
            size = path.stat().st_size
            self.emit("hashing", name=relative_path, size=size)
            files.append(NetworkFile(
                path, name, relative_path, size, _sha256(path, self.cancel_event)
            ))
        return files

    def send_files(self, paths: Iterable[Path]) -> NetworkTransferResult:
        files = self._prepare_files(paths)
        total_bytes = sum(item.size for item in files)
        result = NetworkTransferResult()
        client_nonce = secrets.token_bytes(16)
        self.emit("connecting", host=self.host, port=self.port)
        try:
            raw_connection = socket.create_connection((self.host, self.port), timeout=self.timeout)
            try:
                connection: socket.socket = raw_connection
                if self.tls_fingerprint is not None:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    context.minimum_version = ssl.TLSVersion.TLSv1_3
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                    connection = context.wrap_socket(raw_connection, server_hostname=None)
                    certificate = connection.getpeercert(binary_form=True)  # type: ignore[attr-defined]
                    actual_fingerprint = hashlib.sha256(certificate).hexdigest()
                    if not hmac.compare_digest(actual_fingerprint, self.tls_fingerprint):
                        raise NetworkTransferError("The TLS identity does not match the invitation.")
            except BaseException:
                raw_connection.close()
                raise
            with connection:
                connection.settimeout(0.5)
                _send_control(connection, {
                    "type": "hello", "version": PROTOCOL_VERSION,
                    "client_nonce": client_nonce.hex(),
                })
                challenge = _recv_control(connection, self.cancel_event)
                _expect(challenge, "challenge")
                features = challenge.get("features", [])
                uses_relative_paths = any(
                    item.relative_path != item.name for item in files
                )
                if uses_relative_paths and (
                    not isinstance(features, list) or "relative_paths" not in features
                ):
                    raise NetworkTransferError(
                        "The receiver version does not support complete folders."
                    )
                try:
                    server_nonce = bytes.fromhex(str(challenge["server_nonce"]))
                except (KeyError, ValueError) as exc:
                    raise NetworkTransferError("Invalid authentication challenge.") from exc
                key = _derive_key(self.pairing_code, client_nonce, server_nonce)
                _send_control(connection, {
                    "type": "auth",
                    "proof": _proof(key, b"client", client_nonce, server_nonce),
                })
                authenticated = _recv_control(connection, self.cancel_event)
                _expect(authenticated, "auth_ok")
                expected_server_proof = _proof(key, b"server", client_nonce, server_nonce)
                if not hmac.compare_digest(str(authenticated.get("proof", "")), expected_server_proof):
                    raise NetworkTransferError("The receiver could not be authenticated.")

                manifest = [
                    {
                        "name": item.name,
                        "relative_path": item.relative_path,
                        "size": item.size,
                        "sha256": item.digest,
                    }
                    for item in files
                ]
                _send_control(connection, {
                    "type": "offer", "files": manifest,
                    "signature": _manifest_signature(key, manifest),
                })
                ready = _recv_control(connection, self.cancel_event)
                _expect(ready, "ready")
                raw_offsets = ready.get("offsets", [])
                offsets: list[int] = []
                if isinstance(raw_offsets, list) and len(raw_offsets) == len(files):
                    for raw_offset, item in zip(raw_offsets, files):
                        try:
                            offset = int(raw_offset)
                        except (TypeError, ValueError):
                            offset = 0
                        offsets.append(max(0, min(offset, item.size)))
                else:
                    offsets = [0 for _item in files]
                completed = sum(offsets)
                started_at = time.monotonic()
                for index, item in enumerate(files, start=1):
                    offset = offsets[index - 1]
                    _send_control(connection, {
                        "type": "file",
                        "index": index - 1,
                        "offset": offset,
                    })
                    with item.path.open("rb") as handle:
                        handle.seek(offset)
                        while True:
                            if self.cancel_event.is_set():
                                raise NetworkTransferCancelled
                            chunk = handle.read(NETWORK_CHUNK_SIZE)
                            if not chunk:
                                break
                            connection.sendall(chunk)
                            completed += len(chunk)
                            elapsed = max(time.monotonic() - started_at, 0.001)
                            self.emit(
                                "progress", completed=completed, total=total_bytes,
                                speed=completed / elapsed, file_index=index,
                                total_files=len(files), name=item.relative_path,
                            )
                    reply = _recv_control(connection, self.cancel_event)
                    _expect(reply, "file_ok")
                    result.files += 1
                    result.bytes_transferred += item.size
                _send_control(connection, {"type": "complete"})
                _expect(_recv_control(connection, self.cancel_event), "complete_ok")
                self.emit("complete", files=result.files, total=result.bytes_transferred)
                return result
        except NetworkTransferCancelled:
            self.emit("cancelled")
            raise
        except (OSError, KeyError, ValueError) as exc:
            error = NetworkTransferError(str(exc))
            self.emit("error", message=str(error))
            raise error from exc


class LocalTransferServer:
    """Receive one authenticated batch, then stop listening."""

    def __init__(
        self,
        destination: Path,
        pairing_code: str,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        cancel_event: threading.Event | None = None,
        callback: EventCallback | None = None,
        timeout: float = 20.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.destination = Path(destination)
        self.pairing_code = _validate_pairing_code(pairing_code)
        self.host = host
        self.port = int(port)
        self.cancel_event = cancel_event or threading.Event()
        self.callback = callback or (lambda _event, _data: None)
        self.timeout = timeout
        self.ssl_context = ssl_context
        self.ready_event = threading.Event()
        self.bound_port: int | None = None
        self._listener: socket.socket | None = None

    def emit(self, event: str, **data: object) -> None:
        self.callback(event, data)

    def close(self) -> None:
        self.cancel_event.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass

    def _authenticate(self, connection: socket.socket) -> bytes:
        hello = _recv_control(connection, self.cancel_event)
        _expect(hello, "hello")
        if hello.get("version") != PROTOCOL_VERSION:
            _send_control(connection, {"type": "error", "message": "Incompatible protocol version."})
            raise NetworkTransferError("Incompatible protocol version.")
        try:
            client_nonce = bytes.fromhex(str(hello["client_nonce"]))
        except (KeyError, ValueError) as exc:
            raise NetworkTransferError("Invalid client nonce.") from exc
        if len(client_nonce) != 16:
            raise NetworkTransferError("Invalid client nonce.")
        server_nonce = secrets.token_bytes(16)
        _send_control(connection, {
            "type": "challenge",
            "server_nonce": server_nonce.hex(),
            "features": ["relative_paths"],
        })
        authentication = _recv_control(connection, self.cancel_event)
        _expect(authentication, "auth")
        key = _derive_key(self.pairing_code, client_nonce, server_nonce)
        expected = _proof(key, b"client", client_nonce, server_nonce)
        if not hmac.compare_digest(str(authentication.get("proof", "")), expected):
            _send_control(connection, {"type": "error", "message": "Incorrect pairing code."})
            raise NetworkTransferError("Incorrect pairing code.")
        _send_control(connection, {
            "type": "auth_ok",
            "proof": _proof(key, b"server", client_nonce, server_nonce),
        })
        return key

    def _receive_batch(self, connection: socket.socket, peer: str) -> NetworkTransferResult:
        key = self._authenticate(connection)
        self.emit("authenticated", peer=peer)
        offer = _recv_control(connection, self.cancel_event)
        _expect(offer, "offer")
        raw_files = offer.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise NetworkTransferError("The file offer is empty or invalid.")
        if len(raw_files) > 10_000:
            raise NetworkTransferError("The file offer contains too many files.")
        if not hmac.compare_digest(
            str(offer.get("signature", "")), _manifest_signature(key, raw_files)
        ):
            raise NetworkTransferError("The signed file offer is invalid.")

        manifest: list[tuple[str, str, int, str]] = []
        seen_paths: set[str] = set()
        for raw in raw_files:
            if not isinstance(raw, dict):
                raise NetworkTransferError("Invalid file entry.")
            name = _safe_name(raw.get("name"))
            relative_path = _safe_relative_path(raw.get("relative_path", name))
            if PurePosixPath(relative_path).name != name:
                raise NetworkTransferError(f"Invalid relative path for {name}.")
            key = relative_path.casefold()
            if key in seen_paths:
                raise NetworkTransferError(f"Duplicate destination path: {relative_path}.")
            seen_paths.add(key)
            size = int(raw.get("size", -1))
            digest = str(raw.get("sha256", ""))
            if size < 0 or len(digest) != 64:
                raise NetworkTransferError(f"Invalid metadata for {name}.")
            manifest.append((name, relative_path, size, digest))
        self.destination.mkdir(parents=True, exist_ok=True)
        if not self.destination.is_dir():
            raise NetworkTransferError("The reception destination is not a folder.")
        destination_root = self.destination.resolve()
        receive_plan: list[tuple[str, str, int, str, Path, Path, int]] = []
        for name, relative_path, size, expected_digest in manifest:
            relative = PurePosixPath(relative_path)
            target_directory = self.destination.joinpath(*relative.parts[:-1])
            target_directory.mkdir(parents=True, exist_ok=True)
            try:
                target_directory.resolve().relative_to(destination_root)
            except ValueError as exc:
                raise NetworkTransferError(
                    f"The destination path for {relative_path} leaves the reception folder."
                ) from exc
            target = _available_path(target_directory, name)
            partial = _partial_path_for(target)
            try:
                offset = partial.stat().st_size
            except OSError:
                offset = 0
            if offset < 0 or offset > size:
                try:
                    partial.unlink()
                except OSError:
                    pass
                offset = 0
            receive_plan.append((
                name, relative_path, size, expected_digest, target, partial, offset
            ))
        total_bytes = sum(size for _name, _relative, size, _digest in manifest)
        resume_bytes = sum(offset for *_prefix, offset in receive_plan)
        self.emit("offer", peer=peer, files=len(manifest), total=total_bytes)
        if resume_bytes:
            self.emit("resuming", completed=resume_bytes, total=total_bytes)
        _send_control(connection, {
            "type": "ready",
            "offsets": [offset for *_prefix, offset in receive_plan],
        })

        result = NetworkTransferResult()
        completed = resume_bytes
        started_at = time.monotonic()
        for expected_index, (
            name, relative_path, size, expected_digest, target, partial, offset
        ) in enumerate(receive_plan):
            header = _recv_control(connection, self.cancel_event)
            _expect(header, "file")
            if int(header.get("index", -1)) != expected_index:
                raise NetworkTransferError("Files arrived in an unexpected order.")
            try:
                requested_offset = int(header.get("offset", 0))
            except (TypeError, ValueError):
                requested_offset = 0
            if requested_offset != offset:
                raise NetworkTransferError("Resume offset mismatch.")
            digest = hashlib.sha256()
            received = offset
            try:
                if offset:
                    with partial.open("rb") as existing:
                        while True:
                            if self.cancel_event.is_set():
                                raise NetworkTransferCancelled
                            chunk = existing.read(NETWORK_CHUNK_SIZE)
                            if not chunk:
                                break
                            digest.update(chunk)
                with partial.open("ab" if offset else "wb") as handle:
                    while received < size:
                        if self.cancel_event.is_set():
                            raise NetworkTransferCancelled
                        try:
                            chunk = connection.recv(min(NETWORK_CHUNK_SIZE, size - received))
                        except socket.timeout:
                            continue
                        if not chunk:
                            raise NetworkTransferError("Connection lost while receiving a file.")
                        handle.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        completed += len(chunk)
                        elapsed = max(time.monotonic() - started_at, 0.001)
                        self.emit(
                            "progress", completed=completed, total=total_bytes,
                            speed=completed / elapsed, file_index=expected_index + 1,
                            total_files=len(manifest), name=relative_path,
                        )
                    handle.flush()
                    os.fsync(handle.fileno())
                if not hmac.compare_digest(digest.hexdigest(), expected_digest):
                    try:
                        partial.unlink()
                    except OSError:
                        pass
                    raise NetworkTransferError(f"Integrity verification failed for {name}.")
                os.replace(partial, target)
                result.files += 1
                result.bytes_transferred += size
                result.paths.append(target)
                _send_control(connection, {"type": "file_ok", "index": expected_index})
                self.emit("file_received", name=target.name, path=str(target))
            except BaseException:
                raise

        complete = _recv_control(connection, self.cancel_event)
        _expect(complete, "complete")
        _send_control(connection, {"type": "complete_ok"})
        self.emit("complete", files=result.files, total=result.bytes_transferred)
        return result

    def serve_once(self) -> NetworkTransferResult:
        if self.destination.exists() and not self.destination.is_dir():
            raise NetworkTransferError("The reception destination is not a folder.")
        try:
            family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as listener:
                self._listener = listener
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((self.host, self.port))
                listener.listen(1)
                listener.settimeout(0.5)
                self.bound_port = int(listener.getsockname()[1])
                self.ready_event.set()
                self.emit("listening", host=local_ip_address(), port=self.bound_port)
                while True:
                    if self.cancel_event.is_set():
                        raise NetworkTransferCancelled
                    try:
                        connection, address = listener.accept()
                        break
                    except socket.timeout:
                        continue
                try:
                    connection.settimeout(self.timeout)
                    if self.ssl_context is not None:
                        connection = self.ssl_context.wrap_socket(connection, server_side=True)
                    with connection:
                        connection.settimeout(0.5)
                        return self._receive_batch(connection, str(address[0]))
                except BaseException:
                    connection.close()
                    raise
        except NetworkTransferCancelled:
            self.emit("cancelled")
            raise
        except (OSError, ValueError) as exc:
            if self.cancel_event.is_set():
                self.emit("cancelled")
                raise NetworkTransferCancelled from exc
            error = NetworkTransferError(str(exc))
            self.emit("error", message=str(error))
            raise error from exc
        finally:
            self.ready_event.set()
            self._listener = None


def _decode_datagram(payload: bytes) -> dict[str, object] | None:
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(message, dict) or message.get("magic") != DISCOVERY_MAGIC:
        return None
    if message.get("version") != PROTOCOL_VERSION:
        return None
    return message


def _datagram(kind: str, **values: object) -> bytes:
    message = {
        "magic": DISCOVERY_MAGIC,
        "version": PROTOCOL_VERSION,
        "kind": kind,
        **values,
    }
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_DATAGRAM_SIZE:
        raise NetworkTransferError("Discovery message is too large.")
    return payload


class DiscoveryService:
    """Advertise one running UltraPro instance and receive transfer requests."""

    def __init__(
        self,
        callback: EventCallback | None = None,
        port: int = DISCOVERY_PORT,
        device_name: str | None = None,
        instance_id: str | None = None,
    ) -> None:
        self.callback = callback or (lambda _event, _data: None)
        self.port = int(port)
        self.device_name = (device_name or socket.gethostname()).strip() or "UltraPro PC"
        self.instance_id = instance_id or secrets.token_hex(16)
        self.bound_port: int | None = None
        self.ready_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._state_lock = threading.Lock()
        self._ready_to_receive = False
        self._pending_requests: dict[str, tuple[str, int]] = {}
        self._seen_requests: set[str] = set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.ready_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="UltraProDiscovery")
        self._thread.start()
        self.ready_event.wait(2.0)

    def stop(self) -> None:
        self._stop_event.set()
        port = self.bound_port
        if port is not None:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wakeup:
                    wakeup.sendto(_datagram("stop"), ("127.0.0.1", port))
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def set_ready(self, ready: bool) -> None:
        with self._state_lock:
            self._ready_to_receive = bool(ready)

    def emit(self, event: str, **data: object) -> None:
        self.callback(event, data)

    def _device_response(self) -> bytes:
        with self._state_lock:
            ready = self._ready_to_receive
        return _datagram(
            "device",
            device_id=self.instance_id,
            name=self.device_name,
            ready=ready,
            discovery_port=self.bound_port or self.port,
        )

    def _handle_request(self, message: dict[str, object], address: tuple[str, int]) -> None:
        if message.get("target_id") != self.instance_id:
            return
        request_id = str(message.get("request_id", ""))
        try:
            reply_port = int(message.get("reply_port", 0))
            file_count = max(0, int(message.get("files", 0)))
            total = max(0, int(message.get("total", 0)))
        except (TypeError, ValueError):
            return
        if not request_id or not 1 <= reply_port <= 65535:
            return
        with self._state_lock:
            self._pending_requests[request_id] = (address[0], reply_port)
            if request_id in self._seen_requests:
                return
            self._seen_requests.add(request_id)
        names = message.get("names", [])
        if not isinstance(names, list):
            names = []
        self.emit(
            "transfer_request",
            request_id=request_id,
            sender_id=str(message.get("sender_id", "")),
            sender_name=str(message.get("sender_name", address[0])),
            source_ip=address[0],
            files=file_count,
            total=total,
            names=[str(name) for name in names[:8]],
        )

    def respond_to_request(
        self,
        request_id: str,
        accepted: bool,
        transfer_port: int = 0,
        pairing_code: str = "",
        message: str = "",
    ) -> None:
        with self._state_lock:
            target = self._pending_requests.pop(request_id, None)
        if target is None:
            return
        if accepted:
            _validate_pairing_code(pairing_code)
            if not 1 <= int(transfer_port) <= 65535:
                raise NetworkTransferError("Invalid transfer port.")
        payload = _datagram(
            "request_response",
            device_id=self.instance_id,
            request_id=request_id,
            accepted=bool(accepted),
            host=local_ip_address(),
            port=int(transfer_port),
            pairing_code=pairing_code if accepted else "",
            message=message,
        )
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as response_socket:
                for _ in range(3):
                    response_socket.sendto(payload, target)
        except OSError as exc:
            self.emit("discovery_error", message=str(exc))

    def _run(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
                self._socket = listener
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                listener.bind(("", self.port))
                listener.settimeout(0.5)
                self.bound_port = int(listener.getsockname()[1])
                self.ready_event.set()
                while not self._stop_event.is_set():
                    try:
                        payload, address = listener.recvfrom(MAX_DATAGRAM_SIZE)
                    except socket.timeout:
                        continue
                    message = _decode_datagram(payload)
                    if message is None:
                        continue
                    kind = message.get("kind")
                    if kind == "discover" and message.get("sender_id") != self.instance_id:
                        listener.sendto(self._device_response(), address)
                    elif kind == "request":
                        self._handle_request(message, address)
        except OSError as exc:
            if not self._stop_event.is_set():
                self.emit("discovery_error", message=str(exc))
        finally:
            self._socket = None
            self.ready_event.set()


def discover_devices(
    sender_id: str,
    port: int = DISCOVERY_PORT,
    duration: float = 1.2,
    targets: Iterable[str] | None = None,
    cancel_event: threading.Event | None = None,
    exclude_local: bool = True,
) -> list[DiscoveredDevice]:
    """Broadcast a probe and collect running UltraPro instances."""
    cancel = cancel_event or threading.Event()
    addresses = list(targets or ("255.255.255.255", "127.0.0.1"))
    probe = _datagram("discover", sender_id=sender_id)
    devices: dict[str, DiscoveredDevice] = {}
    local_addresses = local_ipv4_addresses() if exclude_local else set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as scanner:
        scanner.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        scanner.bind(("", 0))
        scanner.settimeout(0.15)
        sent = False
        for target in addresses:
            try:
                scanner.sendto(probe, (target, int(port)))
                sent = True
            except OSError:
                continue
        if not sent:
            raise NetworkTransferError("Could not broadcast the discovery request.")
        deadline = time.monotonic() + max(0.1, duration)
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise NetworkTransferCancelled
            try:
                payload, address = scanner.recvfrom(MAX_DATAGRAM_SIZE)
            except socket.timeout:
                continue
            message = _decode_datagram(payload)
            if message is None or message.get("kind") != "device":
                continue
            device_id = str(message.get("device_id", ""))
            name = str(message.get("name", "")).strip()
            if not device_id or device_id == sender_id or not name:
                continue
            if address[0] in local_addresses:
                continue
            try:
                discovery_port = int(message.get("discovery_port", port))
            except (TypeError, ValueError):
                continue
            if not 1 <= discovery_port <= 65535:
                continue
            devices[device_id] = DiscoveredDevice(
                device_id=device_id,
                name=name,
                host=address[0],
                discovery_port=discovery_port,
                ready=bool(message.get("ready", False)),
            )
    return sorted(devices.values(), key=lambda item: (item.name.casefold(), item.host))


def request_transfer(
    device: DiscoveredDevice,
    sender_id: str,
    sender_name: str,
    files: Iterable[Path],
    cancel_event: threading.Event | None = None,
    timeout: float = 45.0,
) -> TransferInvitation:
    """Ask a discovered peer for permission and wait for connection details."""
    cancel = cancel_event or threading.Event()
    paths = [Path(path) for path in files]
    expanded = _expand_network_paths(paths)
    request_id = secrets.token_hex(16)
    total = sum(path.stat().st_size for path, _relative in expanded)
    payload = _datagram(
        "request",
        target_id=device.device_id,
        request_id=request_id,
        sender_id=sender_id,
        sender_name=sender_name,
        files=len(expanded),
        total=total,
        names=[relative for _path, relative in expanded[:8]],
        reply_port=0,
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as requester:
        requester.bind(("", 0))
        requester.settimeout(0.2)
        reply_port = int(requester.getsockname()[1])
        message = json.loads(payload.decode("utf-8"))
        message["reply_port"] = reply_port
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        deadline = time.monotonic() + max(1.0, timeout)
        next_send = 0.0
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise NetworkTransferCancelled
            now = time.monotonic()
            if now >= next_send:
                requester.sendto(payload, (device.host, device.discovery_port))
                next_send = now + 2.0
            try:
                response_payload, response_address = requester.recvfrom(MAX_DATAGRAM_SIZE)
            except socket.timeout:
                continue
            response = _decode_datagram(response_payload)
            if response is None or response.get("kind") != "request_response":
                continue
            if response.get("request_id") != request_id or response.get("device_id") != device.device_id:
                continue
            if not response.get("accepted"):
                raise NetworkTransferError(str(response.get("message") or "Transfer refused."))
            host = str(response_address[0] or device.host)
            port_value = int(response.get("port", 0))
            code = _validate_pairing_code(str(response.get("pairing_code", "")))
            if not 1 <= port_value <= 65535:
                raise NetworkTransferError("The receiver returned an invalid port.")
            return TransferInvitation(host=host, port=port_value, pairing_code=code)
    raise NetworkTransferError("The other computer did not answer the transfer request.")


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _discover_upnp_service(timeout: float = 2.5) -> tuple[str, str]:
    locations: list[str] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as scanner:
        scanner.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        scanner.settimeout(0.2)
        for search_target in (
            "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
            "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
            "ssdp:all",
        ):
            request = (
                "M-SEARCH * HTTP/1.1\r\n"
                "HOST: 239.255.255.250:1900\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                f"ST: {search_target}\r\n\r\n"
            ).encode("ascii")
            scanner.sendto(request, ("239.255.255.250", 1900))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response, _address = scanner.recvfrom(MAX_DATAGRAM_SIZE)
            except socket.timeout:
                continue
            headers: dict[str, str] = {}
            for line in response.decode("iso-8859-1", errors="ignore").split("\r\n")[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().lower()] = value.strip()
            location = headers.get("location")
            if location and location not in locations:
                locations.append(location)
    for location in locations:
        try:
            with urllib.request.urlopen(location, timeout=3.0) as response:
                root = ET.fromstring(response.read())
        except (OSError, ET.ParseError):
            continue
        for service in root.iter():
            if _xml_local_name(service.tag) != "service":
                continue
            values = {
                _xml_local_name(child.tag): (child.text or "").strip()
                for child in service
            }
            service_type = values.get("serviceType", "")
            if "WANIPConnection" not in service_type and "WANPPPConnection" not in service_type:
                continue
            control_url = urllib.parse.urljoin(location, values.get("controlURL", ""))
            if control_url:
                return control_url, service_type
    raise NetworkTransferError("No compatible UPnP Internet gateway was found.")


def _upnp_action(
    control_url: str,
    service_type: str,
    action: str,
    arguments: dict[str, object],
    timeout: float = 4.0,
) -> dict[str, str]:
    argument_xml = "".join(
        f"<{name}>{saxutils.escape(str(value))}</{name}>"
        for name, value in arguments.items()
    )
    envelope = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{service_type}">{argument_xml}'
        f"</u:{action}></s:Body></s:Envelope>"
    ).encode("utf-8")
    request = urllib.request.Request(
        control_url,
        data=envelope,
        method="POST",
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{service_type}#{action}"',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            root = ET.fromstring(response.read())
    except (OSError, ET.ParseError) as exc:
        raise NetworkTransferError(f"UPnP {action} failed: {exc}") from exc
    return {
        _xml_local_name(element.tag): (element.text or "").strip()
        for element in root.iter()
        if len(element) == 0
    }


@dataclass
class UPnPPortMapping:
    control_url: str
    service_type: str
    external_ip: str
    external_port: int
    internal_port: int

    def close(self) -> None:
        try:
            _upnp_action(
                self.control_url,
                self.service_type,
                "DeletePortMapping",
                {
                    "NewRemoteHost": "",
                    "NewExternalPort": self.external_port,
                    "NewProtocol": "TCP",
                },
            )
        except NetworkTransferError:
            pass


def create_upnp_mapping(
    internal_port: int,
    external_port: int | None = None,
    lease_seconds: int = 1800,
) -> UPnPPortMapping:
    """Open one temporary TCP port and return the router's public endpoint."""
    internal_port = int(internal_port)
    external_port = int(external_port or internal_port)
    if not 1 <= internal_port <= 65535 or not 1 <= external_port <= 65535:
        raise NetworkTransferError("Invalid port for UPnP mapping.")
    control_url, service_type = _discover_upnp_service()
    external = _upnp_action(control_url, service_type, "GetExternalIPAddress", {})
    external_ip = external.get("NewExternalIPAddress", "")
    try:
        public_address = ipaddress.ip_address(external_ip)
    except ValueError as exc:
        raise NetworkTransferError("The router did not report a valid public address.") from exc
    if not public_address.is_global:
        raise NetworkTransferError(
            "The router is behind CGNAT or another private router; direct Internet reception is unavailable."
        )
    _upnp_action(
        control_url,
        service_type,
        "AddPortMapping",
        {
            "NewRemoteHost": "",
            "NewExternalPort": external_port,
            "NewProtocol": "TCP",
            "NewInternalPort": internal_port,
            "NewInternalClient": local_ip_address(),
            "NewEnabled": 1,
            "NewPortMappingDescription": "UltraPro Internet Transfer",
            "NewLeaseDuration": max(60, int(lease_seconds)),
        },
    )
    return UPnPPortMapping(
        control_url, service_type, str(public_address), external_port, internal_port
    )


NETWORK_TEXT = {
    "fr": {
        "button": "Transfert PC",
        "title": "Transfert entre PC",
        "send": "Envoyer",
        "receive": "Recevoir",
        "destination": "Dossier de réception",
        "choose": "Choisir…",
        "port": "Port",
        "code": "Code d’appairage",
        "address": "Adresse du PC destinataire",
        "files": "Fichiers à envoyer",
        "select_files": "Sélectionner les fichiers…",
        "no_files": "Aucun fichier sélectionné",
        "start_receive": "Démarrer la réception",
        "start_send": "Envoyer les fichiers",
        "cancel": "Annuler",
        "ready": "Prêt",
        "pick_destination": "Choisis un dossier de réception.",
        "pick_files": "Choisis au moins un fichier.",
        "invalid_port": "Le port doit être compris entre 1 et 65535.",
        "listening": "En attente sur {host}:{port} — code {code}",
        "connected": "PC authentifié : {peer}",
        "offer": "Réception de {files} fichier(s), {size}",
        "connecting": "Connexion à {host}:{port}…",
        "hashing": "Vérification de {name}…",
        "progress": "{percent:.1f}% — {name} — {speed}/s",
        "file_received": "Reçu : {name}",
        "complete": "Terminé : {files} fichier(s), {size}",
        "cancelled": "Transfert annulé.",
        "error": "Erreur réseau : {message}",
        "nearby_pcs": "PC à proximité avec UltraPro ouvert",
        "refresh": "Rechercher",
        "scanning": "Recherche des PC UltraPro sur le réseau…",
        "no_devices": "Aucun autre PC UltraPro détecté.",
        "device_ready": "Prêt à recevoir",
        "device_available": "Disponible",
        "select_device": "Sélectionne un PC détecté ou utilise la connexion manuelle.",
        "manual_connection": "Connexion manuelle (secours)",
        "requesting": "Demande d’autorisation envoyée à {name}…",
        "incoming_title": "Demande de transfert UltraPro",
        "incoming_question": "{sender} veut t’envoyer {files} fichier(s), soit {size}.\n\nAccepter dans :\n{destination} ?",
        "refused": "Le transfert a été refusé sur l’autre PC.",
        "busy": "L’autre PC effectue déjà un transfert.",
        "discovery_error": "Découverte réseau indisponible : {message}",
        "internet": "Internet P2P",
        "internet_intro": "Entre deux maisons : UltraPro cherche une connexion directe, puis utilise un relais sécurisé seulement si le réseau l’impose.",
        "rendezvous_service": "Service de connexion",
        "rendezvous_help": "Ce service échange seulement les informations de connexion. Les fichiers restent chiffrés de bout en bout.",
        "missing_rendezvous": "Configure d’abord l’adresse du service de connexion UltraPro.",
        "create_universal_code": "Recevoir : créer un code temporaire",
        "universal_code_share": "Code à partager",
        "paste_universal_code": "Code reçu",
        "creating_universal_code": "Création du code de connexion sécurisé…",
        "universal_code_ready": "Code prêt pour {minutes} minutes. En attente de l’autre PC…",
        "automatic_route": "Recherche du meilleur chemin direct ou relayé…",
        "route_connected": "Chemin Internet établi et chiffré.",
        "route_failed": "Aucun chemin direct ou relayé n’a pu être établi.",
        "public_host": "Adresse publique manuelle",
        "internet_manual_help": "Laisse vide pour ouvrir automatiquement le routeur avec UPnP. Utilise ce champ seulement si tu as déjà redirigé le port.",
        "create_invitation": "Créer une invitation Internet",
        "invitation_share": "Invitation à partager",
        "copy": "Copier",
        "paste_invitation": "Invitation reçue",
        "send_internet": "Envoyer par Internet",
        "upnp_mapping": "Ouverture automatique du routeur et création du canal TLS…",
        "invitation_ready": "Invitation prête pour {minutes} minutes. En attente de la connexion…",
        "private_lan": "Les PC avec UltraPro ouvert apparaissent automatiquement sur ce réseau local privé.",
    },
    "en": {
        "button": "PC transfer", "title": "Transfer between PCs", "send": "Send",
        "receive": "Receive", "destination": "Reception folder", "choose": "Choose…",
        "port": "Port", "code": "Pairing code", "address": "Destination PC address",
        "files": "Files to send", "select_files": "Select files…", "no_files": "No files selected",
        "start_receive": "Start receiving", "start_send": "Send files", "cancel": "Cancel",
        "ready": "Ready", "pick_destination": "Choose a reception folder.",
        "pick_files": "Choose at least one file.", "invalid_port": "Port must be between 1 and 65535.",
        "listening": "Waiting on {host}:{port} — code {code}", "connected": "Authenticated PC: {peer}",
        "offer": "Receiving {files} file(s), {size}", "connecting": "Connecting to {host}:{port}…",
        "hashing": "Checking {name}…", "progress": "{percent:.1f}% — {name} — {speed}/s",
        "file_received": "Received: {name}", "complete": "Complete: {files} file(s), {size}",
        "cancelled": "Transfer cancelled.", "error": "Network error: {message}",
        "nearby_pcs": "Nearby PCs with UltraPro open", "refresh": "Scan",
        "scanning": "Looking for UltraPro PCs on the network…",
        "no_devices": "No other UltraPro PC was found.",
        "device_ready": "Ready to receive", "device_available": "Available",
        "select_device": "Select a discovered PC or use the manual connection.",
        "manual_connection": "Manual connection (fallback)",
        "requesting": "Permission request sent to {name}…",
        "incoming_title": "UltraPro transfer request",
        "incoming_question": "{sender} wants to send {files} file(s), {size}.\n\nAccept into:\n{destination}?",
        "refused": "The transfer was refused on the other PC.",
        "busy": "The other PC is already transferring files.",
        "discovery_error": "Network discovery unavailable: {message}",
        "internet": "Internet P2P",
        "internet_intro": "Between two homes: UltraPro tries a direct route first, then uses a secure relay only when required by the network.",
        "rendezvous_service": "Connection service",
        "rendezvous_help": "This service only exchanges connection details. Files remain end-to-end encrypted.",
        "missing_rendezvous": "Configure the UltraPro connection service address first.",
        "create_universal_code": "Receive: create temporary code",
        "universal_code_share": "Code to share",
        "paste_universal_code": "Received code",
        "creating_universal_code": "Creating the secure connection code…",
        "universal_code_ready": "Code ready for {minutes} minutes. Waiting for the other PC…",
        "automatic_route": "Finding the best direct or relayed route…",
        "route_connected": "Encrypted Internet route established.",
        "route_failed": "No direct or relayed route could be established.",
        "public_host": "Manual public address",
        "internet_manual_help": "Leave empty to open the router automatically with UPnP. Use this only after manually forwarding the port.",
        "create_invitation": "Create Internet invitation",
        "invitation_share": "Invitation to share", "copy": "Copy",
        "paste_invitation": "Received invitation",
        "send_internet": "Send over the Internet",
        "upnp_mapping": "Opening the router and creating the TLS channel…",
        "invitation_ready": "Invitation ready for {minutes} minutes. Waiting for connection…",
        "private_lan": "PCs with UltraPro open appear automatically on this private local network.",
    },
}


def network_text(language: str, key: str, **values: object) -> str:
    messages = NETWORK_TEXT.get(language, NETWORK_TEXT["en"])
    template = messages.get(key, NETWORK_TEXT["en"].get(key, key))
    return template.format(**values)
