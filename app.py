"""
SolOptim — Optimizador de autoconsumo solar + batería
Backend FastAPI: conecta con ESIOS (precios pool) y Open-Meteo (radiación solar),
calcula el plan óptimo de carga/descarga y estima el ahorro.
"""

import os
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

ESIOS_TOKEN = os.getenv("ESIOS_TOKEN", "")
# Indicador 600: Precio mercado diario España (€/MWh)
ESIOS_INDICATOR = 600

app = FastAPI(title="SolOptim API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Modelos ──────────────────────────────────────────────

class InstallationParams(BaseModel):
    latitude: float
    longitude: float
    kwp: float  # kW pico instalados
    battery_kwh: float = 0.0  # capacidad batería (0 = sin batería)
    daily_consumption_kwh: float = 10.0  # consumo medio diario
    tilt: float = 30.0  # inclinación paneles
    azimuth: float = 180.0  # orientación (180 = sur)


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

    prices = []
    for h in range(24):
        vals = hourly.get(h, [])
        prices.append(sum(vals) / len(vals) if vals else 0.0)

    return prices


# ── Open-Meteo: predicción solar ─────────────────────────

async def fetch_solar_forecast(
    lat: float, lon: float, kwp: float, tilt: float, azimuth: float, target_date: date
) -> list[float]:
    """Devuelve 24 valores de producción solar estimada en kW para cada hora."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "shortwave_radiation",
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
    radiation = data.get("hourly", {}).get("shortwave_radiation", [])

    # Convertir W/m² → kW producido según kWp instalado
    # Factor simplificado: producción = radiación × kWp × rendimiento / 1000
    # Rendimiento típico ~0.80 (pérdidas por temperatura, cableado, inversor)
    efficiency = 0.80
    production = []
    for r in radiation[:24]:
        r = r or 0
        kw = (r * kwp * efficiency) / 1000.0
        production.append(round(kw, 3))

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
    Algoritmo greedy de optimización:
    - Prioridad 1: autoconsumo directo (solar → consumo)
    - Prioridad 2: cargar batería con excedente solar
    - Prioridad 3: descargar batería en horas caras
    - Prioridad 4: verter excedente a red
    - Prioridad 5: importar de red lo que falte
    """
    soc = 0.0  # Estado de carga actual
    max_charge_rate = battery_kwh * 0.5 if battery_kwh > 0 else 0  # C/2 max
    export_price_factor = 0.06  # €/kWh compensación excedentes (media mercado)

    # Primero: identificar las horas más caras para descarga de batería
    price_ranking = sorted(range(24), key=lambda h: prices_mwh[h], reverse=True)
    discharge_hours = set(price_ranking[:6]) if battery_kwh > 0 else set()

    slots = []
    for h in range(24):
        price_kwh = prices_mwh[h] / 1000.0
        solar = solar_kw[h]
        demand = consumption_kw[h]
        net = solar - demand  # positivo = excedente, negativo = déficit
        action = "grid"
        savings = 0.0
        charged = 0.0
        discharged = 0.0
        exported = 0.0
        grid_import = 0.0

        if net >= 0:
            # Hay excedente solar
            action = "solar"
            savings = demand * price_kwh  # todo el consumo cubierto por solar

            surplus = net
            # Cargar batería con excedente
            if battery_kwh > 0 and soc < battery_kwh:
                can_charge = min(surplus, max_charge_rate, battery_kwh - soc)
                soc += can_charge
                surplus -= can_charge
                charged = can_charge
                if can_charge > 0:
                    action = "battery_charge"

            # Verter el resto a red
            if surplus > 0:
                exported = surplus
                savings += surplus * export_price_factor
                if charged == 0:
                    action = "export"
        else:
            # Déficit: consumo > producción solar
            solar_covered = solar
            deficit = -net

            # Intentar cubrir con batería si es hora cara
            if battery_kwh > 0 and h in discharge_hours and soc > 0:
                can_discharge = min(deficit, max_charge_rate, soc)
                soc -= can_discharge
                deficit -= can_discharge
                discharged = can_discharge
                action = "battery_discharge"
                savings = (solar_covered + discharged) * price_kwh
            else:
                savings = solar_covered * price_kwh

            grid_import = deficit

        slots.append(HourlySlot(
            hour=h,
            price_eur_mwh=round(prices_mwh[h], 2),
            price_eur_kwh=round(prices_mwh[h] / 1000, 4),
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
    return {"status": "ok"}


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
async def serve_index():
    return FileResponse("static/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")
