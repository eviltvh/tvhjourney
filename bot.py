"""THE TVH JOURNEY — acceso a Jellyfin pagado con Mercado Pago, controlado por Telegram."""
import asyncio
import html
import logging
import secrets
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import db
import jellyfin_access as jf
import mp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tvh")

bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
router = Router()
BOT_USERNAME = ""  # se llena al arrancar
PRICE = f"${config.PRICE_MXN:g} MXN"
CHECKOUT_MAX_AGE = (config.CHECKOUT_DAYS + 5) * 86400  # cuánto tiempo se sigue revisando un link
_pay_lock = asyncio.Lock()


# ───────────────────────── utilidades ─────────────────────────

def fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def has_access(row) -> bool:
    return bool(row) and row["expires_at"] > db.now()


def who(u) -> str:
    return f"@{u.username}" if u.username else html.escape(u.full_name)


def pay_button(label: str | None = None) -> InlineKeyboardMarkup:
    label = label or f"PAGAR {PRICE} · {config.PASS_DAYS} DÍAS"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data="pay")]])


_last_warn: dict[int, int] = {}


async def warn_once(key: int, text: str, every_s: int = 3600):
    """Aviso al admin sin spamear: máximo uno por usuario por hora."""
    if db.now() - _last_warn.get(key, 0) >= every_s:
        _last_warn[key] = db.now()
        await notify_admins(text)


async def notify_admins(text: str):
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, text)
        except Exception as e:  # el admin puede no haber abierto el bot
            log.warning("No pude avisar al admin %s: %s", admin, e)


async def safe_send(tg_id: int, text: str, **kw):
    try:
        await bot.send_message(tg_id, text, **kw)
    except Exception as e:
        log.warning("No pude escribirle a %s: %s", tg_id, e)


async def send_credentials(tg_id: int, username: str, password: str, intro: str):
    await bot.send_message(
        tg_id,
        f"{intro}\n\n"
        f"<b>SERVIDOR</b>  <code>{html.escape(config.JELLYFIN_PUBLIC_URL)}</code>\n"
        f"<b>USUARIO</b>   <code>{html.escape(username)}</code>\n"
        f"<b>CONTRASEÑA</b> <code>{html.escape(password)}</code>\n\n"
        "📱 En el cel o la compu: entra a esa dirección o usa la app <b>Jellyfin</b>.\n"
        "📺 En la tele: /tv y te guío, sin escribir contraseña.\n"
        "Si pierdes la contraseña: /password",
        protect_content=True,  # evita reenviarlo
    )


async def ensure_access(tg_id: int) -> bool:
    """Crea o habilita la cuenta de Jellyfin del usuario. Devuelve True si quedó habilitada."""
    row = db.get_user(tg_id)
    try:
        if row["jf_user_id"] and await jf.exists(row["jf_user_id"]):
            await jf.enable(row["jf_user_id"])
            db.set_enabled(tg_id, True)
            await safe_send(
                tg_id,
                f"Tu cuenta <code>{html.escape(row['jf_username'])}</code> está activa. "
                "Mismo usuario y contraseña de antes (si no la recuerdas: /password).",
            )
        else:
            user_id, username, password = await jf.create_user(tg_id, row["tg_username"])
            db.set_jellyfin(tg_id, user_id, username)
            db.set_enabled(tg_id, True)
            await send_credentials(tg_id, username, password, "<b>BIENVENIDO A THE TVH JOURNEY.</b>")
        return True
    except Exception as e:
        log.exception("No pude dar acceso a %s", tg_id)
        await warn_once(
            tg_id,
            f"⚠️ No pude habilitar Jellyfin para <code>{tg_id}</code>: {html.escape(str(e))}\n"
            "Reintento cada pocos minutos.",
        )
        return False


async def disable_user(row):
    if row["jf_user_id"]:
        await jf.disable(row["jf_user_id"])
    db.set_enabled(row["tg_id"], False)


# ───────────────────────── pagos ─────────────────────────

async def checkout_link(tg_id: int) -> str:
    ck = db.reusable_checkout(tg_id, max_age_s=12 * 3600)
    if ck:
        return ck["url"]
    ref = f"tvh-{tg_id}-{secrets.token_hex(4)}"
    back = f"https://t.me/{BOT_USERNAME}?start=pagado"
    pref_id, url = await mp.create_checkout(ref, back)
    db.add_checkout(ref, tg_id, pref_id, url)
    return url


