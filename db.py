"""
База даних магазину (SQLite, файл shop.db).

Таблиці:
  categories  — категорії товарів
  products    — товари (кожен належить до категорії)
  cart        — кошики користувачів (хто, який товар, скільки штук)
  orders      — замовлення (хто, контакти, доставка, сума, статус)
  order_items — що саме було в замовленні
"""

import sqlite3
from datetime import datetime

DB_PATH = "shop.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # вмикаємо перевірку зв'язків між таблицями
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER NOT NULL REFERENCES categories(id),
                name        TEXT    NOT NULL,
                description TEXT    NOT NULL,
                price       INTEGER NOT NULL,          -- ціна в гривнях
                photo_id    TEXT,                      -- id фото в Telegram (може не бути)
                active      INTEGER NOT NULL DEFAULT 1 -- 1 — у каталозі, 0 — прихований
            );

            CREATE TABLE IF NOT EXISTS cart (
                user_id    INTEGER NOT NULL,
                product_id INTEGER NOT NULL REFERENCES products(id),
                qty        INTEGER NOT NULL,
                PRIMARY KEY (user_id, product_id)
            );

            CREATE TABLE IF NOT EXISTS orders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                username   TEXT,
                name       TEXT    NOT NULL,
                phone      TEXT    NOT NULL,
                delivery   TEXT    NOT NULL,   -- "delivery" або "pickup"
                address    TEXT,               -- тільки для доставки
                total      INTEGER NOT NULL,
                status     TEXT    NOT NULL DEFAULT 'new',
                created_at TEXT    NOT NULL
            );

            -- Назву і ціну КОПІЮЄМО в замовлення, а не беремо з products.
            -- Якщо завтра ціна зміниться, у старому замовленні має лишитися стара.
            CREATE TABLE IF NOT EXISTS order_items (
                order_id INTEGER NOT NULL REFERENCES orders(id),
                name     TEXT    NOT NULL,
                price    INTEGER NOT NULL,
                qty      INTEGER NOT NULL
            );
            """
        )
        migrate(conn)
    seed_demo_data()


def migrate(conn):
    """
    «Міграція» — оновлення структури вже існуючої бази.
    У старому shop.db (з етапів 1–2) немає колонок photo_id і active.
    CREATE TABLE IF NOT EXISTS їх не додасть (таблиця вже є), тому додаємо вручну.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
    if "photo_id" not in columns:
        conn.execute("ALTER TABLE products ADD COLUMN photo_id TEXT")
    if "active" not in columns:
        conn.execute("ALTER TABLE products ADD COLUMN active INTEGER NOT NULL DEFAULT 1")


def seed_demo_data():
    """Заповнює магазин прикладами товарів, якщо він порожній."""
    with get_connection() as conn:
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] > 0:
            return

        demo = {
            "☕ Кава": [
                ("Еспресо", "Класичний міцний еспресо, 30 мл", 45),
                ("Капучино", "Еспресо з молочною пінкою, 250 мл", 65),
                ("Лате", "Ніжна кава з великою кількістю молока, 350 мл", 75),
            ],
            "🍰 Десерти": [
                ("Чизкейк", "Нью-Йорк чизкейк з ягідним соусом", 95),
                ("Круасан", "Свіжий масляний круасан", 55),
            ],
            "🥤 Напої": [
                ("Лимонад", "Домашній лимонад з м'ятою, 400 мл", 70),
                ("Какао", "Гаряче какао з маршмелоу, 300 мл", 60),
            ],
        }
        for category, products in demo.items():
            cur = conn.execute("INSERT INTO categories (name) VALUES (?)", (category,))
            category_id = cur.lastrowid
            for name, description, price in products:
                conn.execute(
                    "INSERT INTO products (category_id, name, description, price) VALUES (?, ?, ?, ?)",
                    (category_id, name, description, price),
                )


# ---------- КАТАЛОГ ----------

def get_categories():
    with get_connection() as conn:
        return conn.execute("SELECT * FROM categories ORDER BY id").fetchall()


def get_category(category_id: int):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()


def get_products(category_id: int, include_hidden: bool = False):
    """Товари категорії. Клієнт бачить лише активні, адмін — усі."""
    with get_connection() as conn:
        if include_hidden:
            sql = "SELECT * FROM products WHERE category_id = ? ORDER BY id"
        else:
            sql = "SELECT * FROM products WHERE category_id = ? AND active = 1 ORDER BY id"
        return conn.execute(sql, (category_id,)).fetchall()


