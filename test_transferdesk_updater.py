import tempfile
import unittest
from pathlib import Path

import transferdesk_updater as updater


def release_payload(version="8.0.1", prerelease=False, assets=None):
    return {
        "tag_name": f"v{version}",
        "html_url": f"https://github.com/mato0490/ultra-pro-files-manager/releases/tag/v{version}",
        "draft": False,
        "prerelease": prerelease,
        "assets": assets
        if assets is not None
        else [
            {
                "name": f"TransferDesk-windows-v{version}.zip",
                "browser_download_url": "https://example.test/windows.zip",
                "size": 123,
            },
            {
                "name": f"TransferDesk-windows-v{version}.zip.sha256",
                "browser_download_url": "https://example.test/windows.zip.sha256",
                "size": 80,
            },
            {
                "name": f"TransferDesk-macos-v{version}.zip",
                "browser_download_url": "https://example.test/macos.zip",
                "size": 456,
            },
            {
                "name": f"TransferDesk-macos-v{version}.zip.sha256",
                "browser_download_url": "https://example.test/macos.zip.sha256",
                "size": 80,
            },
        ],
    }


class UpdaterTests(unittest.TestCase):
    def test_version_comparison(self):
        self.assertTrue(updater.is_newer_version("8.0.1", "8.0.0"))
        self.assertTrue(updater.is_newer_version("v8.1", "8.0.9"))
        self.assertFalse(updater.is_newer_version("8.0.0", "8.0.0"))
        self.assertFalse(updater.is_newer_version("8.0.0", "8.0.1"))

    def test_fetch_latest_release_selects_platform_asset(self):
        release = updater.fetch_latest_release(
            "8.0.0",
            platform_key="windows",
            opener=lambda url: release_payload(),
        )

        self.assertIsNotNone(release)
        self.assertEqual(release.version, "8.0.1")
        self.assertEqual(release.asset.name, "TransferDesk-windows-v8.0.1.zip")
        self.assertEqual(release.checksum_asset.name, "TransferDesk-windows-v8.0.1.zip.sha256")

    def test_fetch_latest_release_ignores_prereleases_and_current_version(self):
        self.assertIsNone(
            updater.fetch_latest_release(
                "8.0.0",
                platform_key="windows",
                opener=lambda url: release_payload(prerelease=True),
            )
        )
        self.assertIsNone(
            updater.fetch_latest_release(
                "8.0.1",
                platform_key="windows",
                opener=lambda url: release_payload(),
            )
        )

    def test_fetch_latest_release_requires_matching_asset_and_checksum(self):
        with self.assertRaises(updater.UpdateError):
            updater.fetch_latest_release(
                "8.0.0",
                platform_key="windows",
                opener=lambda url: release_payload(assets=[]),
            )

    def test_parse_sha256_accepts_matching_filename(self):
        digest = "a" * 64
        self.assertEqual(
            updater.parse_sha256(f"{digest}  TransferDesk-windows-v8.0.1.zip", "TransferDesk-windows-v8.0.1.zip"),
            digest,
        )

    def test_checksum_validation_removes_bad_download(self):
        release = updater.fetch_latest_release(
            "8.0.0",
            platform_key="windows",
            opener=lambda url: release_payload(),
        )
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)

            def fake_text(url):
                return f"{'0' * 64}  {release.asset.name}"

            def fake_download(url, destination, expected_size, progress):
                destination.write_bytes(b"bad")

            original_text = updater._read_text
            original_download = updater._download_file
            try:
                updater._read_text = fake_text
                updater._download_file = fake_download
                with self.assertRaises(updater.UpdateError):
                    updater.download_update(release, root_path)
            finally:
                updater._read_text = original_text
                updater._download_file = original_download

            self.assertFalse((root_path / release.asset.name).exists())


if __name__ == "__main__":
    unittest.main()
