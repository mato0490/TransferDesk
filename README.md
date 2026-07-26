# TransferDesk

TransferDesk is a Windows and macOS application for copying, sorting,
transferring, and verifying files. It is useful for emptying removable media,
organizing files, finding exact duplicates, and sending files between two
computers.

## Download the Application

Ready-to-use builds are published on GitHub Releases:

<https://github.com/mato0490/transferdesk/releases>

Download the file that matches your system:

| System | File to download |
| --- | --- |
| Windows | `TransferDesk-windows-vX.Y.Z.zip` |
| macOS | `TransferDesk-macos-vX.Y.Z.zip` |

The `.sha256` files are used to verify download integrity. They are also used
automatically by the built-in update system.

## Windows Installation

1. Download `TransferDesk-windows-vX.Y.Z.zip` from the Releases page.
2. Extract the `.zip` file.
3. Open the `TransferDesk` folder.
4. Run `TransferDesk.exe`.

If Windows shows a SmartScreen warning, choose **More info**, then **Run
anyway** if you trust the version downloaded from this repository.

## macOS Installation

1. Download `TransferDesk-macos-vX.Y.Z.zip` from the Releases page.
2. Extract the `.zip` file.
3. Move `TransferDesk.app` to the **Applications** folder.
4. Open the application.

If macOS blocks the app because it is not notarized, open **System Settings >
Privacy & Security**, then choose **Open Anyway** for TransferDesk.

## Updating

In the application:

1. Open the **Help** tab.
2. Click **Check for updates**.
3. If a new version is available, confirm the download.
4. After SHA-256 verification, confirm the installation.

TransferDesk never downloads an update without confirmation and never installs
it without a second confirmation.

On Windows, a packaged version can replace itself automatically after the
application closes. On macOS, TransferDesk opens the verified archive so you can
finish the installation.

## Quick Start

- **File transfer**: choose a source and destination, then run the preview or
  the transfer.
- **Duplicates**: choose a folder to scan, then move or delete only verified
  duplicates.
- **History**: review recent operations and export a report.
- **P2P / local network**: use the P2P tab to send files to another TransferDesk
  computer.
- **Help**: check the installed version, this PC's code, and available updates.

## Optional Network Configuration

Local transfers work without configuration when both computers are on the same
network.

For Internet transfer by code, TransferDesk can use a compatible rendezvous
service. Create `transferdesk-network.json` at the root of the application or
project:

```json
{
  "rendezvous_url": "https://rendezvous.example.com"
}
```

You can also define the `TRANSFERDESK_RENDEZVOUS_URL` environment variable.
Without this configuration, local and manual modes remain available.

## Build from Source

Requirement: Python 3.12 or 3.13.

### Windows

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements-build.txt
py -m unittest discover -v
py -m PyInstaller --noconfirm --clean TransferDesk.spec
```

The package is created in:

```text
dist\TransferDesk
```

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python -m unittest discover -v
python -m PyInstaller --noconfirm --clean TransferDesk.spec
```

The application is created in:

```text
dist/TransferDesk.app
```

For macOS signing details, see [BUILD-MACOS.md](BUILD-MACOS.md).

## Run in Development Mode

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py transferdesk_qt.py
```

Tests:

```powershell
py -m unittest discover -v
```

Network self-test for a Windows package:

```powershell
.\dist\TransferDesk\TransferDesk.exe --self-test-network
```

## Publish a New Version

The application version is defined in `transferdesk_version.py`.

After updating the version and validating the tests, push a tag:

```powershell
git tag vX.Y.Z
git push origin vX.Y.Z
```

GitHub Actions then automatically builds the Windows and macOS packages, creates
the `.sha256` files, and publishes the GitHub Release.

To complete a release that was already created manually, open **Actions >
Release > Run workflow**, enter the existing tag, for example `v8.0.3`, then run
the workflow. GitHub rebuilds the Windows/macOS assets and replaces them in the
release.

## Useful Links

- [BUILD-MACOS.md](BUILD-MACOS.md): macOS build, signing, and validation.
- [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md): pre-release checklist.
- [CHANGELOG.md](CHANGELOG.md): history of visible changes.
- [MIGRATION-QT.md](MIGRATION-QT.md): technical migration notes for Qt.
