from __future__ import annotations

from datetime import datetime, timedelta
import os
import random

import pymysql
from dotenv import load_dotenv


load_dotenv()
random.seed(20260423)

TARGET_ORDERS_PER_SUPPLIER = 28

PRODUCTS = {
    1: [
        ("精品草鱼夏花", "草鱼", "夏花", 0.42, 0.23, 18, 97, 6800),
        ("抗病草鱼苗", "草鱼", "抗病型", 0.95, 0.54, 45, 96, 5200),
        ("生态鲤鱼苗", "鲤鱼", "一龄", 0.66, 0.35, 35, 95, 4100),
        ("鳙鱼大规格苗", "鳙鱼", "大规格", 1.35, 0.78, 55, 93, 3600),
        ("青鱼强化苗", "青鱼", "二龄", 1.85, 1.02, 62, 94, 3000),
        ("鲫鱼快长苗", "鲫鱼", "快长型", 0.58, 0.31, 26, 96, 7600),
    ],
    2: [
        ("加州鲈寸苗", "鲈鱼", "寸苗", 2.20, 1.25, 38, 92, 5900),
        ("鲈鱼强化苗", "鲈鱼", "强化培育", 3.60, 2.10, 48, 94, 3200),
        ("鲳鱼快长苗", "鲳鱼", "快长型", 1.85, 1.05, 42, 91, 4600),
        ("黄颡鱼精品苗", "黄颡鱼", "精品", 2.80, 1.72, 50, 90, 2800),
        ("鳜鱼驯食苗", "鳜鱼", "驯食型", 4.20, 2.65, 58, 89, 1800),
        ("罗非鱼高产苗", "罗非鱼", "高产型", 0.74, 0.40, 32, 95, 8500),
    ],
    3: [
        ("罗非鱼苗", "罗非鱼", "全雄", 0.72, 0.38, 28, 96, 7200),
        ("斑点叉尾鮰苗", "鮰鱼", "寸苗", 1.95, 1.12, 46, 92, 3300),
        ("青鱼苗", "青鱼", "二龄", 2.40, 1.48, 60, 91, 2200),
        ("鲫鱼苗", "鲫鱼", "一龄", 0.58, 0.29, 30, 95, 6400),
        ("鲤鱼抗逆苗", "鲤鱼", "抗逆型", 0.88, 0.47, 36, 96, 5800),
        ("草鱼精品寸片", "草鱼", "寸片", 1.15, 0.68, 44, 95, 3900),
    ],
}

STATUS_CYCLE = ["draft", "confirmed", "confirmed", "shipped", "shipped", "received", "received"]


def connect():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456"),
        database=os.getenv("MYSQL_DATABASE", "SFweb"),
        charset="utf8mb4",
        autocommit=True,
    )


