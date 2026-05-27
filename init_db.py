from __future__ import annotations

import os
import random
from datetime import datetime, timedelta

import pymysql
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash


load_dotenv()

HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
PORT = int(os.getenv("MYSQL_PORT", "3306"))
USER = os.getenv("MYSQL_USER", "root")
PASSWORD = os.getenv("MYSQL_PASSWORD", "123456")
DATABASE = os.getenv("MYSQL_DATABASE", "SFweb")


SCHEMA = [
    "SET FOREIGN_KEY_CHECKS=0",
    "DROP VIEW IF EXISTS v_latest_pond_readings",
    "DROP TABLE IF EXISTS system_audit_logs, finance_transactions, order_items, purchase_orders, seedling_inventory, seedling_products, automation_rules, ai_diagnoses, device_logs, device_commands, alarms, water_quality_thresholds, sensor_readings, devices, ponds, users, suppliers",
    "SET FOREIGN_KEY_CHECKS=1",
    """
    CREATE TABLE suppliers (
      id INT PRIMARY KEY AUTO_INCREMENT,
      supplier_code VARCHAR(32) NOT NULL UNIQUE,
      name VARCHAR(100) NOT NULL UNIQUE,
      contact_person VARCHAR(50),
      phone VARCHAR(20),
      email VARCHAR(100),
      address VARCHAR(255),
      status ENUM('active','paused','blacklisted') NOT NULL DEFAULT 'active',
      rating DECIMAL(3,2) NOT NULL DEFAULT 4.80,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE users (
      id INT PRIMARY KEY AUTO_INCREMENT,
      username VARCHAR(50) NOT NULL UNIQUE,
      email VARCHAR(100) UNIQUE,
      password_hash VARCHAR(255) NOT NULL,
      full_name VARCHAR(100),
      role ENUM('admin','operator','supplier') NOT NULL DEFAULT 'operator',
      supplier_id INT NULL,
      is_active TINYINT(1) NOT NULL DEFAULT 1,
      last_login DATETIME NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      CONSTRAINT fk_users_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE ponds (
      id INT PRIMARY KEY AUTO_INCREMENT,
      pond_code VARCHAR(32) NOT NULL UNIQUE,
      pond_name VARCHAR(100) NOT NULL UNIQUE,
      fish_type VARCHAR(50) NOT NULL,
      fish_count INT NOT NULL DEFAULT 0,
      volume DECIMAL(10,2) NOT NULL DEFAULT 0,
      water_area DECIMAL(10,2) NULL,
      location VARCHAR(255),
      status ENUM('normal','warning','maintenance','offline') NOT NULL DEFAULT 'normal',
      hardware_ingest_enabled TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否接收UDP硬件数据',
      default_supplier_id INT NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      INDEX idx_pond_status (status),
      CONSTRAINT fk_ponds_supplier FOREIGN KEY (default_supplier_id) REFERENCES suppliers(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE devices (
      id INT PRIMARY KEY AUTO_INCREMENT,
      device_code VARCHAR(64) NOT NULL UNIQUE,
      pond_id INT NOT NULL,
      device_name VARCHAR(100) NOT NULL,
      device_type ENUM('aerator','feeder','pump','camera','sensor','gateway') NOT NULL,
      device_model VARCHAR(100),
      status ENUM('online','offline','fault','maintenance') NOT NULL DEFAULT 'offline',
      work_state ENUM('running','standby','stopped') NOT NULL DEFAULT 'standby',
      power_consumption DECIMAL(10,2) NOT NULL DEFAULT 0,
      last_active DATETIME NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      INDEX idx_devices_pond_status (pond_id, status),
      INDEX idx_devices_type_state (device_type, work_state),
      CONSTRAINT fk_devices_pond FOREIGN KEY (pond_id) REFERENCES ponds(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE sensor_readings (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      pond_id INT NOT NULL,
      temperature DECIMAL(6,2),
      ph_value DECIMAL(5,2),
      food_value DECIMAL(6,2),
      dissolved_oxygen DECIMAL(6,2),
      salinity DECIMAL(6,2),
      ammonia_nitrogen DECIMAL(6,2),
      nitrite_nitrogen DECIMAL(6,2),
      recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_readings_pond_time (pond_id, recorded_at),
      INDEX idx_readings_time (recorded_at),
      CONSTRAINT fk_readings_pond FOREIGN KEY (pond_id) REFERENCES ponds(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE water_quality_thresholds (
      id INT PRIMARY KEY AUTO_INCREMENT,
      pond_id INT NULL,
      parameter_key VARCHAR(50) NOT NULL,
      parameter_name VARCHAR(50) NOT NULL,
      min_value DECIMAL(8,2) NULL,
      max_value DECIMAL(8,2) NULL,
      unit VARCHAR(20),
      warning_level ENUM('low','medium','high','critical') NOT NULL DEFAULT 'medium',
      is_active TINYINT(1) NOT NULL DEFAULT 1,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uk_threshold_scope (pond_id, parameter_key),
      CONSTRAINT fk_thresholds_pond FOREIGN KEY (pond_id) REFERENCES ponds(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE alarms (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      alarm_type ENUM('water_quality','device','ai_disease','communication','system') NOT NULL,
      pond_id INT NULL,
      device_id INT NULL,
      parameter_key VARCHAR(50) NULL,
      severity ENUM('low','medium','high','critical') NOT NULL DEFAULT 'medium',
      message VARCHAR(500) NOT NULL,
      status ENUM('open','acknowledged','resolved') NOT NULL DEFAULT 'open',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      acknowledged_by INT NULL,
      resolved_by INT NULL,
      resolved_at DATETIME NULL,
      INDEX idx_alarms_status_time (status, created_at),
      INDEX idx_alarms_pond_time (pond_id, created_at),
      CONSTRAINT fk_alarms_pond FOREIGN KEY (pond_id) REFERENCES ponds(id),
      CONSTRAINT fk_alarms_device FOREIGN KEY (device_id) REFERENCES devices(id),
      CONSTRAINT fk_alarms_ack_user FOREIGN KEY (acknowledged_by) REFERENCES users(id),
      CONSTRAINT fk_alarms_resolve_user FOREIGN KEY (resolved_by) REFERENCES users(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE device_commands (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      device_id INT NOT NULL,
      command_type ENUM('start','stop','toggle','feed','inspect','reset') NOT NULL,
      requested_by VARCHAR(100) NOT NULL,
      status ENUM('queued','sent','confirmed','failed','cancelled') NOT NULL DEFAULT 'queued',
      requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      confirmed_at DATETIME NULL,
      result_message VARCHAR(255) NULL,
      INDEX idx_commands_device_status (device_id, status),
      CONSTRAINT fk_commands_device FOREIGN KEY (device_id) REFERENCES devices(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE device_logs (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      device_id INT NOT NULL,
      pond_id INT NOT NULL,
      action VARCHAR(50) NOT NULL,
      operator VARCHAR(100),
      previous_state VARCHAR(20),
      current_state VARCHAR(20),
      details VARCHAR(500),
      log_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_device_logs_device_time (device_id, log_time),
      CONSTRAINT fk_device_logs_device FOREIGN KEY (device_id) REFERENCES devices(id),
      CONSTRAINT fk_device_logs_pond FOREIGN KEY (pond_id) REFERENCES ponds(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE ai_diagnoses (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      pond_id INT NOT NULL,
      image_url VARCHAR(255),
      annotated_image_url VARCHAR(255),
      disease_name VARCHAR(100) NOT NULL,
      confidence DECIMAL(5,4) NOT NULL,
      severity ENUM('low','medium','high','critical') NOT NULL DEFAULT 'medium',
      status ENUM('pending','confirmed','dismissed','archived') NOT NULL DEFAULT 'pending',
      model_name VARCHAR(100) DEFAULT 'Atlas200I-FishDisease',
      raw_result LONGTEXT NULL,
      source VARCHAR(50) NOT NULL DEFAULT 'seed',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_ai_pond_time (pond_id, created_at),
      CONSTRAINT fk_ai_pond FOREIGN KEY (pond_id) REFERENCES ponds(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE automation_rules (
      id INT PRIMARY KEY AUTO_INCREMENT,
      rule_name VARCHAR(100) NOT NULL,
      pond_id INT NULL,
      trigger_type VARCHAR(50) NOT NULL,
      trigger_condition VARCHAR(255) NOT NULL,
      action_type VARCHAR(50) NOT NULL,
      is_active TINYINT(1) NOT NULL DEFAULT 1,
      created_by INT NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      INDEX idx_rules_active (is_active),
      CONSTRAINT fk_rules_pond FOREIGN KEY (pond_id) REFERENCES ponds(id),
      CONSTRAINT fk_rules_user FOREIGN KEY (created_by) REFERENCES users(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE seedling_products (
      id INT PRIMARY KEY AUTO_INCREMENT,
      supplier_id INT NOT NULL,
      product_name VARCHAR(100) NOT NULL,
      species VARCHAR(50) NOT NULL,
      grade VARCHAR(50),
      unit_price DECIMAL(10,2) NOT NULL DEFAULT 0,
      cost_price DECIMAL(10,2) NULL,
      growth_cycle_days INT NULL,
      survival_rate DECIMAL(5,2) NULL,
      image_url VARCHAR(255),
      description TEXT,
      is_active TINYINT(1) NOT NULL DEFAULT 1,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      INDEX idx_products_supplier_active (supplier_id, is_active),
      CONSTRAINT fk_products_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE seedling_inventory (
      id INT PRIMARY KEY AUTO_INCREMENT,
      supplier_id INT NOT NULL,
      product_id INT NOT NULL,
      quantity INT NOT NULL DEFAULT 0,
      safety_stock INT NOT NULL DEFAULT 1000,
      last_updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      updated_by VARCHAR(50),
      UNIQUE KEY uk_inventory_supplier_product (supplier_id, product_id),
      CONSTRAINT fk_inventory_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
      CONSTRAINT fk_inventory_product FOREIGN KEY (product_id) REFERENCES seedling_products(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE purchase_orders (
      id INT PRIMARY KEY AUTO_INCREMENT,
      order_no VARCHAR(40) NOT NULL UNIQUE,
      supplier_id INT NOT NULL,
      pond_id INT NOT NULL,
      created_by INT NOT NULL,
      order_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      expected_delivery_date DATE NULL,
      actual_delivery_date DATE NULL,
      status ENUM('draft','confirmed','shipped','received','cancelled') NOT NULL DEFAULT 'draft',
      total_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
      notes TEXT,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      INDEX idx_orders_supplier_status (supplier_id, status),
      CONSTRAINT fk_orders_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
      CONSTRAINT fk_orders_pond FOREIGN KEY (pond_id) REFERENCES ponds(id),
      CONSTRAINT fk_orders_user FOREIGN KEY (created_by) REFERENCES users(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE order_items (
      id INT PRIMARY KEY AUTO_INCREMENT,
      order_id INT NOT NULL,
      product_id INT NOT NULL,
      quantity INT NOT NULL DEFAULT 0,
      unit_price DECIMAL(10,2) NOT NULL DEFAULT 0,
      subtotal DECIMAL(12,2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      CONSTRAINT fk_items_order FOREIGN KEY (order_id) REFERENCES purchase_orders(id) ON DELETE CASCADE,
      CONSTRAINT fk_items_product FOREIGN KEY (product_id) REFERENCES seedling_products(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE finance_transactions (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      supplier_id INT NOT NULL,
      order_id INT NULL,
      transaction_type ENUM('receivable','payment','refund','adjustment') NOT NULL,
      amount DECIMAL(12,2) NOT NULL,
      status ENUM('pending','settled','cancelled') NOT NULL DEFAULT 'pending',
      occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      notes VARCHAR(255),
      INDEX idx_finance_supplier_time (supplier_id, occurred_at),
      CONSTRAINT fk_finance_supplier FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
      CONSTRAINT fk_finance_order FOREIGN KEY (order_id) REFERENCES purchase_orders(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE system_audit_logs (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      user_id INT NULL,
      action VARCHAR(100) NOT NULL,
      resource_type VARCHAR(50),
      resource_id BIGINT,
      ip_address VARCHAR(50),
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      CONSTRAINT fk_audit_user FOREIGN KEY (user_id) REFERENCES users(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

TABLE_COMMENTS = {
    "ai_diagnoses": "AI鱼病识别记录表",
    "alarms": "告警事件表",
    "automation_rules": "自动控制规则表",
    "device_commands": "设备控制指令表",
    "device_logs": "设备操作与告警日志表",
    "devices": "硬件设备信息表",
    "finance_transactions": "供应商财务结算流水表",
    "order_items": "采购订单详情表",
    "ponds": "鱼池基本信息表",
    "purchase_orders": "采购订单主表",
    "seedling_inventory": "鱼苗库存表",
    "seedling_products": "鱼苗产品表",
    "sensor_readings": "传感器水质监测数据表",
    "suppliers": "供应商信息表",
    "system_audit_logs": "系统操作日志表",
    "users": "用户管理表",
    "water_quality_thresholds": "水质阈值配置表",
}


def table_exists(cur, schema: str, table: str) -> bool:
    cur.execute(
        """
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema=%s AND table_name=%s
        """,
        (schema, table),
    )
    return cur.fetchone()[0] > 0


def seed_from_old_database(cur) -> bool:
    if not table_exists(cur, "smart_fishery_db", "ponds"):
        return False
    cur.execute(
        """
        INSERT INTO suppliers (id, supplier_code, name, contact_person, phone, email, address, status, created_at, updated_at)
        SELECT id, CONCAT('SUP-', LPAD(id, 3, '0')), name, contact_person, phone, email, address,
               CASE WHEN status='active' THEN 'active' ELSE 'paused' END,
               COALESCE(created_at, NOW()), COALESCE(updated_at, NOW())
        FROM smart_fishery_db.suppliers
        """
    )
    passwords = {
        "admin": generate_password_hash("admin123"),
        "operator": generate_password_hash("operator123"),
        "supplier1": generate_password_hash("supplier123"),
        "supplier2": generate_password_hash("supplier123"),
    }
    cur.execute(
        """
        INSERT INTO users (id, username, email, password_hash, full_name, role, supplier_id, is_active, last_login, created_at, updated_at)
        SELECT id, username, email, %s, COALESCE(full_name, username),
               CASE WHEN role IN ('admin','operator','supplier') THEN role ELSE 'operator' END,
               supplier_id, COALESCE(is_active, 1), last_login, COALESCE(created_at, NOW()), COALESCE(updated_at, NOW())
        FROM smart_fishery_db.users WHERE username='admin'
        """,
        (passwords["admin"],),
    )
    cur.execute(
        """
        INSERT INTO users (id, username, email, password_hash, full_name, role, supplier_id, is_active, last_login, created_at, updated_at)
        SELECT id, username, email, %s, COALESCE(full_name, username),
               CASE WHEN role IN ('admin','operator','supplier') THEN role ELSE 'operator' END,
               supplier_id, COALESCE(is_active, 1), last_login, COALESCE(created_at, NOW()), COALESCE(updated_at, NOW())
        FROM smart_fishery_db.users WHERE username='operator'
        """,
        (passwords["operator"],),
    )
    for username in ("supplier1", "supplier2"):
        cur.execute(
            """
            INSERT INTO users (id, username, email, password_hash, full_name, role, supplier_id, is_active, last_login, created_at, updated_at)
            SELECT id, username, email, %s, COALESCE(full_name, username), 'supplier',
                   supplier_id, COALESCE(is_active, 1), last_login, COALESCE(created_at, NOW()), COALESCE(updated_at, NOW())
            FROM smart_fishery_db.users WHERE username=%s
            """,
            (passwords[username], username),
        )
    cur.execute(
        """
        INSERT INTO ponds (id, pond_code, pond_name, fish_type, fish_count, volume, water_area, location, status, default_supplier_id, created_at, updated_at)
        SELECT id, CONCAT('POND-', LPAD(id, 3, '0')), pond_name, fish_type, fish_count, volume, volume * 0.64, location,
               CASE status WHEN '正常' THEN 'normal' WHEN '维护中' THEN 'maintenance' ELSE 'warning' END,
               default_supplier_id, COALESCE(created_at, NOW()), COALESCE(updated_at, NOW())
        FROM smart_fishery_db.ponds
        """
    )
    cur.execute(
        """
        INSERT INTO devices (id, device_code, pond_id, device_name, device_type, device_model, status, work_state, power_consumption, last_active, created_at, updated_at)
        SELECT id, CONCAT('DEV-', LPAD(id, 5, '0')), pond_id, device_name,
               CASE device_type WHEN '增氧机' THEN 'aerator' WHEN '投喂机' THEN 'feeder' WHEN '水泵' THEN 'pump' ELSE 'sensor' END,
               device_model,
               CASE status WHEN '在线' THEN 'online' ELSE 'offline' END,
               CASE status WHEN '在线' THEN 'running' ELSE 'standby' END,
               power_consumption, last_active, COALESCE(created_at, NOW()), COALESCE(updated_at, NOW())
        FROM smart_fishery_db.devices
        """
    )
    cur.execute(
        """
        INSERT INTO sensor_readings (id, pond_id, temperature, ph_value, food_value, dissolved_oxygen, salinity, ammonia_nitrogen, nitrite_nitrogen, recorded_at)
        SELECT id, pond_id, temperature, ph_value, food_value, dissolved_oxygen, salinity, ammonia_nitrogen, nitrite_nitrogen, COALESCE(recorded_at, NOW())
        FROM smart_fishery_db.sensor_data
        """
    )
    cur.execute(
        """
        INSERT INTO seedling_products (id, supplier_id, product_name, species, grade, unit_price, cost_price, growth_cycle_days, survival_rate, image_url, description, is_active, created_at, updated_at)
        SELECT id, supplier_id, product_name, species, grade, unit_price, cost_price, growth_cycle_days, survival_rate, image_url, description, COALESCE(is_active, 1), COALESCE(created_at, NOW()), COALESCE(updated_at, NOW())
        FROM smart_fishery_db.seedling_products
        """
    )
    cur.execute(
        """
        INSERT INTO seedling_inventory (id, supplier_id, product_id, quantity, safety_stock, last_updated_at, updated_by)
        SELECT id, supplier_id, product_id, quantity, 1000, COALESCE(last_updated_at, NOW()), updated_by
        FROM smart_fishery_db.seedling_inventory
        """
    )
    cur.execute(
        """
        INSERT INTO purchase_orders (id, order_no, supplier_id, pond_id, created_by, order_date, expected_delivery_date, actual_delivery_date, status, total_amount, notes, created_at, updated_at)
        SELECT id, CONCAT('PO', DATE_FORMAT(COALESCE(order_date, NOW()), '%Y%m%d'), LPAD(id, 4, '0')),
               supplier_id, pond_id, created_by, COALESCE(order_date, NOW()), expected_delivery_date, actual_delivery_date,
               CASE WHEN status IN ('draft','confirmed','shipped','received','cancelled') THEN status ELSE 'draft' END,
               total_amount, notes, COALESCE(created_at, NOW()), COALESCE(updated_at, NOW())
        FROM smart_fishery_db.purchase_orders
        """
    )
    cur.execute(
        """
        INSERT INTO order_items (id, order_id, product_id, quantity, unit_price, created_at)
        SELECT id, order_id, product_id, quantity, unit_price, COALESCE(created_at, NOW())
        FROM smart_fishery_db.order_items
        """
    )
    return True


def seed_fallback(cur) -> None:
    suppliers = [
        ("SUP-001", "清源水产养殖基地", "王经理", "13800138001", "wang@example.com", "浙江宁波育苗基地"),
        ("SUP-002", "锦鲤养殖中心", "李总", "13800138002", "li@example.com", "广东湛江水产中心"),
        ("SUP-003", "生态鱼苗繁育场", "陈女士", "13800138003", "chen@example.com", "福建宁德繁育基地"),
    ]
    cur.executemany(
        "INSERT INTO suppliers (supplier_code,name,contact_person,phone,email,address) VALUES (%s,%s,%s,%s,%s,%s)",
        suppliers,
    )
    users = [
        ("admin", "admin@example.com", generate_password_hash("admin123"), "渔场管理员", "admin", None),
        ("operator", "operator@example.com", generate_password_hash("operator123"), "现场运维", "operator", None),
        ("supplier1", "supplier1@example.com", generate_password_hash("supplier123"), "张供应商", "supplier", 1),
        ("supplier2", "supplier2@example.com", generate_password_hash("supplier123"), "李供应商", "supplier", 2),
    ]
    cur.executemany(
        "INSERT INTO users (username,email,password_hash,full_name,role,supplier_id) VALUES (%s,%s,%s,%s,%s,%s)",
        users,
    )
    ponds = [
        ("POND-001", "一号池", "草鱼", 3500, 500, 320, "北区001", "normal", 1),
        ("POND-002", "二号池", "鲈鱼", 2800, 420, 280, "北区002", "normal", 1),
        ("POND-003", "三号池", "鲶鱼", 4200, 600, 360, "中区001", "warning", 2),
        ("POND-004", "四号池", "鲤鱼", 3000, 450, 300, "中区002", "normal", 2),
        ("POND-005", "五号池", "鳙鱼", 2500, 350, 220, "南区001", "maintenance", 3),
    ]
    cur.executemany(
        "INSERT INTO ponds (pond_code,pond_name,fish_type,fish_count,volume,water_area,location,status,default_supplier_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        ponds,
    )
    products = [
        (1, "一龄草鱼苗", "草鱼", "一龄优质", 0.8, 0.42, 96, 2534),
        (1, "二龄草鱼苗", "草鱼", "二龄", 1.5, 0.83, 94, 2884),
        (2, "鲈鱼苗（寸苗）", "鲈鱼", "寸苗", 2.0, 1.12, 92, 4196),
        (2, "鲈鱼苗（尾苗）", "鲈鱼", "尾苗", 1.2, 0.72, 91, 2183),
    ]
    for supplier_id, name, species, grade, price, cost, rate, quantity in products:
        cur.execute(
            "INSERT INTO seedling_products (supplier_id,product_name,species,grade,unit_price,cost_price,survival_rate) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (supplier_id, name, species, grade, price, cost, rate),
        )
        product_id = cur.lastrowid
        cur.execute(
            "INSERT INTO seedling_inventory (supplier_id,product_id,quantity,safety_stock) VALUES (%s,%s,%s,1000)",
            (supplier_id, product_id, quantity),
        )


def seed_enhancements(cur) -> None:
    thresholds = [
        (None, "temperature", "水温", 18, 30, "degC", "medium"),
        (None, "ph_value", "pH", 6.8, 8.4, "", "high"),
        (None, "food_value", "食物值", 20, 80, "", "medium"),
        (None, "dissolved_oxygen", "溶解氧", 5.5, 12, "mg/L", "critical"),
        (None, "salinity", "盐度", 8, 22, "ppt", "medium"),
        (None, "ammonia_nitrogen", "氨氮", 0, 2.5, "mg/L", "critical"),
        (None, "nitrite_nitrogen", "亚硝酸盐", 0, 1.2, "mg/L", "critical"),
    ]
    cur.executemany(
        "INSERT INTO water_quality_thresholds (pond_id,parameter_key,parameter_name,min_value,max_value,unit,warning_level) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        thresholds,
    )
    cur.execute("SELECT id FROM ponds ORDER BY id")
    pond_ids = [row[0] for row in cur.fetchall()]
    cur.execute("SELECT id, pond_id FROM devices ORDER BY id")
    devices = cur.fetchall()
    if not devices and pond_ids:
        device_types = [("增氧机", "aerator", 750), ("水泵", "pump", 1100), ("投喂机", "feeder", 500), ("摄像头", "camera", 120)]
        for pond_id in pond_ids:
            for index, (cn, dtype, power) in enumerate(device_types, 1):
                cur.execute("SELECT pond_name FROM ponds WHERE id=%s", (pond_id,))
                pond_name = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO devices (device_code,pond_id,device_name,device_type,status,work_state,power_consumption,last_active) VALUES (%s,%s,%s,%s,'online','running',%s,NOW())",
                    (f"DEV-{pond_id}-{index}", pond_id, f"{pond_name}-{cn}{index}", dtype, power),
                )
    ensure_sensor_devices(cur)
    cur.execute("SELECT COUNT(*) FROM sensor_readings")
    if cur.fetchone()[0] < len(pond_ids) * 12:
        start = datetime.now() - timedelta(hours=23)
        for pond_id in pond_ids:
            for hour in range(24):
                drift = (pond_id % 7) / 10
                cur.execute(
                    """
                    INSERT INTO sensor_readings
                    (pond_id,temperature,ph_value,food_value,dissolved_oxygen,salinity,ammonia_nitrogen,nitrite_nitrogen,recorded_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        pond_id,
                        round(24 + random.uniform(-1.6, 2.3) + drift, 2),
                        round(7.2 + random.uniform(-0.35, 0.45), 2),
                        round(62 + random.uniform(-16, 18), 2),
                        round(8.2 + random.uniform(-1.4, 0.7) - drift, 2),
                        round(15 + random.uniform(-2.8, 2.5), 2),
                        round(1.3 + random.uniform(-0.35, 1.1) + drift / 2, 2),
                        round(0.55 + random.uniform(-0.15, 0.55), 2),
                        start + timedelta(hours=hour),
                    ),
                )
    cur.execute("SELECT id FROM devices WHERE status='offline' LIMIT 1")
    offline_device = cur.fetchone()
    offline_id = offline_device[0] if offline_device else None
    alarms = [
        ("water_quality", pond_ids[2] if len(pond_ids) > 2 else pond_ids[0], None, "ammonia_nitrogen", "high", "氨氮持续偏高，建议降低投喂量并执行换水策略。", "open"),
        ("device", pond_ids[2] if len(pond_ids) > 2 else pond_ids[0], offline_id, None, "medium", "水泵心跳中断，自动补水策略已暂停。", "acknowledged"),
        ("ai_disease", pond_ids[1] if len(pond_ids) > 1 else pond_ids[0], None, None, "medium", "AI 识别到疑似烂鳃病图像，建议人工复核。", "open"),
    ]
    cur.executemany(
        "INSERT INTO alarms (alarm_type,pond_id,device_id,parameter_key,severity,message,status) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        alarms,
    )
    ai = [
        (pond_ids[1] if len(pond_ids) > 1 else pond_ids[0], "/static/assets/fish-ai-sample.png", "烂鳃病", 0.86, "medium", "pending"),
        (pond_ids[3] if len(pond_ids) > 3 else pond_ids[0], "/static/assets/fish-ai-sample.png", "白点病", 0.78, "high", "confirmed"),
        (pond_ids[0], "/static/assets/fishery-vr.jpeg", "健康", 0.94, "low", "archived"),
    ]
    cur.executemany(
        "INSERT INTO ai_diagnoses (pond_id,image_url,disease_name,confidence,severity,status) VALUES (%s,%s,%s,%s,%s,%s)",
        ai,
    )
    cur.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    admin_row = cur.fetchone()
    admin_id = admin_row[0] if admin_row else None
    rules = [
        ("低溶氧自动增氧", None, "dissolved_oxygen", "< 5.5 mg/L", "start_aerator", 1, admin_id),
        ("氨氮超标换水", pond_ids[2] if len(pond_ids) > 2 else pond_ids[0], "ammonia_nitrogen", "> 2.5 mg/L", "start_pump", 1, admin_id),
        ("定时精准投喂", None, "schedule", "08:00 / 17:00", "feed", 1, admin_id),
    ]
    cur.executemany(
        "INSERT INTO automation_rules (rule_name,pond_id,trigger_type,trigger_condition,action_type,is_active,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        rules,
    )
    cur.execute("SELECT id, supplier_id, total_amount, status, order_date FROM purchase_orders")
    orders = cur.fetchall()
    for order_id, supplier_id, amount, status, order_date in orders:
        cur.execute(
            "INSERT INTO finance_transactions (supplier_id,order_id,transaction_type,amount,status,occurred_at,notes) VALUES (%s,%s,'receivable',%s,%s,%s,'订单应收')",
            (supplier_id, order_id, amount, "settled" if status in ("received", "shipped") else "pending", order_date),
        )
        cur.execute(
            "INSERT INTO finance_transactions (supplier_id,order_id,transaction_type,amount,status,occurred_at,notes) VALUES (%s,%s,'payment',%s,'settled',%s,'苗种成本')",
            (supplier_id, order_id, round(float(amount) * 0.58, 2), order_date),
        )
    cur.execute("SELECT id, pond_id, status, work_state FROM devices ORDER BY id LIMIT 16")
    for device_id, pond_id, status, work_state in cur.fetchall():
        cur.execute(
            "INSERT INTO device_logs (device_id,pond_id,action,operator,current_state,details) VALUES (%s,%s,%s,'系统策略',%s,%s)",
            (device_id, pond_id, "设备心跳", work_state, f"状态 {status}/{work_state}"),
        )


