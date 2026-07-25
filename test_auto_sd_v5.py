import importlib.util
import os
import plistlib
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import autosd_core as auto_sd_v5
import themes_config as tc
import translations as i18n


def load_legacy_interface():
    module_path = Path(__file__).with_name("auto sd v5.py")
    spec = importlib.util.spec_from_file_location("auto_sd_v5_legacy", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HelpersTests(unittest.TestCase):
    def test_macos_application_data_directory(self):
        with mock.patch.object(auto_sd_v5.sys, "platform", "darwin"), mock.patch.object(
            auto_sd_v5.Path, "home", return_value=Path("/Users/tester")
        ):
            self.assertEqual(
                auto_sd_v5.application_data_dir(),
                Path("/Users/tester/Library/Application Support/AutoSDFileManager"),
            )

    def test_macos_removable_volumes_are_detected_with_diskutil(self):
        volume = Path("/Volumes/CAMERA")
        disk_info = plistlib.dumps({"RemovableMedia": True, "Ejectable": True})
        completed = mock.Mock(returncode=0, stdout=disk_info)
        with mock.patch.object(auto_sd_v5.sys, "platform", "darwin"), mock.patch.object(
            auto_sd_v5.os, "name", "posix"
        ), mock.patch.object(auto_sd_v5.Path, "iterdir", return_value=iter([volume])), mock.patch.object(
            auto_sd_v5.subprocess, "run", return_value=completed
        ) as run:
            self.assertEqual(auto_sd_v5.removable_drives(), [volume])
            run.assert_called_once_with(
                ["/usr/sbin/diskutil", "info", "-plist", str(volume)],
                capture_output=True, timeout=5, check=False,
            )

    def test_extensions_are_normalized_and_deduplicated(self):
        self.assertEqual(
            auto_sd_v5.normalize_extensions("jpg, *.PNG; .jpg, mov"),
            (".jpg", ".png", ".mov"),
        )
        self.assertEqual(auto_sd_v5.normalize_extensions("*.*"), ())

    def test_french_dates_are_displayed_and_parsed(self):
        value = auto_sd_v5.parse_iso_date("19/07/2026", "La date")
        self.assertEqual(value, datetime(2026, 7, 19).date())
        self.assertEqual(auto_sd_v5.format_display_date(value), "19/07/2026")
        self.assertEqual(auto_sd_v5.format_path_date(value), "19-07-2026")
        self.assertEqual(
            auto_sd_v5.parse_iso_date("2026-07-19", "La date"), value
        )
        with self.assertRaises(auto_sd_v5.TransferError):
            auto_sd_v5.parse_iso_date("07/19/2026", "La date")

    def test_every_theme_exposes_every_required_role(self):
        required = {
            "bg", "surface", "surface_alt", "fg", "muted", "primary",
            "success", "danger", "warning", "entry_bg", "entry_fg",
            "border", "log_bg", "log_fg",
        }
        for theme in tc.TOUS_LES_THEMES:
            with self.subTest(theme=theme):
                self.assertTrue(required.issubset(tc.get_palette(theme)))

    def test_translation_catalogs_have_identical_keys(self):
        catalogs = i18n.TRANSLATIONS
        expected = set(catalogs["en"])
        self.assertEqual(set(catalogs), {"en", "fr", "he"})
        for language, catalog in catalogs.items():
            with self.subTest(language=language):
                self.assertEqual(set(catalog), expected)
                self.assertTrue(all(catalog.values()))
        self.assertEqual(i18n.translate("fr", "theme"), "Thème")
        self.assertEqual(i18n.translate("he", "language"), "שפה")

    def test_interface_can_apply_every_configured_theme(self):
        legacy = load_legacy_interface()
        try:
            root = legacy.tk.Tk()
        except legacy.tk.TclError as exc:
            self.skipTest(f"Interface graphique indisponible : {exc}")
        root.withdraw()
        try:
            app = legacy.AutoSDApp(root)
            for language, name in i18n.LANGUAGE_NAMES.items():
                with self.subTest(language=language):
                    app.language_var.set(name)
                    app.on_language_changed()
                    root.update_idletasks()
                    tab_names = [
                        app.notebook.tab(tab_id, "text").strip()
                        for tab_id in app.notebook.tabs()
                    ]
                    expected_tabs = [
                        i18n.translate(language, "tab_transfer"),
                        i18n.translate(language, "tab_duplicates"),
                        i18n.translate(language, "tab_history"),
                    ]
                    self.assertEqual(tab_names, expected_tabs)
                    self.assertEqual(app.language_code, language)
                    self.assertEqual(
                        app.conflict_display_var.get(),
                        i18n.translate(language, "policy_rename"),
                    )
            self.assertTrue(app.duplicate_tree.winfo_exists())
            self.assertTrue(app.history_tree.winfo_exists())
            app.open_network_window()
            root.update_idletasks()
            self.assertTrue(app.network_ui_exists())
            self.assertTrue(app.network_receive_button.winfo_exists())
            self.assertTrue(app.network_send_button.winfo_exists())
            self.assertTrue(app.network_device_tree.winfo_exists())
            self.assertTrue(app.network_scan_button.winfo_exists())
            self.assertTrue(app.internet_create_button.winfo_exists())
            self.assertTrue(app.internet_send_button.winfo_exists())
            self.assertTrue(app.internet_rendezvous_entry.winfo_exists())
            app.close_network_window()
            self.assertTrue(app.preserve_tree_var.get())
            self.assertTrue(app.verify_checksum_var.get())
            self.assertRegex(app.date_start_var.get(), r"^\d{2}/\d{2}/\d{4}$")
            for theme in tc.TOUS_LES_THEMES:
                with self.subTest(theme=theme):
                    app.apply_theme(theme)
                    root.update_idletasks()
                    self.assertEqual(app.theme_var.get(), theme)
        finally:
            app.discovery_service.stop()
            root.destroy()


class TransferEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.source = self.base / "source"
        self.destination = self.base / "destination"
        self.source.mkdir()
        self.destination.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_file(self, relative_path, content, modified="2026-07-18"):
        path = self.source / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        timestamp = datetime.fromisoformat(f"{modified}T12:00:00").timestamp()
        os.utime(path, (timestamp, timestamp))
        return path

    def make_options(self, **changes):
        values = {
            "source": self.source,
            "destination": self.destination,
            "date_mode": "all",
            "create_folder": False,
        }
        values.update(changes)
        return auto_sd_v5.TransferOptions(**values)

    def test_latest_date_and_extension_filter_are_applied(self):
        self.write_file("old.jpg", b"old", "2026-07-17")
        self.write_file("latest.jpg", b"latest", "2026-07-18")
        self.write_file("latest.txt", b"not selected", "2026-07-18")

        options = self.make_options(extensions=(".jpg",), date_mode="latest")
        result = auto_sd_v5.TransferEngine(options).run()

        self.assertEqual(result.copied, 1)
        self.assertEqual((self.destination / "latest.jpg").read_bytes(), b"latest")
        self.assertFalse((self.destination / "old.jpg").exists())
        self.assertFalse((self.destination / "latest.txt").exists())

    def test_name_conflicts_are_renamed_without_overwrite(self):
        self.write_file("camera_a/same.jpg", b"camera A")
        self.write_file("camera_b/same.jpg", b"camera B")

        result = auto_sd_v5.TransferEngine(self.make_options()).run()

        self.assertEqual(result.copied, 2)
        self.assertEqual(result.renamed, 1)
        contents = {path.read_bytes() for path in self.destination.glob("same*.jpg")}
        self.assertEqual(contents, {b"camera A", b"camera B"})

    def test_identical_existing_file_is_verified_before_source_deletion(self):
        source_file = self.write_file("verified.bin", b"same content")
        (self.destination / "verified.bin").write_bytes(b"same content")

        options = self.make_options(delete_source=True)
        result = auto_sd_v5.TransferEngine(options).run()

        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.deleted, 1)
        self.assertFalse(source_file.exists())

    def test_cancelled_copy_removes_partial_destination(self):
        self.write_file("large.bin", b"x" * (auto_sd_v5.CHUNK_SIZE + 100))
        cancel = threading.Event()

        def callback(event, _data):
            if event == "progress":
                cancel.set()

        result = auto_sd_v5.TransferEngine(self.make_options(), cancel, callback).run()

        self.assertTrue(result.cancelled)
        self.assertFalse((self.destination / "large.bin").exists())

    def test_nested_source_and_destination_are_rejected(self):
        nested_destination = self.source / "backup"
        nested_destination.mkdir()
        options = auto_sd_v5.TransferOptions(
            source=self.source,
            destination=nested_destination,
            date_mode="all",
            create_folder=False,
        )
        with self.assertRaises(auto_sd_v5.TransferError):
            auto_sd_v5.TransferEngine(options).validate()

    def test_copy_race_does_not_delete_an_existing_destination(self):
        source_file = self.write_file("source.bin", b"source")
        existing = self.destination / "existing.bin"
        existing.write_bytes(b"must stay")
        engine = auto_sd_v5.TransferEngine(self.make_options())

        with self.assertRaises(FileExistsError):
            engine.copy_file(
                source_file,
                existing,
                completed_before=0,
                total_bytes=6,
                started_at=auto_sd_v5.time.monotonic(),
                file_index=1,
                total_files=1,
            )
        self.assertEqual(existing.read_bytes(), b"must stay")

    def test_preview_and_transfer_preserve_the_folder_tree(self):
        first = self.write_file("camera_a/same.jpg", b"camera A")
        second = self.write_file("camera_b/same.jpg", b"camera B")
        options = self.make_options(preserve_tree=True)
        engine = auto_sd_v5.TransferEngine(options)

        plan = engine.preview()
        result = engine.run()

        self.assertEqual({item.source for item in plan.items}, {first, second})
        self.assertTrue(all(item.action == "copy" for item in plan.items))
        self.assertEqual(result.copied, 2)
        self.assertEqual((self.destination / "camera_a/same.jpg").read_bytes(), b"camera A")
        self.assertEqual((self.destination / "camera_b/same.jpg").read_bytes(), b"camera B")

    def test_sha256_verification_is_counted(self):
        self.write_file("verified.bin", b"content")
        result = auto_sd_v5.TransferEngine(
            self.make_options(verify_checksum=True)
        ).run()

        self.assertEqual(result.copied, 1)
        self.assertEqual(result.verified, 1)

    def test_conflict_policies_skip_replace_and_newer(self):
        source = self.write_file("same.bin", b"new source")
        destination = self.destination / "same.bin"
        destination.write_bytes(b"old destination")

        skipped = auto_sd_v5.TransferEngine(
            self.make_options(conflict_policy="skip")
        ).run()
        self.assertEqual(skipped.conflict_skipped, 1)
        self.assertEqual(destination.read_bytes(), b"old destination")

        replaced = auto_sd_v5.TransferEngine(
            self.make_options(conflict_policy="replace", verify_checksum=True)
        ).run()
        self.assertEqual(replaced.replaced, 1)
        self.assertEqual(destination.read_bytes(), b"new source")

        destination.write_bytes(b"future destination")
        future = datetime.fromisoformat("2030-01-01T12:00:00").timestamp()
        os.utime(destination, (future, future))
        newer = auto_sd_v5.TransferEngine(
            self.make_options(conflict_policy="newer")
        ).run()
        self.assertEqual(newer.conflict_skipped, 1)
        self.assertEqual(destination.read_bytes(), b"future destination")
        self.assertTrue(source.exists())

    def test_automatic_organization_by_date_and_type(self):
        self.write_file("nested/photo.jpg", b"photo", "2026-07-18")
        result = auto_sd_v5.TransferEngine(
            self.make_options(
                preserve_tree=False,
                organization_mode="year_month",
            )
        ).run()
        self.assertEqual(result.copied, 1)
        self.assertTrue((self.destination / "2026/07/photo.jpg").exists())

        other_destination = self.base / "organized_by_type"
        other_destination.mkdir()
        result = auto_sd_v5.TransferEngine(
            self.make_options(destination=other_destination, organization_mode="type")
        ).run()
        self.assertEqual(result.copied, 1)
        self.assertTrue((other_destination / "Images/photo.jpg").exists())

    def test_photo_organization_prefers_exif_capture_date(self):
        path = self.source / "camera/photo.jpg"
        path.parent.mkdir(parents=True)
        exif = auto_sd_v5.Image.Exif()
        exif[36867] = "2020:05:04 10:30:00"
        auto_sd_v5.Image.new("RGB", (8, 8), "red").save(path, exif=exif)
        modified = datetime.fromisoformat("2026-07-18T12:00:00").timestamp()
        os.utime(path, (modified, modified))

        result = auto_sd_v5.TransferEngine(
            self.make_options(organization_mode="year_month")
        ).run()

        self.assertEqual(result.copied, 1)
        self.assertTrue((self.destination / "2020/05/photo.jpg").exists())

    def test_ask_policy_requires_and_applies_a_per_file_decision(self):
        source = self.write_file("conflict.bin", b"source")
        destination = self.destination / "conflict.bin"
        destination.write_bytes(b"destination")
        options = self.make_options(conflict_policy="ask")

        preview = auto_sd_v5.TransferEngine(options).preview()
        self.assertEqual(preview.items[0].action, "ask_conflict")
        with self.assertRaises(auto_sd_v5.TransferError):
            auto_sd_v5.TransferEngine(options).run()

        result = auto_sd_v5.TransferEngine(
            options, conflict_overrides={source: "skip"}
        ).run()
        self.assertEqual(result.conflict_skipped, 1)
        self.assertEqual(destination.read_bytes(), b"destination")

    def test_automatic_backup_folder_uses_french_date_order(self):
        self.write_file("photo.bin", b"content", "2026-07-18")
        options = self.make_options(create_folder=True, folder_name_mode="auto")
        result = auto_sd_v5.TransferEngine(options).run()
        self.assertEqual(result.target_dir.name, "18-07-2026")
        self.assertTrue((result.target_dir / "photo.bin").exists())

    def test_only_preview_selected_paths_are_transferred(self):
        first = self.write_file("first.bin", b"first")
        second = self.write_file("second.bin", b"second")
        result = auto_sd_v5.TransferEngine(
            self.make_options(), selected_paths={second}
        ).run()

        self.assertEqual(result.selected, 1)
        self.assertFalse((self.destination / first.name).exists())
        self.assertEqual((self.destination / second.name).read_bytes(), b"second")

    def test_preview_reports_insufficient_space(self):
        self.write_file("large.bin", b"123456")
        usage = type("Usage", (), {"total": 10, "used": 9, "free": 1})()
        with mock.patch.object(auto_sd_v5.shutil, "disk_usage", return_value=usage):
            plan = auto_sd_v5.TransferEngine(self.make_options()).preview()
        self.assertFalse(plan.enough_space)
        self.assertEqual(plan.required_bytes, 6)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_profiles_are_saved_loaded_and_deleted(self):
        store = auto_sd_v5.ProfileStore(self.base / "profiles.json")
        store.save("Photos", {"extensions": ".jpg", "preserve_tree": True})
        self.assertEqual(store.all()["Photos"]["extensions"], ".jpg")
        store.delete("Photos")
        self.assertEqual(store.all(), {})

    def test_history_is_bounded_and_exportable(self):
        store = auto_sd_v5.HistoryStore(self.base / "history.json")
        with mock.patch.object(auto_sd_v5, "MAX_HISTORY_ENTRIES", 2):
            for index in range(3):
                store.append({
                    "timestamp": str(index), "status": "Complete", "source": "S",
                    "destination": "D", "copied": index, "skipped": 0, "errors": 0,
                })
        self.assertEqual(len(store.entries()), 2)
        report = self.base / "report.txt"
        store.export_text(report)
        contents = report.read_text(encoding="utf-8")
        self.assertIn("Complete", contents)
        french_report = self.base / "rapport.txt"
        store.export_text(french_report, "fr")
        self.assertIn("copié", french_report.read_text(encoding="utf-8"))

    def test_safe_eject_uses_only_a_verified_removable_drive(self):
        completed = type("Completed", (), {"returncode": 0, "stderr": ""})()
        with (
            mock.patch.object(
                auto_sd_v5, "removable_root", side_effect=[Path("E:/"), None]
            ),
            mock.patch.object(auto_sd_v5.subprocess, "run", return_value=completed) as run,
        ):
            auto_sd_v5.eject_removable_drive(Path("E:/DCIM"))
        arguments = run.call_args.args[0]
        self.assertIn("powershell.exe", arguments[0].lower())
        self.assertIn("E:", arguments[-1])


class DuplicateEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_file(self, relative_path, content):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_exact_duplicates_are_grouped_but_same_size_files_are_not(self):
        first = self.write_file("a/first.bin", b"same content")
        second = self.write_file("b/second.bin", b"same content")
        self.write_file("different.bin", b"other value!")

        result = auto_sd_v5.DuplicateEngine(self.root).scan()

        self.assertEqual(result.files_scanned, 3)
        self.assertEqual(result.duplicate_files, 1)
        self.assertEqual(len(result.groups), 1)
        self.assertEqual(set(result.groups[0].files), {first, second})
        self.assertEqual(result.reclaimable_bytes, len(b"same content"))

    def test_duplicates_folder_is_excluded_from_future_scans(self):
        self.write_file("one.bin", b"duplicate")
        self.write_file("two.bin", b"duplicate")
        self.write_file("Duplicates/old.bin", b"duplicate")

        result = auto_sd_v5.DuplicateEngine(self.root).scan()

        self.assertEqual(result.files_scanned, 2)
        self.assertEqual(len(result.groups[0].files), 2)

    def test_verified_duplicate_can_be_moved_without_overwrite(self):
        original = self.write_file("a/photo.jpg", b"photo")
        duplicate = self.write_file("b/photo.jpg", b"photo")
        existing = self.write_file("Duplicates/photo.jpg", b"keep this")
        engine = auto_sd_v5.DuplicateEngine(self.root)
        result = engine.scan()
        duplicate = result.groups[0].duplicates[0]

        moved = engine.apply_action([duplicate], "move")[duplicate]

        self.assertTrue(original.exists())
        self.assertEqual(existing.read_bytes(), b"keep this")
        self.assertEqual(moved.name, "photo (2).jpg")
        self.assertEqual(moved.read_bytes(), b"photo")
        self.assertFalse(duplicate.exists())

    def test_delete_rechecks_content_and_protects_original(self):
        self.write_file("a.bin", b"original")
        duplicate = self.write_file("b.bin", b"original")
        engine = auto_sd_v5.DuplicateEngine(self.root)
        result = engine.scan()
        duplicate = result.groups[0].duplicates[0]
        duplicate.write_bytes(b"changed!")

        with self.assertRaises(auto_sd_v5.TransferError):
            engine.apply_action([duplicate], "delete")

        self.assertTrue(duplicate.exists())
        with self.assertRaises(auto_sd_v5.TransferError):
            engine.apply_action([result.groups[0].original], "delete")


if __name__ == "__main__":
    unittest.main()
