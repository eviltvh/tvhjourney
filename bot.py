"""THE TVH JOURNEY — acceso mensual a Jellyfin pagado con Telegram Stars."""
import asyncio
import html
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

import config
import db
import jellyfin_access as jf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tvh")

bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
router = Router()
PAYLOAD_PREFIX = "tvh_sub:"


# ───────────────────────── utilidades ─────────────────────────

def fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def has_access(row) -> bool:
    return bool(row) and row["expires_at"] > db.now()


def sub_keyboard(label: str = None) -> InlineKeyboardMarkup:
    label = label or f"SUSCRIBIRME · {config.PRICE_STARS} ⭐ / MES"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data="sub")]])


_last_warn: dict[int, int] = {}


async def warn_once(tg_id: int, text: str, every_s: int = 3600):
    """Aviso al admin sin spamear: máximo uno por usuario por hora."""
    if db.now() - _last_warn.get(tg_id, 0) >= every_s:
        _last_warn[tg_id] = db.now()
        await notify_admins(text)


async def notify_admins(text: str):
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, text)
        except Exception as e:  # el admin puede no haber abierto el bot
            log.warning("No pude avisar al admin %s: %s", admin, e)


async def send_credentials(tg_id: int, username: str, password: str, intro: str):
    await bot.send_message(
        tg_id,
        f"{intro}\n\n"
        f"<b>SERVIDOR</b>  <code>{html.escape(config.JELLYFIN_PUBLIC_URL)}</code>\n"
        f"<b>USUARIO</b>   <code>{html.escape(username)}</code>\n"
        f"<b>CONTRASEÑA</b> <code>{html.escape(password)}</code>\n\n"
        "Entra desde el navegador o con la app de Jellyfin (Android, iOS, Android TV, Fire TV, Roku).\n"
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
            await bot.send_message(
                tg_id,
                f"Tu cuenta <code>{html.escape(row['jf_username'])}</code> está activa otra vez. "
                "Mismo usuario y contraseña de antes.",
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
            f"Reintento cada {config.CHECK_EVERY_MIN} min.",
        )
        return False


# ───────────────────────── comandos públicos ─────────────────────────

@router.message(CommandStart())
async def start(m: Message):
    db.upsert_user(m.from_user.id, m.from_user.username)
    row = db.get_user(m.from_user.id)
    if has_access(row):
        await m.answer(
            "<b>THE TVH JOURNEY</b>\n\n"
            f"Tienes acceso hasta el <b>{fmt_date(row['expires_at'])}</b>.\n"
            "/estado · /cuenta · /password · /cancelar"
        )
        return
    await m.answer(
        "<b>THE TVH JOURNEY</b>\n\n"
        "Documento mi camino como artista. Con la suscripción entras a mi servidor "
        "y ves todos los videos del proceso conforme van saliendo.\n\n"
        f"<b>{config.PRICE_STARS} ⭐ al mes.</b> Se renueva solo cada 30 días; "
        "puedes cancelar cuando quieras desde /cancelar.",
        reply_markup=sub_keyboard(),
    )


@router.callback_query(F.data == "sub")
async def subscribe(c: CallbackQuery):
    db.upsert_user(c.from_user.id, c.from_user.username)
    row = db.get_user(c.from_user.id)
    if has_access(row) and not row["canceled"] and not row["comped"]:
        await c.answer("Ya tienes una suscripción activa.", show_alert=True)
        return
    link = await bot.create_invoice_link(
        title="THE TVH JOURNEY",
        description="Acceso mensual a todos los videos del proceso. Se renueva cada 30 días.",
        payload=f"{PAYLOAD_PREFIX}{c.from_user.id}",
        currency="XTR",
        prices=[LabeledPrice(label="Acceso mensual", amount=config.PRICE_STARS)],
        subscription_period=config.SUBSCRIPTION_PERIOD,
    )
    await c.message.answer(
        "Toca para pagar con Telegram Stars:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"PAGAR {config.PRICE_STARS} ⭐", url=link)]]
        ),
    )
    await c.answer()


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    ok = q.invoice_payload == f"{PAYLOAD_PREFIX}{q.from_user.id}" and q.currency == "XTR"
    if ok:
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message="Este enlace de pago no es tuyo. Pide uno nuevo con /start.")


@router.message(F.successful_payment)
async def paid(m: Message):
    sp = m.successful_payment
    tg_id = m.from_user.id
    db.upsert_user(tg_id, m.from_user.username)
    expires = sp.subscription_expiration_date or (db.now() + config.SUBSCRIPTION_PERIOD)
    if not db.record_payment(tg_id, sp.telegram_payment_charge_id, sp.total_amount, sp.is_recurring or False, expires):
        return  # pago duplicado

    renewal = sp.is_recurring and not sp.is_first_recurring
    row = db.get_user(tg_id)
    if renewal and row["jf_enabled"]:
        await m.answer(f"Renovado. Acceso hasta el <b>{fmt_date(expires)}</b>. Gracias por seguir aquí.")
    else:
        await ensure_access(tg_id)
        await m.answer(f"Acceso activo hasta el <b>{fmt_date(expires)}</b>.")

    who = f"@{m.from_user.username}" if m.from_user.username else html.escape(m.from_user.full_name)
    await notify_admins(
        f"⭐ {'Renovación' if renewal else 'Nueva suscripción'}: {who} (<code>{tg_id}</code>) "
        f"· {sp.total_amount} ⭐ · hasta {fmt_date(expires)}"
    )


