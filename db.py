"""Base de datos SQLite: usuarios, suscripciones y pagos."""
import sqlite3
import time
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id         INTEGER PRIMARY KEY,
    tg_username   TEXT,
    jf_user_id    TEXT,              -- Id del usuario en Jellyfin
    jf_username   TEXT,
    expires_at    INTEGER DEFAULT 0, -- unix ts; 0 = sin acceso
    jf_enabled    INTEGER DEFAULT 0, -- 1 si la cuenta de Jellyfin está habilitada
    last_charge   TEXT,              -- telegram_payment_charge_id vigente
    canceled      INTEGER DEFAULT 0, -- 1 si el usuario canceló la renovación
    comped        INTEGER DEFAULT 0, -- 1 si el acceso lo dio el admin sin pago
    created_at    INTEGER
);
CREATE TABLE IF NOT EXISTS payments (
    charge_id     TEXT PRIMARY KEY,
    tg_id         INTEGER,
    amount        INTEGER,
    recurring     INTEGER,
    expires_at    INTEGER,
    refunded      INTEGER DEFAULT 0,
    created_at    INTEGER
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def now() -> int:
    return int(time.time())


def upsert_user(tg_id: int, tg_username: str | None):
    with conn() as c:
        c.execute(
            "INSERT INTO users (tg_id, tg_username, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET tg_username = excluded.tg_username",
            (tg_id, tg_username, now()),
        )


def get_user(tg_id: int) -> sqlite3.Row | None:
    with conn() as c:
        return c.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()


def set_jellyfin(tg_id: int, jf_user_id: str, jf_username: str):
    with conn() as c:
        c.execute(
            "UPDATE users SET jf_user_id = ?, jf_username = ? WHERE tg_id = ?",
            (jf_user_id, jf_username, tg_id),
        )


def set_enabled(tg_id: int, enabled: bool):
    with conn() as c:
        c.execute("UPDATE users SET jf_enabled = ? WHERE tg_id = ?", (int(enabled), tg_id))


def set_canceled(tg_id: int, canceled: bool):
    with conn() as c:
        c.execute("UPDATE users SET canceled = ? WHERE tg_id = ?", (int(canceled), tg_id))


def record_payment(tg_id: int, charge_id: str, amount: int, recurring: bool, expires_at: int) -> bool:
    """Guarda el pago y extiende el acceso. Idempotente por charge_id."""
    with conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO payments (charge_id, tg_id, amount, recurring, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (charge_id, tg_id, amount, int(recurring), expires_at, now()),
        )
        if cur.rowcount == 0:
            return False  # ya procesado
        c.execute(
            "UPDATE users SET expires_at = MAX(expires_at, ?), last_charge = ?, canceled = 0, comped = 0 "
            "WHERE tg_id = ?",
            (expires_at, charge_id, tg_id),
        )
        return True


def comp_access(tg_id: int, days: int) -> int:
    """Acceso de cortesía: suma días a partir de hoy o del vencimiento actual."""
    with conn() as c:
        row = c.execute("SELECT expires_at FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        base = max(now(), row["expires_at"] if row else 0)
        new_exp = base + days * 86400
        c.execute("UPDATE users SET expires_at = ?, comped = 1 WHERE tg_id = ?", (new_exp, tg_id))
        return new_exp


def expire_now(tg_id: int):
    with conn() as c:
        c.execute("UPDATE users SET expires_at = 0 WHERE tg_id = ?", (tg_id,))


def mark_refunded(charge_id: str):
    with conn() as c:
        c.execute("UPDATE payments SET refunded = 1 WHERE charge_id = ?", (charge_id,))


def due_for_disable(grace_seconds: int) -> list[sqlite3.Row]:
    """Cuentas habilitadas cuyo acceso venció (más el periodo de gracia)."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM users WHERE jf_enabled = 1 AND expires_at + ? < ?",
            (grace_seconds, now()),
        ).fetchall()


def pending_enable() -> list[sqlite3.Row]:
    """Usuarios con acceso vigente cuya cuenta todavía no está habilitada (p. ej. Jellyfin estaba caído)."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM users WHERE jf_enabled = 0 AND expires_at > ?", (now(),)
        ).fetchall()


def active_users() -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            "SELECT * FROM users WHERE expires_at > ? ORDER BY expires_at", (now(),)
        ).fetchall()


def stats() -> dict:
    with conn() as c:
        t = now()
        month_ago = t - 30 * 86400
        one = lambda q, *a: c.execute(q, a).fetchone()[0]
        return {
            "usuarios": one("SELECT COUNT(*) FROM users"),
            "activos": one("SELECT COUNT(*) FROM users WHERE expires_at > ?", t),
            "renovando": one(
                "SELECT COUNT(*) FROM users WHERE expires_at > ? AND comped = 0 AND canceled = 0", t
            ),
            "cortesia": one("SELECT COUNT(*) FROM users WHERE expires_at > ? AND comped = 1", t),
            "estrellas_30d": one(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE created_at > ? AND refunded = 0",
                month_ago,
            ),
            "estrellas_total": one("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE refunded = 0"),
        }
