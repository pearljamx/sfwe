from __future__ import annotations

import unittest
import socket
import zlib
from base64 import b64encode

from hardware_runtime import HardwareRuntime, extract_packet_frames, parse_command_suffix, parse_sensor_payload, split_buffered_frames


SAMPLE_TEXT = "DATA:温度值:0.1 PH值:7.0 食物值:46 Salinity:14.3 氧气值:4.0 NH3:0.5 NO2:0.2\t\r\n"


class HardwareRuntimeParserTests(unittest.TestCase):
    def test_parse_sensor_payload_with_data_prefix(self):
        payload = parse_sensor_payload(SAMPLE_TEXT)
        self.assertEqual(
            payload,
            {
                "temperature": 0.1,
                "ph_value": 7.0,
                "food_value": 46.0,
                "salinity": 14.3,
                "dissolved_oxygen": 4.0,
                "ammonia_nitrogen": 0.5,
                "nitrite_nitrogen": 0.2,
            },
        )

    def test_split_buffered_frames(self):
        encoded = (SAMPLE_TEXT * 2).encode("gb2312")
        frames, remainder = split_buffered_frames(encoded)
        self.assertEqual(len(frames), 2)
        self.assertEqual(remainder, b"")
        self.assertTrue(frames[0].decode("gb2312").startswith("DATA:"))

    def test_extract_packet_frames_without_delimiter_keeps_packet(self):
        encoded = SAMPLE_TEXT.strip().encode("gb2312")
        frames = extract_packet_frames(encoded)
        self.assertEqual(len(frames), 1)
        self.assertIn("DATA:", frames[0].decode("gb2312"))

    def test_invalid_frame_raises(self):
        with self.assertRaises(ValueError):
            parse_sensor_payload("DATA:温度值:0.1 PH值:7.0 Salinity:14.3 氧气值:4.0 NH3:0.5 NO2:0.2")

    def test_send_command_without_sender_fails(self):
        runtime = HardwareRuntime(callback=lambda *_args, **_kwargs: None, bind_host="127.0.0.1", bind_port=0, command_port=8081)
        result = runtime.send_command("CMD:PUMP_ON")
        self.assertFalse(result["ok"])
        self.assertIn("尚未收到设备数据", result["message"])

    def test_send_command_uses_latest_sender_port(self):
        accepted = []
        runtime = HardwareRuntime(
            callback=lambda payload, *_args, **_kwargs: accepted.append(payload),
            bind_host="127.0.0.1",
            bind_port=0,
            command_port=8081,
        )
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(1.0)
            source_port = receiver.getsockname()[1]

            runtime._handle_packet(SAMPLE_TEXT.encode("gb2312"), ("127.0.0.1", source_port))
            result = runtime.send_command("CMD:OXYGEN_ON")
            packet, address = receiver.recvfrom(128)

        self.assertTrue(result["ok"])
        self.assertEqual(result["target_port"], source_port)
        self.assertEqual(packet, b"CMD:OXYGEN_ON\r\n")
        self.assertEqual(address[0], "127.0.0.1")

    def test_send_command_falls_back_to_configured_command_port(self):
        runtime = HardwareRuntime(callback=lambda *_args, **_kwargs: None, bind_host="127.0.0.1", bind_port=0, command_port=8081)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(1.0)
            fallback_port = receiver.getsockname()[1]
            runtime.command_port = fallback_port
            runtime.last_sender_ip = "127.0.0.1"
            runtime.last_sender_port = None

            result = runtime.send_command("CMD:PUMP_ON")
            packet, _address = receiver.recvfrom(128)

        self.assertTrue(result["ok"])
        self.assertEqual(result["target_port"], fallback_port)
        self.assertEqual(packet, b"CMD:PUMP_ON\r\n")

    def test_send_command_supports_empty_suffix(self):
        runtime = HardwareRuntime(callback=lambda *_args, **_kwargs: None, bind_host="127.0.0.1", bind_port=0, command_port=8081)
        runtime.command_suffix = ""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(1.0)
            runtime.command_port = receiver.getsockname()[1]
            runtime.last_sender_ip = "127.0.0.1"

            result = runtime.send_command("CMD:PUMP_ON")
            packet, _address = receiver.recvfrom(128)

        self.assertTrue(result["ok"])
        self.assertEqual(packet, b"CMD:PUMP_ON")

    def test_parse_command_suffix_escapes(self):
        self.assertEqual(parse_command_suffix(None), "\r\n")
        self.assertEqual(parse_command_suffix(r"\r\n"), "\r\n")
        self.assertEqual(parse_command_suffix(""), "")

    def test_mixed_frames_drop_entire_packet(self):
        accepted = []
        runtime = HardwareRuntime(
            callback=lambda payload, *_args, **_kwargs: accepted.append(payload),
            bind_host="127.0.0.1",
            bind_port=0,
            command_port=8081,
        )
        packet = SAMPLE_TEXT.encode("gb2312") + b"\nBAD:payload"
        runtime._handle_packet(packet, ("127.0.0.1", 8080))
        self.assertEqual(accepted, [])
        self.assertEqual(runtime.parsed_packets, 0)
        self.assertEqual(runtime.error_count, 1)

    def test_udp_image_chunks_can_reassemble_out_of_order(self):
        images = []
        image = b"\x89PNG\r\n\x1a\nfake-image-bytes"
        crc = f"{zlib.crc32(image) & 0xFFFFFFFF:08x}"
        chunk_a = image[:10]
        chunk_b = image[10:]
        runtime = HardwareRuntime(
            callback=lambda *_args, **_kwargs: None,
            image_callback=lambda data, _timestamp, meta: images.append((data, meta)),
            bind_host="127.0.0.1",
            bind_port=0,
            command_port=8081,
        )
        runtime._handle_packet(f"IMG:START:img001:29:test.png:{len(image)}:2:{crc}".encode("ascii"), ("127.0.0.1", 8080))
        runtime._handle_packet(f"IMG:CHUNK:img001:1:{b64encode(chunk_b).decode('ascii')}".encode("ascii"), ("127.0.0.1", 8080))
        runtime._handle_packet(f"IMG:CHUNK:img001:0:{b64encode(chunk_a).decode('ascii')}".encode("ascii"), ("127.0.0.1", 8080))
        runtime._handle_packet(b"IMG:END:img001", ("127.0.0.1", 8080))
        self.assertEqual(images[0][0], image)
        self.assertEqual(images[0][1]["pond_id"], 29)
        self.assertEqual(runtime.image_completed, 1)

    def test_udp_image_crc_error_is_rejected(self):
        images = []
        image = b"not-a-real-image"
        runtime = HardwareRuntime(
            callback=lambda *_args, **_kwargs: None,
            image_callback=lambda data, _timestamp, meta: images.append((data, meta)),
            bind_host="127.0.0.1",
            bind_port=0,
            command_port=8081,
        )
        runtime._handle_packet(f"IMG:START:img002:29:test.jpg:{len(image)}:1:00000000".encode("ascii"), ("127.0.0.1", 8080))
        runtime._handle_packet(f"IMG:CHUNK:img002:0:{b64encode(image).decode('ascii')}".encode("ascii"), ("127.0.0.1", 8080))
        runtime._handle_packet(b"IMG:END:img002", ("127.0.0.1", 8080))
        self.assertEqual(images, [])
        self.assertEqual(runtime.image_failed, 1)
        self.assertIn("CRC32", runtime.last_image_error)


if __name__ == "__main__":
    unittest.main()
