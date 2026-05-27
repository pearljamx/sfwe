from __future__ import annotations

import atexit
import csv
import io
import os
from functools import wraps
from typing import Any

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from ai_vision import FishDiseaseAI
from hardware_runtime import HardwareRuntime
from store import DatabaseUnavailable, FisheryStore


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY", "sfweb-local-secret")
store = FisheryStore.from_env()
ai_service = FishDiseaseAI(store=store)
hardware_runtime = HardwareRuntime(callback=store.ingest_hardware_frame, image_callback=ai_service.ingest_udp_image)
atexit.register(hardware_runtime.stop)

STATUS_LABELS = {
    "normal": "正常",
    "warning": "预警",
    "maintenance": "维护中",
    "offline": "离线",
    "online": "在线",
    "fault": "故障",
    "running": "运行中",
    "standby": "待机",
    "stopped": "停止",
    "open": "未处理",
    "acknowledged": "处理中",
    "resolved": "已处理",
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "严重",
    "pending": "待复核",
    "confirmed": "已确认",
    "dismissed": "已排除",
    "archived": "已归档",
    "draft": "草稿",
    "shipped": "配送中",
    "received": "已收货",
    "cancelled": "已取消",
    "settled": "已结算",
    "queued": "排队中",
    "sent": "已发送",
    "failed": "失败",
    "active": "合作中",
    "paused": "暂停",
    "blacklisted": "黑名单",
}

TYPE_LABELS = {
    "aerator": "增氧机",
    "feeder": "投喂机",
    "pump": "水泵",
    "camera": "摄像头",
    "sensor": "传感器",
    "gateway": "网关",
    "water_quality": "水质异常",
    "device": "设备异常",
    "ai_disease": "AI 鱼病",
    "communication": "通信异常",
    "system": "系统事件",
    "receivable": "订单应收",
    "payment": "苗种成本",
    "refund": "退款",
    "adjustment": "调账",
}

ROLE_LABELS = {"admin": "管理员", "operator": "运维员", "supplier": "供应商"}
STOCK_LABELS = {"normal": "库存充足", "medium": "接近预警", "warning": "低库存"}

NAV = {
    "admin": [
        ("运营总览", "admin_dashboard", "总"),
        ("三端互联", "admin_interconnect", "联"),
        ("鱼池监控", "admin_ponds", "池"),
        ("设备调度", "admin_devices", "设"),
        ("告警中心", "admin_alerts", "警"),
        ("AI 识别", "admin_ai", "AI"),
        ("自动控制", "admin_automation", "控"),
        ("数据分析", "admin_analytics", "析"),
        ("供应链协同", "admin_supply", "链"),
    ],
    "supplier": [
        ("供应商看板", "supplier_dashboard", "板"),
        ("产品库存", "supplier_products", "品"),
        ("订单协同", "supplier_orders", "单"),
        ("财务统计", "supplier_finance", "财"),
    ],
}


def current_user() -> dict[str, Any] | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return store.get_user_by_id(int(user_id))


