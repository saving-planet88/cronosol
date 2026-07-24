"""
SolOptim — Optimizador de autoconsumo solar + batería
Backend FastAPI: conecta con ESIOS (precios pool) y Open-Meteo (radiación solar),
calcula el plan óptimo de carga/descarga y estima el ahorro.
"""

import json
import logging
import math
import os
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cronosolar")

ESIOS_TOKEN = os.getenv("ESIOS_TOKEN", "")
# Indicador 600: Precio mercado diario España (€/MWh)
ESIOS_INDICATOR = 600

# ── Aviso de uso de Open-Meteo ────────────────────────────
# El plan gratuito de Open-Meteo tiene un límite orientativo de ~10.000
# llamadas/día. Avisamos con margen para poder pasar a plan de pago o
# cachear antes de que el servicio empiece a fallar sin previsión.
OPEN_METEO_DAILY_WARN = 9000
ADMIN_TELEGRAM_CHAT_ID = os.getenv("ADMIN_TELEGRAM_CHAT_ID", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_open_meteo_usage = {"date": None, "count": 0, "alerted": False}


async def _track_open_meteo_call():
    today = str(date.today())
    if _open_meteo_usage["date"] != today:
        _open_meteo_usage["date"] = today
        _open_meteo_usage["count"] = 0
        _open_meteo_usage["alerted"] = False
    _open_meteo_usage["count"] += 1
    if _open_meteo_usage["count"] >= OPEN_METEO_DAILY_WARN and not _open_meteo_usage["alerted"]:
        _open_meteo_usage["alerted"] = True
        msg = f"⚠️ Open-Meteo: {_open_meteo_usage['count']} llamadas hoy ({today}) — cerca o por encima del límite gratuito orientativo."
        log.warning(msg)
        if ADMIN_TELEGRAM_CHAT_ID and TELEGRAM_BOT_TOKEN:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={"chat_id": ADMIN_TELEGRAM_CHAT_ID, "text": msg},
                    )
            except Exception as e:
                log.error(f"No se pudo enviar el aviso de Open-Meteo por Telegram: {e}")

app = FastAPI(title="SolOptim API")
templates = Jinja2Templates(directory="templates")

BLOG_POSTS = [
    {
        "slug": "consumo-y-clima",
        "tag": "Red eléctrica",
        "title": "Por qué la hora de tu enchufe importa para el clima",
        "excerpt": "El precio del mercado eléctrico y la limpieza de la red se mueven casi siempre juntos. Así funciona la conexión, y por qué mover tu consumo aprovecha ambas cosas a la vez.",
        "date": "2026-07-10",
        "read_time": "4 min",
    },
    {
        "slug": "cambio-climatico-5-datos",
        "tag": "Contexto",
        "title": "El cambio climático explicado en 5 datos (con fuentes)",
        "excerpt": "Cinco datos consolidados por la ciencia del clima —IPCC, IEA, IRENA— y por qué decisiones cotidianas como cuándo enciendes un electrodoméstico siguen teniendo sentido.",
        "date": "2026-07-15",
        "read_time": "5 min",
    },
    {
        "slug": "curva-de-demanda",
        "tag": "Red eléctrica",
        "title": "La curva del pato: por qué compensa cargar la batería a mediodía",
        "excerpt": "La demanda eléctrica española y la generación solar no siguen la misma curva. Entender el desajuste explica por qué tener batería cambia tanto las cuentas.",
        "date": "2026-07-19",
        "read_time": "4 min",
    },
    {
        "slug": "cuanto-co2-evita-el-autoconsumo",
        "tag": "Metodología",
        "title": "Cuánto CO₂ evita realmente el autoconsumo solar",
        "excerpt": "Desglosamos la metodología detrás de la cifra de CO₂ evitado que usa CronoSolar: de dónde sale, qué supuestos hace y qué no mide.",
        "date": "2026-07-23",
        "read_time": "4 min",
    },
]
BLOG_POSTS.sort(key=lambda p: p["date"], reverse=True)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Modelos ──────────────────────────────────────────────

