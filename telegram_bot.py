"""
SolOptim — Bot de Telegram (Fase 1.5)
Envia alertas horarias al usuario cuando cambia la accion recomendada.

Flujo conversacional: /start guia al usuario paso a paso (ubicacion —
compartida o escrita — kWp, bateria, consumo) sin exigir sintaxis de
comandos exacta. El bot delega todo el calculo (precios ESIOS + solar
Open-Meteo + optimizacion) a la API interna de la web, para que los
numeros que ve el usuario en Telegram y en el informe web coincidan
siempre.

Uso:
  1. Crea un bot en Telegram hablando con @BotFather -> te da un TOKEN
  2. Anade TELEGRAM_BOT_TOKEN al .env
  3. Ejecuta: python telegram_bot.py
  4. El usuario habla con el bot, manda /start y sigue los pasos
  5. Cada manana recibe el plan del dia, y durante el dia recibe alertas
     solo cuando cambia la accion recomendada
"""

import asyncio
import json
import os
import re
import logging
from datetime import date, datetime

import httpx
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DATA_FILE = os.getenv("DATA_FILE", "users.json")  # Persistencia simple; en el servidor apunta a un volumen
API_INTERNAL_URL = os.getenv("API_INTERNAL_URL", "http://localhost:8000")  # backend, red interna de Docker
WEB_PUBLIC_URL = os.getenv("WEB_PUBLIC_URL", "http://localhost:8000")  # para los enlaces que ve el usuario

ES_GRID_CO2_KG_PER_KWH = 0.19  # factor medio orientativo de la red española

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("soloptim")

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
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "CronoSol/1.0"}) as client:
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
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "CronoSol/1.0"}) as client:
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
        f"{WEB_PUBLIC_URL}/?lat={u['lat']}&lon={u['lon']}&kwp={u['kwp']}"
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


def format_plan_message(plan: dict, u: dict, target_date: date, intro: str = "☀️ *Plan de hoy*") -> str:
    co2 = plan["total_solar_self_consumed_kwh"] * ES_GRID_CO2_KG_PER_KWH
    lines = [
        f"{intro} — {target_date}",
        f"📍 {u.get('location', '?')} | {u['kwp']} kWp | {u['battery']} kWh batería",
        "",
        f"💶 *{plan['total_savings_eur']:.2f} € de ahorro estimado*",
        f"🌍 ~{co2:.1f} kg CO₂ evitados (estimado)",
        "",
    ]
    prev_action = None
    for h in plan["hours"]:
        if h["action"] != prev_action:
            emoji = ACTION_EMOJI.get(h["action"], "•")
            msg = ACTION_MSG.get(h["action"], h["action"]).format(price=h["price_eur_mwh"])
            lines.append(f"`{h['hour']:02d}:00` {emoji} {msg}")
            prev_action = h["action"]
    lines.append("")
    lines.append(f"📊 [Ver informe completo con gráficos]({report_link(u, target_date)})")
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

LOCATION_KEYBOARD = {
    "keyboard": [[{"text": "📍 Compartir mi ubicación", "request_location": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}
REMOVE_KEYBOARD = {"remove_keyboard": True}

STEP_PROMPTS = {
    "kwp": "☀️ ¿Cuántos *kWp* tienes instalados? (ej. 5)\n\nO escribe /plan para usar 5 kWp por defecto.",
    "battery": "🔋 ¿Capacidad de tu *batería* en kWh? (escribe 0 si no tienes)\n\nO escribe /plan para usar 10 kWh por defecto.",
    "consumption": "⚡ ¿Tu *consumo medio diario* en kWh? (ej. 12)\n\nO escribe /plan para usar 12 kWh por defecto.",
}
STEP_ORDER = ["location", "kwp", "battery", "consumption"]


def default_user():
    return {
        "kwp": 5, "battery": 10, "consumption": 12,
        "lat": 41.61, "lon": 2.29, "location": "Granollers",
        "last_action": None, "awaiting": "location",
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
        await send_plan(chat_id, u, users, intro="✅ *Todo listo — tu plan de hoy*")


async def send_plan(chat_id, u, users, intro="☀️ *Plan de hoy*"):
    await send_message(chat_id, "Calculando plan con datos reales...")
    try:
        plan = await fetch_plan(u, date.today())
    except Exception as e:
        log.error(f"Error calculando plan para {chat_id}: {e}")
        await send_message(chat_id, "No he podido calcular el plan ahora mismo. Prueba de nuevo en un minuto con /plan.")
        return
    u["today_plan"] = plan
    u["last_action"] = None
    save_users(users)
    await send_message(chat_id, format_plan_message(plan, u, date.today(), intro))


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
            "☀️ *CronoSol* — Optimiza tu autoconsumo solar\n\n"
            "Te hago 4 preguntas rápidas y ya está. Puedes saltarte cualquiera "
            "escribiendo /plan para usar valores por defecto.\n\n"
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

    if text.startswith("/plan"):
        u["awaiting"] = None
        save_users(users)
        await send_plan(chat_id, u, users)
        return

    if text.startswith("/config"):
        await send_message(
            chat_id,
            f"📍 {u.get('location', '?')} ({u['lat']}, {u['lon']})\n"
            f"☀️ Paneles: {u['kwp']} kWp\n"
            f"🔋 Batería: {u['battery']} kWh\n"
            f"⚡ Consumo: {u['consumption']} kWh/día"
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

    await send_message(chat_id, "No te he entendido. Escribe /plan para ver tu plan de hoy, o /start para reconfigurar.")


# ── Scheduler: alerta cuando cambia la accion ──

async def check_and_alert(users):
    """Ejecutar cada hora. Compara la accion actual con la anterior y alerta si cambia."""
    current_hour = datetime.now().hour

    for chat_id, u in users.items():
        plan = u.get("today_plan")
        if not plan or u.get("awaiting"):
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
    """Ejecutar cada manana (~8:00). Calcula y envia el plan del dia."""
    for chat_id, u in list(users.items()):
        if u.get("awaiting"):
            continue
        try:
            plan = await fetch_plan(u, date.today())
            u["today_plan"] = plan
            u["last_action"] = None
            save_users(users)
            await send_message(chat_id, format_plan_message(plan, u, date.today()))
            log.info(f"Plan diario enviado a {chat_id}")
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
    last_alert_hour = -1
    plan_sent_today = False

    log.info("SolOptim Telegram bot iniciado")

    while True:
        try:
            now = datetime.now()
            if now.hour == 8 and not plan_sent_today:
                await daily_plan(users)
                plan_sent_today = True
            if now.hour != 8:
                plan_sent_today = False

            if now.hour != last_alert_hour:
                await check_and_alert(users)
                last_alert_hour = now.hour

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