def login_required(*roles: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("login"))
            if roles and user["role"] not in roles:
                return redirect(url_for("supplier_dashboard" if user["role"] == "supplier" else "admin_dashboard"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


@app.context_processor
def inject_layout():
    user = current_user()
    nav_key = "supplier" if user and user["role"] == "supplier" else "admin"
    return {
        "current_user": user,
        "nav_items": NAV[nav_key],
        "db_name": store.database,
        "db_connected": store.available,
        "status_label": lambda value: STATUS_LABELS.get(value, value),
        "stock_label": lambda value: STOCK_LABELS.get(value, STATUS_LABELS.get(value, value)),
        "type_label": lambda value: TYPE_LABELS.get(value, value),
        "role_label": lambda value: ROLE_LABELS.get(value, value),
    }


@app.errorhandler(DatabaseUnavailable)
def database_error(error):
    return render_template("db_error.html", message=str(error)), 503


@app.route("/")
def index():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    return redirect(url_for("supplier_dashboard" if user["role"] == "supplier" else "admin_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not store.available:
        raise DatabaseUnavailable(store.last_error or "数据库未连接")

    error = None
    selected_role = request.form.get("role", "admin")
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        selected_role = request.form.get("role", "admin")
        user = store.get_user_by_username(username)
        if not user or not check_password_hash(user["password_hash"], password):
            error = "账号或密码不正确"
        elif selected_role == "supplier" and user["role"] != "supplier":
            error = "请选择供应商身份登录该账号"
        elif selected_role == "admin" and user["role"] == "supplier":
            error = "供应商账号不能进入管理员端"
        else:
            session["user_id"] = user["id"]
            store.touch_login(user["id"])
            return redirect(url_for("supplier_dashboard" if user["role"] == "supplier" else "admin_dashboard"))
    return render_template("login.html", error=error, selected_role=selected_role)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/dashboard")
@login_required("admin", "operator")
def admin_dashboard():
    return render_template("admin/dashboard.html", page="admin_dashboard", title="运营总览", data=store.admin_dashboard())


@app.route("/admin/ponds")
@login_required("admin", "operator")
def admin_ponds():
    data = store.pond_workspace()
    data["hardware"] = hardware_runtime.status(store.hardware_enabled_count())
    return render_template("admin/ponds.html", page="admin_ponds", title="鱼池监控", data=data)


@app.route("/admin/interconnect")
@login_required("admin", "operator")
def admin_interconnect():
    return render_template("admin/interconnect.html", page="admin_interconnect", title="三端互联", data=store.interconnect_workspace())


@app.route("/admin/devices")
@login_required("admin", "operator")
def admin_devices():
    return render_template("admin/devices.html", page="admin_devices", title="设备调度", data=store.device_workspace())


@app.route("/admin/alerts")
@login_required("admin", "operator")
def admin_alerts():
    return render_template("admin/alerts.html", page="admin_alerts", title="告警中心", data=store.alert_workspace())


@app.route("/admin/ai")
@login_required("admin", "operator")
def admin_ai():
    data = store.ai_workspace()
    data["hardware"] = hardware_runtime.status(store.hardware_enabled_count())
    data["model"] = ai_service.status()
    return render_template("admin/ai.html", page="admin_ai", title="AI 鱼病识别", data=data)


@app.route("/admin/automation")
@login_required("admin", "operator")
def admin_automation():
    return render_template("admin/automation.html", page="admin_automation", title="自动控制", data=store.automation_workspace())


@app.route("/admin/analytics")
@login_required("admin", "operator")
def admin_analytics():
    return render_template("admin/analytics.html", page="admin_analytics", title="数据分析", data=store.analytics_workspace())


@app.route("/admin/supply")
@login_required("admin", "operator")
def admin_supply():
    return render_template("admin/supply.html", page="admin_supply", title="供应链协同", data=store.supply_workspace())


@app.route("/supplier/dashboard")
@login_required("supplier")
def supplier_dashboard():
    user = current_user()
    return render_template("supplier/dashboard.html", page="supplier_dashboard", title="供应商看板", data=store.supplier_dashboard(user["supplier_id"]))


@app.route("/supplier/products")
@login_required("supplier")
def supplier_products():
    user = current_user()
    return render_template("supplier/products.html", page="supplier_products", title="产品库存", data=store.supplier_products(user["supplier_id"]))


@app.route("/supplier/orders")
@login_required("supplier")
def supplier_orders():
    user = current_user()
    return render_template("supplier/orders.html", page="supplier_orders", title="订单协同", data=store.supplier_orders(user["supplier_id"]))


@app.route("/supplier/finance")
@login_required("supplier")
def supplier_finance():
    user = current_user()
    return render_template("supplier/finance.html", page="supplier_finance", title="财务统计", data=store.supplier_finance(user["supplier_id"]))


@app.get("/api/realtime")
@login_required("admin", "operator")
def api_realtime():
    return jsonify(store.realtime_snapshot())


@app.post("/api/devices/<int:device_id>/command")
@login_required("admin", "operator")
def api_device_command(device_id: int):
    payload = request.get_json(force=True, silent=True) or {}
    user = current_user()
    return jsonify(store.command_device(device_id, payload.get("action", "toggle"), user["username"], hardware_runtime.send_command))


@app.post("/api/alerts/<int:alert_id>/resolve")
@login_required("admin", "operator")
def api_resolve_alert(alert_id: int):
    user = current_user()
    return jsonify(store.resolve_alert(alert_id, user["id"]))


@app.post("/api/thresholds")
@login_required("admin", "operator")
def api_thresholds():
    payload = request.get_json(force=True)
    return jsonify(store.update_threshold(payload))


@app.get("/api/hardware/status")
@login_required("admin", "operator")
def api_hardware_status():
    return jsonify(hardware_runtime.status(store.hardware_enabled_count()))


@app.get("/api/ai/status")
@login_required("admin", "operator")
def api_ai_status():
    return jsonify({"hardware": hardware_runtime.status(store.hardware_enabled_count()), "model": ai_service.status()})


@app.post("/api/hardware/start")
@login_required("admin", "operator")
def api_hardware_start():
    return jsonify(hardware_runtime.start())


@app.post("/api/hardware/stop")
@login_required("admin", "operator")
def api_hardware_stop():
    return jsonify(hardware_runtime.stop())


@app.post("/api/ponds/<int:pond_id>/hardware-ingest")
@login_required("admin", "operator")
def api_pond_hardware_ingest(pond_id: int):
    payload = request.get_json(force=True, silent=True) or {}
    return jsonify(store.set_hardware_ingest(pond_id, bool(payload.get("enabled"))))


@app.post("/api/orders/<int:order_id>/status")
@login_required("supplier")
def api_order_status(order_id: int):
    user = current_user()
    payload = request.get_json(force=True)
    return jsonify(store.update_order_status(order_id, user["supplier_id"], payload.get("status", "shipped")))


@app.post("/api/products/<int:product_id>/inventory")
@login_required("supplier")
def api_inventory(product_id: int):
    user = current_user()
    payload = request.get_json(force=True)
    return jsonify(store.update_inventory(product_id, user["supplier_id"], int(payload.get("quantity", 0))))


@app.get("/export/water-quality.csv")
@login_required("admin", "operator")
def export_water_quality():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["鱼池", "水温", "pH", "食物值", "盐度", "溶解氧", "氨氮", "亚硝酸盐", "记录时间"])
    writer.writerows(store.export_sensor_rows())
    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=water-quality.csv"},
    )


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        use_reloader=False,
    )