async def send_pay_link(tg_id: int, target: Message):
    try:
        url = await checkout_link(tg_id)
    except Exception as e:
        log.exception("create_checkout")
        await target.answer("No pude generar el link de pago ahorita. Intenta en unos minutos.")
        await warn_once(tg_id, f"⚠️ Mercado Pago falló al crear link para <code>{tg_id}</code>: {html.escape(str(e))}")
        return
    row = db.get_user(tg_id)
    extra = (
        f"\nSe suman a tu acceso actual (vence el {fmt_date(row['expires_at'])}), no pierdes días."
        if has_access(row) else ""
    )
    await target.answer(
        f"<b>{PRICE} · {config.PASS_DAYS} días de acceso</b>{extra}\n\n"
        "Paga con <b>tarjeta, saldo de Mercado Pago, OXXO o SPEI</b>.\n"
        "• Tarjeta y saldo: se activa en cuanto pagas.\n"
        "• OXXO y SPEI: se activa cuando se acredita (puede tardar unas horas).\n\n"
        "Te aviso por aquí en cuanto entre tu pago.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 PAGAR {PRICE}", url=url)],
            [InlineKeyboardButton(text="✅ YA PAGUÉ", callback_data="chk")],
        ]),
    )


async def grant_payment(tg_id: int, ref: str, p: dict) -> bool:
    """Acredita un pago aprobado. Devuelve True si era nuevo."""
    pid = str(p["id"])
    amount = float(p.get("transaction_amount") or 0)
    if p.get("currency_id") != "MXN" or amount + 0.01 < config.PRICE_MXN:
        await warn_once(int(pid[-9:]), f"⚠️ Pago {pid} con monto raro ({amount} {p.get('currency_id')}), revísalo.")
        return False
    method = p.get("payment_type_id") or p.get("payment_method_id") or "?"
    new_exp = db.record_payment(tg_id, pid, ref, amount, method, config.PASS_DAYS)
    if new_exp is None:
        return False  # ya acreditado
    db.set_checkout_status(ref, "approved", done=True)

    row = db.get_user(tg_id)
    if row["jf_enabled"] and row["jf_user_id"]:
        await safe_send(tg_id, f"✅ Pago recibido. Tu acceso ahora vence el <b>{fmt_date(new_exp)}</b>. Gracias por seguir aquí.")
    else:
        await safe_send(tg_id, f"✅ Pago recibido. Acceso hasta el <b>{fmt_date(new_exp)}</b>.")
        await ensure_access(tg_id)

    user = row["tg_username"] and f"@{row['tg_username']}" or str(tg_id)
    await notify_admins(f"💸 {html.escape(user)} pagó ${amount:g} MXN ({method}) · hasta {fmt_date(new_exp)}")
    return True


STATUS_MSG = {
    "pending": "🕓 Tu pago quedó <b>pendiente</b>. Si elegiste OXXO, paga la ficha antes de que venza; "
               "si fue SPEI, en cuanto se acredite te activo solo.",
    "in_process": "🕓 Mercado Pago está revisando tu pago. Te aviso en cuanto se apruebe.",
    "rejected": "❌ Tu pago fue <b>rechazado</b>. Puedes intentar con otra tarjeta o pagar en OXXO con el mismo link.",
    "cancelled": "Tu ficha o link de pago venció sin pagarse. Si aún quieres entrar: /pagar",
}


async def process_checkout(ck) -> str | None:
    """Revisa los pagos de un link. Devuelve el estado más relevante."""
    results = await mp.payments_for(ck["ref"])
    db.mark_polled(ck["ref"])
    statuses = [r.get("status") for r in results]

    for p in results:
        if p.get("status") == "approved":
            await grant_payment(ck["tg_id"], ck["ref"], p)
        elif p.get("status") in ("refunded", "charged_back"):
            await handle_reversal(ck["tg_id"], p)

    if "approved" in statuses:
        return "approved"
    if ck["done"] or not results:
        return None
    status = results[0].get("status")  # el más reciente
    if status in STATUS_MSG and status != ck["last_status"]:
        db.set_checkout_status(ck["ref"], status)
        await safe_send(ck["tg_id"], STATUS_MSG[status])
    return status