class InstallationParams(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    kwp: float = Field(gt=0, le=1000)  # kW pico instalados
    battery_kwh: float = Field(default=0.0, ge=0, le=1000)  # capacidad batería (0 = sin batería)
    daily_consumption_kwh: float = Field(default=10.0, gt=0, le=1000)  # consumo medio diario
    tilt: float = Field(default=30.0, ge=0, le=90)  # inclinación paneles
    azimuth: float = Field(default=180.0, ge=0, le=360)  # orientación (180 = sur, 0/360 = norte)


class HourlySlot(BaseModel):
    hour: int
    price_eur_mwh: float
    price_eur_kwh: float
    solar_kw: float
    consumption_kw: float
    action: str  # "grid" | "solar" | "battery_charge" | "battery_discharge" | "export"
    battery_soc_kwh: float
    savings_eur: float


class OptimizationResult(BaseModel):
    date: str
    location: str
    kwp: float
    battery_kwh: float
    hours: list[HourlySlot]
    total_savings_eur: float
    total_export_revenue_eur: float
    total_grid_cost_eur: float
    total_grid_cost_no_optim_eur: float
    total_solar_self_consumed_kwh: float
    total_exported_kwh: float
    total_grid_imported_kwh: float


# ── ESIOS: precios del pool ──────────────────────────────

async def fetch_esios_prices(target_date: date) -> list[float]:
    """Devuelve 24 precios horarios en €/MWh para la fecha dada."""
    if not ESIOS_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="ESIOS_TOKEN no configurado. Solicita tu token en https://api.esios.ree.es/"
        )

    start = f"{target_date}T00:00:00"
    end = f"{target_date}T23:59:59"
    url = f"https://api.esios.ree.es/indicators/{ESIOS_INDICATOR}"
    headers = {
        "Accept": "application/json; application/vnd.esios-api-v2+json",
        "Content-Type": "application/json",
        "Host": "api.esios.ree.es",
        "Authorization": f'Token token="{ESIOS_TOKEN}"',
        "x-api-key": ESIOS_TOKEN,
    }
    params = {
        "start_date": start,
        "end_date": end,
        "geo_ids[]": 3,  # España
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers, params=params)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Error ESIOS: {resp.text[:300]}"
        )

    data = resp.json()
    values = data.get("indicator", {}).get("values", [])

    # Agrupar por hora (puede haber cuartos de hora)
    hourly: dict[int, list[float]] = {}
    for v in values:
        dt = datetime.fromisoformat(v["datetime"].replace("Z", "+00:00"))
        h = dt.hour
        hourly.setdefault(h, []).append(v["value"])

    missing_hours = [h for h in range(24) if h not in hourly]
    if len(missing_hours) == 24:
        # ESIOS respondió 200 pero sin ningún precio para el día — ya ocurrió antes
        # (geo_id inválido devolvía values:[] con 200 OK). No fabricamos datos: fallamos alto.
        raise HTTPException(
            status_code=502,
            detail=f"ESIOS no tiene precios publicados para {target_date} todavía. "
                    "El día D+1 suele publicarse sobre las 14:00 (hora española)."
        )

    known_avg = sum(sum(v) / len(v) for v in hourly.values()) / len(hourly)
    prices = []
    for h in range(24):
        vals = hourly.get(h)
        if vals:
            prices.append(sum(vals) / len(vals))
        else:
            # Hora suelta sin dato (p.ej. cambio de horario): usamos la media del día,
            # NUNCA 0 — un precio de 0€ falso sesgaría al optimizador a preferir esa hora.
            prices.append(known_avg)

    return prices


# ── Posición solar y transposición al plano del panel ────
# Modelo estándar (Duffie & Beckman) + cielo isotrópico (Liu-Jordan) para
# convertir radiación horizontal en irradiancia sobre el plano inclinado
# del panel, según su inclinación y orientación reales.

MADRID_TZ = ZoneInfo("Europe/Madrid")


