"""
SolOptim — Bot de Telegram (Fase 1.5)
Envia alertas horarias al usuario cuando cambia la accion recomendada.

Uso:
  1. Crea un bot en Telegram hablando con @BotFather → te da un TOKEN
  2. Anade TELEGRAM_BOT_TOKEN al .env
  3. Ejecuta: python telegram_bot.py
  4. El usuario habla con el bot, manda /start y configura su instalacion
  5. Cada manana recibe el plan del dia, y durante el dia recibe alertas
     solo cuando cambia la accion recomendada
"""

import asyncio
import json
import os
import logging
from datetime import date, datetime, timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ESIOS_TOKEN = os.getenv("ESIOS_TOKEN", "")
DATA_FILE = os.getenv("DATA_FILE", "users.json")  # Persistencia simple; en Railway apunta a un volumen montado

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


# ── ESIOS ──

async def fetch_prices(target_date):
    if not ESIOS_TOKEN:
        # Simulados
        return [28,22,18,15,14,16,25,45,62,55,48,38,30,25,20,18,22,35,58,85,92,78,55,38]

    url = f"https://api.esios.ree.es/indicators/600"
    headers = {
        "Accept": "application/json; application/vnd.esios-api-v2+json",
        "Content-Type": "application/json",
        "Host": "api.esios.ree.es",
        "Authorization": f'Token token="{ESIOS_TOKEN}"',
    }
    params = {
        "start_date": f"{target_date}T00:00:00",
        "end_date": f"{target_date}T23:59:59",
        "geo_ids[]": 8741,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers, params=params)

    if resp.status_code != 200:
        log.error(f"ESIOS error: {resp.status_code}")
        return [50] * 24

    data = resp.json()
    values = data.get("indicator", {}).get("values", [])
    hourly = {}
    for v in values:
        dt = datetime.fromisoformat(v["datetime"].replace("Z", "+00:00"))
        hourly.setdefault(dt.hour, []).append(v["value"])

    return [sum(hourly.get(h, [50])) / len(hourly.get(h, [1])) for h in range(24)]


# ── Open-Meteo ──

async def fetch_solar(lat, lon, kwp, target_date):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "shortwave_radiation",
        "start_date": str(target_date), "end_date": str(target_date),
        "timezone": "Europe/Madrid",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)

    if resp.status_code != 200:
        return [0] * 24

    data = resp.json()
    radiation = data.get("hourly", {}).get("shortwave_radiation", [])
    return [round((r or 0) * kwp * 0.8 / 1000, 3) for r in radiation[:24]]


# ── Consumo ──

def get_consumption(daily):
    prof = [2.5,2,1.8,1.5,1.5,2,3,5,5.5,5,4.5,4,4.5,5,4.5,4,4,5,6.5,7,7.5,7,5.5,3.5]
    t = sum(prof)
    return [round((p / t) * daily, 3) for p in prof]


# ── Optimizacion ──

def optimize(prices, solar, cons, bat):
    soc = 0
    mr = bat * 0.5 if bat > 0 else 0
    ranked = sorted(range(24), key=lambda h: prices[h], reverse=True)
    dh = set(ranked[:6]) if bat > 0 else set()

    result = []
    for h in range(24):
        pk = prices[h] / 1000
        sol, dem = solar[h], cons[h]
        net = sol - dem
        act = "grid"

        if net >= 0:
            act = "solar"
            sur = net
            if bat > 0 and soc < bat:
                c = min(sur, mr, bat - soc)
                if c > 0:
                    soc += c
                    sur -= c
                    act = "charge"
            if sur > 0 and act == "solar":
                act = "export"
        else:
            deficit = -net
            if bat > 0 and h in dh and soc > 0:
                d = min(deficit, mr, soc)
                soc -= d
                act = "discharge"

        result.append({"hour": h, "action": act, "price": prices[h], "solar": sol, "soc": round(soc, 1)})

    return result


# ── Mensajes ──

ACTION_EMOJI = {
    "solar": "☀️",
    "charge": "🔋⬆️",
    "discharge": "🔋⬇️",
    "export": "📤",
    "grid": "🔌",
}

ACTION_MSG = {
    "solar": "Autoconsumo directo — el sol cubre tu consumo",
    "charge": "Carga la bateria — hay excedente solar",
    "discharge": "Descarga la bateria — precio alto ({price:.0f} €/MWh)",
    "export": "Vertiendo excedentes a red — bateria llena",
    "grid": "Comprando de red — sin sol ({price:.0f} €/MWh)",
}


def format_morning_plan(plan, user):
    lines = [f"☀️ *Plan para hoy* — {date.today()}", f"📍 {user.get('location', '?')} | {user['kwp']} kWp | {user['battery']} kWh bat", ""]

    prev_action = None
    for h in plan:
        if h["action"] != prev_action:
            emoji = ACTION_EMOJI.get(h["action"], "•")
            msg = ACTION_MSG.get(h["action"], h["action"]).format(price=h["price"])
            lines.append(f"`{h['hour']:02d}:00` {emoji} {msg}")
            prev_action = h["action"]

    return "\n".join(lines)


