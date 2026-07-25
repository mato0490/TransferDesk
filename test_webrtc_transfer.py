import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import webrtc_transfer as webtransfer


class ConnectionCodeTests(unittest.TestCase):
    def test_code_round_trip_and_description_authentication(self) -> None:
        secret = webtransfer.generate_auth_secret()
        code = webtransfer.connection_code("ABCD-EFGH", secret)
        room_id, decoded_secret = webtransfer.parse_connection_code(code)

        self.assertEqual(room_id, "ABCD-EFGH")
        self.assertEqual(decoded_secret, secret)
        signed = webtransfer._signed_description(secret, "offer", "v=0\r\n")
        self.assertEqual(
            webtransfer._verify_description(secret, signed, "offer"),
            "v=0\r\n",
        )
        signed["sdp"] = "v=0\r\na=modified\r\n"
        with self.assertRaises(webtransfer.UniversalTransferError):
            webtransfer._verify_description(secret, signed, "offer")

    def test_service_url_can_be_loaded_from_environment(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"AUTOSD_RENDEZVOUS_URL": "https://connect.example.test/"},
        ):
            self.assertEqual(
                webtransfer.default_rendezvous_url(),
                "https://connect.example.test",
            )

    def test_cloudflare_service_urls_are_rejected(self) -> None:
        for url in (
            "https://example.workers.dev",
            "https://rtc.live.cloudflare.com",
            "https://example.pages.dev",
        ):
            with self.subTest(url=url):
                with self.assertRaises(webtransfer.UniversalTransferError):
                    webtransfer.normalize_rendezvous_url(url)

    def test_service_url_can_be_loaded_from_local_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / "autosd-network.json").write_text(
                '{"rendezvous_url":"https://local.example.test"}', encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
                webtransfer.Path, "cwd", return_value=folder
            ):
                self.assertEqual(
                    webtransfer.default_rendezvous_url(),
                    "https://local.example.test",
                )

    def test_ice_server_fallback_and_port_53_filter(self) -> None:
        fallback = webtransfer._parse_ice_servers(None)
        self.assertEqual(fallback[0].urls, (webtransfer.DEFAULT_STUN_URL,))
        parsed = webtransfer._parse_ice_servers([
            {
                "urls": [
                    "turn:turn.example.test:53?transport=udp",
                    "turns:turn.example.test:443?transport=tcp",
                ],
                "username": "user",
                "credential": "secret",
            }
        ])
        self.assertEqual(
            parsed[0].urls,
            ("turns:turn.example.test:443?transport=tcp",),
        )

    def test_connection_timeout_explains_missing_turn_relay(self) -> None:
        stun_only = (webtransfer.IceServer(("stun:stun.example.test:3478",)),)
        with_turn = (
            webtransfer.IceServer(("stun:stun.example.test:3478",)),
            webtransfer.IceServer(("turns:turn.example.test:443",)),
        )

        self.assertIn("aucun relais TURN", webtransfer._connection_timeout_message(stun_only))
        self.assertNotIn("aucun relais TURN", webtransfer._connection_timeout_message(with_turn))

    def test_manual_offer_and_answer_round_trip(self) -> None:
        secret = webtransfer.generate_auth_secret()
        expires_at = int(webtransfer.time.time()) + 60
        offer = webtransfer.encode_manual_offer(secret, "v=0\r\no=offer\r\n", expires_at)
        decoded_secret, offer_sdp, decoded_expiry = webtransfer.decode_manual_offer(offer)
        self.assertEqual((decoded_secret, offer_sdp, decoded_expiry), (
            secret, "v=0\r\no=offer\r\n", expires_at
        ))

        answer = webtransfer.encode_manual_answer(secret, "v=0\r\no=answer\r\n", expires_at)
        answer_sdp, answer_expiry = webtransfer.decode_manual_answer(answer, secret)
        self.assertEqual((answer_sdp, answer_expiry), (
            "v=0\r\no=answer\r\n", expires_at
        ))
        with self.assertRaises(webtransfer.UniversalTransferError):
            webtransfer.decode_manual_answer(answer, webtransfer.generate_auth_secret())

    def test_expired_manual_offer_is_rejected(self) -> None:
        secret = webtransfer.generate_auth_secret()
        offer = webtransfer.encode_manual_offer(
            secret, "v=0\r\n", int(webtransfer.time.time()) - 1
        )
        with self.assertRaisesRegex(
            webtransfer.UniversalTransferError, "expir"
        ):
            webtransfer.decode_manual_offer(offer)


