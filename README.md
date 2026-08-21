# crema-viewer

Visor de finestres de crema prescrita per a Catalunya: combina observacions
XEMA dels darrers 10 dies amb previsió de 4 models meteorològics (Open-Meteo)
per identificar les hores que compleixen un pla de crema, ja sigui triant un
pla pre-carregat o consultant qualsevol punt manualment.

Publicat a `https://pauguarquec.github.io/crema-viewer/` (protegit amb
contrasenya senzilla, veure secció corresponent).

## Arquitectura

- **`index.html`** — pàgina única (Leaflet + Chart.js autoallotjat + JS
  vanilla), servida per GitHub Pages. Tota la lògica de filtratge, càlcul de
  FMC1h, i avaluació del pla corre al navegador; no hi ha backend.
- **`publish_xema_10d.py`** — s'executa a `labfire.ctfc.cat` (usuari
  `pguarque`). Llegeix els parquet de
  `/home/labfire/data/SMC-STATIONS/{YYYY}/{MM}/station={CODI}/` dels darrers
  10 dies per a totes les estacions XEMA actives (segons el fitxer de
  metadades mensual més recent a `/home/labfire/data/SMC-STATIONS/metadata/`),
  en fa un resample horari i publica `data/observacions_10d.json`.
- **`publish.sh`** — wrapper per cron: executa el publicador amb l'intèrpret
  de l'entorn virtual (`/home/pguarque/graf_env/bin/python`, **no** és conda)
  i fa `git add`/`commit`/`push` si hi ha canvis.
- **`vendor/chart.umd.js`** — Chart.js autoallotjat (no es carrega des de cap
  CDN extern) per evitar problemes de xarxa/firewall.
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

## Pestanyes

La pàgina té dues pestanyes que comparteixen els mateixos panells de
resultats (llindars, finestra marc, context, gràfics), amb **ordre
independent** per cada una:

- **Plans de crema actius**: selector de pla pre-carregat (agrupat per
  regió). En triar un pla, es carreguen automàticament els llindars, es
  centra el mapa a la seva ubicació, i es calculen estacions properes +
  previsió. **El mapa queda bloquejat per a clics manuals** en aquesta
  pestanya (cursor "prohibit"). Ordre dels panells: previsió 5 dies →
  llindars → marc → context.
- **Consulta oberta**: pensada per incendis actius o punts sense pla formal.
  Clic lliure al mapa; els llindars/marc/context surten desplegats per
  defecte (cal editar-los a mà). Ordre dels panells: llindars → marc →
  context → previsió 5 dies.

El radi de cerca d'estacions (5–20 km, per defecte 20) és una única variable
compartida; els dos sliders (un a cada pestanya) es mantenen sincronitzats.

## Capes del mapa

Control flotant "Capes ▾" (cantonada superior dreta), amb tres capes
independents, totes visibles per defecte:

- **Cremes planificades** — color ambre uniforme; popup amb nom, àrea,
  resolució i vigència en clicar.
- **Cremes executades** — color marró uniforme; popup amb data i objectiu.
- **Estacions meteo (XEMA)** — icona de punt petit; reaprofita les metadades
  ja carregades a `observacions_10d.json`, sense cap fetch addicional.

**Basemap**: selector natiu de Leaflet (cantonada inferior esquerra) entre
"Clar" (CartoDB Positron) i "Topogràfic" (OpenTopoMap, útil per valorar
relleu/vegetació a l'hora de triar estacions o interpretar un punt).

## Contrasenya

La pàgina té una pantalla d'entrada amb contrasenya (`GRAF26`, definida a la
constant `AUTH_PASSWORD` dins `index.html`). **No és seguretat real** — el
codi és visible per qualsevol al navegador; només evita entrades accidentals
(enllaç compartit sense voler, indexació per cercadors). Es desa a
`localStorage` un cop encertada, no torna a demanar-se al mateix navegador.

## Variables i codis SMC

| Ús | Codi(s) | Prioritat / agregació |
|---|---|---|
| Temperatura | 32 (instantània) | mitjana horària |
| Humitat relativa | 33 (instantània) | mitjana horària |
| Vent (velocitat, escalar) | 30 (10 m) → 48 (6 m) → 46 (2 m) | primer disponible per estació, mitjana horària |
| Radiació global | 36 | mitjana horària (per FMC1h) |
| Precipitació | 35 | **suma** horària (no mitjana) |

No totes les estacions tenen anemòmetre a 10 m; l'script detecta
automàticament quina alçada hi ha disponible per estació. La direcció del
vent no es publica (no es fa servir al pla de crema).

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
hora real de dades (p.ex. un dia sense publicar encara). Als gràfics
comparatius, aquests forats es dibuixen com a tall a la línia
(`spanGaps:false`), no com si les dades fossin consecutives.

## Finestra marc i context de dies previs

A banda de la finestra horària (temp/HR/vent/FMC1h), el visor avalua per a
cada dia candidat de previsió:

- **Finestra marc** (dia -1/0/+1): llindars de T màx / HR mín / vent màx,
  seguint el format del document oficial de plans de crema prescrita
  (Generalitat, Direcció General de Prevenció, Extinció d'Incendis i
  Salvaments).