def format_alert(h):
    emoji = ACTION_EMOJI.get(h["action"], "•")
    msg = ACTION_MSG.get(h["action"], h["action"]).format(price=h["price"])
    return f"🔔 *{h['hour']:02d}:00* — Cambio de accion\n\n{emoji} {msg}\n\nPrecio pool: {h['price']:.1f} €/MWh | Solar: {h['solar']:.2f} kW | Bateria: {h['soc']:.1f} kWh"


# ── Telegram API (sin libreria, solo httpx) ──

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

async def send_message(chat_id, text, parse_mode="Markdown"):
    async with httpx.AsyncClient() as client:
        await client.post(f"{BASE_URL}/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": parse_mode,
        })

async def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    async with httpx.AsyncClient(timeout=35) as client:
        resp = await client.get(f"{BASE_URL}/getUpdates", params=params)
    return resp.json().get("result", [])


# ── Comando handler ──

async def handle_message(msg, users):
    chat_id = str(msg["chat"]["id"])
    text = msg.get("text", "").strip()

    if text == "/start":
        users[chat_id] = {"kwp": 5, "battery": 10, "consumption": 12, "lat": 41.61, "lon": 2.29, "location": "Granollers", "last_action": None}
        save_users(users)
        await send_message(chat_id,
            "☀️ *SolOptim* — Optimiza tu autoconsumo\n\n"
            "Configura tu instalacion:\n"
            "`/ubicacion <lat> <lon> <nombre>`\n"
            "`/paneles <kWp>`\n"
            "`/bateria <kWh>`\n"
            "`/consumo <kWh/dia>`\n"
            "`/plan` — Ver plan de hoy\n\n"
            "Recibiras alertas automaticas cuando cambie la accion recomendada."
        )
        return

    if chat_id not in users:
        await send_message(chat_id, "Usa /start para comenzar.")
        return

    u = users[chat_id]

    if text.startswith("/ubicacion"):
        parts = text.split(maxsplit=3)
        if len(parts) >= 3:
            u["lat"] = float(parts[1])
            u["lon"] = float(parts[2])
            u["location"] = parts[3] if len(parts) > 3 else f"{u['lat']}, {u['lon']}"
            save_users(users)
            await send_message(chat_id, f"📍 Ubicacion: {u['location']} ({u['lat']}, {u['lon']})")

    elif text.startswith("/paneles"):
        u["kwp"] = float(text.split()[1])
        save_users(users)
        await send_message(chat_id, f"☀️ Paneles: {u['kwp']} kWp")

    elif text.startswith("/bateria"):
        u["battery"] = float(text.split()[1])
        save_users(users)
        await send_message(chat_id, f"🔋 Bateria: {u['battery']} kWh")

    elif text.startswith("/consumo"):
        u["consumption"] = float(text.split()[1])
        save_users(users)
        await send_message(chat_id, f"⚡ Consumo: {u['consumption']} kWh/dia")

    elif text.startswith("/plan"):
        await send_message(chat_id, "Calculando plan de hoy...")
        prices = await fetch_prices(date.today())
        solar = await fetch_solar(u["lat"], u["lon"], u["kwp"], date.today())
        cons = get_consumption(u["consumption"])
        plan = optimize(prices, solar, cons, u["battery"])
        u["today_plan"] = plan
        save_users(users)
        await send_message(chat_id, format_morning_plan(plan, u))

    elif text.startswith("/config"):
        await send_message(chat_id,
            f"📍 {u.get('location', '?')} ({u['lat']}, {u['lon']})\n"
            f"☀️ Paneles: {u['kwp']} kWp\n"
            f"🔋 Bateria: {u['battery']} kWh\n"
            f"⚡ Consumo: {u['consumption']} kWh/dia"
        )


# ── Scheduler: alerta cuando cambia la accion ──

async def check_and_alert(users):
    """Ejecutar cada hora. Compara la accion actual con la anterior y alerta si cambia."""
    now = datetime.now()
    current_hour = now.hour

    for chat_id, u in users.items():
        plan = u.get("today_plan")
        if not plan:
            continue

        current = plan[current_hour] if current_hour < len(plan) else None
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
    for chat_id, u in users.items():
        try:
            prices = await fetch_prices(date.today())
            solar = await fetch_solar(u["lat"], u["lon"], u["kwp"], date.today())
            cons = get_consumption(u["consumption"])
            plan = optimize(prices, solar, cons, u["battery"])
            u["today_plan"] = plan
            u["last_action"] = None
            save_users(users)
            await send_message(chat_id, format_morning_plan(plan, u))
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
            # Comprobar si toca plan diario (8:00)
            now = datetime.now()
            if now.hour == 8 and not plan_sent_today:
                await daily_plan(users)
                plan_sent_today = True
            if now.hour != 8:
                plan_sent_today = False

            # Comprobar alertas horarias
            if now.hour != last_alert_hour:
                await check_and_alert(users)
                last_alert_hour = now.hour

            # Procesar mensajes entrantes
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