@router.message(Command("estado"))
async def estado(m: Message):
    row = db.get_user(m.from_user.id)
    if not has_access(row):
        await m.answer("No tienes acceso activo.", reply_markup=sub_keyboard())
        return
    if row["comped"]:
        extra = "Acceso de cortesía."
    elif row["canceled"]:
        extra = "Renovación cancelada: el acceso termina en esa fecha. /reactivar para seguir."
    else:
        extra = "Se renueva automáticamente."
    await m.answer(f"Acceso hasta el <b>{fmt_date(row['expires_at'])}</b>.\n{extra}")


@router.message(Command("cuenta"))
async def cuenta(m: Message):
    row = db.get_user(m.from_user.id)
    if not row or not row["jf_username"]:
        await m.answer("Todavía no tienes cuenta. Se crea cuando te suscribes.", reply_markup=sub_keyboard())
        return
    status = "activa" if row["jf_enabled"] else "deshabilitada (suscripción vencida)"
    await m.answer(
        f"<b>SERVIDOR</b> <code>{html.escape(config.JELLYFIN_PUBLIC_URL)}</code>\n"
        f"<b>USUARIO</b>  <code>{html.escape(row['jf_username'])}</code>\n"
        f"Cuenta {status}. ¿Olvidaste la contraseña? /password"
    )


@router.message(Command("password"))
async def password(m: Message):
    row = db.get_user(m.from_user.id)
    if not has_access(row) or not row["jf_user_id"]:
        await m.answer("Necesitas una suscripción activa.", reply_markup=sub_keyboard())
        return
    try:
        pw = await jf.reset_password(row["jf_user_id"])
    except Exception as e:
        log.exception("reset password")
        await m.answer("No pude cambiar la contraseña ahorita, intenta en un rato.")
        await notify_admins(f"⚠️ Falló /password de <code>{m.from_user.id}</code>: {html.escape(str(e))}")
        return
    await send_credentials(m.from_user.id, row["jf_username"], pw, "<b>NUEVA CONTRASEÑA.</b>")


async def _set_renewal(m: Message, cancel: bool):
    row = db.get_user(m.from_user.id)
    if not has_access(row) or not row["last_charge"] or row["comped"]:
        await m.answer("No tienes una suscripción con renovación automática.")
        return
    await bot.edit_user_star_subscription(
        user_id=m.from_user.id,
        telegram_payment_charge_id=row["last_charge"],
        is_canceled=cancel,
    )
    db.set_canceled(m.from_user.id, cancel)
    if cancel:
        await m.answer(
            f"Renovación cancelada. Sigues teniendo acceso hasta el <b>{fmt_date(row['expires_at'])}</b>.\n"
            "Si cambias de opinión: /reactivar"
        )
    else:
        await m.answer("Listo, tu suscripción se vuelve a renovar automáticamente.")


@router.message(Command("cancelar"))
async def cancelar(m: Message):
    await _set_renewal(m, cancel=True)


@router.message(Command("reactivar"))
async def reactivar(m: Message):
    await _set_renewal(m, cancel=False)


@router.message(Command("terms"))
async def terms(m: Message):
    await m.answer(
        "<b>TÉRMINOS</b>\n\n"
        f"• La suscripción cuesta {config.PRICE_STARS} ⭐ cada 30 días y se renueva sola hasta que la canceles con /cancelar.\n"
        "• Da acceso personal a los videos de THE TVH JOURNEY en mi servidor Jellyfin. "
        "La cuenta es para ti: no la compartas.\n"
        f"• Máximo {config.MAX_SESSIONS or 'sin límite de'} reproducciones al mismo tiempo.\n"
        "• El servidor es casero: puede haber caídas o mantenimiento.\n"
        "• Al vencer la suscripción la cuenta se deshabilita; si regresas, se reactiva con tu historial.\n"
        "• Problemas con un pago: /paysupport"
    )


@router.message(Command("paysupport"))
async def paysupport(m: Message):
    await m.answer(
        "Si tuviste un problema con un pago, escríbeme aquí mismo qué pasó (fecha y monto) "
        "y lo reviso. Los reembolsos se hacen en Stars a tu cuenta de Telegram."
    )
    await notify_admins(f"🆘 /paysupport de <code>{m.from_user.id}</code> (@{m.from_user.username})")


