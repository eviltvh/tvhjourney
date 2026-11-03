"""Configuración de The TVH Journey. Todo sale del archivo .env."""
import os
from dotenv import load_dotenv

load_dotenv()


def _ints(value: str) -> list[int]:
    return [int(x) for x in value.replace(" ", "").split(",") if x]


def _strs(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "si", "sí", "yes")


BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = _ints(os.getenv("ADMIN_IDS", ""))

# Mercado Pago
# Tus integraciones → tu app → Credenciales de producción → Access Token (APP_USR-...)
MP_ACCESS_TOKEN = os.environ["MP_ACCESS_TOKEN"]
MP_API_URL = os.getenv("MP_API_URL", "https://api.mercadopago.com").rstrip("/")
# Precio del pase en pesos y cuántos días da
PRICE_MXN = float(os.getenv("PRICE_MXN", "79"))
PASS_DAYS = int(os.getenv("PASS_DAYS", "30"))
# Texto que aparece en el estado de cuenta de la tarjeta (máx. ~13 caracteres)
STATEMENT_DESCRIPTOR = os.getenv("STATEMENT_DESCRIPTOR", "TVHJOURNEY")
# Días que dura la ficha de OXXO / el link de pago
CHECKOUT_DAYS = int(os.getenv("CHECKOUT_DAYS", "3"))

# Jellyfin
# URL que usa el bot para hablar con Jellyfin (normalmente local)
JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://127.0.0.1:8096").rstrip("/")
JELLYFIN_API_KEY = os.environ["JELLYFIN_API_KEY"]
# URL pública que se manda a los suscriptores para entrar desde fuera
JELLYFIN_PUBLIC_URL = os.getenv("JELLYFIN_PUBLIC_URL", JELLYFIN_URL).rstrip("/")
# Bibliotecas que ven los suscriptores (nombres exactos en Jellyfin)
JELLYFIN_LIBRARIES = _strs(os.getenv("JELLYFIN_LIBRARIES", "The TVH Journey"))
# Reproducciones simultáneas por cuenta (evita que compartan la cuenta). 0 = sin límite
MAX_SESSIONS = int(os.getenv("MAX_SESSIONS", "2"))
# ¿Permitir descargar los videos?
ALLOW_DOWNLOADS = _bool(os.getenv("ALLOW_DOWNLOADS", "false"))

# Días antes del vencimiento en que se manda el recordatorio para renovar
REMIND_DAYS = int(os.getenv("REMIND_DAYS", "3"))
# Horas de gracia después de vencer antes de deshabilitar la cuenta
GRACE_HOURS = int(os.getenv("GRACE_HOURS", "24"))
# Cada cuántos segundos revisa pagos recientes en Mercado Pago
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))

DB_PATH = os.getenv("DB_PATH", "tvh_journey.db")

if PRICE_MXN < 10:
    raise ValueError("PRICE_MXN debe ser al menos 10 (mínimo de OXXO)")
