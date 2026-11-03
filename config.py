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

# Precio mensual en Telegram Stars (máx. 10000 para suscripciones)
PRICE_STARS = int(os.getenv("PRICE_STARS", "250"))

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

# Horas de gracia después de la expiración antes de deshabilitar (por si la renovación llega tarde)
GRACE_HOURS = int(os.getenv("GRACE_HOURS", "24"))
# Cada cuántos minutos revisa suscripciones vencidas
CHECK_EVERY_MIN = int(os.getenv("CHECK_EVERY_MIN", "15"))

DB_PATH = os.getenv("DB_PATH", "tvh_journey.db")

# Telegram solo acepta 30 días exactos para suscripciones
SUBSCRIPTION_PERIOD = 2592000

if not 1 <= PRICE_STARS <= 10000:
    raise ValueError("PRICE_STARS debe estar entre 1 y 10000")