- **Context previs**: precipitació acumulada als 10 dies previs, nombre de
  nits consecutives sense recuperació d'humitat de matinada (HR de 00-09h
  per sota d'un llindar), i nombre de dies amb vent fort a la darrera
  setmana.

Un dia només compta com a vàlid si la finestra marc **i** el context previs
es compleixen. A la franja horària i als gràfics comparatius, les hores es
marquen en tres estats: verd (dins de pla), groc/ambre (hora OK però context
no), gris (fora de pla).

**Nota important sobre camps buits**: un llindar buit es tracta com "sense
restricció" (±infinit a `readPlan()`), mai com `NaN` — un bug antic feia que
un sol llindar buit bloquegés totes les hores de tots els dies, encara que
la resta de condicions es complissin.

## Plans pre-carregats (`data/plans_llindars.json`)

Fitxer editable a mà (o via el formulari "+ Afegir PdC nou" al visor) amb un
objecte per pla actiu:

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
- Entrades amb `id_pla` buit s'ignoren al desplegable (no cal esborrar-les,
  es poden anar omplint a poc a poc).
- **Ubicació**: prioritat `lat`/`lon` → `utm_x`/`utm_y` (ETRS89, zona per
  defecte 31N, conversió amb fórmules de Snyder) → centroide del polígon a
  `cremes_planificades.geojson` cercat per `id_pla` (només funciona si
  l'`id_pla` coincideix exactament amb el GeoJSON, que no cobreix plans molt
  recents).

**Formulari "+ Afegir PdC nou"**: dins la pestanya "Plans de crema actius",
genera i **descarrega** un `plans_llindars.json` complet i actualitzat (no
escriu al repo directament — GitHub Pages és estàtic, sense backend). Cal
substituir el fitxer al repo i fer `git push` manualment perquè es publiqui.
Camps obligatoris: `id_pla`, `nom`, `regio`, i com a mínim una parella
d'ubicació completa (lat+lon o UTM X+Y).

## Gràfics comparatius (4 models)

Dins de "Finestra de crema — previsió 5 dies": 4 gràfics (Temperatura, HR,
Vent, FMC1h+Precipitació), cadascun amb:

- Línia negra: observat (últims 5 dies, mitjana regional).
- 4 línies de color: ICON-EU, ECMWF IFS HRES, GFS, AROME France (previsió 5 dies).
- Banda grisa ombrejada: llindar del pla (mín-màx), sense línia de contorn.
- Ombrejat verd/ambre per hora: mateix estat que la franja horària (dins de
  pla / hora OK però context no).
- **Marca "Avui"** (línia sòlida) a la frontera exacta obs/previsió.
- **Cursor sincronitzat**: passar el ratolí per un gràfic dibuixa una línia
  vertical a la mateixa hora als altres tres, per comparar variables.
- Al gràfic de FMC1h, barres de precipitació en un eix Y secundari
  **sense reservar espai visual** (per no desalinear l'amplada respecte als
  altres tres gràfics) — les etiquetes de mm es dibuixen a sobre del propi
  gràfic. Escala **fixa** a 20 mm (no depèn de les dades) perquè es pugui
  comparar d'un cop d'ull entre consultes diferents.

Una sola crida a Open-Meteo obté els 4 models alhora
(`models=icon_eu,ecmwf_ifs025,gfs_seamless,arome_france`); el FMC1h de cada
model es calcula per separat (Open-Meteo no proporciona aquesta variable).

**Important**: el model ECMWF antic (`ecmwf_ifs04`, resolució 0,4°) va
quedar obsolet — Open-Meteo el va substituir per `ecmwf_ifs025` (0,25°).

## Cron (a labfire.ctfc.cat)

```
15 3 * * * /home/pguarque/cremes_viewer/publish.sh >> /home/pguarque/logs/crema-viewer.log 2>&1
```

Un sol cop al dia, a les 03:15 UTC (les dades XEMA es pengen al servidor a
les 3 UTC).

## Previsió (Open-Meteo)

Sense clau d'API, CORS habilitat, límit gratuït de 10.000 crides/dia (ús no
comercial). Models: `best_match`, `icon_eu`, `ecmwf_ifs025`, `gfs_seamless`,
`arome_france`. La velocitat de vent que retorna l'API és en km/h; es
converteix a m/s al client.

**`best_match`** no és un model propi: Open-Meteo selecciona automàticament
el de més resolució disponible per la zona (pot barrejar-ne més d'un dins
del mateix horitzó de 5 dies), i **no indica quin ha fet servir** a la
resposta. Recomanable evitar-lo per l'avaluació del pla si es vol saber amb
certesa quin model ha donat el resultat mostrat.

## Pendent / decisions obertes

- Si en el futur el pla de crema necessita direcció de vent, caldrà afegir-la
  fent mitjana **vectorial** (no aritmètica) per evitar l'error de wrap-around
  a prop de 0°/360°.
- Automatitzar l'alta de plans nous (Google Form + script que actualitzi
  `plans_llindars.json` sol via el mateix cron) — valorat i descartat de
  moment per volum d'ús baix; el formulari intern ja evita l'edició manual
  de JSON.
- Si mai cal tornar a exportar els GeoJSON de cremes, fer-ho via QGIS (no
  ArcGIS Pro "Features To JSON", que dona geometria `null` en aquest entorn).
