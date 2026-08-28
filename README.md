# crema-viewer

Visor de finestres de crema prescrita per a Catalunya: combina observacions
XEMA dels darrers 10 dies amb previsió de 4 models meteorològics (Open-Meteo)
per identificar les hores que compleixen un pla de crema, ja sigui triant un
pla pre-carregat o consultant qualsevol punt manualment.

Publicat a `https://pauguarquec.github.io/crema-viewer/`.

## Arquitectura

- **`index.html`** — pàgina única (Leaflet + Chart.js autoallotjat + JS
  vanilla), servida per GitHub Pages. Tota la lògica de filtratge, càlcul de
  FMC1h, i avaluació del pla corre al navegador; no hi ha backend propi
  (l'única peça de servidor és el Cloudflare Worker, veure més avall).
- **`publish_xema_10d.py`** — llegeix els parquet de
  `/home/labfire/data/SMC-STATIONS/{YYYY}/{MM}/station={CODI}/` dels darrers
  10 dies per a totes les estacions XEMA actives, en fa un resample horari
  (convertint de UTC a hora local abans de qualsevol càlcul, veure secció
  "Fus horari") i publica `data/observacions_10d.json`.
- **`compute_plans_status.py`** — calcula la disponibilitat (verd/groc/
  vermell) de tots els plans amb ubicació coneguda, contra els 4 models
  (veure secció "Disponibilitat de plans / semàfor").
- **`publish.sh`** — wrapper per cron: corre `publish_xema_10d.py` i publica
  `observacions_10d.json`. Cron: **03:15 i 15:15 UTC** (coincidint amb quan
  es pengen les dades XEMA al servidor).
- **`publish_plans_status.sh`** — wrapper separat per `compute_plans_status.py`;
  corre més sovint (cron: **04, 07, 10, 13, 16, 19, 22 UTC**) perquè la
  previsió dels 4 models canvia diverses vegades al dia i el semàfor es
  quedaria desactualitzat si només es calculés un cop. Tots dos scripts fan
  `git pull` abans de `push`, per si el Worker (veure més avall) ha fet un
  commit directament mentre el clonatge de labfire estava desactualitzat.
- **`vendor/chart.umd.js`** — Chart.js autoallotjat (no es carrega des de cap
  CDN extern) per evitar problemes de xarxa/firewall.
- **`worker-afegir-pla.js`** — codi del Cloudflare Worker que publica
  automàticament els plans nous del formulari (veure secció corresponent).
- **`data/cremes_planificades.geojson`** i **`data/cremes_executades.geojson`**
  — polígons de parcel·les (planificades i executades), exportats des
  d'ArcGIS Pro/QGIS a partir de la capa de l'aplicació ArcGIS del Departament
  d'Interior. Simplificats (coordenades arrodonides a 5 decimals, sense
  coordenada Z, només camps rellevants) abans de publicar-los. **Nota**:
  l'exportador GeoJSON natiu d'ArcGIS Pro ("Features To JSON") va donar
  geometria `null` en aquest entorn; l'exportació via QGIS ("Desa entitats
  com...") va funcionar correctament — si mai cal regenerar-los, millor
  passar per QGIS directament.
- **`data/plans_llindars.json`** — llindars pre-carregats per pla de crema
  actiu (veure secció "Plans pre-carregats").
- **`data/plans_status.json`** — sortida de `compute_plans_status.py` (veure
  secció "Disponibilitat de plans / semàfor").

## Pestanyes

La pàgina té dues pestanyes que comparteixen els mateixos panells de
resultats (llindars, finestra marc, context, gràfics), amb **ordre
independent** per cada una:

- **Plans de crema actius**: selector de pla pre-carregat (agrupat per
  regió), botó **"+ Afegir PdC nou"** (veure secció del formulari). En triar
  un pla, es carreguen automàticament els llindars, es centra el mapa a la
  seva ubicació, i es calculen estacions properes + previsió. **El mapa
  queda bloquejat per a clics manuals** en aquesta pestanya (cursor
  "prohibit"). Ordre dels panells: previsió 5 dies → llindars → marc →
  context.
