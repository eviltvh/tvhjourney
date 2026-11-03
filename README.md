# THE TVH JOURNEY

Bot de Telegram que vende acceso mensual a tu servidor Jellyfin con **Telegram Stars**.

- El usuario paga con Stars, y la suscripción se renueva sola cada 30 días (así lo maneja Telegram).
- En el primer pago, el bot crea una cuenta en Jellyfin que **solo ve tus bibliotecas** y le manda usuario y contraseña por DM.
- Si no renueva, la cuenta se deshabilita tras un periodo de gracia. No se borra: si regresa, conserva su historial.
- Cada cuenta tiene un límite de reproducciones simultáneas para que no la compartan.

## 1. Instalar Jellyfin en la laptop (Debian)

```bash
curl -s https://repo.jellyfin.org/install-debuntu.sh -O && \
curl -s https://repo.jellyfin.org/install-debuntu.sh.sha256sum -O && \
sha256sum -c install-debuntu.sh.sha256sum
sudo bash install-debuntu.sh
```

Después:

1. Abre `http://<ip-de-la-laptop>:8096`, crea tu usuario admin y agrega una biblioteca llamada **The TVH Journey** (tipo *Películas* o *Videos caseros*) que apunte a tu carpeta de videos.
   - Jellyfin corre como el usuario `jellyfin`: `sudo setfacl -R -m u:jellyfin:rx /ruta/a/tus/videos`
2. Ve a **Panel → Claves API → +** y crea una con el nombre `tvh-journey`. Esa va en `JELLYFIN_API_KEY`.
3. Si ya no vas a usar Plex: `sudo apt remove plexmediaserver`

## 2. Que se pueda entrar desde fuera de tu casa

Jellyfin no tiene relay como Plex, así que tú debes exponer el servidor. Opciones:

- **Port forwarding + HTTPS (recomendado):** reserva una IP fija para la laptop en el router y redirige el 443 a la laptop. Pon Caddy como proxy con un dominio (o un DDNS gratis como DuckDNS):
  ```
  tvh.tudominio.com {
      reverse_proxy 127.0.0.1:8096
  }
  ```
  Caddy saca el certificado solo. Ojo: si tu ISP usa **CGNAT** (pasa con algunos ISPs en México), el port forwarding no funciona y tendrías que pedir IP pública o usar un VPS como túnel.
- **Cloudflare Tunnel:** evita abrir puertos, pero antes revisa sus términos sobre streaming de video.

La URL final va en `JELLYFIN_PUBLIC_URL`.

## 3. Crear el bot

1. Con @BotFather: `/newbot`. Copia el token.
2. Saca tu ID de Telegram con @userinfobot. Va en `ADMIN_IDS`.
3. Para cobrar en Stars no necesitas proveedor de pagos.

## 4. Instalar y correr

```bash
cd ~/tvh-journey
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
.venv/bin/python bot.py
```

En Telegram, mándale `/jfcheck` al bot. Debe responder ✅ con el nombre de tu servidor y la biblioteca.

Para dejarlo corriendo siempre:

```bash
# el .service ya viene con el usuario tvhin y la ruta /home/tvhin/tvh-journey
sudo cp tvh-journey.service /etc/systemd/system/
sudo systemctl enable --now tvh-journey
journalctl -u tvh-journey -f   # ver logs
```

## Comandos

| Usuarios | |
|---|---|
| `/start` | Info y botón para suscribirse |
| `/estado` | Hasta cuándo tiene acceso |
| `/cuenta` | Usuario y URL del servidor |
| `/password` | Genera una contraseña nueva |
| `/cancelar` · `/reactivar` | Apaga o prende la renovación automática |
| `/terms` · `/paysupport` | Obligatorios para cobrar en Stars |
| cualquier texto | Te llega a ti como mensaje de soporte |

| Admin | |
|---|---|
| `/stats` | Usuarios activos y Stars cobradas |
| `/lista` | Activos (🔁 renueva · ⏹ canceló · 🎁 cortesía) |
| `/anuncio <texto>` | Mensaje a todos los activos. **Responde a una foto o video con `/anuncio`** para reenviarlo: así publicas avances del journey |
| `/responder <tg_id> <texto>` | Contestar soporte |
| `/dar <tg_id> <días>` | Acceso de cortesía |
| `/quitar <tg_id>` | Cortar acceso ya |
| `/reembolso <tg_id>` | Reembolsa el último pago en Stars y corta el acceso |
| `/jfcheck` | Prueba la conexión con Jellyfin |

## Cómo funciona por dentro

- `bot.py`: handlers de Telegram y una tarea que cada `CHECK_EVERY_MIN` deshabilita cuentas vencidas y reintenta las que fallaron (por ejemplo, si Jellyfin estaba apagado al pagar).
- `jellyfin_access.py`: crear, habilitar y deshabilitar usuarios, y resetear contraseñas vía la API de Jellyfin.
- `db.py`: SQLite con usuarios y pagos. Los pagos son idempotentes por `charge_id`.
- Las cuentas se crean ocultas (no salen en la pantalla de login), sin admin, sin borrar contenido y sin Live TV. Descargas apagadas salvo `ALLOW_DOWNLOADS=true`.

## Notas

- Telegram solo permite suscripciones de 30 días y como máximo 10,000 ⭐.
- Los Stars cobrados se retiran vía Fragment desde el balance del bot; Telegram los retiene unos días antes de poder retirarlos.
- Si la laptop se duerme, nadie puede ver nada. Desactiva la suspensión al cerrar la tapa (`HandleLidSwitch=ignore` en `/etc/systemd/logind.conf`).
