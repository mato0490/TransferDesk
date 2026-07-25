# AutoSD File Manager

AutoSD is a Windows and macOS desktop application for sorting, copying, and
transferring files. The current interface uses **PySide6 / Qt Quick**, with a
shared Python engine for removable media, duplicate handling, local network
transfers, and Internet P2P transfers.

> Last documentation update: July 26, 2026.

## Features

- copy files from a source folder to a destination folder;
- filter by file extension, verify copies, and preserve folder trees;
- selectable transfer preview, date filters, and organization by date or type;
- conflict policies: rename, skip, replace, keep newest, or decide file by file;
- verified duplicate detection and controlled deletion;
- saved profiles and operation history;
- text export for transfer history;
- removable media detection and safe eject on Windows and macOS;
- direct device-to-device transfer on the local network;
- Internet P2P transfer with WebRTC, either through a rendezvous service or by
  manual offer/answer exchange, with STUN and optional TURN relay support;
- direct TCP socket P2P transfer with a stable receiver code, without a
  rendezvous service and without WebRTC offer/answer text to copy;
- automatic resume for direct socket transfers after connection loss: partial
  `.autosd-part` files are preserved, the sender resumes from the available
  offset, SHA-256 integrity is checked before finalization, and both sender and
  receiver reconnect continuously until success or cancellation;
- detailed P2P progress, visible completion status, and P2P/local network
  transfers recorded in history;
- Help tab with installed version, persistent PC code, GitHub Releases update
  checks, explicit download/install confirmations, and `AUTOSD_UPDATE_URL` as a
  manual fallback page;
- light and dark interface, available in French, English, and Hebrew with
  right-to-left layout support;
- adaptive window for small displays and high Windows scaling, with scrolling on
  long pages.

## Development Setup

Requirements: Python 3.12 or 3.13.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

Run the application:

```powershell
py autosd_qt.py
```

The UI-independent business engine is in `autosd_core.py`. The legacy Tkinter
client remains available in `auto sd v5.py` for compatibility, but it now uses
the shared engine and is no longer included in the Qt package.

## Tests

```powershell
py -m unittest discover -v
```

Quick network self-test for a packaged build:

```powershell
.\dist\AutoSD-FileManager\AutoSD-FileManager.exe --self-test-network
```

The `.github/workflows/ci.yml` workflow runs Python tests on Windows and macOS,
then builds and checks both PyInstaller packages. The
`.github/workflows/release.yml` workflow publishes GitHub Releases automatically
when a tag matching `vX.Y.Z` is pushed.

## Building The Application

Install build dependencies, then use the provided spec file:

```powershell
py -m pip install -r requirements-build.txt
py -m PyInstaller --noconfirm --clean AutoSD-FileManager.spec
```

On Windows, the result is placed in `dist/AutoSD-FileManager`. For macOS, see
[BUILD-MACOS.md](BUILD-MACOS.md).

The Windows binary automatically receives version metadata from
`autosd_version.py`. The reference build uses a separate `dist-release` folder
to avoid mixing older attempts with the validated deliverable. On macOS, the
spec file first produces an onedir package before embedding it in the `.app`, so
Qt dependencies can be signed and verified separately.

The hook in `pyinstaller_hooks/` limits bundled QML modules to those used by the
interface. This prevents adding Qt WebEngine, 3D, charts, and other unused
modules. The accepted PyInstaller major version is pinned in
`requirements-build.txt`, separately from runtime dependencies. The source icon
is `assets/autosd-icon.svg`; its 1024 px PNG version is used by PyInstaller to
produce OS-specific resources.

## Releases And Updates

The application checks the latest stable GitHub Release from
`mato0490/ultra-pro-files-manager`, ignores prereleases, compares the release
tag with `autosd_version.py`, then selects the asset for the current platform:

- `AutoSD-FileManager-windows-vX.Y.Z.zip`;
- `AutoSD-FileManager-macos-vX.Y.Z.zip`;
- matching SHA-256 files named with the same asset plus `.sha256`.

AutoSD never downloads a release without user confirmation. After the archive
is downloaded, the `.sha256` file must validate before the update becomes ready
to install. AutoSD then asks a second time before starting installation. If the
GitHub API is unavailable or no matching asset exists, the Help tab can open the
release page manually; `AUTOSD_UPDATE_URL` is still accepted as the fallback URL.

On Windows packaged builds, installation starts an external PowerShell updater
that waits for AutoSD to close, replaces the packaged folder, then relaunches
the app. Source-tree runs only open the verified archive location. On macOS,
AutoSD opens the verified archive for the user to complete installation.

## Internet P2P Transfer

The compatible rendezvous service does not carry file bytes. It only exchanges
WebRTC connection information. The secret included in the temporary code stays
between the two devices and authenticates the connection.

AutoSD no longer embeds a public rendezvous address or Cloudflare integration.
To use Internet transfer by code, provide a compatible rendezvous service URL in
`autosd-network.json`:

```json
{
  "rendezvous_url": "https://rendezvous.example.com"
}
```

