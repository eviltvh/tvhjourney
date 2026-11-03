# THE TVH JOURNEY

Bot de Telegram que vende pases de acceso a tu servidor Jellyfin, cobrados con **Mercado Pago**.

- El usuario toca **PAGAR** y abre un link de Mercado Pago: tarjeta, saldo, OXXO o SPEI. El dinero te llega a tu cuenta de Mercado Pago en pesos.
- Cada pago da 30 días. No se cobra solo: 3 días antes de que venza, el bot le manda un recordatorio con el botón para renovar. Si paga antes, los días se suman y no pierde ninguno.
- En el primer pago, el bot crea una cuenta en Jellyfin que **solo ve tu biblioteca** y le manda usuario y contraseña por DM.
- En la tele entra con `/tv`: la tele muestra un código, lo manda al bot y queda adentro sin escribir contraseña.
- Si no renueva, la cuenta se pausa. No se borra: si regresa, conserva su historial.
- El bot revisa los pagos preguntándole a Mercado Pago, sin webhooks, así que no necesita puertos abiertos.

## 1. Jellyfin

1. Abre `http://<ip-de-la-laptop>:8096`, crea tu usuario admin y agrega una biblioteca llamada **The TVH Journey** (tipo *Videos caseros y fotos*) que apunte a tu carpeta de videos.
2. En **Panel → General** activa **Quick Connect** para que las teles puedan entrar con código.
3. En **Panel → Claves API → +** crea una clave llamada `tvh-journey`. Va en `JELLYFIN_API_KEY`.

## 2. Mercado Pago

1. Entra a **mercadopago.com.mx/developers** con tu cuenta y ve a **Tus integraciones → Crear aplicación**. Elige pagos online / Checkout Pro.
2. En la app, entra a **Credenciales de producción** y copia el **Access Token** (empieza con `APP_USR-`). Va en `MP_ACCESS_TOKEN`. Es como una contraseña: no lo compartas ni lo subas a GitHub.
3. Para probar sin dinero real, usa las **credenciales de prueba** y un usuario de prueba comprador. Cuando funcione, cambia al token de producción.

## 3. Bot de Telegram

1. Con @BotFather: `/newbot`. Copia el token y ponlo en `BOT_TOKEN`.
2. Saca tu ID con @userinfobot y ponlo en `ADMIN_IDS`.

## 4. Instalar y correr

```bash
cd ~/tvh-journey
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env && nano .env
.venv/bin/python bot.py
```

En Telegram, mándale `/check` al bot. Debe responder ✅ para Jellyfin y ✅ para Mercado Pago.

Para dejarlo corriendo siempre:

```bash
# el .service ya viene con el usuario tvhin y la ruta /home/tvhin/tvh-journey
sudo cp tvh-journey.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tvh-journey
journalctl -u tvh-journey -f   # ver logs
```

Para actualizar: `git pull && sudo systemctl restart tvh-journey`.

## 5. Acceso desde fuera de tu casa

Jellyfin no tiene relay como Plex, así que tienes que exponerlo tú. La opción recomendada es port forwarding del 443 hacia la laptop, con Caddy como proxy y un dominio (o DuckDNS gratis):

```
tvh.tudominio.com {
    reverse_proxy 127.0.0.1:8096
}
```

Caddy saca el certificado HTTPS solo. Si tu ISP usa **CGNAT**, el port forwarding no funciona: hay que pedir IP pública o usar un túnel. La URL final va en `JELLYFIN_PUBLIC_URL`.

## Comandos

| Usuarios | |
|---|---|
| `/start` | Info y botón para pagar |
| `/pagar` | Link de pago (si ya tiene acceso, suma días) |
| `/estado` | Hasta cuándo tiene acceso |
| `/tv` | Pasos para la tele + login con código. También basta con mandar los 6 números |
| `/cuenta` | Usuario y URL del servidor |
| `/password` | Genera una contraseña nueva |
| `/ayuda` | Cómo funciona |
| cualquier texto | Te llega a ti como mensaje de soporte |

| Admin | |
|---|---|
| `/check` | Prueba Jellyfin y Mercado Pago |
| `/stats` | Activos y pesos cobrados (brutos) |
| `/lista` | Activos (💳 pagó · 🎁 cortesía) con su ID |
| `/anuncio <texto>` | Mensaje a todos los activos. **Responde a una foto o video con `/anuncio`** para reenviarlo: así publicas avances del journey |
| `/responder <tg_id> <texto>` | Contestar soporte |
| `/dar <tg_id> <días>` | Acceso de cortesía |
| `/quitar <tg_id>` | Cortar acceso ya |
| `/reembolso <tg_id>` | Reembolsa el último pago en Mercado Pago y corta el acceso |

## Cómo funciona por dentro

- `mp.py`: crea links de Checkout Pro con `external_reference = tvh-<tg_id>-<random>` y busca los pagos de cada link.
- `bot.py`: revisa los links recientes cada `POLL_SECONDS` (los de más de una hora, cada 10 min). Si un pago queda aprobado, da 30 días. Si queda pendiente o rechazado, le avisa al usuario. Si hay contracargo o reembolso, corta el acceso. Cada 10 min manda recordatorios y pausa cuentas vencidas.
- `jellyfin_access.py`: crea, habilita y pausa usuarios, resetea contraseñas y aprueba códigos Quick Connect.
- `db.py`: SQLite. Cada pago se acredita una sola vez por su id de Mercado Pago.
- El botón **YA PAGUÉ** y el regreso desde Mercado Pago (`t.me/<bot>?start=pagado`) revisan el pago al instante.

## Notas

- Mercado Pago cobra comisión por cada pago y tarda unos días en liberar el dinero, según tu configuración. Revisa las condiciones vigentes en tu cuenta.
- Si Mercado Pago no acepta un reembolso desde la API, el bot te avisa. En ese caso hazlo desde la app de Mercado Pago y luego usa `/quitar`.
- Si la laptop se duerme, nadie puede ver nada. Desactiva la suspensión al cerrar la tapa (`HandleLidSwitch=ignore` en `/etc/systemd/logind.conf`).