def ensure_product(cur, supplier_id: int, product: tuple) -> tuple[int, float, float]:
    name, species, grade, price, cost, cycle, rate, quantity = product
    cur.execute("SELECT id FROM seedling_products WHERE supplier_id=%s AND product_name=%s", (supplier_id, name))
    row = cur.fetchone()
    if row:
        product_id = row[0]
    else:
        cur.execute(
            """
            INSERT INTO seedling_products
            (supplier_id, product_name, species, grade, unit_price, cost_price, growth_cycle_days, survival_rate, description)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (supplier_id, name, species, grade, price, cost, cycle, rate, f"{species}{grade}鱼苗，适合规模化养殖基地补苗。"),
        )
        product_id = cur.lastrowid

    cur.execute(
        """
        INSERT INTO seedling_inventory (supplier_id, product_id, quantity, safety_stock, updated_by)
        VALUES (%s,%s,%s,1500,'seed-enhance')
        ON DUPLICATE KEY UPDATE
          quantity=GREATEST(quantity, VALUES(quantity)),
          safety_stock=VALUES(safety_stock),
          updated_by='seed-enhance'
        """,
        (supplier_id, product_id, quantity),
    )
    return product_id, float(price), float(cost)


def build_order_lines(products: list[tuple[int, float, float]]) -> tuple[list[tuple[int, int, float]], float, float]:
    line_count = random.randint(1, min(3, len(products)))
    selected = random.sample(products, line_count)
    lines: list[tuple[int, int, float]] = []
    revenue = 0.0
    cost_total = 0.0
    for product_id, price, cost in selected:
        quantity = random.randint(450, 2600)
        lines.append((product_id, quantity, price))
        revenue += price * quantity
        cost_total += cost * quantity
    return lines, round(revenue, 2), round(cost_total, 2)


def seed_stock_signals(cur, product_lookup: dict[int, list[tuple[int, float, float]]]) -> None:
    for supplier_id, products in product_lookup.items():
        for product, quantity in zip(products[:2], [1200, 2100]):
            product_id = product[0]
            cur.execute(
                """
                UPDATE seedling_inventory
                SET quantity=%s, safety_stock=1500, updated_by='seed-enhance'
                WHERE supplier_id=%s AND product_id=%s
                """,
                (quantity, supplier_id, product_id),
            )


def main() -> None:
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM ponds ORDER BY id")
        pond_ids = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
        admin = cur.fetchone()
        if not pond_ids or not admin:
            raise RuntimeError("SFweb 缺少 ponds 或 admin 用户，请先运行 init_db.py。")
        admin_id = admin[0]

        product_lookup = {
            supplier_id: [ensure_product(cur, supplier_id, product) for product in products]
            for supplier_id, products in PRODUCTS.items()
        }
        seed_stock_signals(cur, product_lookup)

        for supplier_id, products in product_lookup.items():
            cur.execute("SELECT COUNT(*) FROM purchase_orders WHERE supplier_id=%s", (supplier_id,))
            current = cur.fetchone()[0]
            for index in range(current + 1, TARGET_ORDERS_PER_SUPPLIER + 1):
                order_day = datetime.now() - timedelta(days=random.randint(1, 180))
                status = STATUS_CYCLE[index % len(STATUS_CYCLE)]
                lines, amount, cost = build_order_lines(products)
                order_no = f"SF{supplier_id}{order_day:%Y%m%d}{index:03d}"
                cur.execute(
                    """
                    INSERT IGNORE INTO purchase_orders
                    (order_no, supplier_id, pond_id, created_by, order_date, expected_delivery_date,
                     actual_delivery_date, status, total_amount, notes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        order_no,
                        supplier_id,
                        random.choice(pond_ids),
                        admin_id,
                        order_day,
                        (order_day + timedelta(days=7)).date(),
                        (order_day + timedelta(days=8)).date() if status == "received" else None,
                        status,
                        amount,
                        "比赛演示订单，体现鱼苗采购、配送、签收与结算闭环。",
                    ),
                )
                order_id = cur.lastrowid
                if not order_id:
                    continue
                for product_id, quantity, unit_price in lines:
                    cur.execute(
                        "INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (%s,%s,%s,%s)",
                        (order_id, product_id, quantity, unit_price),
                    )
                finance_status = "settled" if status in {"shipped", "received"} else "pending"
                cur.execute(
                    """
                    INSERT INTO finance_transactions
                    (supplier_id, order_id, transaction_type, amount, status, occurred_at, notes)
                    VALUES (%s,%s,'receivable',%s,%s,%s,'订单应收')
                    """,
                    (supplier_id, order_id, amount, finance_status, order_day),
                )
                cur.execute(
                    """
                    INSERT INTO finance_transactions
                    (supplier_id, order_id, transaction_type, amount, status, occurred_at, notes)
                    VALUES (%s,%s,'payment',%s,'settled',%s,'苗种成本')
                    """,
                    (supplier_id, order_id, cost, order_day),
                )

    conn.close()
    print(f"Enhanced supplier data in SFweb: target {TARGET_ORDERS_PER_SUPPLIER} orders per supplier.")


if __name__ == "__main__":
    main()