The `AUTOSD_RENDEZVOUS_URL` environment variable can also provide this address
without rebuilding the application. Without configuration, the field stays empty
and local transfer continues to work normally. Known Cloudflare domains
(`workers.dev`, `pages.dev`, and direct Cloudflare domains) are rejected by the
client.

The selected service may provide its own STUN/TURN servers. Otherwise AutoSD
uses only a public fallback STUN server to attempt a direct connection.

The **Manual connection without a server** section can fully replace the
rendezvous service with two copy/paste steps:

1. On the sending PC, choose the files and click **Create offer**.
2. Copy the displayed offer to the receiving PC by any channel you choose.
3. On the receiver, paste the offer, choose the destination folder, and click
   **Create answer**.
4. Copy that answer back to the sender, then click **Import answer**.

The offer and answer are signed, size-limited, and expire after about
15 minutes. They contain the WebRTC network information needed for the session,
so they should only be shared with the other participant. Files then use the
same encrypted WebRTC channel as the code-based mode. This method removes the
need for a rendezvous service, but not NAT limitations: without a TURN relay,
some networks or CGNAT connections can still prevent a direct connection.

Both devices should use the same recent AutoSD version for code-based WebRTC
transfer. An older installation may use an incompatible P2P protocol version,
even on the same local network. On failure, AutoSD opens and keeps the detailed
error in the **Show error** button in the status bar; this text can be copied
for diagnostics. Connection timeouts also mention when the configured service
does not provide a TURN relay.

## Direct Socket Transfer

The **Direct socket transfer** section in the P2P tab is intended for local or
reachable private networks where one PC can connect directly to the other.

Receiver flow:

1. Choose the destination folder.
2. Start socket reception.
3. Copy the displayed code, for example `123456@192.168.1.20`.

Sender flow:

1. Paste the receiver code.
2. Choose files or folders.
3. Start the socket send.

The six-digit part of the code is stable for the receiving PC. The address is
shown when reception starts. If AutoSD cannot listen on the default port, the
code includes an explicit port such as `123456@192.168.1.20:49152`.

If the connection drops, both sides reconnect automatically while the operation
remains open. The receiver keeps partial `.autosd-part` files, negotiates the
already received offsets on the next connection, and verifies the final SHA-256
before replacing the partial files with completed files. The user should only
see progress pause and continue. Cancelling the operation stops the retry loop.

## Local Network Transfer

Both devices must run AutoSD on the same network. In the **P2P** tab, the
receiver first chooses its destination folder. The sender then searches for
devices, selects the receiver and files, and starts the send. The receiver must
accept the AutoSD prompt; the pairing code is then negotiated automatically.
Discovery excludes all AutoSD instances running on the sender PC, even if they
use a different instance ID.

The sender can select individual files or a complete folder. When a folder is
selected, all files are sent recursively and the folder tree is recreated in the
destination folder. Folder sending requires the current version on the receiver;
individual file sending remains compatible with the older local protocol.

This local mode, reached through **Search** and **Send locally**, should be
preferred over Internet transfer by code when both PCs are on the same network.
The current Qt client and the legacy Tkinter client `auto sd v5.py` in this
repository share the same local module; this guarantee does not extend to an old
installed executable from before the current protocol was added.

## Architecture

| Component | Role |
| --- | --- |
| `autosd_qt.py` | PySide6 entry point and bridge between QML and Python |
| `qml/Main.qml` | Qt Quick interface |
| `autosd_core.py` | copy engine, duplicates, profiles, history, and removable media |
| `auto sd v5.py` | legacy Tkinter interface, kept outside the main package |
| `network_transfer.py` | discovery, local network transfers, and direct socket transfers |
| `webrtc_transfer.py` | rendezvous/manual negotiation and Internet WebRTC transfers |
| `autosd_updater.py` | GitHub Releases lookup, asset download, checksum verification, installer launch |
| `themes_config.py` | palettes and themes for the legacy interface |
| `translations.py` | interface translations |
| `AutoSD-FileManager.spec` | Windows/macOS PyInstaller configuration |
| `pyinstaller_hooks/` | PyInstaller collection limited to used QML modules |

Profiles and history are stored as JSON in the user data directory, not in the
repository.

The application version is defined once in `autosd_version.py`. Local
environments, caches, older build folders, and machine-specific configuration
are excluded by `.gitignore`. `autosd-network.example.json` remains the template
to copy for local network configuration.

## Project Documentation

- [MIGRATION-QT.md](MIGRATION-QT.md): migration from Tkinter to Qt Quick;
- [BUILD-MACOS.md](BUILD-MACOS.md): macOS build and signing;
- [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md): Windows, macOS, and real-device
  network validation before release;
- [CHANGELOG.md](CHANGELOG.md): visible change history.

## Maintenance Rule

Any functional, technical, configuration, installation, or usage change must
update this documentation in the same change. Any visible change must also be
added to the `Non publié` section of `CHANGELOG.md`. This rule is also recorded
in `AGENTS.md` so it is applied during future repository work.