async def handle_reversal(tg_id: int, p: dict):
    """Contracargo o reembolso hecho desde Mercado Pago: cortar el acceso de ese pago."""
    pid = str(p["id"])
    pay = db.last_payment(tg_id)
    if not pay or pay["payment_id"] != pid:
        return
    db.mark_refunded(pid)
    db.expire_now(tg_id)
    row = db.get_user(tg_id)
    try:
        await disable_user(row)
    except Exception:
        log.exception("disable tras reversa")
    await notify_admins(f"↩️ El pago {pid} de <code>{tg_id}</code> quedó {p.get('status')}. Acceso cortado.")


async def check_user_payments(tg_id: int) -> str | None:
    async with _pay_lock:
        best = None
        for ck in db.checkouts_of(tg_id, CHECKOUT_MAX_AGE):
            st = await process_checkout(ck)
            if st == "approved":
                best = "approved"
            elif st and not best:
                best = st
        return best


# ───────────────────────── comandos públicos ─────────────────────────

@router.message(CommandStart())
async def start(m: Message, command: CommandObject):
    db.upsert_user(m.from_user.id, m.from_user.username)
    if command.args == "pagado":  # regresó desde Mercado Pago
        st = await check_user_payments(m.from_user.id)
        if st in (None, "pending", "in_process"):
            await m.answer("Gracias. En cuanto Mercado Pago confirme tu pago te aviso por aquí.")
        return
    row = db.get_user(m.from_user.id)
    if has_access(row):
        await m.answer(
            "<b>THE TVH JOURNEY</b>\n\n"
            f"Tienes acceso hasta el <b>{fmt_date(row['expires_at'])}</b>.\n"
            "/tv · /cuenta · /password · /pagar (sumar días) · /ayuda"
        )
        return
    await m.answer(
        "<b>THE TVH JOURNEY</b>\n\n"
        "Documento mi camino como artista. Con el pase entras a mi servidor "
        "y ves todos los videos del proceso conforme van saliendo, en tu cel, compu o tele.\n\n"
        f"<b>{PRICE} por {config.PASS_DAYS} días.</b> Sin renovación automática: "
        "antes de que se acabe te aviso para que renueves si quieres.",
        reply_markup=pay_button(),
    )


@router.callback_query(F.data == "pay")
async def pay_cb(c: CallbackQuery):
    db.upsert_user(c.from_user.id, c.from_user.username)
    await c.answer()
    await send_pay_link(c.from_user.id, c.message)


@router.message(Command("pagar"))
async def pagar(m: Message):
    db.upsert_user(m.from_user.id, m.from_user.username)
    await send_pay_link(m.from_user.id, m)


@router.callback_query(F.data == "chk")
async def chk_cb(c: CallbackQuery):
    try:
        st = await check_user_payments(c.from_user.id)
    except Exception:
        log.exception("chk")
        await c.answer("No pude revisar ahorita, intenta en un minuto.", show_alert=True)
        return
    if st == "approved":
        await c.answer("¡Listo! Ya está tu acceso.")
    elif st in ("pending", "in_process"):
        await c.answer("Tu pago sigue pendiente. Te aviso solo en cuanto se acredite.", show_alert=True)
    else:
        await c.answer(
            "Todavía no veo tu pago. Si acabas de pagar, espera un minuto: te aviso solo.",
            show_alert=True,
        )


