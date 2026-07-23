"""
CronoSolar — Bot de Telegram (Fase 1.5)

Mecánica de alertas (revisada jul 2026):
- Mensaje automático UNA vez al día, por la TARDE (no por la mañana),
  con el plan de MAÑANA — así hay tiempo de sobra para programar el
  inversor con calma la noche antes. ESIOS publica los precios D+1
  sobre las 14h, así que el envío se hace después de esa hora.
- Sin alertas horarias por defecto: si ya programaste tu inversor con
  las franjas del día, el propio inversor ejecuta el plan — un aviso
  cada vez que cambia la acción sería ruido redundante. Queda como
  opción manual (/avisos on) solo para quien no pueda programar su
  inversor y necesite que se lo recuerden en tiempo real.
- /plan a mano, a media tarde/noche del día en curso, muestra el resto
  del día (no los tramos ya pasados) y, si ya ha pasado la hora en que
  ESIOS publica el día siguiente, sugiere directamente /plan manana.

Flujo conversacional: /start guia al usuario paso a paso (ubicacion —
compartida o escrita — kWp, bateria, consumo, inversor) sin exigir
sintaxis de comandos exacta. El bot delega todo el calculo (precios
ESIOS + solar Open-Meteo + optimizacion) a la API interna de la web,
para que los numeros que ve el usuario en Telegram y en el informe web
coincidan siempre.

Uso:
  1. Crea un bot en Telegram hablando con @BotFather -> te da un TOKEN
  2. Anade TELEGRAM_BOT_TOKEN al .env
  3. Ejecuta: python telegram_bot.py
"""

import asyncio
import json
import os
import re
import logging
from datetime import date, datetime, timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DATA_FILE = os.getenv("DATA_FILE", "users.json")  # Persistencia simple; en el servidor apunta a un volumen
API_INTERNAL_URL = os.getenv("API_INTERNAL_URL", "http://localhost:8000")  # backend, red interna de Docker
WEB_PUBLIC_URL = os.getenv("WEB_PUBLIC_URL", "http://localhost:8000")  # para los enlaces que ve el usuario

ES_GRID_CO2_KG_PER_KWH = 0.19  # factor medio orientativo de la red española
DAILY_SEND_HOUR = 16  # ESIOS publica D+1 sobre las 14h; enviamos con margen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("cronosolar")

# ── Persistencia de usuarios ──

def load_users():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(DATA_FILE, "w") as f:
        json.dump(users, f, indent=2)


# ── Geocoding (Nominatim) — para ubicacion escrita a mano ──

async def geocode_place(query: str):
    """Devuelve (lat, lon, nombre) o None si no se encuentra."""
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "CronoSolar/1.0"}) as client:
        resp = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{query}, Spain", "format": "json", "limit": 1, "accept-language": "es"},
        )
    if resp.status_code != 200:
        return None
    data = resp.json()
    if not data:
        return None
    d = data[0]
    name = ", ".join(d["display_name"].split(",")[:2])
    return float(d["lat"]), float(d["lon"]), name


async def reverse_geocode(lat: float, lon: float):
    """Devuelve un nombre legible para unas coordenadas, o 'lat, lon' si falla."""
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "CronoSolar/1.0"}) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "json", "accept-language": "es"},
            )
        if resp.status_code == 200:
            d = resp.json()
            addr = d.get("address", {})
            name = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality")
            if name:
                return name
    except Exception:
        pass
    return f"{lat:.3f}, {lon:.3f}"


# ── API interna: un unico calculo, compartido con la web ──

async def fetch_plan(u: dict, target_date: date):
    """Llama a la API interna y devuelve el OptimizationResult completo (dict)."""
    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.post(
            f"{API_INTERNAL_URL}/api/optimize",
            params={"target_date": str(target_date)},
            json={
                "latitude": u["lat"],
                "longitude": u["lon"],
                "kwp": u["kwp"],
                "battery_kwh": u["battery"],
                "daily_consumption_kwh": u["consumption"],
            },
        )
    resp.raise_for_status()
    return resp.json()