def _solar_position(lat_deg: float, lon_deg: float, dt_local: datetime) -> tuple[float, float]:
    """Devuelve (elevación, azimut) del sol en grados. Azimut: 0=N, 90=E, 180=S, 270=O."""
    n = dt_local.timetuple().tm_yday
    b = math.radians(360 / 365 * (n - 81))
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)  # minutos

    utc_offset_hours = dt_local.utcoffset().total_seconds() / 3600
    local_std_meridian = utc_offset_hours * 15
    tc = 4 * (lon_deg - local_std_meridian) + eot  # minutos
    solar_time = dt_local.hour + dt_local.minute / 60 + tc / 60
    hour_angle = math.radians(15 * (solar_time - 12))

    decl = math.radians(23.45 * math.sin(math.radians(360 / 365 * (284 + n))))
    lat = math.radians(lat_deg)

    cos_zenith = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = math.acos(cos_zenith)
    elevation = 90 - math.degrees(zenith)
    if elevation <= 0:
        return elevation, 180.0

    sin_zenith = math.sin(zenith)
    if abs(sin_zenith) < 1e-6:
        return elevation, 180.0

    cos_gamma_s = (cos_zenith * math.sin(lat) - math.sin(decl)) / (sin_zenith * math.cos(lat))
    cos_gamma_s = max(-1.0, min(1.0, cos_gamma_s))
    gamma_s = math.degrees(math.acos(cos_gamma_s))
    if hour_angle < 0:
        gamma_s = -gamma_s  # mañana: sol al este de sur

    azimuth = (180 + gamma_s) % 360  # 0=N, 90=E, 180=S, 270=O (mismo criterio que InstallationParams.azimuth)
    return elevation, azimuth


def _poa_irradiance(ghi: float, dni: float, dhi: float, tilt_deg: float, panel_azimuth_deg: float,
                     elevation_deg: float, sun_azimuth_deg: float) -> float:
    """Irradiancia sobre el plano del panel (W/m²) dadas GHI/DNI/DHI horizontales."""
    if elevation_deg <= 0:
        return 0.0
    tilt = math.radians(tilt_deg)
    zenith = math.radians(90 - elevation_deg)
    cos_aoi = (
        math.cos(zenith) * math.cos(tilt)
        + math.sin(zenith) * math.sin(tilt) * math.cos(math.radians(sun_azimuth_deg - panel_azimuth_deg))
    )
    beam = dni * max(0.0, cos_aoi)
    diffuse = dhi * (1 + math.cos(tilt)) / 2  # cielo isotrópico
    ground_albedo = 0.2
    reflected = ghi * ground_albedo * (1 - math.cos(tilt)) / 2
    return max(0.0, beam + diffuse + reflected)


# ── Open-Meteo: predicción solar ─────────────────────────

async def fetch_solar_forecast(
    lat: float, lon: float, kwp: float, tilt: float, azimuth: float, target_date: date
) -> list[float]:
    """Devuelve 24 valores de producción solar estimada en kW para cada hora."""
    await _track_open_meteo_call()
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "shortwave_radiation,direct_normal_irradiance,diffuse_radiation",
        "start_date": str(target_date),
        "end_date": str(target_date),
        "timezone": "Europe/Madrid",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Error Open-Meteo: {resp.text[:300]}"
        )

    data = resp.json()
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    ghi_list = hourly.get("shortwave_radiation", [])
    dni_list = hourly.get("direct_normal_irradiance", [])
    dhi_list = hourly.get("diffuse_radiation", [])

    # Open-Meteo debería devolver 24 puntos horarios para un solo día. Si la
    # respuesta viene incompleta (cambio de formato, corte parcial, etc.) NO
    # rellenamos con producción 0 en silencio: eso simularía un día nublado
    # falso y le diría al usuario que no merece la pena cargar la batería.
    if len(times) < 24 or len(ghi_list) < 24 or len(dni_list) < 24 or len(dhi_list) < 24:
        raise HTTPException(
            status_code=502,
            detail="La previsión solar de Open-Meteo llegó incompleta para ese día. Inténtalo de nuevo en unos minutos."
        )

    # Techo físico de irradiancia (W/m²) para detectar valores corruptos del
    # proveedor sin tumbar la petición — se recorta, no se descarta el resto del día.
    PHYSICAL_MAX_IRRADIANCE = 1400.0

    # Rendimiento del sistema (pérdidas por temperatura, cableado, inversor)
    efficiency = 0.80
    production = []
    for i in range(24):
        ghi = min(ghi_list[i] or 0, PHYSICAL_MAX_IRRADIANCE)
        dni = min(dni_list[i] or 0, PHYSICAL_MAX_IRRADIANCE)
        dhi = min(dhi_list[i] or 0, PHYSICAL_MAX_IRRADIANCE)

        # Los valores de Open-Meteo son medias de la hora anterior al timestamp;
        # usamos el punto medio del intervalo para calcular la posición solar.
        dt_local = datetime.fromisoformat(times[i]).replace(tzinfo=MADRID_TZ) - timedelta(minutes=30)
        elevation, sun_azimuth = _solar_position(lat, lon, dt_local)
        poa = _poa_irradiance(ghi, dni, dhi, tilt, azimuth, elevation, sun_azimuth)

        kw = (poa * kwp * efficiency) / 1000.0
        production.append(round(max(0.0, kw), 3))

    return production


