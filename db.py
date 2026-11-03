"""Base de datos SQLite: usuarios, links de pago y pagos."""
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
    comped        INTEGER DEFAULT 0, -- 1 si el acceso vigente lo dio el admin sin pago
    reminded_for  INTEGER DEFAULT 0, -- expires_at para el que ya se mandó recordatorio
    created_at    INTEGER
);
CREATE TABLE IF NOT EXISTS checkouts (
    ref           TEXT PRIMARY KEY,  -- external_reference en Mercado Pago
    tg_id         INTEGER,
    pref_id       TEXT,
    url           TEXT,
    created_at    INTEGER,
    last_polled   INTEGER DEFAULT 0,
    last_status   TEXT,              -- último estado avisado al usuario
    done          INTEGER DEFAULT 0  -- 1 cuando ya se acreditó un pago con este link
);
CREATE TABLE IF NOT EXISTS payments (
    payment_id    TEXT PRIMARY KEY,  -- id del pago en Mercado Pago
    tg_id         INTEGER,
    ref           TEXT,
    amount        REAL,
    method        TEXT,
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


# ── usuarios ──

def upsert_user(tg_id: int, tg_username: str | None):
    with conn() as c:
        c.execute(
            "INSERT INTO users (tg_id, tg_username, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET tg_username = COALESCE(excluded.tg_username, tg_username)",
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


def comp_access(tg_id: int, days: int) -> int:
    """Acceso de cortesía: suma días a partir de hoy o del vencimiento actual."""
    with conn() as c:
        row = c.execute("SELECT expires_at FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        new_exp = max(now(), row["expires_at"] if row else 0) + days * 86400
        c.execute("UPDATE users SET expires_at = ?, comped = 1 WHERE tg_id = ?", (new_exp, tg_id))
        return new_exp


def expire_now(tg_id: int):
    with conn() as c:
        c.execute("UPDATE users SET expires_at = 0 WHERE tg_id = ?", (tg_id,))


# ── links de pago ──

def add_checkout(ref: str, tg_id: int, pref_id: str, url: str):
    with conn() as c:
        c.execute(
            "INSERT INTO checkouts (ref, tg_id, pref_id, url, created_at) VALUES (?, ?, ?, ?, ?)",
            (ref, tg_id, pref_id, url, now()),
        )


def reusable_checkout(tg_id: int, max_age_s: int) -> sqlite3.Row | None:
    """Link sin usar y reciente, para no crear uno nuevo cada vez que tocan el botón."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM checkouts WHERE tg_id = ? AND done = 0 AND last_status IS NULL "
            "AND created_at > ? ORDER BY created_at DESC LIMIT 1",
            (tg_id, now() - max_age_s),
        ).fetchone()


def checkouts_of(tg_id: int, max_age_s: int) -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            "SELECT * FROM checkouts WHERE tg_id = ? AND created_at > ?", (tg_id, now() - max_age_s)
        ).fetchall()


def checkouts_to_poll(max_age_s: int) -> list[sqlite3.Row]:
    """Links recientes que toca revisar: cada ciclo los de la última hora, cada 10 min los demás."""
    t = now()
    with conn() as c:
        return c.execute(
            "SELECT * FROM checkouts WHERE created_at > ? AND "
            "(created_at > ? OR last_polled < ?)",
            (t - max_age_s, t - 3600, t - 600),
        ).fetchall()


def mark_polled(ref: str):
    with conn() as c:
        c.execute("UPDATE checkouts SET last_polled = ? WHERE ref = ?", (now(), ref))


def set_checkout_status(ref: str, status: str, done: bool = False):
    with conn() as c:
        c.execute(
            "UPDATE checkouts SET last_status = ?, done = MAX(done, ?) WHERE ref = ?",
            (status, int(done), ref),
        )


# ── pagos ──

def record_payment(tg_id: int, payment_id: str, ref: str, amount: float, method: str, days: int) -> int | None:
    """Acredita un pago y extiende el acceso desde hoy o desde el vencimiento actual.

    Idempotente: si el pago ya estaba registrado devuelve None.
    """
    with conn() as c:
        if c.execute("SELECT 1 FROM payments WHERE payment_id = ?", (payment_id,)).fetchone():
            return None
        row = c.execute("SELECT expires_at FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        new_exp = max(now(), row["expires_at"] if row else 0) + days * 86400
        c.execute(
            "INSERT INTO payments (payment_id, tg_id, ref, amount, method, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (payment_id, tg_id, ref, amount, method, new_exp, now()),
        )
        c.execute("UPDATE users SET expires_at = ?, comped = 0 WHERE tg_id = ?", (new_exp, tg_id))
        return new_exp


def last_payment(tg_id: int) -> sqlite3.Row | None:
    with conn() as c:
        return c.execute(
            "SELECT * FROM payments WHERE tg_id = ? AND refunded = 0 ORDER BY created_at DESC LIMIT 1",
            (tg_id,),
        ).fetchone()


def mark_refunded(payment_id: str):
    with conn() as c:
        c.execute("UPDATE payments SET refunded = 1 WHERE payment_id = ?", (payment_id,))


# ── tareas periódicas ──

def due_for_disable(grace_seconds: int) -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            "SELECT * FROM users WHERE jf_enabled = 1 AND expires_at + ? < ?", (grace_seconds, now())
        ).fetchall()


def pending_enable() -> list[sqlite3.Row]:
    """Usuarios con acceso vigente cuya cuenta no está habilitada (p. ej. Jellyfin estaba caído)."""
    with conn() as c:
        return c.execute(
            "SELECT * FROM users WHERE jf_enabled = 0 AND expires_at > ?", (now(),)
        ).fetchall()


def due_for_reminder(window_s: int) -> list[sqlite3.Row]:
    t = now()
    with conn() as c:
        return c.execute(
            "SELECT * FROM users WHERE expires_at > ? AND expires_at < ? AND reminded_for != expires_at",
            (t, t + window_s),
        ).fetchall()


def mark_reminded(tg_id: int, expires_at: int):
    with conn() as c:
        c.execute("UPDATE users SET reminded_for = ? WHERE tg_id = ?", (expires_at, tg_id))


def active_users() -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            "SELECT * FROM users WHERE expires_at > ? ORDER BY expires_at", (now(),)
        ).fetchall()


def stats() -> dict:
    with conn() as c:
        t = now()
        one = lambda q, *a: c.execute(q, a).fetchone()[0]
        return {
            "usuarios": one("SELECT COUNT(*) FROM users"),
            "activos": one("SELECT COUNT(*) FROM users WHERE expires_at > ?", t),
            "cortesia": one("SELECT COUNT(*) FROM users WHERE expires_at > ? AND comped = 1", t),
            "pagos_30d": one("SELECT COUNT(*) FROM payments WHERE created_at > ? AND refunded = 0", t - 30 * 86400),
            "mxn_30d": one(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE created_at > ? AND refunded = 0",
                t - 30 * 86400,
            ),
            "mxn_total": one("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE refunded = 0"),
        }
