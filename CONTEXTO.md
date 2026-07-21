# CronoSol — Contexto completo del proyecto

## Qué es

Optimizador de autoconsumo solar + batería. Herramienta open source que dice al usuario cuándo cargar/descargar batería y cuándo verter excedentes, basándose en el precio horario del pool eléctrico (ESIOS/REE) y la predicción solar (Open-Meteo).

## Nombre

CronoSol (provisional, Salva lo está trabajando por su cuenta).

## Posicionamiento

"Impacto primero, ahorro como recompensa." No es solo una herramienta de ahorro — es una herramienta climática. El dinero ahorrado es la prueba de que estás haciendo lo correcto.

Equilibrio narrativo:
- Landing: el hero habla de sincronizarse con el sol, no de dinero. Pero el primer dato cuantificable es € ahorrados + kg CO₂ evitados, siempre juntos, mismo peso visual.
- Dashboard: € ahorrados y kg CO₂ evitados a la misma altura.
- Alertas: texto práctico ("descarga batería — precio alto"), resumen diario incluye CO₂ evitado.
- Blog: alterna artículos de ahorro práctico con artículos de impacto climático.
- Página /impacto: sostenibilidad al 100%, contador de comunidad, metodología CO₂, alineación ODS. Es la página para grants e instituciones.

## Fases del producto

### Fase 1 — Calculadora + plan visual (HECHO - MVP)
- Usuario introduce ubicación (mapa Leaflet interactivo, click para pinear, o código postal) + kWp + batería + consumo diario
- Llama a ESIOS (precios pool hora a hora) y Open-Meteo (radiación solar)
- Genera plan óptimo de carga/descarga con algoritmo greedy
- Muestra KPIs (ahorro €, CO₂ evitado, autoconsumo, excedente), gráfico SVG, tabla horaria

### Fase 1.5 — Alertas en tiempo real
- Alertas cuando cambia la acción recomendada ("ahora carga batería", "ahora descarga")
- Canales gratuitos: Telegram bot + email + PWA push notifications
- WhatsApp: solo si hay demanda, cobrando 1€/mes al usuario. Si hay demanda se desarrolla.
- Bot de Telegram ya construido (telegram_bot.py)

### Fase 2 — Automatización hardware (futuro)
- Integración con API de inversores (Huawei FusionSolar, Fronius, Victron, Enphase)
- El plan se ejecuta automáticamente sin intervención del usuario
- Aquí se monetiza: suscripción mensual

## Doble UX

1. **Web** — dashboard completo, calculadora, resultados, blog, página de impacto
2. **Chatbot** (Telegram + WhatsApp futuro) — canal de captación y retención. Onboarding en 5 mensajes (compartir ubicación nativa + datos instalación), genera link al resultado en la web, envía alertas diarias.

## Estrategia de precios

- **Gratis**: calculadora + alertas (Telegram/email/push). Siempre gratis.
- **Plan Pro (4,99€/mes)**: automatización con inversor + historial de ahorro real + informe mensual. (Fase 2)
- **Plan Instalador (49€/mes)**: gestiona hasta 50 instalaciones, dashboard multi-planta, marca blanca. (Fase 2)
- **WhatsApp alerts (1€/mes)**: solo si hay demanda validada.

## Estructura web prevista

```
cronosol.es (o el dominio final)
│
├── / (landing)
│   Hero: sincronizarse con el sol, no dinero
│   Calculadora simplificada inline → ahorro estimado anual
│   Cómo funciona (3 pasos)
│   Cifras de ahorro + impacto
│   CTA: "Empieza gratis"
│
├── /optimizar (herramienta completa)
│   Mapa + config + resultados
│   Acceso libre sin registro
│
├── /blog (SEO)
│   Alternar ahorro práctico + impacto climático
│
├── /precios
│   Gratis / Pro / Instalador
│
├── /impacto
│   Contador comunidad, CO₂ evitado, equivalencias
│   Metodología, ODS, para grants
│
└── /instaladores (landing B2B)
    Propuesta para integradores solares
```

## Plan de financiación

### Pista 1 (3-6 meses): producto + usuarios + pilotos
- Subir MVP con datos reales
- Primeros 100 usuarios en alertas
- Contactar 2-3 comunidades energéticas locales (directorio IDAE)
- API para instaladores: ingresos modestos

### Pista 2 (6-12 meses): grants pre-aceleración
- EIT Jumpstarter 2027 (categoría Energy & Renewables, EIT InnoEnergy)
- Clean Cities Spain ClimAccelerator 2027 (Climate-KIC + UPM)
- Ambos piden: idea validada con tracción, open source, impacto climático medible

### Más adelante
- LIFE CSA energía limpia (85,5M€, plazo sept cada año)
- IDAE CE Implementa (via alianza con comunidad energética)
- Horizonte Europa Clúster 5 (requiere consorcio 3 países)

## Entidad legal

Slow Philosophy S.L. (HQ Islas Baleares) — misma entidad que AgroScoring.

## APIs y datos

### ESIOS/REE (precios pool eléctrico)
- API REST, token gratuito solicitado por email a consultasios@ree.es
- Token ya obtenido por Salva
- Indicador 600: precio mercado diario España (€/MWh)
- Publica cada día ~14h precios D+1
- Necesita backend (no funciona directo desde navegador por CORS + auth)

### Open-Meteo (predicción solar)
- API REST gratuita, sin auth, CORS habilitado
- Funciona directamente desde el navegador
- Endpoint: api.open-meteo.com/v1/forecast
- Parámetro: shortwave_radiation (W/m²)
- Conversión: producción_kW = (radiación × kWp × 0.80) / 1000

### Nominatim (geocoding)
- API gratuita de OpenStreetMap
- Acepta código postal o nombre de localidad
- Sin auth, funciona desde navegador
- Rate limit: 1 req/segundo

## Stack técnico

- Backend: Python + FastAPI + httpx
- Frontend: HTML/CSS/JS vanilla + Leaflet (mapa)
- Telegram bot: Python puro con httpx (sin librería telegram)
- Persistencia usuarios: JSON (MVP), migrar a DB cuando escale
- Despliegue: cualquier VPS con Python 3.10+ / o PaaS (Railway, Render)

## Competencia analizada

### Alertas subastas BOE (descartado, saturado)
MapaSubastas, AlertaSubastas, SubastasIA, MundoSubastas, SubastaFácil

### Optimización energética autoconsumo (el espacio elegido)
- No hay producto autoservicio que cruce precio pool + predicción solar + batería para pyme/residencial
- PV-Maps: solo plantas grandes
- Apps de inversores (Huawei, Fronius): monitorización sin optimización de mercado
- Comercializadoras: incentivo opuesto (que consumas más, no menos)

## Perfil del fundador

Salvador Martínez (Salva). Product manager en Raona (consultora Microsoft, Barcelona). Cofundador de AgroScoring (scoring de fincas agrícolas). En transición profesional hacia producto digital de impacto (climate/social tech). Certificación en innovación social. Formándose en teoría del cambio e IMM (Acumen Academy, grupo Slow Impact).
