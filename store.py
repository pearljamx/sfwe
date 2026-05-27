from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal
from threading import Lock
from typing import Any, Callable

import pymysql
from dotenv import load_dotenv


class DatabaseUnavailable(RuntimeError):
    pass


PARAMETERS = [
    ("temperature", "水温", "degC"),
    ("ph_value", "pH", ""),
    ("food_value", "食物值", ""),
    ("salinity", "盐度", "ppt"),
    ("dissolved_oxygen", "溶解氧", "mg/L"),
    ("ammonia_nitrogen", "氨氮", "mg/L"),
    ("nitrite_nitrogen", "亚硝酸盐", "mg/L"),
]

PARAMETER_META = {key: {"name": name, "unit": unit} for key, name, unit in PARAMETERS}
DEDUPE_WINDOW_SECONDS = 5
DEVICE_COMMAND_MAP = {
    "pump": {"start": "CMD:PUMP_ON", "stop": "CMD:PUMP_OFF"},
    "aerator": {"start": "CMD:OXYGEN_ON", "stop": "CMD:OXYGEN_OFF"},
    "feeder": {"start": "CMD:FEEDER_ON", "stop": "CMD:FEEDER_OFF", "feed": "CMD:FEEDER_ON"},
}


def clean(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def format_number(value: Any) -> str:
    if value is None or value == "":
        return "-"
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def same_metrics(left: dict[str, Any], right: dict[str, Any]) -> bool:
    for key, _, _ in PARAMETERS:
        if abs(float(left.get(key) or 0) - float(right.get(key) or 0)) > 1e-9:
            return False
    return True


def risk_score(row: dict[str, Any]) -> int:
    score = 12
    oxygen = float(row.get("dissolved_oxygen") or 0)
    ph = float(row.get("ph_value") or 7)
    ammonia = float(row.get("ammonia_nitrogen") or 0)
    nitrite = float(row.get("nitrite_nitrogen") or 0)
    if oxygen < 5.5:
        score += 28
    elif oxygen < 6.5:
        score += 14
    if ph < 6.8 or ph > 8.4:
        score += 18
    if ammonia > 2.5:
        score += 24
    elif ammonia > 2.0:
        score += 12
    if nitrite > 1.2:
        score += 18
    return min(score, 96)


class FisheryStore:
    def __init__(
        self,
        connection: Any | None,
        database: str,
        connect_kwargs: dict[str, Any],
        last_error: str | None = None,
    ):
        self.connection = connection
        self.database = database
        self.connect_kwargs = connect_kwargs
        self.last_error = last_error
        self.available = connection is not None
        self._query_lock = Lock()

    @classmethod
    def from_env(cls) -> "FisheryStore":
        load_dotenv()
        database = os.getenv("MYSQL_DATABASE", "SFweb")
        connect_kwargs = {
            "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", "123456"),
            "database": database,
            "charset": "utf8mb4",
            "cursorclass": pymysql.cursors.DictCursor,
        }
        try:
            connection = pymysql.connect(**connect_kwargs, autocommit=True)
            return cls(connection, database, connect_kwargs)
        except Exception as exc:
            return cls(None, database, connect_kwargs, f"无法连接 MySQL 数据库 {database}: {exc}")

    def _ensure(self) -> None:
        if not self.available or not self.connection:
            raise DatabaseUnavailable(self.last_error or "数据库不可用")

    def _open_connection(self, autocommit: bool = True):
        return pymysql.connect(**self.connect_kwargs, autocommit=autocommit)

    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self._ensure()
        with self._query_lock:
            self.connection.ping(reconnect=True)
            with self.connection.cursor() as cursor:
                cursor.execute(sql, args)
                return clean(list(cursor.fetchall()))

    def one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def execute(self, sql: str, args: tuple[Any, ...] = ()) -> int:
        self._ensure()
        with self._query_lock:
            self.connection.ping(reconnect=True)
            with self.connection.cursor() as cursor:
                return cursor.execute(sql, args)

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        return self.one("SELECT * FROM users WHERE id=%s AND is_active=1", (user_id,))

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM users WHERE username=%s AND is_active=1", (username,))

    def touch_login(self, user_id: int) -> None:
        self.execute("UPDATE users SET last_login=NOW() WHERE id=%s", (user_id,))

    def hardware_enabled_count(self) -> int:
        row = self.one("SELECT COUNT(*) AS count FROM ponds WHERE hardware_ingest_enabled=1")
        return int(row["count"] or 0) if row else 0

    def set_hardware_ingest(self, pond_id: int, enabled: bool) -> dict[str, Any]:
        pond = self.one("SELECT id, pond_name FROM ponds WHERE id=%s", (pond_id,))
        if not pond:
            return {"ok": False, "message": "鱼池不存在"}
        self.execute("UPDATE ponds SET hardware_ingest_enabled=%s WHERE id=%s", (1 if enabled else 0, pond_id))
        return {"ok": True, "enabled": bool(enabled), "pond_name": pond["pond_name"]}

    def latest_ponds(self) -> list[dict[str, Any]]:
        rows = self.query(
            """
            SELECT p.id AS pond_id, p.pond_name, p.fish_type, p.fish_count, p.volume,
                   p.location, p.status, p.pond_code, p.hardware_ingest_enabled,
                   r.temperature, r.ph_value, r.food_value, r.dissolved_oxygen,
                   r.salinity, r.ammonia_nitrogen, r.nitrite_nitrogen, r.recorded_at,
                   sd.id AS sensor_device_id, sd.status AS sensor_status,
                   sd.work_state AS sensor_work_state, sd.last_active AS sensor_last_active
            FROM ponds p
            LEFT JOIN sensor_readings r ON r.id = (
                SELECT x.id FROM sensor_readings x
                WHERE x.pond_id = p.id
                ORDER BY x.recorded_at DESC, x.id DESC
                LIMIT 1
            )
            LEFT JOIN devices sd ON sd.id = (
                SELECT d.id FROM devices d
                WHERE d.pond_id = p.id AND d.device_type='sensor'
                ORDER BY d.id
                LIMIT 1
            )
            ORDER BY p.id
            """
        )
        for row in rows:
            row["hardware_ingest_enabled"] = bool(row.get("hardware_ingest_enabled"))
            row["risk_score"] = risk_score(row)
        return rows

    def trend(self, metric: str = "temperature", pond_id: int | None = None, limit: int = 24) -> dict[str, Any]:
        allowed = {key for key, _, _ in PARAMETERS}
        if metric not in allowed:
            metric = "temperature"
        args: list[Any] = []
        where = ""
        if pond_id:
            where = "WHERE pond_id=%s"
            args.append(pond_id)
        rows = self.query(
            f"""
            SELECT recorded_at, ROUND(AVG({metric}), 2) AS value
            FROM (
                SELECT pond_id, recorded_at, {metric}
                FROM sensor_readings
                {where}
                ORDER BY recorded_at DESC
                LIMIT %s
            ) t
            GROUP BY recorded_at
            ORDER BY recorded_at
            """,
            tuple(args + [limit]),
        )
        return {"labels": [str(row["recorded_at"])[11:16] for row in rows], "values": [row["value"] for row in rows]}

    def admin_dashboard(self) -> dict[str, Any]:
        ponds = self.latest_ponds()
        device_count = self.one("SELECT COUNT(*) AS count FROM devices")["count"]
        online_count = self.one("SELECT COUNT(*) AS count FROM devices WHERE status='online'")["count"]
        open_alarm_count = self.one("SELECT COUNT(*) AS count FROM alarms WHERE status <> 'resolved'")["count"]
        total_fish = sum(int(p["fish_count"] or 0) for p in ponds)
        avg_risk = round(sum(p["risk_score"] for p in ponds) / max(len(ponds), 1))
        return {
            "stats": [
                {"label": "鱼池运行", "value": len(ponds), "unit": "座"},
                {"label": "在线设备", "value": online_count, "unit": f"/ {device_count}"},
                {"label": "养殖尾数", "value": total_fish, "unit": "尾"},
                {"label": "待处理告警", "value": open_alarm_count, "unit": "条"},
                {"label": "综合风险", "value": avg_risk, "unit": "分"},
            ],
            "ponds": ponds,
            "alerts": self.alerts(limit=5),
            "ai": self.ai_records(limit=4),
            "trend": self.trend("temperature"),
            "oxygen": self.trend("dissolved_oxygen"),
            "device_mix": self.device_mix(),
        }

    def pond_workspace(self) -> dict[str, Any]:
        return {"ponds": self.latest_ponds(), "parameters": PARAMETERS, "trend": {key: self.trend(key) for key, _, _ in PARAMETERS}}

    def interconnect_workspace(self) -> dict[str, Any]:
        latest = self.latest_ponds()
        commands = self.query(
            """
            SELECT c.*, d.device_name, p.pond_name
            FROM device_commands c
            JOIN devices d ON d.id=c.device_id
            JOIN ponds p ON p.id=d.pond_id
            ORDER BY c.requested_at DESC
            LIMIT 12
            """
        )
        events = self.query(
            """
            SELECT 'sensor' AS event_type, p.pond_name, CONCAT('水质数据上传：', r.temperature, 'degC / pH ', r.ph_value) AS message, r.recorded_at AS event_time
            FROM sensor_readings r JOIN ponds p ON p.id=r.pond_id
            ORDER BY r.recorded_at DESC
            LIMIT 5
            """
        )
        events += self.query(
            """
            SELECT alarm_type AS event_type, COALESCE(p.pond_name, '全场') AS pond_name, message, a.created_at AS event_time
            FROM alarms a LEFT JOIN ponds p ON p.id=a.pond_id
            ORDER BY a.created_at DESC
            LIMIT 5
            """
        )
        events = sorted(events, key=lambda row: str(row["event_time"]), reverse=True)[:8]
        return {
            "cards": [
                {"title": "硬件端", "value": len(latest), "desc": "鱼池节点持续上传水质与设备状态"},
                {"title": "Web 端", "value": len(commands), "desc": "控制指令写入队列并形成日志闭环"},
                {"title": "VR 端", "value": len(self.ai_records(limit=20)), "desc": "孪生场景同步告警、AI 识别和设备状态"},
            ],
            "latest": latest,
            "commands": commands,
            "events": events,
        }

    def device_mix(self) -> list[dict[str, Any]]:
        return self.query("SELECT device_type AS name, COUNT(*) AS value FROM devices GROUP BY device_type ORDER BY value DESC")

    def device_workspace(self) -> dict[str, Any]:
        return {
            "devices": self.query(
                """
                SELECT d.*, p.pond_name
                FROM devices d JOIN ponds p ON p.id=d.pond_id
                ORDER BY p.id, d.device_type, d.id
                """
            ),
            "logs": self.query(
                """
                SELECT l.*, p.pond_name, d.device_name
                FROM device_logs l
                JOIN ponds p ON p.id=l.pond_id
                JOIN devices d ON d.id=l.device_id
                ORDER BY l.log_time DESC
                LIMIT 20
                """
            ),
            "mix": self.device_mix(),
        }

    def alerts(self, limit: int | None = None) -> list[dict[str, Any]]:
        suffix = "LIMIT %s" if limit else ""
        args = (limit,) if limit else ()
        return self.query(
            f"""
            SELECT a.*, p.pond_name, d.device_name
            FROM alarms a
            LEFT JOIN ponds p ON p.id=a.pond_id
            LEFT JOIN devices d ON d.id=a.device_id
            ORDER BY FIELD(a.status,'open','acknowledged','resolved'), a.created_at DESC
            {suffix}
            """,
            args,
        )

    def alert_workspace(self) -> dict[str, Any]:
        return {"alerts": self.alerts(), "thresholds": self.query("SELECT * FROM water_quality_thresholds ORDER BY id")}

    def ai_records(self, limit: int | None = None) -> list[dict[str, Any]]:
        suffix = "LIMIT %s" if limit else ""
        args = (limit,) if limit else ()
        return self.query(
            f"""
            SELECT a.*, p.pond_name
            FROM ai_diagnoses a JOIN ponds p ON p.id=a.pond_id
            ORDER BY a.created_at DESC
            {suffix}
            """,
            args,
        )

    def ai_workspace(self) -> dict[str, Any]:
        records = self.ai_records()
        return {
            "records": records,
            "ponds": self.latest_ponds(),
            "summary": [
                {"label": "识别记录", "value": len(records), "unit": "条"},
                {"label": "待复核", "value": sum(1 for item in records if item["status"] == "pending"), "unit": "条"},
                {"label": "高风险", "value": sum(1 for item in records if item["severity"] in {"high", "critical"}), "unit": "条"},
            ],
        }

    def record_ai_diagnosis(self, record: dict[str, Any], alarm_confidence: float = 0.75) -> dict[str, Any]:
        self._ensure()
        confidence = max(0.0, min(float(record.get("confidence") or 0), 0.9999))
        raw_result = record.get("raw_result") or {}
        connection = self._open_connection(autocommit=False)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, pond_name FROM ponds WHERE id=%s", (record["pond_id"],))
                pond = cursor.fetchone()
                if not pond:
                    raise ValueError("鱼池不存在")
                cursor.execute(
                    """
                    INSERT INTO ai_diagnoses
                    (pond_id, image_url, annotated_image_url, disease_name, confidence, severity,
                     status, model_name, raw_result, source, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """,
                    (
                        record["pond_id"],
                        record.get("image_url"),
                        record.get("annotated_image_url"),
                        record.get("disease_name") or "待模型识别",
                        confidence,
                        record.get("severity") or "low",
                        record.get("status") or "pending",
                        record.get("model_name") or "YOLO",
                        json.dumps(raw_result, ensure_ascii=False, separators=(",", ":")),
                        record.get("source") or "udp_hardware",
                    ),
                )
                diagnosis_id = cursor.lastrowid
                should_alarm = (
                    record.get("disease_name") not in {"健康", "待模型识别"}
                    and confidence >= alarm_confidence
                )
                if should_alarm:
                    self._upsert_ai_alarm(cursor, pond, record, confidence)
            connection.commit()
            return {"ok": True, "id": diagnosis_id, "alarm_created": bool(should_alarm)}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def automation_workspace(self) -> dict[str, Any]:
        return {
            "rules": self.query(
                """
                SELECT r.*, COALESCE(p.pond_name, '全场') AS pond_name
                FROM automation_rules r
                LEFT JOIN ponds p ON p.id=r.pond_id
                ORDER BY r.is_active DESC, r.id
                """
            ),
            "thresholds": self.query("SELECT * FROM water_quality_thresholds ORDER BY id"),
            "logs": self.query("SELECT * FROM device_logs ORDER BY log_time DESC LIMIT 12"),
        }

    def analytics_workspace(self) -> dict[str, Any]:
        energy = self.query(
            """
            SELECT p.pond_name AS name, ROUND(SUM(d.power_consumption), 2) AS value
            FROM devices d JOIN ponds p ON p.id=d.pond_id
            WHERE d.status='online'
            GROUP BY p.id, p.pond_name
            ORDER BY p.id
            """
        )
        latest = self.latest_ponds()
        risk = [
            {"name": "低风险", "value": sum(1 for p in latest if p["risk_score"] < 35)},
            {"name": "关注", "value": sum(1 for p in latest if 35 <= p["risk_score"] < 65)},
            {"name": "高风险", "value": sum(1 for p in latest if p["risk_score"] >= 65)},
        ]
        return {
            "parameters": PARAMETERS,
            "trend": {key: self.trend(key) for key, _, _ in PARAMETERS},
            "risk_distribution": risk,
            "energy": energy,
            "orders": self.query("SELECT status, COUNT(*) AS value FROM purchase_orders GROUP BY status"),
        }

    def supply_workspace(self) -> dict[str, Any]:
        suppliers = self.query(
            """
            SELECT s.*,
                   COUNT(DISTINCT p.id) AS product_count,
                   COALESCE(SUM(i.quantity), 0) AS inventory_count,
                   COUNT(DISTINCT o.id) AS order_count,
                   COALESCE(SUM(o.total_amount), 0) AS order_amount
            FROM suppliers s
            LEFT JOIN seedling_products p ON p.supplier_id=s.id
            LEFT JOIN seedling_inventory i ON i.product_id=p.id AND i.supplier_id=s.id
            LEFT JOIN purchase_orders o ON o.supplier_id=s.id
            GROUP BY s.id
            ORDER BY s.id
            """
        )
        products = self.query(
            """
            SELECT p.*, s.name AS supplier_name, i.quantity, i.safety_stock
            FROM seedling_products p
            JOIN suppliers s ON s.id=p.supplier_id
            LEFT JOIN seedling_inventory i ON i.product_id=p.id AND i.supplier_id=p.supplier_id
            ORDER BY s.id, p.id
            """
        )
        orders = self.query(
            """
            SELECT o.*, s.name AS supplier_name, p.pond_name,
                   GROUP_CONCAT(CONCAT(sp.product_name, ' x ', oi.quantity) SEPARATOR '、') AS items
            FROM purchase_orders o
            JOIN suppliers s ON s.id=o.supplier_id
            JOIN ponds p ON p.id=o.pond_id
            LEFT JOIN order_items oi ON oi.order_id=o.id
            LEFT JOIN seedling_products sp ON sp.id=oi.product_id
            GROUP BY o.id
            ORDER BY o.order_date DESC
            LIMIT 20
            """
        )
        finance = self.query(
            """
            SELECT s.name AS supplier_name,
                   SUM(CASE WHEN f.transaction_type='receivable' THEN f.amount ELSE 0 END) AS revenue,
                   SUM(CASE WHEN f.transaction_type='payment' THEN f.amount ELSE 0 END) AS cost,
                   SUM(CASE WHEN f.status='pending' THEN f.amount ELSE 0 END) AS pending_amount
            FROM suppliers s
            LEFT JOIN finance_transactions f ON f.supplier_id=s.id
            GROUP BY s.id, s.name
            ORDER BY s.id
            """
        )
        return {"suppliers": suppliers, "products": products, "orders": orders, "finance": finance}

    def realtime_snapshot(self) -> dict[str, Any]:
        return self.admin_dashboard()

    def command_device(
        self,
        device_id: int,
        action: str,
        operator: str,
        send_command: Callable[[str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        device = self.one("SELECT * FROM devices WHERE id=%s", (device_id,))
        if not device:
            return {"ok": False, "message": "设备不存在"}
        normalized = self._resolve_device_action(device, action)
        if not normalized["ok"]:
            return normalized

        udp_result = send_command(normalized["command"]) if send_command else {"ok": False, "message": "UDP 发送器不可用"}
        command_status = "sent" if udp_result["ok"] else "failed"
        result_message = udp_result["message"]
        self.execute(
            """
            INSERT INTO device_commands (device_id, command_type, requested_by, status, result_message)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (device_id, normalized["command_type"], operator, command_status, result_message),
        )

        if not udp_result["ok"]:
            return {
                "ok": False,
                "message": result_message,
                "command": normalized["command"],
                "command_type": normalized["command_type"],
            }

        next_state = normalized["next_state"]
        next_status = normalized["next_status"]
        previous_state = device["work_state"]
        self.execute("UPDATE devices SET work_state=%s, status=%s, last_active=NOW() WHERE id=%s", (next_state, next_status, device_id))
        self.execute(
            """
            INSERT INTO device_logs (device_id, pond_id, action, operator, previous_state, current_state, details)
            VALUES (%s, %s, 'UDP远程控制', %s, %s, %s, %s)
            """,
            (
                device_id,
                device["pond_id"],
                operator,
                previous_state,
                next_state,
                f"{normalized['command']} 已发送至 {udp_result['target_ip']}:{udp_result['target_port']}",
            ),
        )
        return {
            "ok": True,
            "state": next_state,
            "command": normalized["command"],
            "message": result_message,
            "target_ip": udp_result["target_ip"],
            "target_port": udp_result["target_port"],
        }

    def _resolve_device_action(self, device: dict[str, Any], action: str) -> dict[str, Any]:
        action = action if action in {"start", "stop", "toggle", "feed"} else action
        device_type = device["device_type"]
        if device_type not in DEVICE_COMMAND_MAP:
            return {"ok": False, "message": "无对应硬件指令"}

        if action == "toggle":
            action = "stop" if device["work_state"] == "running" else "start"

        command_map = DEVICE_COMMAND_MAP[device_type]
        if action not in command_map:
            return {"ok": False, "message": "无对应硬件指令"}

        if action == "stop":
            next_state = "standby"
            next_status = "online"
        else:
            next_state = "running"
            next_status = "online"

        return {
            "ok": True,
            "command_type": action,
            "command": command_map[action],
            "next_state": next_state,
            "next_status": next_status,
        }

    def resolve_alert(self, alert_id: int, user_id: int) -> dict[str, Any]:
        self.execute("UPDATE alarms SET status='resolved', resolved_by=%s, resolved_at=NOW() WHERE id=%s", (user_id, alert_id))
        return {"ok": True}

    def update_threshold(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.execute(
            "UPDATE water_quality_thresholds SET min_value=%s, max_value=%s, is_active=%s WHERE id=%s",
            (payload.get("min_value"), payload.get("max_value"), 1 if payload.get("is_active", True) else 0, payload.get("id")),
        )
        return {"ok": True}

    def ingest_hardware_frame(
        self,
        payload: dict[str, float],
        recorded_at: datetime,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure()
        meta = meta or {}
        summary = {
            "enabled_ponds": 0,
            "inserted_readings": 0,
            "deduped_readings": 0,
            "opened_alarms": 0,
            "updated_alarms": 0,
            "resolved_alarms": 0,
            "device_logs": 0,
            "raw_text": meta.get("text_preview"),
            "raw_hex": meta.get("hex_preview"),
        }
        connection = self._open_connection(autocommit=False)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT p.id AS pond_id, p.pond_code, p.pond_name,
                           d.id AS sensor_device_id, d.status AS sensor_status,
                           d.work_state AS sensor_work_state
                    FROM ponds p
                    LEFT JOIN devices d ON d.id = (
                        SELECT x.id FROM devices x
                        WHERE x.pond_id=p.id AND x.device_type='sensor'
                        ORDER BY x.id
                        LIMIT 1
                    )
                    WHERE p.hardware_ingest_enabled=1
                    ORDER BY p.id
                    """
                )
                ponds = list(cursor.fetchall())
                summary["enabled_ponds"] = len(ponds)
                if not ponds:
                    connection.commit()
                    return summary

                thresholds = self._load_threshold_map(cursor, [pond["pond_id"] for pond in ponds])
                for pond in ponds:
                    sensor_device = self._ensure_sensor_device(cursor, pond)
                    if self._is_duplicate_reading(cursor, pond["pond_id"], payload, recorded_at):
                        summary["deduped_readings"] += 1
                    else:
                        self._insert_sensor_reading(cursor, pond["pond_id"], payload, recorded_at)
                        summary["inserted_readings"] += 1

                    if self._update_sensor_heartbeat(cursor, sensor_device, pond, recorded_at):
                        summary["device_logs"] += 1

                    alarm_stats = self._sync_threshold_alarms(
                        cursor=cursor,
                        pond=pond,
                        sensor_device=sensor_device,
                        payload=payload,
                        thresholds=thresholds.get(pond["pond_id"], {}),
                        recorded_at=recorded_at,
                    )
                    summary["opened_alarms"] += alarm_stats["opened"]
                    summary["updated_alarms"] += alarm_stats["updated"]
                    summary["resolved_alarms"] += alarm_stats["resolved"]

            connection.commit()
            return summary
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _load_threshold_map(self, cursor, pond_ids: list[int]) -> dict[int, dict[str, dict[str, Any]]]:
        placeholders = ",".join(["%s"] * len(pond_ids))
        cursor.execute(
            f"""
            SELECT *
            FROM water_quality_thresholds
            WHERE is_active=1 AND (pond_id IS NULL OR pond_id IN ({placeholders}))
            ORDER BY pond_id IS NULL, pond_id, id
            """,
            tuple(pond_ids),
        )
        rows = list(cursor.fetchall())
        global_map: dict[str, dict[str, Any]] = {}
        scoped: dict[int, dict[str, dict[str, Any]]] = {pond_id: {} for pond_id in pond_ids}
        for row in rows:
            target = global_map if row["pond_id"] is None else scoped.setdefault(int(row["pond_id"]), {})
            target[row["parameter_key"]] = row
        return {pond_id: {**global_map, **scoped.get(pond_id, {})} for pond_id in pond_ids}

    def _ensure_sensor_device(self, cursor, pond: dict[str, Any]) -> dict[str, Any]:
        if pond.get("sensor_device_id"):
            return {
                "id": int(pond["sensor_device_id"]),
                "status": pond.get("sensor_status") or "offline",
                "work_state": pond.get("sensor_work_state") or "standby",
            }
        cursor.execute(
            """
            INSERT INTO devices
            (device_code, pond_id, device_name, device_type, status, work_state, power_consumption, last_active)
            VALUES (%s, %s, %s, 'sensor', 'offline', 'standby', 0, NULL)
            """,
            (f"SENSOR-{pond['pond_code']}", pond["pond_id"], f"{pond['pond_name']}-水质传感器"),
        )
        return {"id": cursor.lastrowid, "status": "offline", "work_state": "standby"}

    def _is_duplicate_reading(self, cursor, pond_id: int, payload: dict[str, float], recorded_at: datetime) -> bool:
        cursor.execute(
            """
            SELECT temperature, ph_value, food_value, salinity, dissolved_oxygen,
                   ammonia_nitrogen, nitrite_nitrogen, recorded_at
            FROM sensor_readings
            WHERE pond_id=%s
            ORDER BY recorded_at DESC, id DESC
            LIMIT 1
            """,
            (pond_id,),
        )
        latest = cursor.fetchone()
        if not latest or not latest.get("recorded_at"):
            return False
        delta = (recorded_at - latest["recorded_at"]).total_seconds()
        if delta < 0 or delta > DEDUPE_WINDOW_SECONDS:
            return False
        return same_metrics(latest, payload)

    def _insert_sensor_reading(self, cursor, pond_id: int, payload: dict[str, float], recorded_at: datetime) -> None:
        cursor.execute(
            """
            INSERT INTO sensor_readings
            (pond_id, temperature, ph_value, food_value, salinity, dissolved_oxygen, ammonia_nitrogen, nitrite_nitrogen, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                pond_id,
                payload["temperature"],
                payload["ph_value"],
                payload["food_value"],
                payload["salinity"],
                payload["dissolved_oxygen"],
                payload["ammonia_nitrogen"],
                payload["nitrite_nitrogen"],
                recorded_at,
            ),
        )

    def _update_sensor_heartbeat(
        self,
        cursor,
        sensor_device: dict[str, Any],
        pond: dict[str, Any],
        recorded_at: datetime,
    ) -> bool:
        previous_status = sensor_device.get("status") or "offline"
        previous_state = sensor_device.get("work_state") or "standby"
        transitioned = previous_status != "online" or previous_state != "running"
        cursor.execute(
            "UPDATE devices SET status='online', work_state='running', last_active=%s WHERE id=%s",
            (recorded_at, sensor_device["id"]),
        )
        if transitioned:
            cursor.execute(
                """
                INSERT INTO device_logs (device_id, pond_id, action, operator, previous_state, current_state, details, log_time)
                VALUES (%s, %s, '硬件采集上线', 'UDP采集', %s, 'running', %s, %s)
                """,
                (
                    sensor_device["id"],
                    pond["pond_id"],
                    previous_state,
                    f"状态 {previous_status}/{previous_state} 切换为 online/running",
                    recorded_at,
                ),
            )
        sensor_device["status"] = "online"
        sensor_device["work_state"] = "running"
        return transitioned

    def _sync_threshold_alarms(
        self,
        cursor,
        pond: dict[str, Any],
        sensor_device: dict[str, Any],
        payload: dict[str, float],
        thresholds: dict[str, dict[str, Any]],
        recorded_at: datetime,
    ) -> dict[str, int]:
        summary = {"opened": 0, "updated": 0, "resolved": 0}
        for parameter_key, value in payload.items():
            threshold = thresholds.get(parameter_key)
            if not threshold:
                continue
            is_abnormal = self._out_of_threshold(value, threshold)
            cursor.execute(
                """
                SELECT id, status
                FROM alarms
                WHERE alarm_type='water_quality'
                  AND pond_id=%s
                  AND parameter_key=%s
                  AND status IN ('open', 'acknowledged')
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (pond["pond_id"], parameter_key),
            )
            current_alarm = cursor.fetchone()
            if is_abnormal:
                message = self._format_threshold_message(pond["pond_name"], threshold, value)
                if current_alarm:
                    cursor.execute(
                        """
                        UPDATE alarms
                        SET severity=%s, message=%s, created_at=%s, device_id=%s
                        WHERE id=%s
                        """,
                        (threshold["warning_level"], message, recorded_at, sensor_device["id"], current_alarm["id"]),
                    )
                    summary["updated"] += 1
                else:
                    cursor.execute(
                        """
                        INSERT INTO alarms
                        (alarm_type, pond_id, device_id, parameter_key, severity, message, status, created_at)
                        VALUES ('water_quality', %s, %s, %s, %s, %s, 'open', %s)
                        """,
                        (
                            pond["pond_id"],
                            sensor_device["id"],
                            parameter_key,
                            threshold["warning_level"],
                            message,
                            recorded_at,
                        ),
                    )
                    summary["opened"] += 1
            elif current_alarm:
                cursor.execute(
                    "UPDATE alarms SET status='resolved', resolved_at=%s WHERE id=%s",
                    (recorded_at, current_alarm["id"]),
                )
                summary["resolved"] += 1
        return summary

    def _out_of_threshold(self, value: float, threshold: dict[str, Any]) -> bool:
        minimum = threshold.get("min_value")
        maximum = threshold.get("max_value")
        if minimum is not None and float(value) < float(minimum):
            return True
        if maximum is not None and float(value) > float(maximum):
            return True
        return False

    def _format_threshold_message(self, pond_name: str, threshold: dict[str, Any], value: float) -> str:
        unit = threshold.get("unit") or ""
        minimum = format_number(threshold.get("min_value"))
        maximum = format_number(threshold.get("max_value"))
        return (
            f"{pond_name}{threshold['parameter_name']}超出阈值：当前 {format_number(value)}{unit}，"
            f"阈值 {minimum}~{maximum}{unit}"
        )

    def _upsert_ai_alarm(
        self,
        cursor,
        pond: dict[str, Any],
        record: dict[str, Any],
        confidence: float,
    ) -> None:
        severity = record.get("severity") or "medium"
        disease_name = record.get("disease_name") or "疑似鱼病"
        message = f"{pond['pond_name']}AI识别到疑似{disease_name}，置信度 {round(confidence * 100)}%，建议人工复核。"
        cursor.execute(
            """
            SELECT id
            FROM alarms
            WHERE alarm_type='ai_disease'
              AND pond_id=%s
              AND parameter_key=%s
              AND status IN ('open', 'acknowledged')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (pond["id"], disease_name),
        )
        current = cursor.fetchone()
        if current:
            cursor.execute(
                """
                UPDATE alarms
                SET severity=%s, message=%s, created_at=NOW()
                WHERE id=%s
                """,
                (severity, message, current["id"]),
            )
        else:
            cursor.execute(
                """
                INSERT INTO alarms (alarm_type, pond_id, parameter_key, severity, message, status, created_at)
                VALUES ('ai_disease', %s, %s, %s, %s, 'open', NOW())
                """,
                (pond["id"], disease_name, severity, message),
            )

    def supplier_dashboard(self, supplier_id: int) -> dict[str, Any]:
        supplier = self.one("SELECT * FROM suppliers WHERE id=%s", (supplier_id,))
        products_data = self.supplier_products(supplier_id)
        orders_data = self.supplier_orders(supplier_id)
        products = products_data["products"]
        orders = orders_data["orders"]
        finance = self.supplier_finance(supplier_id)
        low_stock = [product for product in products if product["stock_status"] in {"warning", "medium"}]
        pending_delivery = sum(1 for order in orders if order["status"] in {"confirmed", "shipped"})
        top_products = sorted(products, key=lambda item: float(item["sales_amount"] or 0), reverse=True)[:5]
        return {
            "supplier": supplier,
            "stats": [
                {"label": "在售产品", "value": len(products), "unit": "种"},
                {"label": "库存总量", "value": sum(int(p["quantity"] or 0) for p in products), "unit": "尾"},
                {"label": "待交付订单", "value": pending_delivery, "unit": "单"},
                {"label": "待结算金额", "value": finance["receivable"], "unit": "元"},
                {"label": "销售额", "value": finance["revenue"], "unit": "元"},
            ],
            "products": products,
            "top_products": top_products,
            "low_stock": low_stock[:6],
            "orders": orders[:8],
            "status_counts": orders_data["status_counts"],
            "sales": finance["series"],
        }

    def supplier_products(self, supplier_id: int) -> dict[str, Any]:
        products = self.query(
            """
            SELECT p.id, p.supplier_id, p.product_name, p.species, p.grade,
                   p.unit_price, p.cost_price, p.growth_cycle_days, p.survival_rate,
                   p.image_url, p.description, p.is_active, p.created_at, p.updated_at,
                   COALESCE(i.quantity, 0) AS quantity,
                   COALESCE(i.safety_stock, 0) AS safety_stock,
                   COALESCE(sold.sold_quantity, 0) AS sold_quantity,
                   COALESCE(sold.sales_amount, 0) AS sales_amount,
                   COALESCE(sold.order_count, 0) AS order_count,
                   CASE
                     WHEN COALESCE(i.quantity, 0) <= COALESCE(i.safety_stock, 0) THEN 'warning'
                     WHEN COALESCE(i.quantity, 0) <= COALESCE(i.safety_stock, 0) * 1.5 THEN 'medium'
                     ELSE 'normal'
                   END AS stock_status
            FROM seedling_products p
            LEFT JOIN seedling_inventory i ON i.product_id=p.id AND i.supplier_id=p.supplier_id
            LEFT JOIN (
                SELECT oi.product_id,
                       SUM(oi.quantity) AS sold_quantity,
                       SUM(oi.quantity * oi.unit_price) AS sales_amount,
                       COUNT(DISTINCT oi.order_id) AS order_count
                FROM order_items oi
                JOIN purchase_orders o ON o.id=oi.order_id
                WHERE o.supplier_id=%s
                GROUP BY oi.product_id
            ) sold ON sold.product_id=p.id
            WHERE p.supplier_id=%s
            ORDER BY
              CASE
                WHEN COALESCE(i.quantity, 0) <= COALESCE(i.safety_stock, 0) THEN 0
                WHEN COALESCE(i.quantity, 0) <= COALESCE(i.safety_stock, 0) * 1.5 THEN 1
                ELSE 2
              END,
              sold_quantity DESC,
              p.id DESC
            """,
            (supplier_id, supplier_id),
        )
        for product in products:
            product["quantity"] = int(product["quantity"] or 0)
            product["safety_stock"] = int(product["safety_stock"] or 0)
            product["sold_quantity"] = int(product["sold_quantity"] or 0)
            product["order_count"] = int(product["order_count"] or 0)
            product["unit_price"] = float(product["unit_price"] or 0)
            product["cost_price"] = float(product["cost_price"] or 0)
            product["sales_amount"] = round(float(product["sales_amount"] or 0), 2)
        return {
            "products": products,
            "summary": [
                {"label": "产品品类", "value": len(products), "unit": "种"},
                {"label": "库存总量", "value": sum(item["quantity"] for item in products), "unit": "尾"},
                {"label": "低库存预警", "value": sum(1 for item in products if item["stock_status"] in {"warning", "medium"}), "unit": "项"},
                {"label": "累计供货", "value": sum(item["sold_quantity"] for item in products), "unit": "尾"},
            ],
        }

    def supplier_orders(self, supplier_id: int) -> dict[str, Any]:
        status_counts = self.query(
            """
            SELECT status, COUNT(*) AS count
            FROM purchase_orders
            WHERE supplier_id=%s
            GROUP BY status
            ORDER BY FIELD(status, 'draft', 'confirmed', 'shipped', 'received', 'cancelled')
            """,
            (supplier_id,),
        )
        orders = self.query(
            """
            SELECT o.*, p.pond_name,
                   GROUP_CONCAT(CONCAT(sp.product_name, ' x ', oi.quantity) ORDER BY sp.product_name SEPARATOR '、') AS items,
                   COALESCE(SUM(oi.quantity), 0) AS total_quantity,
                   COUNT(oi.id) AS item_count,
                   CASE
                     WHEN o.status='draft' THEN 'confirmed'
                     WHEN o.status='confirmed' THEN 'shipped'
                     WHEN o.status='shipped' THEN 'received'
                     ELSE NULL
                   END AS next_status,
                   CASE
                     WHEN o.status='draft' THEN '确认订单'
                     WHEN o.status='confirmed' THEN '安排发货'
                     WHEN o.status='shipped' THEN '确认签收'
                     ELSE '流程完成'
                   END AS action_label
            FROM purchase_orders o
            JOIN ponds p ON p.id=o.pond_id
            LEFT JOIN order_items oi ON oi.order_id=o.id
            LEFT JOIN seedling_products sp ON sp.id=oi.product_id
            WHERE o.supplier_id=%s
            GROUP BY o.id
            ORDER BY o.order_date DESC
            """,
            (supplier_id,),
        )
        for order in orders:
            order["total_amount"] = round(float(order["total_amount"] or 0), 2)
            order["total_quantity"] = int(order["total_quantity"] or 0)
            order["item_count"] = int(order["item_count"] or 0)
        return {
            "orders": orders,
            "status_counts": [{"status": row["status"], "count": int(row["count"] or 0)} for row in status_counts],
        }

    def supplier_finance(self, supplier_id: int) -> dict[str, Any]:
        rows = self.query(
            """
            SELECT DATE_FORMAT(occurred_at, '%%m月') AS month,
                   SUM(CASE WHEN transaction_type='receivable' THEN amount ELSE 0 END) AS revenue,
                   SUM(CASE WHEN transaction_type='payment' THEN amount ELSE 0 END) AS cost,
                   SUM(CASE WHEN status='pending' THEN amount ELSE 0 END) AS receivable
            FROM finance_transactions
            WHERE supplier_id=%s
            GROUP BY DATE_FORMAT(occurred_at, '%%Y-%%m'), DATE_FORMAT(occurred_at, '%%m月')
            ORDER BY MIN(occurred_at)
            """,
            (supplier_id,),
        )
        for row in rows:
            row["revenue"] = round(float(row["revenue"] or 0), 2)
            row["cost"] = round(float(row["cost"] or 0), 2)
            row["receivable"] = round(float(row["receivable"] or 0), 2)
            row["profit"] = round(row["revenue"] - row["cost"], 2)
        revenue = round(sum(float(r["revenue"] or 0) for r in rows), 2)
        cost = round(sum(float(r["cost"] or 0) for r in rows), 2)
        receivable = round(sum(float(r["receivable"] or 0) for r in rows), 2)
        order_count = self.one("SELECT COUNT(*) AS count FROM purchase_orders WHERE supplier_id=%s", (supplier_id,))["count"]
        settled = self.one(
            "SELECT COUNT(*) AS count FROM finance_transactions WHERE supplier_id=%s AND transaction_type='receivable' AND status='settled'",
            (supplier_id,),
        )["count"]
        total = self.one(
            "SELECT COUNT(*) AS count FROM finance_transactions WHERE supplier_id=%s AND transaction_type='receivable'",
            (supplier_id,),
        )["count"]
        recent = self.query(
            """
            SELECT f.*, o.order_no
            FROM finance_transactions f
            LEFT JOIN purchase_orders o ON o.id=f.order_id
            WHERE f.supplier_id=%s
            ORDER BY f.occurred_at DESC
            LIMIT 12
            """,
            (supplier_id,),
        )
        for item in recent:
            item["amount"] = round(float(item["amount"] or 0), 2)
        status_summary = self.query(
            """
            SELECT status, COUNT(*) AS count, SUM(amount) AS amount
            FROM finance_transactions
            WHERE supplier_id=%s
            GROUP BY status
            ORDER BY FIELD(status, 'pending', 'settled', 'cancelled')
            """,
            (supplier_id,),
        )
        for item in status_summary:
            item["count"] = int(item["count"] or 0)
            item["amount"] = round(float(item["amount"] or 0), 2)
        return {
            "series": rows,
            "revenue": revenue,
            "cost": cost,
            "profit": round(revenue - cost, 2),
            "receivable": receivable,
            "avg_order": round(revenue / max(int(order_count), 1), 2),
            "settled_rate": round(int(settled) * 100 / max(int(total), 1)),
            "recent": recent,
            "status_summary": status_summary,
        }

    def update_order_status(self, order_id: int, supplier_id: int, status: str) -> dict[str, Any]:
        if status not in {"draft", "confirmed", "shipped", "received", "cancelled"}:
            return {"ok": False}
        self.execute("UPDATE purchase_orders SET status=%s WHERE id=%s AND supplier_id=%s", (status, order_id, supplier_id))
        return {"ok": True}

    def update_inventory(self, product_id: int, supplier_id: int, quantity: int) -> dict[str, Any]:
        self.execute(
            "UPDATE seedling_inventory SET quantity=%s, updated_by='supplier-web' WHERE product_id=%s AND supplier_id=%s",
            (quantity, product_id, supplier_id),
        )
        return {"ok": True}

    def export_sensor_rows(self) -> list[list[Any]]:
        rows = self.query(
            """
            SELECT p.pond_name, r.temperature, r.ph_value, r.food_value,
                   r.salinity, r.dissolved_oxygen, r.ammonia_nitrogen,
                   r.nitrite_nitrogen, r.recorded_at
            FROM sensor_readings r JOIN ponds p ON p.id=r.pond_id
            ORDER BY r.recorded_at DESC
            LIMIT 300
            """
        )
        return [
            [
                row["pond_name"],
                row["temperature"],
                row["ph_value"],
                row["food_value"],
                row["salinity"],
                row["dissolved_oxygen"],
                row["ammonia_nitrogen"],
                row["nitrite_nitrogen"],
                row["recorded_at"],
            ]
            for row in rows
        ]