def report_link(u: dict, target_date: date) -> str:
    return (
        f"{WEB_PUBLIC_URL}/optimizar?lat={u['lat']}&lon={u['lon']}&kwp={u['kwp']}"
        f"&battery={u['battery']}&consumption={u['consumption']}&date={target_date}"
    )


# ── Mensajes ──

ACTION_EMOJI = {
    "solar": "☀️",
    "battery_charge": "🔋⬆️",
    "battery_discharge": "🔋⬇️",
    "export": "📤",
    "grid": "🔌",
}

ACTION_MSG = {
    "solar": "Autoconsumo directo — el sol cubre tu consumo",
    "battery_charge": "Carga la batería — hay excedente solar",
    "battery_discharge": "Descarga la batería — precio alto ({price:.0f} €/MWh)",
    "export": "Vertiendo excedentes a red — batería llena",
    "grid": "Comprando de red — sin sol ({price:.0f} €/MWh)",
}

KM_PER_KG_CO2 = 1 / 0.13  # equivalencia orientativa: ~0.13 kg CO2 por km en coche de gasolina medio


def format_plan_message(plan: dict, u: dict, target_date: date, intro: str, hour_from: int = 0) -> str:
    """hour_from > 0 se usa para mostrar solo el resto del día (tramos ya pasados omitidos)."""
    co2 = plan["total_solar_self_consumed_kwh"] * ES_GRID_CO2_KG_PER_KWH
    km_equiv = co2 * KM_PER_KG_CO2
    lines = [
        f"{intro} — {target_date}",
        f"📍 {u.get('location', '?')} | {u['kwp']} kWp | {u['battery']} kWh batería",
        "",
        f"💶 *{plan['total_savings_eur']:.2f} € de ahorro estimado (día completo)*",
        f"🌍 ~{co2:.1f} kg CO₂ evitados — como no coger el coche {km_equiv:.0f} km (estimado)",
        "",
    ]
    prev_action = None
    shown = 0
    for h in plan["hours"]:
        if h["hour"] < hour_from:
            continue
        if h["action"] != prev_action:
            emoji = ACTION_EMOJI.get(h["action"], "•")
            msg = ACTION_MSG.get(h["action"], h["action"]).format(price=h["price_eur_mwh"])
            lines.append(f"`{h['hour']:02d}:00` {emoji} {msg}")
            prev_action = h["action"]
            shown += 1
    if shown == 0:
        lines.append("_El día ya ha terminado — no quedan tramos por delante._")
    lines.append("")
    lines.append(f"📊 [Ver informe completo con gráficos]({report_link(u, target_date)})")
    inv = u.get("inverter")
    if inv:
        lines.append(f"🔧 [Cómo programar tu {INVERTER_LABELS[inv]}]({WEB_PUBLIC_URL}/tutoriales#{inv})")
    else:
        lines.append(f"🔧 [Guías para programar tu inversor]({WEB_PUBLIC_URL}/tutoriales)")
    return "\n".join(lines)


def format_alert(h: dict) -> str:
    emoji = ACTION_EMOJI.get(h["action"], "•")
    msg = ACTION_MSG.get(h["action"], h["action"]).format(price=h["price_eur_mwh"])
    return (
        f"🔔 *{h['hour']:02d}:00* — Cambio de acción\n\n{emoji} {msg}\n\n"
        f"Precio pool: {h['price_eur_mwh']:.1f} €/MWh | Solar: {h['solar_kw']:.2f} kW | "
        f"Batería: {h['battery_soc_kwh']:.1f} kWh"
    )


# ── Telegram API (sin libreria, solo httpx) ──

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

async def send_message(chat_id, text, parse_mode="Markdown", reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": False}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE_URL}/sendMessage", json=payload)
        if r.status_code != 200:
            log.error(f"sendMessage error {r.status_code}: {r.text[:200]}")

