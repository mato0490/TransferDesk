import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import network_transfer as network


class LocalNetworkTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.source = self.base / "source"
        self.destination = self.base / "destination"
        self.source.mkdir()
        self.destination.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def start_server(self, code="123456"):
        server = network.LocalTransferServer(
            self.destination, code, host="127.0.0.1", port=0, timeout=3
        )
        outcome = {}

        def serve():
            try:
                outcome["result"] = server.serve_once()
            except BaseException as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        self.assertTrue(server.ready_event.wait(3), "server did not start")
        self.assertIsNotNone(server.bound_port)
        return server, worker, outcome

    def test_authenticated_round_trip_preserves_content(self):
        first = self.source / "photo.bin"
        second = self.source / "notes.txt"
        first.write_bytes(bytes(range(256)) * 5000)
        second.write_text("AutoSD local transfer", encoding="utf-8")
        server, worker, outcome = self.start_server()

        client = network.LocalTransferClient(
            "127.0.0.1", server.bound_port, "123456", timeout=3
        )
        result = client.send_files([first, second])
        worker.join(3)

        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", outcome)
        self.assertEqual(result.files, 2)
        self.assertEqual(outcome["result"].files, 2)
        self.assertEqual((self.destination / first.name).read_bytes(), first.read_bytes())
        self.assertEqual((self.destination / second.name).read_bytes(), second.read_bytes())
        self.assertFalse(list(self.destination.glob("*.autosd-part")))

    def test_complete_folder_preserves_nested_tree(self):
        album = self.source / "Album"
        nested = album / "Jour 1" / "Sélection"
        nested.mkdir(parents=True)
        cover = album / "cover.txt"
        photo = nested / "photo.bin"
        cover.write_text("album cover", encoding="utf-8")
        photo.write_bytes(bytes(range(128)) * 300)
        server, worker, outcome = self.start_server()

        result = network.LocalTransferClient(
            "127.0.0.1", server.bound_port, "123456", timeout=3
        ).send_files([album])
        worker.join(3)

        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", outcome)
        self.assertEqual(result.files, 2)
        self.assertEqual(
            (self.destination / "Album" / "cover.txt").read_text(encoding="utf-8"),
            "album cover",
        )
        self.assertEqual(
            (self.destination / "Album" / "Jour 1" / "Sélection" / "photo.bin").read_bytes(),
            photo.read_bytes(),
        )
        self.assertFalse(list(self.destination.rglob("*.autosd-part")))

    def test_existing_destination_is_not_overwritten(self):
        source = self.source / "same.txt"
        source.write_text("new", encoding="utf-8")
        existing = self.destination / "same.txt"
        existing.write_text("keep", encoding="utf-8")
        server, worker, outcome = self.start_server()

        network.LocalTransferClient(
            "127.0.0.1", server.bound_port, "123456", timeout=3
        ).send_files([source])
        worker.join(3)

        self.assertNotIn("error", outcome)
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep")
        self.assertEqual(
            (self.destination / "same (2).txt").read_text(encoding="utf-8"), "new"
        )

    def test_wrong_pairing_code_is_rejected(self):
        source = self.source / "secret.txt"
        source.write_text("must not arrive", encoding="utf-8")
        server, worker, outcome = self.start_server(code="654321")

        client = network.LocalTransferClient(
            "127.0.0.1", server.bound_port, "123456", timeout=3
        )
        with self.assertRaises(network.NetworkTransferError):
            client.send_files([source])
        worker.join(3)

        self.assertIsInstance(outcome.get("error"), network.NetworkTransferError)
        self.assertFalse((self.destination / source.name).exists())

    def test_waiting_receiver_can_be_cancelled_cleanly(self):
        server, worker, outcome = self.start_server()

        server.close()
        worker.join(3)

        self.assertFalse(worker.is_alive())
        self.assertIsInstance(outcome.get("error"), network.NetworkTransferCancelled)

    def test_interrupted_large_transfer_can_be_retried_cleanly(self):
        source = self.source / "large-retry.bin"
        source.write_bytes(b"AutoSD" * 1_500_000)
        cancel = threading.Event()

        def cancel_after_progress(event, data):
            if event == "progress" and int(data.get("completed", 0)) > 0:
                cancel.set()

        server, worker, _outcome = self.start_server()
        client = network.LocalTransferClient(
            "127.0.0.1",
            server.bound_port,
            "123456",
            cancel_event=cancel,
            callback=cancel_after_progress,
            timeout=3,
        )
        with self.assertRaises(network.NetworkTransferCancelled):
            client.send_files([source])
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse((self.destination / source.name).exists())
        partials = list(self.destination.glob(f"*{network.RESUME_PART_SUFFIX}"))
        self.assertTrue(partials)

        retry_events = []
        retry_server, retry_worker, retry_outcome = self.start_server()
        retry_server.callback = lambda event, data: retry_events.append((event, data))
        result = network.LocalTransferClient(
            "127.0.0.1", retry_server.bound_port, "123456", timeout=5
        ).send_files([source])
        retry_worker.join(5)

        self.assertFalse(retry_worker.is_alive())
        self.assertNotIn("error", retry_outcome)
        self.assertEqual(result.files, 1)
        self.assertEqual((self.destination / source.name).read_bytes(), source.read_bytes())
        self.assertFalse(list(self.destination.glob(f"*{network.RESUME_PART_SUFFIX}")))
        resumed = [data for event, data in retry_events if event == "resuming"]
        self.assertTrue(resumed)
        self.assertGreater(int(resumed[0]["completed"]), 0)

    def test_unsafe_manifest_name_is_rejected(self):
        for name in ("../escape.txt", "folder/file.txt", "", ".."):
            with self.subTest(name=name):
                with self.assertRaises(network.NetworkTransferError):
                    network._safe_name(name)

    def test_unsafe_relative_paths_are_rejected(self):
        for path in ("../escape.txt", "/absolute.txt", "folder\\file.txt", "", "."):
            with self.subTest(path=path):
                with self.assertRaises(network.NetworkTransferError):
                    network._safe_relative_path(path)

    def test_pairing_codes_are_always_six_digits(self):
        for _ in range(50):
            code = network.generate_pairing_code()
            self.assertRegex(code, r"^\d{6}$")

    def test_tls_pinned_transfer_preserves_content(self):
        source = self.source / "encrypted.txt"
        source.write_text("encrypted over TLS", encoding="utf-8")
        identity = network.create_tls_identity()
        server = network.LocalTransferServer(
            self.destination, "112233", host="127.0.0.1", port=0,
            timeout=3, ssl_context=identity.context,
        )
        outcome = {}

        def receive():
            try:
                outcome["result"] = server.serve_once()
            except BaseException as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=receive, daemon=True)
        worker.start()
        server.ready_event.wait(2)
        try:
            result = network.LocalTransferClient(
                "127.0.0.1", server.bound_port, "112233", timeout=3,
                tls_fingerprint=identity.fingerprint,
            ).send_files([source])
            worker.join(3)
        finally:
            identity.close()

        self.assertEqual(result.files, 1)
        self.assertNotIn("error", outcome)
        self.assertEqual(
            (self.destination / source.name).read_text(encoding="utf-8"),
            "encrypted over TLS",
        )


