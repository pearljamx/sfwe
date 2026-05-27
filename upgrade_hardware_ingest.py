from __future__ import annotations

import os

import pymysql


HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
PORT = int(os.getenv("MYSQL_PORT", "3306"))
USER = os.getenv("MYSQL_USER", "root")
PASSWORD = os.getenv("MYSQL_PASSWORD", "123456")
DATABASE = os.getenv("MYSQL_DATABASE", "SFweb")


def column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s AND column_name=%s
        """,
        (DATABASE, table, column),
    )
    return bool(cur.fetchone()["count"])


def threshold_exists(cur, parameter_key: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*) AS count
        FROM water_quality_thresholds
        WHERE pond_id IS NULL AND parameter_key=%s
        """,
        (parameter_key,),
    )
    return bool(cur.fetchone()["count"])


def main() -> None:
    conn = pymysql.connect(
        host=HOST,
        port=PORT,
        user=USER,
        password=PASSWORD,
        database=DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    added_column = False
    refreshed_column_comment = False
    created_sensors = 0
    created_threshold = False
    migrated_logs = 0
    added_ai_columns = 0
    try:
        with conn.cursor() as cur:
            if not column_exists(cur, "ponds", "hardware_ingest_enabled"):
                cur.execute(
                    """
                    ALTER TABLE ponds
                    ADD COLUMN hardware_ingest_enabled TINYINT(1) NOT NULL DEFAULT 0
                    COMMENT '是否接收UDP硬件数据'
                    AFTER status
                    """
                )
                added_column = True
            else:
                cur.execute(
                    """
                    ALTER TABLE ponds
                    MODIFY COLUMN hardware_ingest_enabled TINYINT(1) NOT NULL DEFAULT 0
                    COMMENT '是否接收UDP硬件数据'
                    AFTER status
                    """
                )
                refreshed_column_comment = True

            cur.execute("SELECT id, pond_code, pond_name FROM ponds ORDER BY id")
            ponds = list(cur.fetchall())
            for pond in ponds:
                cur.execute(
                    "SELECT id FROM devices WHERE pond_id=%s AND device_type='sensor' LIMIT 1",
                    (pond["id"],),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    """
                    INSERT INTO devices
                    (device_code, pond_id, device_name, device_type, status, work_state, power_consumption, last_active)
                    VALUES (%s, %s, %s, 'sensor', 'offline', 'standby', 0, NULL)
                    """,
                    (f"SENSOR-{pond['pond_code']}", pond["id"], f"{pond['pond_name']}-水质传感器"),
                )
                created_sensors += 1

            if not threshold_exists(cur, "food_value"):
                cur.execute(
                    """
                    INSERT INTO water_quality_thresholds
                    (pond_id, parameter_key, parameter_name, min_value, max_value, unit, warning_level, is_active)
                    VALUES (NULL, 'food_value', '食物值', 20, 80, '', 'medium', 1)
                    """
                )
                created_threshold = True

            cur.execute(
                """
                UPDATE device_logs
                SET operator='UDP采集'
                WHERE operator='串口采集'
                """
            )
            migrated_logs = cur.rowcount or 0

            ai_columns = [
                ("annotated_image_url", "ADD COLUMN annotated_image_url VARCHAR(255) NULL AFTER image_url"),
                ("raw_result", "ADD COLUMN raw_result LONGTEXT NULL AFTER model_name"),
                ("source", "ADD COLUMN source VARCHAR(50) NOT NULL DEFAULT 'seed' AFTER raw_result"),
            ]
            for column_name, ddl in ai_columns:
                if not column_exists(cur, "ai_diagnoses", column_name):
                    cur.execute(f"ALTER TABLE ai_diagnoses {ddl}")
                    added_ai_columns += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(
        f"升级完成: 新增字段={int(added_column)}，字段注释刷新={int(refreshed_column_comment)}，"
        f"补齐传感器={created_sensors}，补齐食物值阈值={int(created_threshold)}，"
        f"历史日志迁移={migrated_logs}，AI字段新增={added_ai_columns}"
    )


if __name__ == "__main__":
    main()