def apply_table_comments(cur) -> None:
    for table, comment in TABLE_COMMENTS.items():
        cur.execute(f"ALTER TABLE `{table}` COMMENT=%s", (comment,))


def ensure_sensor_devices(cur) -> None:
    cur.execute("SELECT id, pond_code, pond_name FROM ponds ORDER BY id")
    for pond_id, pond_code, pond_name in cur.fetchall():
        cur.execute("SELECT id FROM devices WHERE pond_id=%s AND device_type='sensor' LIMIT 1", (pond_id,))
        if cur.fetchone():
            continue
        cur.execute(
            """
            INSERT INTO devices
            (device_code, pond_id, device_name, device_type, status, work_state, power_consumption, last_active)
            VALUES (%s, %s, %s, 'sensor', 'offline', 'standby', 0, NULL)
            """,
            (f"SENSOR-{pond_code}", pond_id, f"{pond_name}-水质传感器"),
        )


def main() -> None:
    conn = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, charset="utf8mb4", autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DATABASE}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.execute(f"USE `{DATABASE}`")
        for statement in SCHEMA:
            cur.execute(statement)
        migrated = seed_from_old_database(cur)
        if not migrated:
            seed_fallback(cur)
        seed_enhancements(cur)
        apply_table_comments(cur)
    conn.close()
    print(f"Initialized database {DATABASE} using {'smart_fishery_db migration' if migrated else 'built-in seed rows'}.")


if __name__ == "__main__":
    main()
