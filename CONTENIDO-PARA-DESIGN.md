# CronoSol — Brief de contenido (solo texto, sin diseño)

Contenido real ya escrito para las 3 páginas actuales. Úsalo como base de copy en Claude Design — la paleta y el tono visual ya están decididos aparte, esto es solo qué dice cada sitio.

---

## Página / (Landing)

**Nav:** Inicio · Cómo funciona · Alertas · Impacto · Tutoriales · Calculadora

**Kicker (encima del titular):** Guía offline · Autoconsumo solar

**Titular:** Vive al ritmo del sol, no del contador.

**Subtítulo:** CronoSol cruza el precio real del mercado eléctrico con la previsión de tu producción solar, y te dice hora a hora cuándo cargar la batería, cuándo descargarla y cuándo verter excedentes.

**Mini-calculadora del hero:**
- Título: Calcula tu ahorro en 10 segundos
- Campos: Tu localidad o código postal / Paneles (kWp) / Batería (kWh)
- Botón: Calcular ahorro anual estimado
- Resultado: dos cifras lado a lado — "€ ahorro anual estimado" y "kg CO₂ evitados al año"
- Link de resultado: Ver plan detallado de hoy, hora a hora →
- Nota bajo la calculadora: Sin registro. Datos reales de ESIOS/REE y Open-Meteo.

**Sección "Cómo funciona" (kicker: Cómo funciona · título: Tres datos, un plan hora a hora):**
1. **Configura tu instalación** — Ubicación (o compártela desde Telegram), paneles instalados y capacidad de tu batería. Nada de facturas ni contratos que subir.
2. **Cruzamos precio y producción** — Precio horario real del pool eléctrico (ESIOS/REE) + tu producción solar prevista (Open-Meteo), hora a hora, para el día que quieras.
3. **Recibes el plan óptimo** — Cuándo autoconsumir, cuándo cargar batería con el excedente, cuándo descargarla porque el precio está caro, y cuándo verter a red.

**Sección "Así son las alertas" (kicker: Un mensaje, no cuatro · título: Así son las alertas de Telegram):**
- Mockup de un mensaje real de Telegram (contenido de ejemplo):
  - ☀️ Plan de hoy — 2026-07-22
  - 📍 Granollers | 5 kWp | 10 kWh batería
  - 💶 1,91 € de ahorro estimado
  - 🌍 ~1,5 kg CO₂ evitados — como no coger el coche 12 km
  - 09:00 🔋⬆️ Carga la batería — hay excedente solar
  - 19:00 🔋⬇️ Descarga la batería — precio alto (152 €/MWh)
  - 📊 Ver informe completo con gráficos
  - 🔧 Cómo programar tu Huawei FusionSolar
- Texto explicativo: Un solo mensaje por la mañana, no una alerta cada hora que nadie tiene tiempo de leer. Programas tu inversor una vez y te olvidas el resto del día. El ahorro en € y el CO₂ evitado van siempre juntos — y con enlace directo a cómo programar las franjas horarias según la marca de tu inversor.
- Botón: Probar el bot →

**Sección "Impacto" (kicker: Impacto primero · título: El ahorro es la prueba de que haces lo correcto):**
- Cifra 1: 180 € — Ahorro medio anual estimado para una instalación de 5 kWp + 10 kWh de batería en un hogar tipo
- Cifra 2: 340 kg — CO₂ evitado al año para esa misma instalación, por desplazar consumo de red a autoconsumo solar
- Equivalencias: 🚗 ≈ 2.600 km sin coger el coche · 🌳 ≈ 15 árboles creciendo un año
- Metodología: ¿De dónde sale este número? Multiplicamos los kWh que autoconsumes directamente del sol por el factor medio de emisión de la red eléctrica española (~0,19 kg CO₂/kWh) — es decir, la energía que ya no compras a la red porque la generas y usas tú mismo.
- Nota: Cifras orientativas — usa la calculadora de arriba para tu caso concreto. No es solo una herramienta de ahorro: es una herramienta climática.

**CTA final:**
- Título: Empieza gratis, sin registro
- Subtítulo: La calculadora completa y las alertas de Telegram son gratis siempre.
- Botones: Abrir la calculadora → / Alertas por Telegram

**Footer:** CronoSol — Slow Philosophy S.L. · Datos: ESIOS/REE, Open-Meteo

---

## Página /optimizar (Calculadora completa)

**Header:** Logo + "CronoSol" · badge "MVP Fase 1.5"

**Bloque Ubicación:**
- Campo: Código postal o localidad (placeholder "08401 o Granollers") + botón Buscar
- Campos: Latitud / Longitud
- Mapa interactivo (click para pinear)
- Hint sobre el mapa: Haz click en el mapa para pinear tu ubicacion