class WebRTCDataChannelTests(unittest.TestCase):
    SIGNALING_TIMEOUT = 10

    def test_encrypted_data_channel_transfers_file(self) -> None:
        asyncio.run(self._round_trip())

    def test_manual_copy_paste_negotiation_transfers_file(self) -> None:
        with tempfile.TemporaryDirectory() as source_raw, tempfile.TemporaryDirectory() as destination_raw:
            source = Path(source_raw) / "manual.bin"
            payload = os.urandom(96_000)
            source.write_bytes(payload)
            destination = Path(destination_raw)
            cancel_event = threading.Event()
            offer_ready = threading.Event()
            offer_holder: dict[str, str] = {}
            results: dict[str, object] = {}
            errors: list[BaseException] = []

            def sender_callback(name: str, data: dict) -> None:
                if name == "manual_offer_ready":
                    offer_holder["value"] = str(data["payload"])
                    offer_ready.set()

            sender = webtransfer.ManualSender(
                cancel_event, sender_callback, ice_servers=()
            )

            def run_sender() -> None:
                try:
                    results["sender"] = sender.send_files([source])
                except BaseException as exc:
                    errors.append(exc)

            sender_thread = threading.Thread(target=run_sender)
            sender_thread.start()
            self.assertTrue(offer_ready.wait(self.SIGNALING_TIMEOUT))

            def receiver_callback(name: str, data: dict) -> None:
                if name == "manual_answer_ready":
                    sender.accept_answer(str(data["payload"]))

            receiver = webtransfer.ManualReceiver(
                destination,
                offer_holder["value"],
                cancel_event,
                receiver_callback,
                ice_servers=(),
            )

            def run_receiver() -> None:
                try:
                    results["receiver"] = receiver.receive_once()
                except BaseException as exc:
                    errors.append(exc)

            receiver_thread = threading.Thread(target=run_receiver)
            receiver_thread.start()
            sender_thread.join(self.SIGNALING_TIMEOUT)
            receiver_thread.join(self.SIGNALING_TIMEOUT)
            if sender_thread.is_alive() or receiver_thread.is_alive():
                cancel_event.set()
                sender_thread.join(3)
                receiver_thread.join(3)
            self.assertFalse(sender_thread.is_alive())
            self.assertFalse(receiver_thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(results["sender"].files, 1)
            self.assertEqual(results["receiver"].files, 1)
            self.assertEqual((destination / source.name).read_bytes(), payload)

    async def _round_trip(self) -> None:
        from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

        with tempfile.TemporaryDirectory() as source_raw, tempfile.TemporaryDirectory() as destination_raw:
            source = Path(source_raw) / "large.bin"
            payload = os.urandom(180_000)
            source.write_bytes(payload)
            destination = Path(destination_raw)
            cancel_event = threading.Event()
            sender_pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            receiver_pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            receiver_channel = asyncio.get_running_loop().create_future()

            @receiver_pc.on("datachannel")
            def on_datachannel(channel) -> None:
                receiver_channel.set_result(channel)

            channel = sender_pc.createDataChannel("autosd-test", ordered=True)
            try:
                offer = await asyncio.wait_for(
                    sender_pc.createOffer(), self.SIGNALING_TIMEOUT
                )
                await asyncio.wait_for(
                    sender_pc.setLocalDescription(offer), self.SIGNALING_TIMEOUT
                )
                await asyncio.wait_for(
                    receiver_pc.setRemoteDescription(RTCSessionDescription(
                        sdp=sender_pc.localDescription.sdp,
                        type="offer",
                    )),
                    self.SIGNALING_TIMEOUT,
                )
                answer = await asyncio.wait_for(
                    receiver_pc.createAnswer(), self.SIGNALING_TIMEOUT
                )
                await asyncio.wait_for(
                    receiver_pc.setLocalDescription(answer), self.SIGNALING_TIMEOUT
                )
                await asyncio.wait_for(
                    sender_pc.setRemoteDescription(RTCSessionDescription(
                        sdp=receiver_pc.localDescription.sdp,
                        type="answer",
                    )),
                    self.SIGNALING_TIMEOUT,
                )
                received_channel = await asyncio.wait_for(receiver_channel, 10)
                await webtransfer._wait_channel_open(channel, cancel_event, 10)
                receive_task = asyncio.create_task(webtransfer._receive_files(
                    received_channel,
                    destination,
                    cancel_event,
                    lambda _event, _data: None,
                ))
                sent = await webtransfer._send_files(
                    channel,
                    [source],
                    cancel_event,
                    lambda _event, _data: None,
                )
                received = await asyncio.wait_for(receive_task, 10)
                self.assertEqual(sent.files, 1)
                self.assertEqual(received.bytes_transferred, len(payload))
                self.assertEqual((destination / "large.bin").read_bytes(), payload)
            finally:
                await asyncio.gather(
                    webtransfer._close_peer(sender_pc),
                    webtransfer._close_peer(receiver_pc),
                )


if __name__ == "__main__":
    unittest.main()