# ── Perfil de consumo tipo ────────────────────────────────

def generate_consumption_profile(daily_kwh: float) -> list[float]:
    """Genera un perfil de consumo horario tipo residencial/pyme (en kW)."""
    # Perfil normalizado: porcentaje del consumo diario por hora
    profile = [
        2.5, 2.0, 1.8, 1.5, 1.5, 2.0,   # 00-05 (noche)
        3.0, 5.0, 5.5, 5.0, 4.5, 4.0,    # 06-11 (mañana)
        4.5, 5.0, 4.5, 4.0, 4.0, 5.0,    # 12-17 (mediodía/tarde)
        6.5, 7.0, 7.5, 7.0, 5.5, 3.5,    # 18-23 (noche punta)
    ]
    total = sum(profile)
    return [round((p / total) * daily_kwh, 3) for p in profile]


# ── Algoritmo de optimización ─────────────────────────────

def optimize(
    prices_mwh: list[float],
    solar_kw: list[float],
    consumption_kw: list[float],
    battery_kwh: float,
) -> list[HourlySlot]:
    """
    - Prioridad 1: autoconsumo directo (solar → consumo)
    - Prioridad 2: cargar batería con excedente solar (cronológico: no se
      puede cargar con sol que aún no ha salido)
    - Prioridad 3: descargar batería en las horas de déficit más caras del
      día, asignando la carga disponible por orden de precio pero respetando
      SIEMPRE cuánta energía se ha acumulado ya en ese punto del día (no se
      puede gastar por la mañana una carga que todavía no existe)
    - Prioridad 4: verter excedente a red
    - Prioridad 5: importar de red lo que falte
    """
    max_charge_rate = battery_kwh * 0.5 if battery_kwh > 0 else 0  # C/2 max
    export_price_factor = 0.06  # €/kWh compensación excedentes (media mercado)

    net_list = [solar_kw[h] - consumption_kw[h] for h in range(24)]  # +excedente / -déficit

    # Pasada 1 (cronológica): simula solo la carga con excedente solar, para
    # saber cuánta energía hay realmente disponible en cada momento del día.
    soc = 0.0
    soc_ceiling = [0.0] * 24  # energía acumulada hasta el final de cada hora, sin descargar
    charged_list = [0.0] * 24
    for h in range(24):
        net = net_list[h]
        if net >= 0 and battery_kwh > 0 and soc < battery_kwh:
            can_charge = min(net, max_charge_rate, battery_kwh - soc)
            soc += can_charge
            charged_list[h] = can_charge
        soc_ceiling[h] = soc

    # Pasada 2: reparte la descarga entre las horas de déficit por precio
    # descendente. Antes se fijaba un top-6 global por precio, lo que
    # desperdiciaba huecos en horas de madrugada donde la batería aún no
    # tenía carga (quedando fuera horas caras y sí alcanzables, como las
    # últimas de la noche). Ahora cada hora solo recibe lo que realmente
    # queda disponible en ese punto de la cronología.
    discharge_alloc = [0.0] * 24
    if battery_kwh > 0:
        deficit_hours = [h for h in range(24) if net_list[h] < 0]
        deficit_hours.sort(key=lambda h: prices_mwh[h], reverse=True)
        for h in deficit_hours:
            want = min(-net_list[h], max_charge_rate)
            if want <= 0:
                continue
            already_committed = sum(discharge_alloc[h2] for h2 in range(h + 1))
            available = max(0.0, soc_ceiling[h] - already_committed)
            discharge_alloc[h] = min(want, available)

    # Pasada 3: construye los tramos hora a hora con las decisiones ya tomadas
    slots = []
    soc = 0.0
    for h in range(24):
        price_kwh = prices_mwh[h] / 1000.0
        solar = solar_kw[h]
        demand = consumption_kw[h]
        net = net_list[h]
        action = "grid"
        savings = 0.0

        if net >= 0:
            action = "solar"
            savings = demand * price_kwh  # todo el consumo cubierto por solar

            surplus = net
            charged = charged_list[h]
            soc += charged
            surplus -= charged
            if charged > 0:
                action = "battery_charge"

            if surplus > 0:
                savings += surplus * export_price_factor
                if charged == 0:
                    action = "export"
        else:
            solar_covered = solar
            discharged = discharge_alloc[h]
            soc -= discharged
            if discharged > 0:
                action = "battery_discharge"
            savings = (solar_covered + discharged) * price_kwh

        slots.append(HourlySlot(
            hour=h,
            price_eur_mwh=round(prices_mwh[h], 2),
            price_eur_kwh=round(price_kwh, 4),
            solar_kw=round(solar, 3),
            consumption_kw=round(demand, 3),
            action=action,
            battery_soc_kwh=round(soc, 2),
            savings_eur=round(savings, 4),
        ))

    return slots