@router.message(F.text & ~F.text.startswith("/"))
async def inbox(m: Message):
    """Cualquier texto libre de un usuario le llega al admin (soporte)."""
    if is_admin(m.from_user.id):
        return
    who = f"@{m.from_user.username}" if m.from_user.username else html.escape(m.from_user.full_name)
    await notify_admins(
        f"✉️ {who} (<code>{m.from_user.id}</code>):\n{html.escape(m.text)}\n\n"
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
        f"usuarios: {s['usuarios']}\nactivos: {s['activos']}\n"
        f"renovando: {s['renovando']} · cortesía: {s['cortesia']}\n"
        f"⭐ últimos 30 días: {s['estrellas_30d']}\n⭐ total: {s['estrellas_total']}"
    )


@admin.message(Command("lista"))
async def lista(m: Message):
    rows = db.active_users()
    if not rows:
        await m.answer("Nadie activo.")
        return
    lines = []
    for r in rows:
        tag = "🎁" if r["comped"] else ("⏹" if r["canceled"] else "🔁")
        who = f"@{r['tg_username']}" if r["tg_username"] else str(r["tg_id"])
        lines.append(f"{tag} {html.escape(who)} · {html.escape(r['jf_username'] or '—')} · {fmt_date(r['expires_at'])}")
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
    try:
        await bot.send_message(tg_id, f"🎁 Te regalaron acceso hasta el <b>{fmt_date(exp)}</b>.")
    except Exception:
        pass
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
    if row["jf_user_id"]:
        await jf.disable(row["jf_user_id"])
    db.set_enabled(tg_id, False)
    await m.answer(f"Acceso de {tg_id} cortado.")


@admin.message(Command("reembolso"))
async def reembolso(m: Message, command: CommandObject):
    """/reembolso <tg_id> — reembolsa el último pago y corta el acceso."""
    try:
        tg_id = int(command.args)
    except (TypeError, ValueError):
        await m.answer("Uso: /reembolso &lt;tg_id&gt;")
        return
    row = db.get_user(tg_id)
    if not row or not row["last_charge"]:
        await m.answer("Ese usuario no tiene pagos.")
        return
    await bot.refund_star_payment(user_id=tg_id, telegram_payment_charge_id=row["last_charge"])
    db.mark_refunded(row["last_charge"])
    db.expire_now(tg_id)
    if row["jf_user_id"]:
        await jf.disable(row["jf_user_id"])
    db.set_enabled(tg_id, False)
    await m.answer(f"Reembolsado y acceso cortado para {tg_id}.")
    try:
        await bot.send_message(tg_id, "Tu último pago fue reembolsado y el acceso quedó cerrado.")
    except Exception:
        pass


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


@admin.message(Command("jfcheck"))
async def jfcheck(m: Message):
    try:
        await m.answer("✅ " + html.escape(await jf.check()))
    except Exception as e:
        await m.answer("❌ " + html.escape(str(e)))


# ───────────────────────── tarea periódica ─────────────────────────

async def maintenance_loop():
    grace = config.GRACE_HOURS * 3600
    while True:
        try:
            for r in db.due_for_disable(grace):
                try:
                    if r["jf_user_id"]:
                        await jf.disable(r["jf_user_id"])
                    db.set_enabled(r["tg_id"], False)
                    log.info("Deshabilitado %s", r["tg_id"])
                    await bot.send_message(
                        r["tg_id"],
                        "Tu acceso a THE TVH JOURNEY terminó. Tu cuenta y tu historial se quedan guardados; "
                        "si te vuelves a suscribir se reactiva igual.",
                        reply_markup=sub_keyboard("VOLVER · " + f"{config.PRICE_STARS} ⭐ / MES"),
                    )
                except Exception:
                    log.exception("Fallo al deshabilitar %s", r["tg_id"])
            for r in db.pending_enable():
                await ensure_access(r["tg_id"])
        except Exception:
            log.exception("maintenance_loop")
        await asyncio.sleep(config.CHECK_EVERY_MIN * 60)


# ───────────────────────── arranque ─────────────────────────

async def main():
    db.init()
    try:
        log.info("Jellyfin: %s", await jf.check())
    except Exception as e:
        log.error("Jellyfin no está listo: %s", e)

    dp = Dispatcher()
    dp.include_router(admin)
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Inicio y suscripción"),
        BotCommand(command="estado", description="Hasta cuándo tienes acceso"),
        BotCommand(command="cuenta", description="Tu usuario y el servidor"),
        BotCommand(command="password", description="Nueva contraseña"),
        BotCommand(command="cancelar", description="Cancelar la renovación"),
        BotCommand(command="reactivar", description="Reactivar la renovación"),
        BotCommand(command="terms", description="Términos"),
        BotCommand(command="paysupport", description="Ayuda con pagos"),
    ])

    loop_task = asyncio.create_task(maintenance_loop())
    try:
        await dp.start_polling(bot)
    finally:
        loop_task.cancel()
        await jf.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
