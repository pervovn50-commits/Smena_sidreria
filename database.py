import sqlite3
from datetime import datetime

DB_PATH = "cafe.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id       INTEGER PRIMARY KEY,
            username    TEXT,
            full_name   TEXT,
            role        TEXT NOT NULL DEFAULT 'pending'
                            CHECK(role IN ('pending','barista','manager','rejected')),
            approved_by INTEGER,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS menu_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            category    TEXT NOT NULL CHECK(category IN ('dessert','food')),
            shelf_hours INTEGER NOT NULL DEFAULT 120,
            active      INTEGER NOT NULL DEFAULT 1,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS display_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_item_id INTEGER NOT NULL REFERENCES menu_items(id),
            added_by_id  INTEGER NOT NULL REFERENCES users(tg_id),
            added_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at   TIMESTAMP NOT NULL,
            reminded     INTEGER NOT NULL DEFAULT 0,
            status       TEXT NOT NULL DEFAULT 'active'
                           CHECK(status IN ('active','sold','written_off'))
        );

        CREATE TABLE IF NOT EXISTS operations (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            display_id        INTEGER REFERENCES display_items(id),
            item_name         TEXT NOT NULL,
            employee_id       INTEGER NOT NULL REFERENCES users(tg_id),
            employee_name     TEXT NOT NULL,
            op_type           TEXT NOT NULL CHECK(op_type IN ('writeoff','sale')),
            reason            TEXT,
            hours_on_display  REAL,
            created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)

# ── ПОЛЬЗОВАТЕЛИ ────────────────────────────────────────────

def upsert_user(tg_id, username, full_name):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO users (tg_id, username, full_name)
            VALUES (?,?,?)
            ON CONFLICT(tg_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name
        """, (tg_id, username, full_name))

def get_user(tg_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()

def set_user_role(tg_id, role, approved_by):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET role=?, approved_by=? WHERE tg_id=?",
            (role, approved_by, tg_id)
        )

def get_users_by_role(role):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE role=?", (role,)).fetchall()

def get_all_staff():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE role IN ('barista','manager') ORDER BY role, full_name"
        ).fetchall()

# ── МЕНЮ ────────────────────────────────────────────────────

def add_menu_item(name, category, shelf_hours):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO menu_items (name, category, shelf_hours) VALUES (?,?,?)",
            (name, category, shelf_hours)
        )

def deactivate_menu_item(item_id):
    with get_db() as conn:
        conn.execute("UPDATE menu_items SET active=0 WHERE id=?", (item_id,))

def update_menu_item_hours(item_id, hours):
    with get_db() as conn:
        conn.execute("UPDATE menu_items SET shelf_hours=? WHERE id=?", (hours, item_id))

def get_menu_items(category=None):
    with get_db() as conn:
        if category:
            return conn.execute(
                "SELECT * FROM menu_items WHERE active=1 AND category=? ORDER BY name",
                (category,)
            ).fetchall()
        return conn.execute(
            "SELECT * FROM menu_items WHERE active=1 ORDER BY category, name"
        ).fetchall()

def get_menu_item_by_id(item_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM menu_items WHERE id=?", (item_id,)).fetchone()

# ── ВИТРИНА ─────────────────────────────────────────────────

def add_display_items(menu_item_id, shelf_hours, added_by_id, quantity):
    from datetime import timedelta
    now     = datetime.now()
    expires = now + timedelta(hours=shelf_hours)
    with get_db() as conn:
        for _ in range(quantity):
            conn.execute("""
                INSERT INTO display_items (menu_item_id, added_by_id, added_at, expires_at)
                VALUES (?,?,?,?)
            """, (menu_item_id, added_by_id, now, expires))

def get_active_display_items():
    with get_db() as conn:
        return conn.execute("""
            SELECT di.id, mi.name, mi.category, u.full_name as added_by,
                   di.added_at, di.expires_at,
                   ROUND((julianday(di.expires_at) - julianday('now','localtime')) * 24, 1) as hours_left
            FROM display_items di
            JOIN menu_items mi ON mi.id = di.menu_item_id
            JOIN users u ON u.tg_id = di.added_by_id
            WHERE di.status = 'active'
            ORDER BY di.expires_at
        """).fetchall()

def get_dessert_stock():
    with get_db() as conn:
        return conn.execute("""
            SELECT mi.name,
                   COUNT(*) as qty,
                   MIN(ROUND((julianday(di.expires_at) - julianday('now','localtime')) * 24, 1)) as min_hours,
                   MAX(ROUND((julianday(di.expires_at) - julianday('now','localtime')) * 24, 1)) as max_hours,
                   SUM(CASE WHEN di.expires_at <= datetime('now','localtime') THEN 1 ELSE 0 END) as expired_qty
            FROM display_items di
            JOIN menu_items mi ON mi.id = di.menu_item_id
            WHERE di.status = 'active' AND mi.category = 'dessert'
            GROUP BY mi.name
            ORDER BY min_hours
        """).fetchall()

def close_display_item(display_id, status):
    with get_db() as conn:
        conn.execute("UPDATE display_items SET status=? WHERE id=?", (status, display_id))

def get_expiring_items():
    with get_db() as conn:
        return conn.execute("""
            SELECT di.id, mi.name, di.expires_at, u.full_name as added_by
            FROM display_items di
            JOIN menu_items mi ON mi.id = di.menu_item_id
            JOIN users u ON u.tg_id = di.added_by_id
            WHERE di.status = 'active'
              AND di.reminded = 0
              AND datetime(di.expires_at) <= datetime('now','localtime','+2 hours')
        """).fetchall()

def mark_reminded(display_id):
    with get_db() as conn:
        conn.execute("UPDATE display_items SET reminded=1 WHERE id=?", (display_id,))

# ── ОПЕРАЦИИ ────────────────────────────────────────────────

def add_operation(display_id, item_name, employee_id, employee_name,
                  op_type, reason, hours_on_display):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO operations
              (display_id, item_name, employee_id, employee_name,
               op_type, reason, hours_on_display)
            VALUES (?,?,?,?,?,?,?)
        """, (display_id, item_name, employee_id, employee_name,
              op_type, reason, hours_on_display))

def get_today_operations():
    with get_db() as conn:
        return conn.execute("""
            SELECT * FROM operations
            WHERE DATE(created_at) = DATE('now','localtime')
            ORDER BY created_at
        """).fetchall()