# ── Endpoints ─────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "open_meteo_calls_today": _open_meteo_usage["count"],
        "open_meteo_date": _open_meteo_usage["date"],
    }


@app.post("/api/optimize", response_model=OptimizationResult)
async def api_optimize(
    params: InstallationParams,
    target_date: Optional[str] = Query(None, description="Fecha YYYY-MM-DD (default: mañana)")
):
    if target_date:
        td = date.fromisoformat(target_date)
    else:
        td = date.today() + timedelta(days=1)

    # Obtener datos reales
    prices = await fetch_esios_prices(td)
    solar = await fetch_solar_forecast(
        params.latitude, params.longitude,
        params.kwp, params.tilt, params.azimuth, td
    )
    consumption = generate_consumption_profile(params.daily_consumption_kwh)

    # Optimizar
    slots = optimize(prices, solar, consumption, params.battery_kwh)

    # Calcular totales
    total_savings = sum(s.savings_eur for s in slots)
    total_export = sum(
        max(0, s.solar_kw - s.consumption_kw) for s in slots if s.action in ("export",)
    )
    total_export_revenue = total_export * 0.06
    total_grid = sum(
        max(0, s.consumption_kw - s.solar_kw) for s in slots
        if s.action in ("grid",)
    )
    total_grid_cost = sum(
        max(0, s.consumption_kw - s.solar_kw) * (s.price_eur_mwh / 1000)
        for s in slots if s.action in ("grid",)
    )
    total_no_optim = sum(
        s.consumption_kw * (s.price_eur_mwh / 1000) for s in slots
    )
    total_self = sum(min(s.solar_kw, s.consumption_kw) for s in slots)

    return OptimizationResult(
        date=str(td),
        location=f"{params.latitude:.2f}, {params.longitude:.2f}",
        kwp=params.kwp,
        battery_kwh=params.battery_kwh,
        hours=slots,
        total_savings_eur=round(total_savings, 2),
        total_export_revenue_eur=round(total_export_revenue, 2),
        total_grid_cost_eur=round(total_grid_cost, 2),
        total_grid_cost_no_optim_eur=round(total_no_optim, 2),
        total_solar_self_consumed_kwh=round(total_self, 2),
        total_exported_kwh=round(total_export, 2),
        total_grid_imported_kwh=round(total_grid, 2),
    )