- **Consulta oberta**: pensada per incendis actius o punts sense pla formal.
  Clic lliure al mapa; els llindars/marc/context surten desplegats per
  defecte (cal editar-los a mà). Ordre dels panells: llindars → marc →
  context → previsió 5 dies.

El radi de cerca d'estacions (5–20 km, per defecte 20) és una única variable
compartida; els dos sliders (un a cada pestanya) es mantenen sincronitzats.

## Capes del mapa

Control flotant "Capes ▾" (cantonada superior dreta):

- **Cremes planificades** — color ambre uniforme; popup amb nom, àrea,
  resolució i vigència en clicar.
- **Cremes executades** — color marró uniforme; popup amb data i objectiu.
- **Estacions meteo (XEMA)** — punt petit, discret (semi-transparent, sense
  vora); reaprofita les metadades ja carregades a `observacions_10d.json`,
  sense cap fetch addicional.
- **Disponibilitat plans (5 dies)** — semàfor (verd/groc/vermell) precalculat
  per `compute_plans_status.py` (veure secció dedicada). Únic checkbox marcat
  per defecte; la resta s'activen manualment per no saturar la primera
  ullada al mapa.

**Basemap**: selector natiu de Leaflet (cantonada inferior esquerra) entre
"Clar" (CartoDB Positron) i "Topogràfic" (OpenTopoMap). **El basemap "Clar"
requereix una clau d'API de CARTO** des de finals d'agost 2026 (canvi de
política del proveïdor, gratuïta fins a 5M peticions/mes) — la clau viu a la
constant `CARTO_API_KEY` a `index.html`, restringida per domini
(`pauguarquec.github.io` + `localhost`) al tauler de CARTO. Sense clau vàlida
surt la marca d'aigua "API KEY REQUIRED" sobre el mapa (el mapa segueix
funcionant, només l'estètica es veu afectada).

## Disponibilitat de plans / semàfor

Als companys els interessava veure d'un cop d'ull, al mapa inicial, quins
plans tenen finestra real als propers 5 dies. Precalculat (no en directe)
perquè avaluar els 4 models × tots els plans a cada visita seria lent i
gastaria la quota d'Open-Meteo.

**Lògica del semàfor** (`compute_plans_status.py`, port fidel de la mateixa
lògica JS — cal mantenir els dos costats sincronitzats si es canvia algun
càlcul):
- Per cada un dels 4 models (mai `best_match`, ambigu): **verd** del model
  si ≥6 hores consecutives compleixen tot (llindar + context); **groc** si
  no arriba a verd però ≥6h consecutives compleixen només el llindar horari.
- Consens: 🟢 verd si ≥2 models donen verd; 🟡 groc si no arriba a verd però
  ≥2 models donen verd-o-groc; 🔴 vermell la resta.
- Només es calculen els plans amb `lat`/`lon` o `utm_x`/`utm_y` ja definits
  (no es cerca centroide al geojson per aquesta capa).

**En clicar una icona del semàfor**: selecciona automàticament el pla al
desplegable **i** el model de previsió que realment ha fet sortir aquell
color (prioritat fixa ICON-EU > ECMWF > AROME > GFS entre els que arriben a
6h — mai `best_match`, que podria mostrar una previsió diferent de la que
va generar el semàfor). Hover mostra un popup ràpid sense clicar.

**Els números poden variar entre el que veus al mapa i el que veus en
directe**: els models s'actualitzen diverses vegades al dia, així que el
semàfor és sempre "una foto de fa una estona" — per això es recalcula cada
poques hores (veure cron a dalt) i no un sol cop al dia.

## Variables i codis SMC