def get_product(product_id: int):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


# ---------- АДМІНКА: КАТАЛОГ ----------

def add_category(name: str) -> int:
    with get_connection() as conn:
        return conn.execute("INSERT INTO categories (name) VALUES (?)", (name,)).lastrowid


def add_product(category_id, name, description, price, photo_id) -> int:
    with get_connection() as conn:
        return conn.execute(
            """
            INSERT INTO products (category_id, name, description, price, photo_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (category_id, name, description, price, photo_id),
        ).lastrowid


def update_price(product_id: int, price: int):
    with get_connection() as conn:
        conn.execute("UPDATE products SET price = ? WHERE id = ?", (price, product_id))


def update_photo(product_id: int, photo_id: str):
    with get_connection() as conn:
        conn.execute("UPDATE products SET photo_id = ? WHERE id = ?", (photo_id, product_id))


def toggle_active(product_id: int) -> bool:
    """
    Ховає або повертає товар. Повертає новий стан (True — активний).

    Товар не видаляємо, а ховаємо («м'яке видалення»): так не ламаються зв'язки
    з іншими таблицями, і товар можна повернути одним натисканням.
    """
    with get_connection() as conn:
        conn.execute("UPDATE products SET active = 1 - active WHERE id = ?", (product_id,))
        active = conn.execute(
            "SELECT active FROM products WHERE id = ?", (product_id,)
        ).fetchone()["active"]
        if not active:
            # прихований товар прибираємо з усіх кошиків
            conn.execute("DELETE FROM cart WHERE product_id = ?", (product_id,))
        return bool(active)


# ---------- КОШИК ----------

def add_to_cart(user_id: int, product_id: int):
    """Додає 1 шт. Якщо товар уже в кошику — збільшує кількість."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO cart (user_id, product_id, qty) VALUES (?, ?, 1)
            ON CONFLICT (user_id, product_id) DO UPDATE SET qty = qty + 1
            """,
            (user_id, product_id),
        )


def decrease_in_cart(user_id: int, product_id: int):
    """Зменшує на 1 шт. Якщо стало 0 — прибирає товар з кошика."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE cart SET qty = qty - 1 WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        )
        conn.execute("DELETE FROM cart WHERE user_id = ? AND qty <= 0", (user_id,))


def get_cart(user_id: int):
    """Товари в кошику разом з назвою і ціною (JOIN — «склеюємо» дві таблиці)."""
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT p.id, p.name, p.price, c.qty
            FROM cart c
            JOIN products p ON p.id = c.product_id
            WHERE c.user_id = ? AND p.active = 1
            ORDER BY p.name
            """,
            (user_id,),
        ).fetchall()


def clear_cart(user_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))


# ---------- ЗАМОВЛЕННЯ ----------

def create_order(user_id, username, name, phone, delivery, address):
    """
    Перетворює кошик на замовлення. Повертає id замовлення
    або None, якщо кошик порожній.

    Усе відбувається в ОДНІЙ транзакції (with conn): або виконаються всі кроки
    (створити замовлення, скопіювати товари, очистити кошик), або жоден.
    """
    with get_connection() as conn:
        items = conn.execute(
            """
            SELECT p.name, p.price, c.qty
            FROM cart c JOIN products p ON p.id = c.product_id
            WHERE c.user_id = ? AND p.active = 1
            ORDER BY p.name
            """,
            (user_id,),
        ).fetchall()
        if not items:
            return None

        total = sum(item["price"] * item["qty"] for item in items)
        cur = conn.execute(
            """
            INSERT INTO orders (user_id, username, name, phone, delivery, address, total, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, username, name, phone, delivery, address, total,
                datetime.now().strftime("%d.%m.%Y %H:%M"),
            ),
        )
        order_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO order_items (order_id, name, price, qty) VALUES (?, ?, ?, ?)",
            [(order_id, item["name"], item["price"], item["qty"]) for item in items],
        )
        conn.execute("DELETE FROM cart WHERE user_id = ?", (user_id,))
        return order_id


def get_order(order_id: int):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def get_order_items(order_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
        ).fetchall()


def get_user_orders(user_id: int, limit: int = 5):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def get_active_orders():
    """Замовлення, які ще треба обробити (нові і ті, що готуються)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE status IN ('new', 'accepted') ORDER BY id"
        ).fetchall()


def set_order_status(order_id: int, status: str):
    with get_connection() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))