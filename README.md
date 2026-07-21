# SolOptim — Optimizador de autoconsumo solar

MVP Fase 1: recomendador + calculadora de ahorro.
Conecta con datos reales de ESIOS (precios pool eléctrico) y Open-Meteo (radiación solar).

## Setup rápido

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Configurar token ESIOS
cp .env.example .env
# Edita .env y pega tu token (solicítalo gratis en https://api.esios.ree.es/)

# 3. Arrancar
uvicorn app:app --host 0.0.0.0 --port 8000

# 4. Abrir http://localhost:8000
```

## Despliegue en servidor

Cualquier VPS con Python 3.10+:

```bash
git clone <repo> && cd soloptim
pip install -r requirements.txt
cp .env.example .env && nano .env  # pegar token ESIOS
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 2
```

Para producción, pon un nginx delante como reverse proxy al puerto 8000.

## Qué hace

1. Consulta los **precios horarios del pool eléctrico** de ESIOS/REE para el día seleccionado
2. Consulta la **predicción de radiación solar** de Open-Meteo para la ubicación del usuario
3. Estima la **producción solar hora a hora** según los kWp instalados
4. Genera un **perfil de consumo tipo** basado en el consumo diario declarado
5. Ejecuta un **algoritmo de optimización greedy** que decide para cada hora:
   - Autoconsumo directo (solar → consumo)
   - Carga de batería con excedente solar
   - Descarga de batería en horas caras
   - Vertido de excedente a red
   - Compra de red cuando no queda otra opción
6. Calcula el **ahorro estimado** vs. no tener solar/batería

## Fase 2 (pendiente)

Integración con API de inversores (Huawei FusionSolar, Fronius, Victron)
para ejecutar el plan de carga/descarga automáticamente.