| Ús | Codi(s) | Prioritat / agregació |
|---|---|---|
| Temperatura | 32 (instantània) | mitjana horària |
| Humitat relativa | 33 (instantània) | mitjana horària |
| Vent (velocitat, escalar) | 30 (10 m) → 48 (6 m) → 46 (2 m) | primer disponible per estació, mitjana horària |
| Direcció del vent | 31/49/47 (aparellats amb la velocitat) | **mitjana vectorial** (mai aritmètica, per evitar l'error de wrap-around a prop de 0°/360°) |
| Radiació global | 36 | mitjana horària (per FMC1h) |
| Precipitació | 35 | **suma** horària (no mitjana) |

No totes les estacions tenen anemòmetre a 10 m; l'script detecta
automàticament quina alçada hi ha disponible per estació (i fa servir la
direcció aparellada a la mateixa alçada).

## Fus horari

Les dades cru del SMC són **UTC** (confirmat comparant el pic de radiació
amb el migdia solar real: 11:30 UTC = 13:30 local a l'agost, correcte).
`publish_xema_10d.py` les **converteix a hora local (Europe/Madrid)** just
després de llegir-les, abans de qualsevol resample — perquè coincideixi
exactament amb el format que ja fa servir Open-Meteo
(`timezone=Europe/Madrid`). Sense aquesta conversió, combinar observacions i
previsió al client desalineava totes les hores solapades per 1-2h (segons
horari d'estiu), no només un forat puntual.

**Marge residual assumit**: com que les observacions es publiquen 2 cops/dia
(03:15 i 15:15 UTC, cobrint com a molt fins fa unes hores) i la previsió
sempre arrenca a mitjanit local, hi ha una finestra de fins a ~12h on les
hores més recents d'avui es mostren amb previsió en lloc d'observació real,
fins al pròxim publish. Acceptat conscientment (previsió a curt termini sol
ser prou fidel); es podria escurçar publicant més sovint si mai calgués.

## Model FMC1h (humitat de combustible 1h)

El visor calcula en client (JS) una aproximació operativa del model de Nelson
per a combustible fi (1h): equilibrium moisture content horari (fórmules de
Simard 1968) corregit per radiació solar, integrat amb un temps de resposta
d'1h, més un salt d'humectació proporcional a la pluja horària (saturació
~35%). **No és el model de difusió complet de Nelson (2000)** — és una
simplificació d'equilibri+retard. Si es vol coherència exacta amb el
pipeline NFDRS4/Nelson d'ONOFRE, cal substituir la funció `calcFMC1h()` per
una crida a la implementació real.

El càlcul corre sobre una **sèrie horària contínua i alineada per marca
horària real** (no per índex de llista): uneix les observacions regionals
amb la previsió del punt clicat, deixant `null` explícit allà on falta una
hora real de dades. Als gràfics comparatius, aquests forats es dibuixen com
a tall a la línia (`spanGaps:false`), no com si les dades fossin
consecutives. **Quan una hora té tant observació real com previsió (les
hores ja passades d'avui, que Open-Meteo sempre inclou des de mitjanit),
l'observació real té prioritat** — la previsió només s'hi fa servir si no hi
ha cap dada real per aquella hora concreta.

## Finestra marc i context de dies previs

A banda de la finestra horària (temp/HR/vent/FMC1h), el visor avalua per a
cada dia candidat de previsió:

- **Finestra marc** (dia -1/0/+1): llindars de T màx / HR mín / vent màx,
  seguint el format del document oficial de plans de crema prescrita
  (Generalitat, Direcció General de Prevenció, Extinció d'Incendis i
  Salvaments). Quan falla, el missatge indica **exactament quin dia i quina
  variable** (p.ex. `marc (dia+1: Vent màx 8.2m/s > 5.6m/s)`), no només la
  paraula "marc".
- **Context previs**: precipitació acumulada als 10 dies previs; recuperació
  d'humitat nocturna com la **mitjana** (no el màxim) de la HR entre les
  **2:00 i les 6:00 locals** (5 lectures), comparada amb un llindar
  configurable; i nombre de dies amb vent fort a la darrera setmana.

Un dia només compta com a vàlid si la finestra marc **i** el context previs
es compleixen. A la franja horària i als gràfics comparatius, les hores es
marquen en tres estats: verd (dins de pla), groc/ambre (hora OK però context
no), gris (fora de pla) — el mateix resum de context es mostra sota cada dia
a la franja horària (el panell "Context" antic queda amagat, redundant).

**Nota important sobre camps buits**: un llindar buit es tracta com "sense
restricció" (±infinit a `readPlan()`), mai com `NaN` — un bug antic feia que
un sol llindar buit bloquegés totes les hores de tots els dies, encara que
la resta de condicions es complissin.

## Plans pre-carregats (`data/plans_llindars.json`)

```json
{
  "id_pla": "REC_2022_01",
  "nom": "Nom de la parcel·la",
  "regio": "REC",
  "lat": 41.5, "lon": 1.2,
  "utm_x": null, "utm_y": null, "utm_zone": 31,
  "finestra": {
    "temp":     { "baix": 10, "desitjat": 18, "alt": 24 },
    "hr":       { "baix": 25, "desitjat": 45, "alt": 70 },
    "vent_kmh": { "baix": 0,  "desitjat": 10, "alt": 20 },
    "fmc1h":    { "baix": 8,  "desitjat": 10, "alt": 12 }
  },
  "marc": {
    "dia_m1": { "tmax": null, "hrmin": 40, "vent_kmh_max": null },
    "dia_0":  { "tmax": 25,   "hrmin": 25, "vent_kmh_max": 25 },
    "dia_p1": { "tmax": 25,   "hrmin": 40, "vent_kmh_max": 20 }
  }
}
```

**Detalls importants:**
- Vent en **km/h** (com al PDF oficial); el visor el converteix a m/s.
- `baix`/`alt` no sempre estan en ordre ascendent (p.ex. a la HR, menys
  humitat = més risc, així que "Alt" pot ser numèricament més petit que
  "Baix") — el visor sempre agafa el mínim/màxim **real** dels dos valors,
  independentment de quin camp els contingui.
- Si només un dels dos (`baix`/`alt`) està definit, es respecta quin és
  (baix→mínim, alt→màxim) i l'altre queda sense restricció.
- Entrades amb `id_pla` buit s'ignoren al desplegable.
- **Ubicació**: prioritat `lat`/`lon` → `utm_x`/`utm_y` (ETRS89, zona per
  defecte 31N, conversió amb fórmules de Snyder) → centroide del polígon a
  `cremes_planificades.geojson` cercat per `id_pla` (només per al desplegable
  de plans; la capa del semàfor no fa aquesta cerca).

## Formulari "+ Afegir PdC nou" i Cloudflare Worker

Dins la pestanya "Plans de crema actius". Camps obligatoris: `id_pla`,
`nom`, `regio`, i com a mínim una parella d'ubicació completa (lat+lon o UTM
X+Y).

**Publicació automàtica** via un Cloudflare Worker (`worker-afegir-pla.js`,
desplegat per separat a Cloudflare, no forma part del build de la pàgina):
el formulari envia les dades del pla directament al Worker, que ell mateix
llegeix `plans_llindars.json` de GitHub, hi afegeix el pla, i fa el commit
— sense que cap token de GitHub passi mai pel navegador. Protegit per una
contrasenya compartida (la mateixa `AUTH_PASSWORD` del visor) com a segona
barrera, a banda del token de GitHub (fine-grained, només amb accés
d'escriptura a aquest repo concret).

**Si el Worker falla** per qualsevol motiu (xarxa, contrasenya incorrecta,
token caducat...), el formulari cau automàticament al mètode manual:
descarrega un `plans_llindars.json` complet i actualitzat per substituir al
repo a mà.

**Important**: com que el Worker pot fer commits directament a GitHub sense
passar pel clonatge de labfire, cal el `git pull` als scripts de cron
(ja inclòs) perquè no quedin rebutjats per "branques divergents". El semàfor
d'un pla afegit pel formulari no apareixerà fins al pròxim cicle de
`publish_plans_status.sh` (com a molt unes hores), ja que aquest botó només
publica el pla — no dispara cap recàlcul de disponibilitat.

## Gràfics comparatius (4 models)

Dins de "Finestra de crema — previsió 5 dies": 4 gràfics (Temperatura, HR,
Vent, FMC1h+Precipitació), cadascun amb:

- Línia negra: observat (últims 5 dies, mitjana regional).
- 4 línies de color: ICON-EU, ECMWF IFS HRES, GFS, AROME France (previsió 5 dies).
- Banda grisa ombrejada: llindar del pla (mín-màx), sense línia de contorn.
- Ombrejat verd/ambre per hora: mateix estat que la franja horària.
- **Marca "Avui"** a la frontera exacta obs/previsió.
- **Cursor sincronitzat**: passar el ratolí (o el dit, al mòbil) per un
  gràfic mostra un tooltip propi (no el natiu de Chart.js — poc fiable en
  tàctil) i dibuixa una línia vertical als altres tres alhora, per comparar
  variables. El tooltip surt per defecte a l'esquerra del cursor.
- Al gràfic de vent, **fletxes de direcció** a la part superior (observat:
  mitjana regional vectorial; previsió: del model seleccionat al
  desplegable, no els 4 alhora).
- Al gràfic de FMC1h, barres de precipitació en un eix Y secundari sense
  reservar espai visual (per no desalinear l'amplada respecte als altres
  tres). Escala **fixa** a 20 mm perquè es pugui comparar entre consultes.

Una sola crida a Open-Meteo obté els 4 models alhora
(`models=icon_eu,ecmwf_ifs,gfs_seamless,arome_france`); el FMC1h de cada
model es calcula per separat (Open-Meteo no proporciona aquesta variable).

**Model ECMWF**: es fa servir `ecmwf_ifs` (IFS HRES, resolució nativa ~9km),
no `ecmwf_ifs025` (0,25°/~25km) ni l'antic `ecmwf_ifs04` (obsolet). L'HRES a
resolució completa només és d'accés obert des de l'1 d'octubre de 2025.

## Mòbil i tauleta

Layout responsiu per sota de 820px d'amplada: mapa i panell apilats en
vertical (en lloc de costat a costat), formularis de graella col·lapsats a
1-2 columnes, zoom inicial del mapa una mica més allunyat (menys alçada
disponible). Els tooltips (franja horària i gràfics) es tanquen en tocar
fora, ja que en tàctil `mouseleave` mai es dispara sol; arrossegar el dit
per un gràfic actualitza el tooltip en temps real (amb `preventDefault` al
`touchmove` perquè el navegador no ho interpreti com a scroll de pàgina).

## Previsió (Open-Meteo)

Sense clau d'API, CORS habilitat, límit gratuït de 10.000 crides/dia (ús no
comercial). Models: `best_match`, `icon_eu`, `ecmwf_ifs`, `gfs_seamless`,
`arome_france`. La velocitat i direcció del vent que retorna l'API són en
km/h i graus respectivament; es converteixen a m/s al client (la direcció
es manté en graus).

**`best_match`** no és un model propi: Open-Meteo selecciona automàticament
el de més resolució disponible per la zona (pot barrejar-ne més d'un dins
del mateix horitzó de 5 dies), i **no indica quin ha fet servir** a la
resposta. Mai es fa servir per calcular el semàfor de disponibilitat, només
és l'opció per defecte al desplegable de la vista detallada.
