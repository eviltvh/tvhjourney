"""Todo lo que toca Jellyfin vive aquí.

Cada suscriptor recibe una cuenta propia en tu servidor. Al vencer la suscripción
la cuenta se deshabilita (no se borra), así que si vuelve a pagar conserva su
historial y su progreso.
"""
import logging
import re
import secrets
import string

import aiohttp

import config

log = logging.getLogger(__name__)

_HEADERS = {
    # Formato recomendado; X-Emby-Token y api_key están deprecados
    "Authorization": f'MediaBrowser Token="{config.JELLYFIN_API_KEY}"',
    "Content-Type": "application/json",
}

_session: aiohttp.ClientSession | None = None
_folder_ids: list[str] | None = None


class JellyfinError(RuntimeError):
    pass


async def _http() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            base_url=config.JELLYFIN_URL + "/",
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=20),
        )
    return _session


async def close():
    if _session and not _session.closed:
        await _session.close()


async def _req(method: str, path: str, json=None, ok=(200, 204)):
    s = await _http()
    async with s.request(method, path.lstrip("/"), json=json) as r:
        if r.status not in ok:
            body = (await r.text())[:300]
            raise JellyfinError(f"{method} {path} → {r.status}: {body}")
        if r.status == 204 or r.content_length == 0:
            return None
        if "json" in r.headers.get("Content-Type", ""):
            return await r.json()
        return await r.text()


async def _library_ids() -> list[str]:
    global _folder_ids
    if _folder_ids is None:
        folders = await _req("GET", "/Library/VirtualFolders")
        by_name = {f["Name"]: f["ItemId"] for f in folders}
        missing = [n for n in config.JELLYFIN_LIBRARIES if n not in by_name]
        if missing:
            raise JellyfinError(
                f"No existen en Jellyfin: {', '.join(missing)}. Disponibles: {', '.join(by_name)}"
            )
        _folder_ids = [by_name[n] for n in config.JELLYFIN_LIBRARIES]
    return _folder_ids


async def check() -> str:
    """Valida conexión, API key y bibliotecas."""
    info = await _req("GET", "/System/Info")
    await _library_ids()
    qc = "activo" if await quickconnect_enabled() else "APAGADO (actívalo en el panel)"
    return (
        f"{info.get('ServerName')} · Jellyfin {info.get('Version')} · "
        f"bibliotecas: {', '.join(config.JELLYFIN_LIBRARIES)} · Quick Connect {qc}"
    )


async def quickconnect_enabled() -> bool:
    try:
        return bool(await _req("GET", "/QuickConnect/Enabled"))
    except JellyfinError:
        return False


async def authorize_tv(user_id: str, code: str) -> bool:
    """Aprueba el código Quick Connect que muestra la tele, a nombre de este usuario."""
    s = await _http()
    async with s.post("QuickConnect/Authorize", params={"code": code, "userId": user_id}) as r:
        if r.status in (200, 204):
            return True
        if r.status in (400, 404):  # código inválido o expirado
            return False
        raise JellyfinError(f"QuickConnect → {r.status}: {(await r.text())[:200]}")


def new_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _clean_username(base: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_.-]", "", base or "").lower()
    return base[:24] or "viajero"


async def _apply_policy(user_id: str, disabled: bool):
    """Deja la cuenta viendo solo tus bibliotecas, sin permisos de admin."""
    user = await _req("GET", f"/Users/{user_id}")
    policy = user["Policy"]
    policy.update(
        IsAdministrator=False,
        IsHidden=True,  # no aparece en la pantalla de login
        IsDisabled=disabled,
        EnableAllFolders=False,
        EnabledFolders=await _library_ids(),
        EnableRemoteAccess=True,
        EnableMediaPlayback=True,
        EnableContentDownloading=config.ALLOW_DOWNLOADS,
        EnableContentDeletion=False,
        EnableContentDeletionFromFolders=[],
        EnableLiveTvAccess=False,
        EnableLiveTvManagement=False,
        EnableCollectionManagement=False,
        EnableSubtitleManagement=False,
        EnableRemoteControlOfOtherUsers=False,
        EnableSharedDeviceControl=False,
        EnablePublicSharing=False,
        MaxActiveSessions=config.MAX_SESSIONS,
    )
    await _req("POST", f"/Users/{user_id}/Policy", json=policy)


async def create_user(tg_id: int, tg_username: str | None) -> tuple[str, str, str]:
    """Crea la cuenta. Devuelve (user_id, username, password)."""
    base = _clean_username(tg_username or f"tvh{tg_id}")
    password = new_password()
    for name in (base, f"{base}_{tg_id % 100000}", f"tvh{tg_id}"):
        try:
            user = await _req("POST", "/Users/New", json={"Name": name, "Password": password})
        except JellyfinError as e:
            if " 400" in str(e):  # nombre ocupado → probar el siguiente
                continue
            raise
        await _apply_policy(user["Id"], disabled=False)
        log.info("Jellyfin: creado %s para tg %s", name, tg_id)
        return user["Id"], name, password
    raise JellyfinError(f"No pude crear un nombre de usuario libre para tg {tg_id}")


async def enable(user_id: str):
    await _apply_policy(user_id, disabled=False)


async def disable(user_id: str):
    """Deshabilitar corta también las sesiones abiertas: Jellyfin rechaza sus tokens."""
    await _apply_policy(user_id, disabled=True)


async def reset_password(user_id: str) -> str:
    password = new_password()
    await _req("POST", f"/Users/{user_id}/Password", json={"NewPw": password})
    return password


async def exists(user_id: str) -> bool:
    try:
        await _req("GET", f"/Users/{user_id}")
        return True
    except JellyfinError as e:
        if " 404" in str(e):
            return False
        raise