@router.message(Command("estado"))
async def estado(m: Message):
    row = db.get_user(m.from_user.id)
    if not has_access(row):
        await m.answer("No tienes acceso activo.", reply_markup=pay_button())
        return
    days = max(0, (row["expires_at"] - db.now()) // 86400)
    extra = " (cortesía)" if row["comped"] else ""
    await m.answer(
        f"Acceso{extra} hasta el <b>{fmt_date(row['expires_at'])}</b> · te quedan {days} días.\n"
        "Para sumar días: /pagar"
    )


@router.message(Command("cuenta"))
async def cuenta(m: Message):
    row = db.get_user(m.from_user.id)
    if not row or not row["jf_username"]:
        await m.answer("Todavía no tienes cuenta. Se crea cuando pagas tu pase.", reply_markup=pay_button())
        return
    status = "activa" if row["jf_enabled"] else "pausada (tu pase venció)"
    await m.answer(
        f"<b>SERVIDOR</b> <code>{html.escape(config.JELLYFIN_PUBLIC_URL)}</code>\n"
        f"<b>USUARIO</b>  <code>{html.escape(row['jf_username'])}</code>\n"
        f"Cuenta {status}. ¿Olvidaste la contraseña? /password"
    )


@router.message(Command("password"))
async def password(m: Message):
    row = db.get_user(m.from_user.id)
    if not has_access(row) or not row["jf_user_id"]:
        await m.answer("Necesitas un pase activo.", reply_markup=pay_button())
        return
    try:
        pw = await jf.reset_password(row["jf_user_id"])
    except Exception as e:
        log.exception("reset password")
        await m.answer("No pude cambiar la contraseña ahorita, intenta en un rato.")
        await warn_once(m.from_user.id, f"⚠️ Falló /password de <code>{m.from_user.id}</code>: {html.escape(str(e))}")
        return
    await send_credentials(m.from_user.id, row["jf_username"], pw, "<b>NUEVA CONTRASEÑA.</b>")


@router.message(Command("ayuda", "terms", "paysupport"))
async def ayuda(m: Message):
    await m.answer(
        "<b>CÓMO FUNCIONA</b>\n\n"
        f"• Cada pago de {PRICE} te da {config.PASS_DAYS} días de acceso. No se cobra solo: "
        f"{config.REMIND_DAYS} días antes de que venza te aviso para que renueves si quieres.\n"
        "• Pagas en Mercado Pago con tarjeta, saldo, OXXO o SPEI.\n"
        "• La cuenta es personal: no la compartas. "
        f"Máximo {config.MAX_SESSIONS or 'sin límite de'} reproducciones al mismo tiempo.\n"
        "• El servidor es casero: puede haber caídas o mantenimiento.\n"
        "• Si dejas vencer el pase, tu cuenta se pausa; al volver a pagar se reactiva con tu historial.\n\n"
        "¿Algún problema con un pago o con el servidor? Escríbeme aquí mismo y te contesto."
    )


TV_STEPS = (
    "<b>📺 VER EN TU TELE</b>\n\n"
    "<b>1.</b> En la tienda de apps de tu tele busca e instala <b>Jellyfin</b>.\n"
    "<b>2.</b> Ábrela y escribe esta dirección (solo la primera vez):\n"
    "<code>{url}</code>\n"
    "<b>3.</b> Elige <b>Quick Connect</b>. Te va a salir un código de 6 números.\n"
    "<b>4.</b> Mándame ese código aquí y tu tele entra sola.\n\n"
    "<i>Funciona en Samsung (2021 o más nueva), LG, Roku, Android TV / Google TV, Fire TV y Apple TV. "
    "Si tu tele no tiene la app, conéctale un Chromecast con Google TV, Fire Stick o Roku.</i>"
)


@router.message(Command("tv"))
async def tv(m: Message, command: CommandObject):
    row = db.get_user(m.from_user.id)
    if not has_access(row) or not row["jf_user_id"]:
        await m.answer("Necesitas un pase activo.", reply_markup=pay_button())
        return
    code = (command.args or "").strip()
    if code:
        await _tv_code(m, row, code)
    else:
        await m.answer(TV_STEPS.format(url=html.escape(config.JELLYFIN_PUBLIC_URL)))


@router.message(F.text.regexp(r"^\s*\d{6}\s*$"))
async def tv_code_bare(m: Message):
    """Si un suscriptor manda solo 6 números, es el código de su tele."""
    row = db.get_user(m.from_user.id)
    if has_access(row) and row["jf_user_id"]:
        await _tv_code(m, row, m.text.strip())
    else:
        await inbox(m)


async def _tv_code(m: Message, row, code: str):
    if not code.isdigit() or len(code) != 6:
        await m.answer("El código son 6 números, como <code>123456</code>.")
        return
    try:
        ok = await jf.authorize_tv(row["jf_user_id"], code)
    except Exception as e:
        log.exception("quickconnect")
        await m.answer("No pude conectar tu tele ahorita. Intenta en un rato.")
        await warn_once(m.from_user.id, f"⚠️ Quick Connect falló para <code>{m.from_user.id}</code>: {html.escape(str(e))}")
        return
    if ok:
        await m.answer("✅ Listo, tu tele ya entró. Disfruta el journey.")
    else:
        await m.answer(
            "Ese código no funcionó o ya expiró. En la tele vuelve a elegir <b>Quick Connect</b> "
            "y mándame el código nuevo rápido (dura pocos minutos)."
        )


@router.message(F.text & ~F.text.startswith("/"))
async def inbox(m: Message):
    """Cualquier texto libre de un usuario le llega al admin (soporte)."""
    if is_admin(m.from_user.id):
        return
    await notify_admins(
        f"✉️ {who(m.from_user)} (<code>{m.from_user.id}</code>):\n{html.escape(m.text)}\n\n"
        f"Contesta con /responder {m.from_user.id} &lt;texto&gt;"
    )
    await m.answer("Recibido, te contesto por aquí.")


# ───────────────────────── admin ─────────────────────────

admin = Router()
admin.message.filter(lambda m: is_admin(m.from_user.id))


@admin.message(Command("stats"))
async def stats(m: Message):
    s = db.stats()
    await m.answer(
        "<b>STATS</b>\n"
        f"usuarios: {s['usuarios']}\nactivos: {s['activos']} (cortesía: {s['cortesia']})\n"
        f"pagos últimos 30 días: {s['pagos_30d']} · ${s['mxn_30d']:,.2f} MXN\n"
        f"total cobrado: ${s['mxn_total']:,.2f} MXN\n"
        "<i>Montos brutos, antes de comisión de Mercado Pago.</i>"
    )


@admin.message(Command("lista"))
async def lista(m: Message):
    rows = db.active_users()
    if not rows:
        await m.answer("Nadie activo.")
        return
    lines = []
    for r in rows:
        tag = "🎁" if r["comped"] else "💳"
        name = f"@{r['tg_username']}" if r["tg_username"] else str(r["tg_id"])
        lines.append(f"{tag} {html.escape(name)} · <code>{r['tg_id']}</code> · "
                     f"{html.escape(r['jf_username'] or '—')} · {fmt_date(r['expires_at'])}")
    await m.answer("\n".join(lines[:100]))


@admin.message(Command("dar"))
async def dar(m: Message, command: CommandObject):
    """/dar <tg_id> <días> — acceso de cortesía."""
    try:
        tg_id, days = (int(x) for x in (command.args or "").split())
    except ValueError:
        await m.answer("Uso: /dar &lt;tg_id&gt; &lt;días&gt;")
        return
    db.upsert_user(tg_id, None)
    exp = db.comp_access(tg_id, days)
    ok = await ensure_access(tg_id)
    await safe_send(tg_id, f"🎁 Te regalaron acceso hasta el <b>{fmt_date(exp)}</b>.")
    await m.answer(f"Listo: {tg_id} hasta {fmt_date(exp)}." + ("" if ok else " (Jellyfin falló, reintento automático)"))


@admin.message(Command("quitar"))
async def quitar(m: Message, command: CommandObject):
    """/quitar <tg_id> — corta el acceso ya."""
    try:
        tg_id = int(command.args)
    except (TypeError, ValueError):
        await m.answer("Uso: /quitar &lt;tg_id&gt;")
        return
    row = db.get_user(tg_id)
    if not row:
        await m.answer("No existe.")
        return
    db.expire_now(tg_id)
    await disable_user(row)
    await m.answer(f"Acceso de {tg_id} cortado.")


@admin.message(Command("reembolso"))
async def reembolso(m: Message, command: CommandObject):
    """/reembolso <tg_id> — reembolsa el último pago en Mercado Pago y corta el acceso."""
    try:
        tg_id = int(command.args)
    except (TypeError, ValueError):
        await m.answer("Uso: /reembolso &lt;tg_id&gt;")
        return
    pay = db.last_payment(tg_id)
    if not pay:
        await m.answer("Ese usuario no tiene pagos sin reembolsar.")
        return
    try:
        await mp.refund(pay["payment_id"])
    except Exception as e:
        await m.answer(
            f"Mercado Pago no aceptó el reembolso: {html.escape(str(e))}\n"
            "Hazlo desde la app de Mercado Pago y luego usa /quitar."
        )
        return
    db.mark_refunded(pay["payment_id"])
    db.expire_now(tg_id)
    await disable_user(db.get_user(tg_id))
    await m.answer(f"Reembolsados ${pay['amount']:g} MXN y acceso cortado para {tg_id}.")
    await safe_send(tg_id, "Tu último pago fue reembolsado y el acceso quedó cerrado.")


@admin.message(Command("anuncio"))
async def anuncio(m: Message, command: CommandObject):
    """/anuncio <texto>, o responde a cualquier mensaje (foto, video…) con /anuncio para mandarlo a los activos."""
    src = m.reply_to_message
    if not src and not command.args:
        await m.answer("Escribe /anuncio &lt;texto&gt; o responde a un mensaje con /anuncio.")
        return
    sent = failed = 0
    for r in db.active_users():
        try:
            if src:
                await bot.copy_message(r["tg_id"], m.chat.id, src.message_id)
            else:
                await bot.send_message(r["tg_id"], command.args)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # respeta el límite de ~30 msg/s
    await m.answer(f"Anuncio enviado a {sent}" + (f" · fallaron {failed}" if failed else ""))


@admin.message(Command("responder"))
async def responder(m: Message, command: CommandObject):
    """/responder <tg_id> <texto> — contestar a un suscriptor."""
    try:
        tg_id, text = (command.args or "").split(maxsplit=1)
        await bot.send_message(int(tg_id), f"<b>THE TVH JOURNEY:</b> {html.escape(text)}")
        await m.answer("Enviado.")
    except ValueError:
        await m.answer("Uso: /responder &lt;tg_id&gt; &lt;texto&gt;")


@admin.message(Command("check", "jfcheck"))
async def check(m: Message):
    lines = []
    for name, fn in (("Jellyfin", jf.check), ("Mercado Pago", mp.check)):
        try:
            lines.append(f"✅ {name}: {html.escape(await fn())}")
        except Exception as e:
            lines.append(f"❌ {name}: {html.escape(str(e))}")
    await m.answer("\n".join(lines))


# ───────────────────────── tareas periódicas ─────────────────────────

async def poll_payments_once():
    for ck in db.checkouts_to_poll(CHECKOUT_MAX_AGE):
        async with _pay_lock:  # se suelta entre links para que "YA PAGUÉ" no espere todo el ciclo
            try:
                await process_checkout(ck)
            except Exception as e:
                log.warning("poll %s: %s", ck["ref"], e)
        await asyncio.sleep(0.2)


async def payments_loop():
    while True:
        try:
            await poll_payments_once()
        except Exception:
            log.exception("payments_loop")
        await asyncio.sleep(config.POLL_SECONDS)


async def maintenance_loop():
    while True:
        try:
            await maintenance_once()
        except Exception:
            log.exception("maintenance_loop")
        await asyncio.sleep(600)


async def maintenance_once():
    grace = config.GRACE_HOURS * 3600
    for r in db.due_for_reminder(config.REMIND_DAYS * 86400):
        db.mark_reminded(r["tg_id"], r["expires_at"])
        await safe_send(
            r["tg_id"],
            f"⏳ Tu acceso a THE TVH JOURNEY vence el <b>{fmt_date(r['expires_at'])}</b>.\n"
            f"Si quieres seguir, renueva aquí: se suman {config.PASS_DAYS} días a partir de esa fecha, no pierdes ninguno.",
            reply_markup=pay_button(f"RENOVAR · {PRICE}"),
        )
    for r in db.due_for_disable(grace):
        try:
            await disable_user(r)
            log.info("Deshabilitado %s", r["tg_id"])
            await safe_send(
                r["tg_id"],
                "Tu pase de THE TVH JOURNEY terminó. Tu cuenta y tu historial se quedan guardados; "
                "si vuelves a pagar se reactiva igual.",
                reply_markup=pay_button(f"VOLVER · {PRICE}"),
            )
        except Exception:
            log.exception("Fallo al deshabilitar %s", r["tg_id"])
    for r in db.pending_enable():
        await ensure_access(r["tg_id"])


# ───────────────────────── arranque ─────────────────────────

async def main():
    global BOT_USERNAME
    db.init()
    BOT_USERNAME = (await bot.get_me()).username
    for name, fn in (("Jellyfin", jf.check), ("Mercado Pago", mp.check)):
        try:
            log.info("%s: %s", name, await fn())
        except Exception as e:
            log.error("%s no está listo: %s", name, e)

    dp = Dispatcher()
    dp.include_router(admin)
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Inicio"),
        BotCommand(command="pagar", description=f"Pagar o sumar {config.PASS_DAYS} días"),
        BotCommand(command="estado", description="Hasta cuándo tienes acceso"),
        BotCommand(command="tv", description="Ver en tu tele"),
        BotCommand(command="cuenta", description="Tu usuario y el servidor"),
        BotCommand(command="password", description="Nueva contraseña"),
        BotCommand(command="ayuda", description="Cómo funciona y soporte"),
    ])

    tasks = [asyncio.create_task(payments_loop()), asyncio.create_task(maintenance_loop())]
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        await jf.close()
        await mp.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