class DiscoveryTests(unittest.TestCase):
    def test_running_instance_is_discovered(self):
        service = network.DiscoveryService(
            port=0, device_name="Reception PC", instance_id="receiver-id"
        )
        service.start()
        try:
            self.assertIsNotNone(service.bound_port)
            devices = network.discover_devices(
                sender_id="sender-id",
                port=service.bound_port,
                duration=0.4,
                targets=["127.0.0.1"],
                exclude_local=False,
            )
        finally:
            service.stop()

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].device_id, "receiver-id")
        self.assertEqual(devices[0].name, "Reception PC")
        self.assertEqual(devices[0].host, "127.0.0.1")

    def test_same_computer_is_excluded_from_discovery(self):
        service = network.DiscoveryService(
            port=0, device_name="Second local instance", instance_id="local-instance"
        )
        service.start()
        try:
            devices = network.discover_devices(
                sender_id="sender-id",
                port=service.bound_port,
                duration=0.3,
                targets=["127.0.0.1"],
            )
        finally:
            service.stop()

        self.assertEqual(devices, [])

    def test_transfer_request_returns_accepted_invitation(self):
        events = []
        service = None

        def callback(event, data):
            events.append((event, data))
            if event == "transfer_request":
                service.respond_to_request(
                    data["request_id"], accepted=True,
                    transfer_port=54321, pairing_code="246810",
                )

        service = network.DiscoveryService(
            callback=callback, port=0, device_name="Target", instance_id="target-id"
        )
        service.start()
        device = network.DiscoveredDevice(
            "target-id", "Target", "127.0.0.1", service.bound_port
        )
        try:
            with tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "request.txt"
                source.write_text("request metadata", encoding="utf-8")
                invitation = network.request_transfer(
                    device, "sender-id", "Sender", [source], timeout=2
                )
        finally:
            service.stop()

        self.assertEqual(invitation.host, "127.0.0.1")
        self.assertEqual(invitation.port, 54321)
        self.assertEqual(invitation.pairing_code, "246810")
        self.assertTrue(any(event == "transfer_request" for event, _data in events))

    def test_discovery_request_and_file_transfer_work_end_to_end(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            destination = base / "received"
            destination.mkdir()
            source = base / "airdrop.txt"
            source.write_text("AutoSD nearby transfer", encoding="utf-8")
            transfer_outcome = {}
            server_worker = None
            service = None

            def callback(event, data):
                nonlocal server_worker
                if event != "transfer_request":
                    return
                server = network.LocalTransferServer(
                    destination, "135790", host="127.0.0.1", port=0, timeout=3
                )

                def receive():
                    try:
                        transfer_outcome["result"] = server.serve_once()
                    except BaseException as exc:
                        transfer_outcome["error"] = exc

                server_worker = threading.Thread(target=receive, daemon=True)
                server_worker.start()
                server.ready_event.wait(2)
                service.respond_to_request(
                    data["request_id"], accepted=True,
                    transfer_port=server.bound_port, pairing_code="135790",
                )

            service = network.DiscoveryService(
                callback=callback, port=0, device_name="Nearby PC", instance_id="nearby-id"
            )
            service.start()
            try:
                devices = network.discover_devices(
                    "sender-id", port=service.bound_port, duration=0.3,
                    targets=["127.0.0.1"],
                    exclude_local=False,
                )
                invitation = network.request_transfer(
                    devices[0], "sender-id", "Sender PC", [source], timeout=3
                )
                sent = network.LocalTransferClient(
                    invitation.host, invitation.port, invitation.pairing_code, timeout=3
                ).send_files([source])
                server_worker.join(3)
            finally:
                service.stop()

            self.assertEqual(sent.files, 1)
            self.assertNotIn("error", transfer_outcome)
            self.assertEqual(
                (destination / source.name).read_text(encoding="utf-8"),
                "AutoSD nearby transfer",
            )


class InternetP2PTests(unittest.TestCase):
    def test_invitation_round_trip_and_expiration(self):
        invitation = network.InternetInvitation(
            host="203.0.113.8",
            port=48722,
            pairing_code="123456",
            tls_fingerprint="ab" * 32,
            expires_at=2_000_000_000,
            device_name="My PC",
        )

        decoded = network.InternetInvitation.decode(invitation.encode(), now=1_900_000_000)

        self.assertEqual(decoded, invitation)
        with self.assertRaises(network.NetworkTransferError):
            network.InternetInvitation.decode(invitation.encode(), now=2_000_000_001)
        with self.assertRaises(network.NetworkTransferError):
            network.InternetInvitation.decode(
                network.INTERNET_INVITATION_PREFIX + "A" * 5000,
                now=1_900_000_000,
            )

    def test_upnp_mapping_uses_public_address_and_can_be_closed(self):
        actions = []

        def action(_url, _service, name, arguments, timeout=4.0):
            actions.append((name, arguments, timeout))
            if name == "GetExternalIPAddress":
                return {"NewExternalIPAddress": "8.8.8.8"}
            return {}

        with (
            mock.patch.object(
                network, "_discover_upnp_service",
                return_value=("http://router/control", "urn:test:WANIPConnection:1"),
            ),
            mock.patch.object(network, "_upnp_action", side_effect=action),
            mock.patch.object(network, "local_ip_address", return_value="192.168.1.10"),
        ):
            mapping = network.create_upnp_mapping(48722)
            mapping.close()

        self.assertEqual(mapping.external_ip, "8.8.8.8")
        self.assertEqual(mapping.external_port, 48722)
        self.assertEqual(
            [name for name, _arguments, _timeout in actions],
            ["GetExternalIPAddress", "AddPortMapping", "DeletePortMapping"],
        )

    def test_cgnat_address_is_rejected(self):
        with (
            mock.patch.object(
                network, "_discover_upnp_service",
                return_value=("http://router/control", "urn:test:WANIPConnection:1"),
            ),
            mock.patch.object(
                network, "_upnp_action",
                return_value={"NewExternalIPAddress": "100.64.1.2"},
            ),
        ):
            with self.assertRaises(network.NetworkTransferError):
                network.create_upnp_mapping(48722)


if __name__ == "__main__":
    unittest.main()