@app.get("/api/prices/{target_date}")
async def api_prices(target_date: str):
    """Devuelve precios horarios del pool para una fecha."""
    td = date.fromisoformat(target_date)
    prices = await fetch_esios_prices(td)
    return {"date": str(td), "prices_eur_mwh": prices}


@app.get("/api/solar/{target_date}")
async def api_solar(
    target_date: str,
    lat: float = Query(...),
    lon: float = Query(...),
    kwp: float = Query(5.0),
    tilt: float = Query(30.0),
    azimuth: float = Query(180.0),
):
    """Devuelve predicción de producción solar horaria."""
    td = date.fromisoformat(target_date)
    solar = await fetch_solar_forecast(lat, lon, kwp, tilt, azimuth, td)
    return {"date": str(td), "production_kw": solar}


# ── Servir frontend ──────────────────────────────────────

@app.get("/")
async def serve_landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request, "active": "landing"})

@app.get("/optimizar")
async def serve_optimizar(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "active": "optimizar"})

@app.get("/tutoriales")
async def serve_tutoriales(request: Request):
    return templates.TemplateResponse("tutoriales.html", {"request": request, "active": "tutoriales"})

@app.get("/impacto")
async def serve_impacto(request: Request):
    return templates.TemplateResponse("impacto.html", {"request": request, "active": "impacto"})

@app.get("/blog")
async def serve_blog_index(request: Request):
    return templates.TemplateResponse("blog_index.html", {"request": request, "active": "blog", "posts": BLOG_POSTS})

@app.get("/blog/{slug}")
async def serve_blog_post(request: Request, slug: str):
    post = next((p for p in BLOG_POSTS if p["slug"] == slug), None)
    if not post:
        raise HTTPException(status_code=404, detail="Artículo no encontrado")
    return templates.TemplateResponse("blog_post.html", {"request": request, "active": "blog", "post": post})

@app.get("/instaladores")
async def serve_instaladores(request: Request):
    return templates.TemplateResponse("instaladores.html", {"request": request, "active": "instaladores"})

@app.get("/privacidad")
async def serve_privacidad(request: Request):
    return templates.TemplateResponse("legal_privacidad.html", {"request": request, "active": "privacidad"})

@app.get("/aviso-legal")
async def serve_aviso_legal(request: Request):
    return templates.TemplateResponse("legal_aviso.html", {"request": request, "active": "aviso-legal"})


class InstallerLead(BaseModel):
    nombre: str
    email: str
    tipo: str
    mensaje: Optional[str] = None


LEADS_FILE = os.getenv("LEADS_FILE", "instaladores_leads.json")


@app.post("/api/instaladores")
async def submit_installer_lead(lead: InstallerLead):
    leads = []
    if os.path.exists(LEADS_FILE):
        with open(LEADS_FILE) as f:
            leads = json.load(f)
    leads.append({**lead.model_dump(), "received_at": datetime.utcnow().isoformat()})
    with open(LEADS_FILE, "w") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)
    return {"ok": True}

@app.get("/robots.txt")
async def robots_txt(request: Request):
    body = f"User-agent: *\nAllow: /\nSitemap: {request.base_url}sitemap.xml\n"
    return Response(content=body, media_type="text/plain")


@app.get("/sitemap.xml")
async def sitemap_xml(request: Request):
    base = str(request.base_url)
    static_pages = [
        ("", "1.0"),
        ("optimizar", "0.9"),
        ("tutoriales", "0.8"),
        ("impacto", "0.7"),
        ("blog", "0.7"),
        ("instaladores", "0.5"),
        ("privacidad", "0.2"),
        ("aviso-legal", "0.2"),
    ]
    urls = [f"<url><loc>{base}{path}</loc><priority>{priority}</priority></url>" for path, priority in static_pages]
    for post in BLOG_POSTS:
        urls.append(
            f"<url><loc>{base}blog/{post['slug']}</loc><lastmod>{post['date']}</lastmod><priority>0.6</priority></url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(urls)
        + "</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


app.mount("/static", StaticFiles(directory="static"), name="static")
