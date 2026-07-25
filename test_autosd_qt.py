import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

try:
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QSignalSpy
    import autosd_qt
except (ImportError, SystemExit):
    autosd_qt = None


@unittest.skipIf(autosd_qt is None, "PySide6 indisponible")
class QtBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.bridge = autosd_qt.AutoSDBridge()

    def tearDown(self):
        self.bridge.shutdown()

    def test_languages_and_rtl_are_exposed(self):
        spy = QSignalSpy(self.bridge.languageChanged)
        self.bridge.language = "he"
        self.assertTrue(self.bridge.rtl)
        self.assertEqual(self.bridge.text("local_transfer"), "העברה ברשת המקומית")
        self.assertEqual(spy.count(), 1)
        self.bridge.language = "en"
        self.assertEqual(self.bridge.text("local_transfer"), "Local network transfer")
        self.bridge.language = "fr"
        self.assertEqual(self.bridge.text("local_transfer"), "Transfert sur le réseau local")
        self.assertFalse(self.bridge.rtl)

    def test_theme_is_normalized(self):
        self.bridge.theme = "dark"
        self.assertEqual(self.bridge.theme, "dark")
        self.bridge.theme = "unknown"
        self.assertEqual(self.bridge.theme, "light")

    def test_rendezvous_url_has_no_bundled_service(self):
        self.assertEqual(self.bridge.rendezvousUrl, "")

    def test_cancel_sets_thread_safe_event(self):
        self.bridge.cancel()
        self.assertTrue(self.bridge._cancel.is_set())

    def test_worker_translates_result_to_signal(self):
        signals = autosd_qt.WorkerSignals()
        spy = QSignalSpy(signals.event)
        signals.event.emit("progress", {"completed": 1, "total": 2})
        self.assertEqual(spy.count(), 1)

    def test_copy_to_clipboard(self):
        self.bridge.copyToClipboard("ABCD-EFGH.SECRET-CODE")
        self.assertEqual(QGuiApplication.clipboard().text(), "ABCD-EFGH.SECRET-CODE")

    def test_transfer_validation_rejects_missing_source(self):
        spy = QSignalSpy(self.bridge.notification)
        self.bridge.previewTransfer({"source": "", "destination": "C:/backup"})
        self.assertEqual(spy.count(), 1)
        self.assertFalse(self.bridge.busy)

    def test_transfer_validation_rejects_bad_extension(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source")
            destination = os.path.join(root, "destination")
            os.mkdir(source)
            spy = QSignalSpy(self.bridge.notification)
            self.bridge.previewTransfer({
                "source": source, "destination": destination,
                "extensions": "jpg, ../exe",
            })
            self.assertEqual(spy.count(), 1)
            self.assertFalse(self.bridge.busy)

    def test_p2p_validation_rejects_invalid_code(self):
        spy = QSignalSpy(self.bridge.notification)
        self.bridge.sendP2P("not-a-code", "https://example.com", [])
        self.assertEqual(spy.count(), 1)
        self.assertFalse(self.bridge.busy)

    def test_p2p_events_update_visible_state(self):
        spy = QSignalSpy(self.bridge.p2pStateChanged)
        self.bridge._p2p_event("room_ready", {"code": "ABCD-EFGH.SECRET-CODE"})
        self.assertEqual(self.bridge.p2pState, "waiting")
        self.assertEqual(self.bridge.p2pCode, "ABCD-EFGH.SECRET-CODE")
        self.bridge._p2p_event("connecting", {})
        self.assertEqual(self.bridge.p2pState, "connecting")
        self.bridge._p2p_event("progress", {"completed": 1, "total": 2})
        self.assertEqual(self.bridge.p2pState, "transferring")
        self.bridge._p2p_event("complete", {"files": 1, "total": 2})
        self.assertEqual(self.bridge.p2pState, "success")
        self.assertGreaterEqual(spy.count(), 4)
        self.assertEqual(self.bridge.p2pStats["percent"], 100)
        self.assertIn("2.00 B", self.bridge.p2pStats["summary"])

    def test_manual_p2p_events_expose_copyable_payload(self):
        spy = QSignalSpy(self.bridge.manualPayloadChanged)
        self.bridge._p2p_event("manual_offer_ready", {"payload": "AUTOSD-MANUAL-1.offer"})
        self.assertEqual(self.bridge.p2pState, "manual_waiting")
        self.assertEqual(self.bridge.manualPayloadKind, "offer")
        self.assertEqual(self.bridge.manualPayload, "AUTOSD-MANUAL-1.offer")
        self.assertEqual(spy.count(), 1)

        self.bridge.copyManualPayload()
        self.assertEqual(
            QGuiApplication.clipboard().text(), "AUTOSD-MANUAL-1.offer"
        )

    def test_manual_clear_resets_payload_code_and_state(self):
        self.bridge._p2p_event("room_ready", {"code": "ABCD-EFGH.SECRET-CODE"})
        self.bridge._p2p_event("manual_offer_ready", {"payload": "AUTOSD-MANUAL-1.offer"})

        self.bridge.clearManualP2P()

        self.assertEqual(self.bridge.p2pState, "idle")
        self.assertEqual(self.bridge.p2pCode, "")
        self.assertEqual(self.bridge.manualPayload, "")
        self.assertEqual(self.bridge.manualPayloadKind, "")

    def test_p2p_completion_is_added_to_history(self):
        result = SimpleNamespace(files=2, bytes_transferred=4096)

        self.bridge._p2p_done(result, "send", "C:/a.jpg; C:/b.jpg", "P2P manuel")

        self.assertEqual(self.bridge.history[0]["kind"], "p2p")
        self.assertEqual(self.bridge.history[0]["copied"], 2)
        self.assertEqual(self.bridge.history[0]["bytes"], 4096)
        self.assertEqual(self.bridge.p2pState, "success")

    def test_local_completion_is_added_to_history(self):
        result = SimpleNamespace(files=1, bytes_transferred=128)

        self.bridge._local_transfer_done(result, "receive", "Réseau local", "C:/dest")

        self.assertEqual(self.bridge.history[0]["kind"], "local")
        self.assertEqual(self.bridge.history[0]["copied"], 1)
        self.assertEqual(self.bridge.history[0]["destination"], "C:/dest")

    def test_socket_code_uses_stable_pairing_code_and_default_port(self):
        self.assertRegex(self.bridge.socketPairingCode, r"^\d{6}$")
        formatted = self.bridge._format_socket_code("192.168.1.20", autosd_qt.nt.DEFAULT_PORT)

        host, port, code = self.bridge._parse_socket_code(formatted)

        self.assertEqual(host, "192.168.1.20")
        self.assertEqual(port, autosd_qt.nt.DEFAULT_PORT)
        self.assertEqual(code, self.bridge.socketPairingCode)

    def test_socket_code_accepts_explicit_port(self):
        host, port, code = self.bridge._parse_socket_code("123456@10.0.0.5:49152")

        self.assertEqual((host, port, code), ("10.0.0.5", 49152, "123456"))

    def test_socket_listening_event_exposes_copyable_code(self):
        self.bridge._p2p_event("listening", {"host": "127.0.0.1", "port": 49152})

        self.assertEqual(
            self.bridge.socketCode,
            f"{self.bridge.socketPairingCode}@127.0.0.1:49152",
        )
        self.assertEqual(self.bridge.p2pState, "waiting")

    def test_socket_resuming_event_updates_visible_stats(self):
        self.bridge._p2p_event("resuming", {"completed": 512, "total": 1024})

        self.assertEqual(self.bridge.p2pState, "transferring")
        self.assertEqual(self.bridge.p2pStats["percent"], 50.0)

    def test_socket_send_rejects_invalid_code(self):
        spy = QSignalSpy(self.bridge.notification)

        self.bridge.sendSocket("bad-code", ["C:/missing.txt"])

        self.assertEqual(spy.count(), 1)
        self.assertFalse(self.bridge.busy)

    def test_socket_send_starts_client_with_files_and_folders(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.txt"
            source.write_text("socket", encoding="utf-8")
            result = SimpleNamespace(files=1, bytes_transferred=6)
            created = {}

            class FakeClient:
                def __init__(self, host, port, pairing_code, cancel_event, callback, timeout):
                    created["args"] = (host, port, pairing_code, cancel_event, callback, timeout)

                def send_files(self, paths):
                    created["paths"] = list(paths)
                    return result

            with mock.patch.object(autosd_qt.nt, "LocalTransferClient", FakeClient), mock.patch.object(
                self.bridge,
                "_start",
                side_effect=lambda operation, done=None, failed=None: done(operation()),
            ):
                self.bridge.sendSocket("123456@127.0.0.1:49152", [str(source)])

            self.assertEqual(created["args"][:3], ("127.0.0.1", 49152, "123456"))
            self.assertEqual(created["paths"], [source])
            self.assertEqual(self.bridge.history[0]["kind"], "p2p")

    def test_socket_send_retries_until_success_after_network_errors(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.txt"
            source.write_text("socket", encoding="utf-8")
            result = SimpleNamespace(files=1, bytes_transferred=6)
            attempts = {"count": 0}

            class FlakyClient:
                def __init__(self, *_args, **_kwargs):
                    pass

                def send_files(self, _paths):
                    attempts["count"] += 1
                    if attempts["count"] < 4:
                        raise autosd_qt.nt.NetworkTransferError("connection lost")
                    return result

            with mock.patch.object(autosd_qt.nt, "LocalTransferClient", FlakyClient), mock.patch.object(
                self.bridge, "_wait_socket_retry"
            ), mock.patch.object(
                self.bridge,
                "_start",
                side_effect=lambda operation, done=None, failed=None: done(operation()),
            ):
                self.bridge.sendSocket("123456@127.0.0.1:49152", [str(source)])

            self.assertEqual(attempts["count"], 4)
            self.assertEqual(self.bridge.history[0]["kind"], "p2p")

    def test_socket_receive_relistens_until_success_after_network_errors(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "received"
            destination.mkdir()
            result = SimpleNamespace(files=1, bytes_transferred=6)
            attempts = {"count": 0}

            class FlakyServer:
                bound_port = 49152

                def __init__(self, *_args, **_kwargs):
                    pass

                def serve_once(self):
                    attempts["count"] += 1
                    if attempts["count"] < 4:
                        raise autosd_qt.nt.NetworkTransferError("connection lost")
                    return result

            with mock.patch.object(autosd_qt.nt, "LocalTransferServer", FlakyServer), mock.patch.object(
                self.bridge, "_wait_socket_retry"
            ), mock.patch.object(
                self.bridge,
                "_start",
                side_effect=lambda operation, done=None, failed=None: done(operation()),
            ):
                self.bridge.startSocketReceive(str(destination))

            self.assertEqual(attempts["count"], 4)
            self.assertEqual(self.bridge.history[0]["kind"], "p2p")

    def test_manual_answer_requires_an_active_sender(self):
        spy = QSignalSpy(self.bridge.notification)
        self.bridge.submitManualAnswer("invalid")
        self.assertEqual(spy.count(), 1)
        self.assertFalse(self.bridge.busy)

    def test_p2p_timeout_is_reported_as_expired(self):
        self.bridge._fail_p2p("Délai dépassé pendant la connexion Internet.")
        self.assertEqual(self.bridge.p2pState, "expired")
        self.assertEqual(
            self.bridge.lastError,
            "Délai dépassé pendant la connexion Internet.",
        )

    def test_error_detail_is_persistent_and_empty_errors_are_replaced(self):
        spy = QSignalSpy(self.bridge.lastErrorChanged)
        self.bridge._fail("")

        self.assertEqual(
            self.bridge.lastError,
            "L’opération a échoué sans fournir de message d’erreur.",
        )
        self.assertEqual(spy.count(), 1)

        with mock.patch.object(self.bridge._pool, "start"):
            self.bridge._start(lambda: None)
        self.assertEqual(self.bridge.lastError, "")


    def test_discovered_devices_are_exposed_to_qml(self):
        device = autosd_qt.nt.DiscoveredDevice(
            device_id="peer-1",
            name="Studio",
            host="192.168.1.20",
            discovery_port=48192,
            ready=True,
        )
        with mock.patch.object(
            autosd_qt.nt, "discover_devices", return_value=[device]
        ) as discover, mock.patch.object(
            self.bridge,
            "_start",
            side_effect=lambda operation, done=None, failed=None: done(operation()),
        ):
            self.bridge.scanDevices()

        discover.assert_called_once_with(
            sender_id=self.bridge._discovery_service.instance_id,
            duration=2.5,
            cancel_event=self.bridge._cancel,
        )
        self.assertEqual(self.bridge.devices[0]["id"], "peer-1")
        self.assertIn("Studio", self.bridge.devices[0]["label"])

    def test_local_send_rejects_unknown_device(self):
        spy = QSignalSpy(self.bridge.notification)
        self.bridge.sendLocal("missing", ["C:/missing.txt"])
        self.assertEqual(spy.count(), 1)
        self.assertFalse(self.bridge.busy)

    def test_local_request_can_be_rejected(self):
        self.bridge._pending_local_requests.add("request-1")
        with mock.patch.object(
            self.bridge._discovery_service, "respond_to_request"
        ) as respond:
            self.bridge.respondLocalRequest("request-1", False, "")
        respond.assert_called_once_with(
            "request-1", accepted=False, message="Transfert refusé."
        )
        self.assertNotIn("request-1", self.bridge._pending_local_requests)

    def test_qml_interface_loads(self):
        engine = autosd_qt.QQmlApplicationEngine()
        engine.rootContext().setContextProperty("backend", self.bridge)
        qml = Path(autosd_qt.__file__).with_name("qml") / "Main.qml"
        engine.load(autosd_qt.QUrl.fromLocalFile(str(qml)))
        self.assertTrue(engine.rootObjects())
        self.assertIsNotNone(engine.rootObjects()[0].findChild(QObject, "errorDialog"))
        self.assertIsNotNone(engine.rootObjects()[0].findChild(QObject, "localFolderDialog"))
        self.assertIsNotNone(engine.rootObjects()[0].findChild(QObject, "manualInput"))
        self.assertIsNotNone(engine.rootObjects()[0].findChild(QObject, "manualOutput"))

    def test_qml_compact_window_keeps_long_pages_scrollable(self):
        engine = autosd_qt.QQmlApplicationEngine()
        engine.rootContext().setContextProperty("backend", self.bridge)
        qml = Path(autosd_qt.__file__).with_name("qml") / "Main.qml"
        engine.load(autosd_qt.QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        root.setWidth(900)
        root.setHeight(620)
        self.app.processEvents()

        transfer_scroll = root.findChild(QObject, "transferScroll")
        p2p_scroll = root.findChild(QObject, "p2pScroll")
        self.assertIsNotNone(transfer_scroll)
        self.assertIsNotNone(p2p_scroll)
        self.assertGreater(transfer_scroll.property("contentHeight"), transfer_scroll.property("height"))
        self.assertLessEqual(
            transfer_scroll.property("contentWidth"),
            transfer_scroll.property("availableWidth") + 1,
        )
        root.setProperty("page", 3)
        self.app.processEvents()
        self.assertGreater(p2p_scroll.property("contentHeight"), p2p_scroll.property("height"))
        self.assertLessEqual(
            p2p_scroll.property("contentWidth"),
            p2p_scroll.property("availableWidth") + 1,
        )

    def test_preview_exposes_items_and_applies_conflict_decision(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            destination = Path(root) / "destination"
            source.mkdir()
            destination.mkdir()
            source_file = source / "photo.jpg"
            source_file.write_bytes(b"new")
            (destination / "photo.jpg").write_bytes(b"old")
            settings = {
                "source": str(source),
                "destination": str(destination),
                "extensions": "jpg",
                "dateMode": "all",
                "createFolder": False,
                "verify": True,
                "preserveTree": False,
                "deleteSource": False,
                "conflictPolicy": "ask",
                "organization": "none",
            }
            ready = QSignalSpy(self.bridge.previewReady)
            with mock.patch.object(
                self.bridge,
                "_start",
                side_effect=lambda operation, done=None, failed=None: done(operation()),
            ):
                self.bridge.previewTransfer(settings)

            self.assertEqual(ready.count(), 1)
            self.assertEqual(len(self.bridge.previewItems), 1)
            self.assertEqual(self.bridge.previewItems[0]["action"], "ask_conflict")
            self.assertTrue(self.bridge.previewSummary["enoughSpace"])

            with mock.patch.object(self.bridge, "_start_transfer_engine") as start:
                self.bridge.startPreviewedTransfer([{
                    "source": str(source_file),
                    "selected": True,
                    "action": "replace",
                }])
            engine = start.call_args.args[0]
            self.assertEqual(engine.selected_paths, {source_file.resolve()})
            self.assertEqual(engine.conflict_overrides[source_file.resolve()], "replace")


if __name__ == "__main__":
    unittest.main()