async def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    async with httpx.AsyncClient(timeout=35) as client:
        resp = await client.get(f"{BASE_URL}/getUpdates", params=params)
    return resp.json().get("result", [])

async def set_bot_commands():
    """Registra los comandos en Telegram para que salgan con descripción al escribir '/'."""
    commands = [
        {"command": "plan", "description": "Lo que queda de hoy"},
        {"command": "start", "description": "Configurar o reconfigurar"},
        {"command": "config", "description": "Ver tu configuración"},
        {"command": "avisos", "description": "Avisos horarios on/off"},
        {"command": "hora", "description": "Hora del plan diario"},
        {"command": "marca", "description": "Marca de tu inversor"},
    ]
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{BASE_URL}/setMyCommands", json={"commands": commands})
        if r.status_code != 200:
            log.error(f"setMyCommands error {r.status_code}: {r.text[:200]}")

LOCATION_KEYBOARD = {
    "keyboard": [[{"text": "📍 Compartir mi ubicación", "request_location": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}
REMOVE_KEYBOARD = {"remove_keyboard": True}

# Teclado persistente con las acciones principales — así el usuario no tiene
# que aprenderse ni escribir comandos para lo del día a día. Los ajustes más
# finos (marca de inversor, hora exacta) siguen siendo comandos de texto,
# pero esos se tocan una vez y ya está.
BTN_PLAN_HOY = "📊 Plan de hoy"
BTN_PLAN_MANANA = "🌙 Plan de mañana"
BTN_CONFIG = "⚙️ Mi configuración"
BTN_AVISOS = "🔔 Avisos horarios"
BTN_TUTORIALES = "🔧 Cómo programar mi inversor"

MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": BTN_PLAN_HOY}, {"text": BTN_PLAN_MANANA}],
        [{"text": BTN_CONFIG}, {"text": BTN_AVISOS}],
        [{"text": BTN_TUTORIALES}],
    ],
    "resize_keyboard": True,
}

STEP_PROMPTS = {
    "kwp": "☀️ ¿Cuántos *kWp* tienes instalados? (ej. 5)\n\nO escribe /plan para usar 5 kWp por defecto.",
    "battery": "🔋 ¿Capacidad de tu *batería* en kWh? (escribe 0 si no tienes)\n\nO escribe /plan para usar 10 kWh por defecto.",
    "consumption": "⚡ ¿Tu *consumo medio diario* en kWh? (ej. 12)\n\nO escribe /plan para usar 12 kWh por defecto.",
    "inverter": "🔌 Última pregunta: ¿qué *marca de inversor* tienes — Huawei, Fronius, Victron u otra?\n\nAsí te paso el enlace directo a cómo programar las franjas horarias. Escribe /plan para saltarte esto.",
}
STEP_ORDER = ["location", "kwp", "battery", "consumption", "inverter"]

INVERTER_LABELS = {
    "huawei": "Huawei FusionSolar", "fronius": "Fronius Solar.web",
    "victron": "Victron VRM", "otro": "tu inversor",
}


def normalize_inverter(text: str) -> str:
    t = text.lower()
    for key in ("huawei", "fronius", "victron"):
        if key in t:
            return key
    return "otro"


def default_user():
    return {
        "kwp": 5, "battery": 10, "consumption": 12,
        "lat": 41.61, "lon": 2.29, "location": "Granollers",
        "inverter": None,
        "hourly_alerts": False,       # opt-in: solo para quien no puede programar su inversor
        "alert_hour": DAILY_SEND_HOUR,  # hora del envío diario, configurable con /hora
        "plan": None, "plan_date": None, "last_action": None,
        "awaiting": "location",
    }


def extract_number(text: str):
    m = re.search(r"[-+]?\d*[.,]?\d+", text)
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


async def advance_step(chat_id, u, users, current_step):
    """Pasa al siguiente paso del onboarding, o lo termina y manda el plan."""
    idx = STEP_ORDER.index(current_step)
    if idx + 1 < len(STEP_ORDER):
        next_step = STEP_ORDER[idx + 1]
        u["awaiting"] = next_step
        save_users(users)
        await send_message(chat_id, STEP_PROMPTS[next_step], reply_markup=REMOVE_KEYBOARD)
    else:
        u["awaiting"] = None
        save_users(users)
        await send_message(chat_id, "Ya tienes todo listo. Usa los botones de abajo para lo del día a día 👇", reply_markup=MAIN_KEYBOARD)
        await send_today_plan(chat_id, u, users, intro="✅ *Todo listo — así queda hoy*")


async def send_today_plan(chat_id, u, users, intro=None):
    """/plan a mano: el resto de HOY (tramos ya pasados fuera), con empujón a manana si toca."""
    now = datetime.now()
    await send_message(chat_id, "Calculando plan con datos reales...")
    try:
        plan = await fetch_plan(u, date.today())
    except Exception as e:
        log.error(f"Error calculando plan para {chat_id}: {e}")
        await send_message(chat_id, "No he podido calcular el plan ahora mismo. Prueba de nuevo en un minuto con /plan.")
        return
    # Solo refrescamos el plan usado para avisos horarios si es el de hoy
    u["plan"] = plan
    u["plan_date"] = str(date.today())
    u["last_action"] = None
    save_users(users)
    if intro is None:
        intro = "☀️ *Lo que queda de hoy*" if now.hour > 0 else "☀️ *Plan de hoy*"
    msg = format_plan_message(plan, u, date.today(), intro, hour_from=now.hour)
    if now.hour >= 14:
        msg += "\n\n💡 Ya está publicado el precio de mañana — mira también /plan manana y déjalo programado esta noche."
    await send_message(chat_id, msg)


async def send_tomorrow_plan(chat_id, u, users):
    tomorrow = date.today() + timedelta(days=1)
    await send_message(chat_id, "Calculando el plan de mañana...")
    try:
        plan = await fetch_plan(u, tomorrow)
    except Exception as e:
        log.error(f"Error calculando plan de mañana para {chat_id}: {e}")
        await send_message(chat_id, "No he podido calcular el plan de mañana — puede que ESIOS aún no lo haya publicado (suele estar sobre las 14h). Prueba en un rato.")
        return
    await send_message(chat_id, format_plan_message(plan, u, tomorrow, "🌙 *Plan de mañana*"))


# ── Comando handler ──

async def handle_message(msg, users):
    chat_id = str(msg["chat"]["id"])

    # Ubicacion compartida via boton nativo de Telegram (funciona en cualquier momento)
    if "location" in msg:
        loc = msg["location"]
        u = users.setdefault(chat_id, default_user())
        u["lat"], u["lon"] = loc["latitude"], loc["longitude"]
        u["location"] = await reverse_geocode(u["lat"], u["lon"])
        await send_message(chat_id, f"📍 Ubicación: *{u['location']}*")
        if u.get("awaiting") == "location":
            await advance_step(chat_id, u, users, "location")
        else:
            save_users(users)
        return

    text = msg.get("text", "").strip()
    if not text:
        return

    if text == "/start":
        users[chat_id] = default_user()
        save_users(users)
        await send_message(
            chat_id,
            "☀️ *CronoSolar* — Optimiza tu autoconsumo solar\n\n"
            "Te hago 5 preguntas rápidas y ya está. Puedes saltarte cualquiera "
            "escribiendo /plan para usar valores por defecto.\n\n"
            "Cada tarde (a las 16:00 por defecto, cambiable con /hora) te mando el "
            "plan del día siguiente para que programes tu inversor con calma y te "
            "olvides. Sin avisos cada hora — para eso está el propio inversor.\n\n"
            "Primero: ¿dónde está tu instalación? Comparte tu ubicación o "
            "escribe el nombre de tu localidad.",
            reply_markup=LOCATION_KEYBOARD,
        )
        return

    if chat_id not in users:
        await send_message(chat_id, "Usa /start para comenzar.")
        return

    u = users[chat_id]
    awaiting = u.get("awaiting")

    # Botones del teclado persistente — mismo destino que sus comandos equivalentes
    if text == BTN_PLAN_HOY:
        u["awaiting"] = None
        save_users(users)
        await send_today_plan(chat_id, u, users)
        return

    if text == BTN_PLAN_MANANA:
        await send_tomorrow_plan(chat_id, u, users)
        return

    if text == BTN_CONFIG:
        avisos = "on" if u.get("hourly_alerts") else "off"
        await send_message(
            chat_id,
            f"📍 {u.get('location', '?')} ({u['lat']}, {u['lon']})\n"
            f"☀️ Paneles: {u['kwp']} kWp\n"
            f"🔋 Batería: {u['battery']} kWh\n"
            f"⚡ Consumo: {u['consumption']} kWh/día\n"
            f"🔌 Inversor: {INVERTER_LABELS.get(u.get('inverter'), 'no indicado')} (/marca huawei|fronius|victron|otro)\n"
            f"⏰ Plan diario a las: {u.get('alert_hour', DAILY_SEND_HOUR):02d}:00 (/hora HH)\n"
            f"🔔 Avisos horarios: {avisos}"
        )
        return

    if text == BTN_AVISOS:
        u["hourly_alerts"] = not u.get("hourly_alerts", False)
        save_users(users)
        if u["hourly_alerts"]:
            await send_message(chat_id, "🔔 Avisos horarios *activados* — te avisaré cada vez que cambie la acción recomendada, además del plan de la tarde. Pulsa de nuevo el botón para desactivarlos.")
        else:
            await send_message(chat_id, "🔕 Avisos horarios *desactivados* — seguirás recibiendo el plan cada tarde para programar tu inversor.")
        return

    if text == BTN_TUTORIALES:
        inv = u.get("inverter")
        link = f"{WEB_PUBLIC_URL}/tutoriales#{inv}" if inv else f"{WEB_PUBLIC_URL}/tutoriales"
        await send_message(chat_id, f"🔧 [Cómo programar tu inversor]({link})")
        return

    # Comandos explicitos — funcionan siempre, incluso a media conversacion
    if text.startswith("/ubicacion"):
        parts = text.split(maxsplit=3)
        if len(parts) >= 3:
            u["lat"] = float(parts[1])
            u["lon"] = float(parts[2])
            u["location"] = parts[3] if len(parts) > 3 else f"{u['lat']}, {u['lon']}"
            save_users(users)
            await send_message(chat_id, f"📍 Ubicación: {u['location']} ({u['lat']}, {u['lon']})")
        return

    if text.startswith("/paneles"):
        try:
            u["kwp"] = float(text.split()[1])
            save_users(users)
            await send_message(chat_id, f"☀️ Paneles: {u['kwp']} kWp")
        except (IndexError, ValueError):
            await send_message(chat_id, "Formato: /paneles 5")
        return

    if text.startswith("/bateria"):
        try:
            u["battery"] = float(text.split()[1])
            save_users(users)
            await send_message(chat_id, f"🔋 Batería: {u['battery']} kWh")
        except (IndexError, ValueError):
            await send_message(chat_id, "Formato: /bateria 10")
        return

    if text.startswith("/consumo"):
        try:
            u["consumption"] = float(text.split()[1])
            save_users(users)
            await send_message(chat_id, f"⚡ Consumo: {u['consumption']} kWh/día")
        except (IndexError, ValueError):
            await send_message(chat_id, "Formato: /consumo 12")
        return

    if text.startswith("/marca"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await send_message(chat_id, "Formato: /marca huawei (o fronius, victron, otro)")
            return
        inv = normalize_inverter(parts[1])
        u["inverter"] = inv
        save_users(users)
        await send_message(chat_id, f"🔌 Inversor: {INVERTER_LABELS[inv]}")
        return

    if text.startswith("/avisos"):
        parts = text.split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else None
        if arg in ("on", "activar", "si", "sí"):
            u["hourly_alerts"] = True
            save_users(users)
            await send_message(chat_id, "🔔 Avisos horarios *activados*. Te avisaré cada vez que cambie la acción recomendada, además del plan diario de la tarde.")
        elif arg in ("off", "desactivar", "no"):
            u["hourly_alerts"] = False
            save_users(users)
            await send_message(chat_id, "🔕 Avisos horarios *desactivados*. Seguirás recibiendo el plan cada tarde para programar tu inversor.")
        else:
            estado = "activados" if u.get("hourly_alerts") else "desactivados"
            await send_message(
                chat_id,
                f"Los avisos horarios están *{estado}*.\n\n"
                "Si puedes programar franjas horarias en tu inversor (ver /tutoriales), no los necesitas — el plan de la tarde es suficiente. "
                "Si tu inversor no permite programación y necesitas que te avisen en tiempo real cada vez que toca cambiar de acción, actívalos con /avisos on."
            )
        return

    if text.startswith("/hora"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await send_message(chat_id, f"⏰ Tu plan diario llega a las *{u.get('alert_hour', DAILY_SEND_HOUR):02d}:00*.\n\nPara cambiarla: /hora 18 (entre las 14 y las 22, cuando ya está publicado el precio de mañana).")
            return
        try:
            hour = int(parts[1].strip())
        except ValueError:
            await send_message(chat_id, "Formato: /hora 18 (un número de 0 a 23)")
            return
        if not (14 <= hour <= 22):
            await send_message(chat_id, "Elige una hora entre las 14 y las 22 — antes de las 14h ESIOS aún no ha publicado el precio de mañana.")
            return
        u["alert_hour"] = hour
        save_users(users)
        await send_message(chat_id, f"⏰ Tu plan diario llegará a las *{hour:02d}:00* a partir de hoy.")
        return

    if text.startswith("/plan"):
        parts = text.split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        u["awaiting"] = None
        save_users(users)
        if arg in ("manana", "mañana", "tomorrow"):
            await send_tomorrow_plan(chat_id, u, users)
        else:
            await send_today_plan(chat_id, u, users)
        return

    if text.startswith("/config"):
        avisos = "on" if u.get("hourly_alerts") else "off"
        await send_message(
            chat_id,
            f"📍 {u.get('location', '?')} ({u['lat']}, {u['lon']})\n"
            f"☀️ Paneles: {u['kwp']} kWp\n"
            f"🔋 Batería: {u['battery']} kWh\n"
            f"⚡ Consumo: {u['consumption']} kWh/día\n"
            f"🔌 Inversor: {INVERTER_LABELS.get(u.get('inverter'), 'no indicado')}\n"
            f"⏰ Plan diario a las: {u.get('alert_hour', DAILY_SEND_HOUR):02d}:00 (/hora HH)\n"
            f"🔔 Avisos horarios: {avisos} (/avisos on|off)"
        )
        return

    # Flujo conversacional: texto libre interpretado segun el paso actual
    if awaiting == "location":
        result = await geocode_place(text)
        if not result:
            await send_message(chat_id, "No he encontrado esa localidad. Prueba con otro nombre, o comparte tu ubicación con el botón.", reply_markup=LOCATION_KEYBOARD)
            return
        u["lat"], u["lon"], u["location"] = result
        await send_message(chat_id, f"📍 Ubicación: *{u['location']}*")
        await advance_step(chat_id, u, users, "location")
        return

    if awaiting in ("kwp", "battery", "consumption"):
        num = extract_number(text)
        if num is None:
            await send_message(chat_id, "No he entendido ese número, prueba de nuevo (ej. 5.5).")
            return
        field = {"kwp": "kwp", "battery": "battery", "consumption": "consumption"}[awaiting]
        u[field] = num
        label = {"kwp": f"☀️ Paneles: {num} kWp", "battery": f"🔋 Batería: {num} kWh", "consumption": f"⚡ Consumo: {num} kWh/día"}[awaiting]
        await send_message(chat_id, label)
        await advance_step(chat_id, u, users, awaiting)
        return

    if awaiting == "inverter":
        inv = normalize_inverter(text)
        u["inverter"] = inv
        await send_message(chat_id, f"🔌 Inversor: {INVERTER_LABELS[inv]}")
        await advance_step(chat_id, u, users, "inverter")
        return

    await send_message(chat_id, "No te he entendido. Escribe /plan para ver lo que queda de hoy, /plan manana para el de mañana, o /start para reconfigurar.")


# ── Scheduler: alerta cuando cambia la accion (solo opt-in, /avisos on) ──

async def check_and_alert(users):
    """Ejecutar cada hora. Solo para usuarios con hourly_alerts activado."""
    current_hour = datetime.now().hour
    today_str = str(date.today())

    for chat_id, u in users.items():
        if not u.get("hourly_alerts") or u.get("awaiting"):
            continue
        plan = u.get("plan")
        if not plan or not isinstance(plan, dict) or u.get("plan_date") != today_str:
            continue

        hours = plan.get("hours", [])
        current = hours[current_hour] if current_hour < len(hours) else None
        if not current:
            continue

        last = u.get("last_action")
        if current["action"] != last:
            u["last_action"] = current["action"]
            save_users(users)
            try:
                await send_message(chat_id, format_alert(current))
                log.info(f"Alerta enviada a {chat_id}: {current['action']}")
            except Exception as e:
                log.error(f"Error enviando a {chat_id}: {e}")


async def daily_plan(users):
    """Se llama una vez por hora. Cada usuario recibe su plan de MANANA solo
    cuando el reloj llega a su 'alert_hour' (por defecto 16:00, configurable
    con /hora). plan_date ya apuntando a manana es la señal de 'ya enviado
    hoy', para no duplicar si esta funcion se llama varias veces en la
    misma hora."""
    now_hour = datetime.now().hour
    tomorrow = date.today() + timedelta(days=1)
    tomorrow_str = str(tomorrow)
    for chat_id, u in list(users.items()):
        if u.get("awaiting"):
            continue
        if u.get("alert_hour", DAILY_SEND_HOUR) != now_hour:
            continue
        if u.get("plan_date") == tomorrow_str:
            continue  # ya se le envio hoy
        try:
            plan = await fetch_plan(u, tomorrow)
            # Se guarda como el plan "activo" — cuando llegue manana, ya sera "hoy"
            u["plan"] = plan
            u["plan_date"] = tomorrow_str
            u["last_action"] = None
            save_users(users)
            await send_message(chat_id, format_plan_message(plan, u, tomorrow, "🌙 *Plan de mañana* — prográmalo esta noche y olvídate"))
            log.info(f"Plan de mañana enviado a {chat_id} (hora configurada: {now_hour}:00)")
        except Exception as e:
            log.error(f"Error plan diario {chat_id}: {e}")


# ── Main loop ──

async def main():
    if not TELEGRAM_TOKEN:
        print("ERROR: Configura TELEGRAM_BOT_TOKEN en .env")
        print("  1. Habla con @BotFather en Telegram")
        print("  2. Crea un bot con /newbot")
        print("  3. Copia el token al .env")
        return

    users = load_users()
    offset = None
    last_hour_tick = -1

    await set_bot_commands()
    log.info("CronoSolar Telegram bot iniciado")

    while True:
        try:
            now = datetime.now()
            if now.hour != last_hour_tick:
                await daily_plan(users)      # cada usuario, a su alert_hour configurada
                await check_and_alert(users)  # solo opt-in (/avisos on)
                last_hour_tick = now.hour

            updates = await get_updates(offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                if "message" in upd:
                    await handle_message(upd["message"], users)

        except Exception as e:
            log.error(f"Error en loop: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