**Bloque Instalación:**
- Campos: Potencia paneles (kWp instalados) / Batería (kWh, 0 = sin batería) / Consumo diario (kWh/día medio) / Fecha (Día a optimizar)
- Configuración rápida (presets): "Piso con placas" / "Casa + batería" / "Casa grande" / "Pyme / Nave"
- Botón: Optimizar dia

**Mensaje de estado inicial:** Indica tu ubicacion, configura tu instalacion y pulsa Optimizar dia.

**Etiquetas de origen de datos:** ☀️ Solar: Open-Meteo (datos reales) · 💶 Precios: ESIOS/REE (datos reales)

**KPIs de resultado:**
- Ahorro estimado (€) — "X% menos vs. sin solar"
- Autoconsumo solar (kWh) — "Directamente del sol"
- Coste red (€) — "vs. X € sin solar"
- Excedente vertido (kWh) — "~X € compensación"

**Sección gráfico:** "Curvas — [ubicación] — [fecha]" + leyenda: Producción solar / Consumo / Precio pool / Batería SOC

**Sección alertas (destacada, borde ámbar):**
- Título: Fase 1.5 — Alertas Telegram que recibirias
- Texto: Por Telegram recibes un solo mensaje por la mañana con el plan del día (no una alerta por cada cambio de hora) — programas tu inversor una vez y te olvidas. Pruébalo: @Cronosolar_bot.
- Texto: 🔧 ¿No sabes cómo programar las franjas horarias en tu inversor? Guías paso a paso por marca →

**Tabla:** "Plan horario completo" — columnas: Hora / Precio / Solar / Consumo / Accion / Bateria / Ahorro

**Acciones (etiquetas usadas en toda la app):** Autoconsumo · Carga bateria · Descarga bateria · Vertido a red · Compra de red

---

## Página /tutoriales

**Kicker:** Configuración
**Título:** Cómo programar tu inversor
**Intro:** CronoSol te dice a qué hora conviene cargar o descargar la batería — pero quien ejecuta ese plan es tu inversor. Aquí tienes el enlace directo a la documentación oficial de cada fabricante para programar las franjas horarias.

**Aviso:** Los menús exactos varían según el modelo y la versión de firmware/app — por eso enlazamos siempre a la fuente oficial del fabricante en vez de describir pasos que pueden quedar desactualizados. Si algo no coincide con tu equipo, contacta con tu instalador.

**Tarjetas por marca (acordeón):**

1. **Huawei FusionSolar** — En la app FusionSolar, la función se llama "Scheduled Charging" (carga programada) dentro de los parámetros de batería. Enlaces: Scheduled Charging — FusionSolar App User Manual / Battery Parameters — FusionSolar App User Manual.

2. **Fronius Solar.web** — Fronius llama a esta función "Time-of-Use" (control de batería por franjas horarias). No todos los inversores domésticos la traen activada de fábrica — si no la ves, consulta con tu instalador. Enlaces: Correctly Setting the Time-of-Use Storage (PDF oficial) / Time-of-Use Settings with Fronius Hybrid Inverters (PDF oficial).

3. **Victron VRM** — En Victron se configura como "Scheduled Charging" dentro del menú ESS del dispositivo GX, gestionado desde VRM. Enlaces: ESS: Scheduled Charging — Victron Energy / Control your devices in VRM — VRM Portal Manual.

4. **Otra marca / genérico** — Busca en el manual o la app de tu inversor los términos "Time of Use", "carga programada" o "gestión de batería" — la mayoría de inversores modernos con batería permiten programar cuándo cargar y descargar según el reloj. Si no encuentras la opción, contacta con tu instalador: puede estar bloqueada de fábrica. ¿Nos cuentas qué marca tienes? Escríbenos al bot con /marca <nombre> y añadiremos su enlace oficial aquí.

**CTA final:** ¿Aún no tienes tu plan de hoy? → Abrir la calculadora →

---

## Bot de Telegram (@Cronosolar_bot) — para referencia de tono conversacional

**/start:** ☀️ CronoSol — Optimiza tu autoconsumo solar. Te hago 5 preguntas rápidas y ya está. Puedes saltarte cualquiera escribiendo /plan para usar valores por defecto. Primero: ¿dónde está tu instalación? Comparte tu ubicación o escribe el nombre de tu localidad.

**Preguntas del onboarding:**
- ☀️ ¿Cuántos kWp tienes instalados?
- 🔋 ¿Capacidad de tu batería en kWh? (escribe 0 si no tienes)
- ⚡ ¿Tu consumo medio diario en kWh?
- 🔌 ¿Qué marca de inversor tienes — Huawei, Fronius, Victron u otra?

**Mensaje final del plan:** incluye ahorro en €, CO₂ evitado + equivalencia en km, cambios de acción hora a hora, enlace al informe web y enlace al tutorial de la marca de inversor.
