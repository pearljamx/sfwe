from __future__ import annotations

import logging
import os
import re
import socket
import threading
import time
import zlib
from base64 import b64decode
from datetime import datetime
from typing import Any, Callable


LOGGER = logging.getLogger(__name__)
DELIMITERS = (b"\t\r\n", b"\r\n", b"\n")
FIELD_PATTERNS = {
    "temperature": re.compile(r"温度值\s*[:：]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    "ph_value": re.compile(r"PH值\s*[:：]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    "food_value": re.compile(r"食物值\s*[:：]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    "salinity": re.compile(r"Salinity\s*[:：]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    "dissolved_oxygen": re.compile(r"氧气值\s*[:：]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    "ammonia_nitrogen": re.compile(r"NH3\s*[:：]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    "nitrite_nitrogen": re.compile(r"NO2\s*[:：]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
}

FrameCallback = Callable[[dict[str, float], datetime, dict[str, Any]], Any]
ImageCallback = Callable[[bytes, datetime, dict[str, Any]], Any]


def preview_hex(raw: bytes, limit: int = 64) -> str:
    text = raw[:limit].hex(" ")
    if len(raw) > limit:
        return f"{text} ..."
    return text


def preview_text(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    if len(compact) > limit:
        return f"{compact[:limit]}..."
    return compact


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def parse_command_suffix(value: str | None) -> str:
    if value is None:
        return "\r\n"
    if value == "":
        return ""
    return value.encode("utf-8").decode("unicode_escape")


def split_buffered_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    frames: list[bytes] = []
    while buffer:
        candidates = []
        for delimiter in DELIMITERS:
            index = buffer.find(delimiter)
            if index != -1:
                candidates.append((index, len(delimiter)))
        if not candidates:
            break
        index, width = min(candidates, key=lambda item: item[0])
        frames.append(buffer[:index])
        buffer = buffer[index + width :]
    return frames, buffer


def extract_packet_frames(packet: bytes) -> list[bytes]:
    frames, remainder = split_buffered_frames(packet)
    candidates = list(frames)
    if remainder.strip():
        candidates.append(remainder)
    if not candidates and packet.strip():
        candidates.append(packet)
    return [item.strip() for item in candidates if item and item.strip()]


def parse_sensor_payload(text: str) -> dict[str, float]:
    normalized = normalize_text(text.strip())
    if normalized.startswith("DATA:"):
        normalized = normalized[5:].strip()
    payload: dict[str, float] = {}
    for key, pattern in FIELD_PATTERNS.items():
        match = pattern.search(normalized)
        if not match:
            raise ValueError(f"缺少字段 {key}")
        payload[key] = float(match.group(1))
    return payload


class HardwareRuntime:
    def __init__(
        self,
        callback: FrameCallback,
        image_callback: ImageCallback | None = None,
        bind_host: str | None = None,
        bind_port: int | None = None,
        command_port: int | None = None,
        encoding: str | None = None,
    ):
        self.callback = callback
        self.image_callback = image_callback
        self.mode = "udp"
        self.bind_host = bind_host or os.getenv("UDP_BIND_HOST", "0.0.0.0")
        self.bind_port = int(bind_port if bind_port is not None else os.getenv("UDP_BIND_PORT", "8080"))
        self.command_port = int(command_port if command_port is not None else os.getenv("UDP_SEND_PORT", "8081"))
        self.encoding = encoding or os.getenv("UDP_ENCODING", "gb2312")
        self.command_suffix = parse_command_suffix(os.getenv("UDP_COMMAND_SUFFIX", r"\r\n"))
        self.recv_size = int(os.getenv("UDP_RECV_SIZE", "8192"))
        self.image_timeout_seconds = int(os.getenv("UDP_IMAGE_TIMEOUT_SECONDS", "30"))
        self.supported = True

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._image_sessions: dict[str, dict[str, Any]] = {}

        self.running = False
        self.total_bytes = 0
        self.total_packets = 0
        self.parsed_packets = 0
        self.error_count = 0
        self.last_packet_time: str | None = None
        self.last_sender_ip: str | None = None
        self.last_sender_port: int | None = None
        self.last_error: str | None = None
        self.last_text_preview: str | None = None
        self.last_hex_preview: str | None = None
        self.image_packets = 0
        self.image_completed = 0
        self.image_failed = 0
        self.image_bytes_received = 0
        self.last_image_time: str | None = None
        self.last_image_id: str | None = None
        self.last_image_error: str | None = None

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {"ok": True, "message": "UDP 接收已在运行"}
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.bind_host, self.bind_port))
                sock.settimeout(0.5)
            except Exception as exc:
                self.last_error = f"打开 UDP 监听失败: {exc}"
                return {"ok": False, "message": self.last_error}

            self._socket = sock
            self.bind_port = int(sock.getsockname()[1])
            self._stop_event.clear()
            self.running = True
            self.last_error = None
            self._thread = threading.Thread(target=self._run, name="sfweb-udp-runtime", daemon=True)
            self._thread.start()
            return {"ok": True, "message": "UDP 接收已启动"}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self.running and not self._thread:
                return {"ok": True, "message": "UDP 接收已停止"}
            self.running = False
            self._stop_event.set()
            active_thread = self._thread
            active_socket = self._socket
            self._thread = None
            self._socket = None

        if active_socket is not None:
            try:
                active_socket.close()
            except Exception:
                LOGGER.exception("关闭 UDP 套接字失败")
        if active_thread and active_thread.is_alive():
            active_thread.join(timeout=2.0)
        return {"ok": True, "message": "UDP 接收已停止"}

    def status(self, enabled_ponds: int = 0) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self.mode,
                "supported": self.supported,
                "running": self.running,
                "bind_host": self.bind_host,
                "bind_port": self.bind_port,
                "command_port": self.command_port,
                "encoding": self.encoding,
                "total_packets": self.total_packets,
                "parsed_packets": self.parsed_packets,
                "total_bytes": self.total_bytes,
                "error_count": self.error_count,
                "last_packet_time": self.last_packet_time,
                "last_sender_ip": self.last_sender_ip,
                "last_sender_port": self.last_sender_port,
                "last_text_preview": self.last_text_preview,
                "last_hex_preview": self.last_hex_preview,
                "last_error": self.last_error,
                "enabled_ponds": enabled_ponds,
                "image_sessions": len(self._image_sessions),
                "image_packets": self.image_packets,
                "image_completed": self.image_completed,
                "image_failed": self.image_failed,
                "image_bytes_received": self.image_bytes_received,
                "last_image_time": self.last_image_time,
                "last_image_id": self.last_image_id,
                "last_image_error": self.last_image_error,
            }

    def send_command(self, command: str) -> dict[str, Any]:
        with self._lock:
            target_ip = self.last_sender_ip
            target_port = self.last_sender_port or self.command_port
            encoding = self.encoding

        if not target_ip:
            return {"ok": False, "message": "尚未收到设备数据，无法确定下发目标"}

        try:
            payload = f"{command}{self.command_suffix}".encode(encoding)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sent_bytes = sock.sendto(payload, (target_ip, target_port))
            return {
                "ok": True,
                "command": command,
                "target_ip": target_ip,
                "target_port": target_port,
                "sent_bytes": sent_bytes,
                "message": "UDP 指令已发出",
            }
        except Exception as exc:
            return {
                "ok": False,
                "command": command,
                "target_ip": target_ip,
                "target_port": target_port,
                "message": f"UDP 指令发送失败: {exc}",
            }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._socket is None:
                    break
                packet, address = self._socket.recvfrom(self.recv_size)
            except socket.timeout:
                continue
            except OSError as exc:
                if self._stop_event.is_set():
                    break
                self._record_error(exc, b"", None)
                break
            except Exception as exc:
                self._record_error(exc, b"", None)
                break

            with self._lock:
                self.total_packets += 1
                self.total_bytes += len(packet)
                self.last_hex_preview = preview_hex(packet)
            self._handle_packet(packet, address)

        with self._lock:
            self.running = False

    def _handle_packet(self, packet: bytes, address: tuple[str, int]) -> None:
        timestamp = datetime.now()
        try:
            if packet.strip().startswith(b"IMG:"):
                self._handle_image_packet(packet, address, timestamp)
                return

            frames = extract_packet_frames(packet)
            if not frames:
                raise ValueError("空 UDP 数据包")

            parsed_frames: list[tuple[dict[str, float], dict[str, Any]]] = []
            for raw_frame in frames:
                text = raw_frame.decode(self.encoding)
                normalized = normalize_text(text)
                if not normalized.startswith("DATA:"):
                    raise ValueError("不是 DATA 帧")
                payload = parse_sensor_payload(normalized)
                meta = {
                    "text_preview": preview_text(text),
                    "hex_preview": preview_hex(raw_frame),
                    "sender_ip": address[0],
                    "sender_port": address[1],
                }
                parsed_frames.append((payload, meta))

            for payload, meta in parsed_frames:
                self.callback(payload, timestamp, meta)

            last_meta = parsed_frames[-1][1]

            with self._lock:
                self.parsed_packets += len(parsed_frames)
                self.last_packet_time = timestamp.strftime("%Y-%m-%d %H:%M:%S")
                self.last_sender_ip = address[0]
                self.last_sender_port = address[1]
                self.last_text_preview = last_meta["text_preview"]
                self.last_hex_preview = last_meta["hex_preview"]
        except Exception as exc:
            self._record_error(exc, packet, address)

    def _handle_image_packet(self, packet: bytes, address: tuple[str, int], timestamp: datetime) -> None:
        now = time.monotonic()
        self._expire_image_sessions(now)
        try:
            text = packet.decode("ascii").strip()
            if text.startswith("IMG:START:"):
                self._handle_image_start(text, address, now, timestamp, packet)
                return
            if text.startswith("IMG:CHUNK:"):
                self._handle_image_chunk(text, address, now, timestamp, packet)
                return
            if text.startswith("IMG:END:"):
                self._handle_image_end(text, address, timestamp, packet)
                return
            raise ValueError("未知 IMG 数据包")
        except Exception as exc:
            self._record_image_error(exc, packet, address)

    def _handle_image_start(
        self,
        text: str,
        address: tuple[str, int],
        now: float,
        timestamp: datetime,
        raw: bytes,
    ) -> None:
        parts = text.split(":", 7)
        if len(parts) != 8:
            raise ValueError("IMG START 字段不完整")
        _, _, image_id, pond_id, filename, total_size, total_chunks, crc32 = parts
        total_size_int = int(total_size)
        total_chunks_int = int(total_chunks)
        if total_size_int <= 0 or total_chunks_int <= 0:
            raise ValueError("IMG START 图片大小或分片数量无效")
        with self._lock:
            self._image_sessions[image_id] = {
                "image_id": image_id,
                "pond_id": int(pond_id),
                "filename": filename,
                "total_size": total_size_int,
                "total_chunks": total_chunks_int,
                "crc32": crc32.lower().replace("0x", ""),
                "chunks": {},
                "sender_ip": address[0],
                "sender_port": address[1],
                "created_at": now,
                "updated_at": now,
            }
            self.image_packets += 1
            self.last_packet_time = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            self.last_sender_ip = address[0]
            self.last_sender_port = address[1]
            self.last_text_preview = preview_text(text)
            self.last_hex_preview = preview_hex(raw)

    def _handle_image_chunk(
        self,
        text: str,
        address: tuple[str, int],
        now: float,
        timestamp: datetime,
        raw: bytes,
    ) -> None:
        parts = text.split(":", 4)
        if len(parts) != 5:
            raise ValueError("IMG CHUNK 字段不完整")
        _, _, image_id, chunk_index, payload = parts
        index = int(chunk_index)
        decoded = b64decode(payload.encode("ascii"), validate=True)
        with self._lock:
            session = self._image_sessions.get(image_id)
            if not session:
                raise ValueError(f"未知图片会话 {image_id}")
            if index < 0 or index >= int(session["total_chunks"]):
                raise ValueError("IMG CHUNK 分片序号越界")
            session["chunks"][index] = decoded
            session["updated_at"] = now
            self.image_packets += 1
            self.image_bytes_received += len(decoded)
            self.last_packet_time = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            self.last_sender_ip = address[0]
            self.last_sender_port = address[1]
            self.last_text_preview = f"IMG:CHUNK:{image_id}:{index} ({len(session['chunks'])}/{session['total_chunks']})"
            self.last_hex_preview = preview_hex(raw)

    def _handle_image_end(self, text: str, address: tuple[str, int], timestamp: datetime, raw: bytes) -> None:
        parts = text.split(":", 2)
        if len(parts) != 3:
            raise ValueError("IMG END 字段不完整")
        image_id = parts[2]
        with self._lock:
            session = self._image_sessions.get(image_id)
            if not session:
                raise ValueError(f"未知图片会话 {image_id}")
            total_chunks = int(session["total_chunks"])
            chunks = session["chunks"]
            missing = [index for index in range(total_chunks) if index not in chunks]
            if missing:
                raise ValueError(f"IMG 图片缺少分片 {missing[:5]}")
            image_bytes = b"".join(chunks[index] for index in range(total_chunks))
            if len(image_bytes) != int(session["total_size"]):
                raise ValueError("IMG 图片大小校验失败")
            actual_crc = f"{zlib.crc32(image_bytes) & 0xFFFFFFFF:08x}"
            if actual_crc != str(session["crc32"]).lower():
                raise ValueError("IMG CRC32 校验失败")
            meta = {
                "image_id": image_id,
                "pond_id": session["pond_id"],
                "filename": session["filename"],
                "total_size": session["total_size"],
                "total_chunks": total_chunks,
                "crc32": actual_crc,
                "sender_ip": address[0],
                "sender_port": address[1],
            }
            del self._image_sessions[image_id]

        if not self.image_callback:
            raise ValueError("图片处理回调未配置")
        self.image_callback(image_bytes, timestamp, meta)

        with self._lock:
            self.image_packets += 1
            self.image_completed += 1
            self.last_image_time = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            self.last_image_id = image_id
            self.last_image_error = None
            self.last_packet_time = self.last_image_time
            self.last_sender_ip = address[0]
            self.last_sender_port = address[1]
            self.last_text_preview = f"IMG:END:{image_id}"
            self.last_hex_preview = preview_hex(raw)

    def _expire_image_sessions(self, now: float) -> None:
        with self._lock:
            expired = [
                image_id
                for image_id, session in self._image_sessions.items()
                if now - float(session["updated_at"]) > self.image_timeout_seconds
            ]
            for image_id in expired:
                del self._image_sessions[image_id]
            if expired:
                self.image_failed += len(expired)
                self.last_image_error = f"{len(expired)} 个图片会话接收超时"

    def _record_image_error(self, exc: Exception, raw: bytes, address: tuple[str, int] | None) -> None:
        with self._lock:
            self.image_failed += 1
            self.last_image_error = str(exc)
        self._record_error(exc, raw, address)

    def _record_error(self, exc: Exception, raw: bytes, address: tuple[str, int] | None) -> None:
        LOGGER.warning("UDP 数据包处理失败: %s", exc)
        with self._lock:
            self.error_count += 1
            self.last_error = str(exc)
            if address:
                self.last_sender_ip = address[0]
                self.last_sender_port = address[1]
            if raw:
                try:
                    self.last_text_preview = preview_text(raw.decode(self.encoding))
                except Exception:
                    self.last_text_preview = None
                self.last_hex_preview = preview_hex(raw)
