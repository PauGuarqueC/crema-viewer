# crema-viewer

Visor de finestres de crema prescrita: combina observacions XEMA dels darrers
10 dies (radi 20 km al voltant d'un punt) amb previsió Open-Meteo (5 dies) per
identificar les hores que compleixen un pla de crema definit per l'usuari
(temperatura, humitat relativa, vent).

## Arquitectura

- **`index.html`** — pàgina única (Leaflet + JS vanilla), servida per GitHub
  Pages. En clicar un punt del mapa:
  - filtra `data/observacions_10d.json` per estacions dins 20 km (haversine
    en client) i en calcula la mitjana,
  - crida directament `api.open-meteo.com` amb les coordenades del punt
    (sense passar per cap servidor propi),
  - avalua el pla de crema hora a hora i pinta la franja de 5 dies.
- **`publish_xema_10d.py`** — s'executa a `labfire.ctfc.cat`. Llegeix els
  parquet de `/home/labfire/data/SMC-STATIONS/{YYYY}/{MM}/station={CODI}/`
  dels darrers 10 dies per a totes les estacions XEMA actives (segons el
  fitxer de metadades mensual més recent), en fa un resample horari i
  publica `data/observacions_10d.json`.
- **`publish.sh`** — wrapper per cron: executa el publicador i fa
  `git push` si hi ha canvis. Mateix patró que `echotops-viewer`.

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
simplificació d'equilibri+retard, habitual en aplicacions operatives
lleugeres. Si es vol coherència exacta amb el pipeline NFDRS4/Nelson
d'ONOFRE, cal substituir la funció `calcFMC1h()` a `index.html` per una
crida a la implementació real (probablement caldria moure aquest càlcul al
servidor, ja que la implementació completa no és trivial de portar a JS).

El càlcul corre sobre una **sèrie contínua** que uneix els 10 dies
d'observacions (mitjana regional de les estacions dins 20 km) amb els 5 dies
de previsió Open-Meteo del punt clicat, per tal que el valor de FMC del
primer dia de previsió arrenqui de l'estat real i no d'un valor arbitrari.

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
es compleixen; les hores d'aquell dia que compleixen la finestra horària
però no el context es marquen visualment de forma diferenciada (color
d'avís) a la franja horària.

## Cron (a labfire.ctfc.cat)

```
# Actualitza 2 cops al dia (ajustar segons freqüència d'interès)
20 5,17 * * * /home/labfire/crema-viewer/publish.sh >> /home/labfire/logs/crema-viewer.log 2>&1
```

## Previsió (Open-Meteo)

Sense clau d'API, CORS habilitat, límit gratuït de 10.000 crides/dia (ús no
comercial). Model seleccionable des de la UI: `best_match`, `icon_eu`,
`ecmwf_ifs04`, `gfs_seamless`, `arome_france`. La velocitat de vent que
retorna l'API és en km/h; es converteix a m/s al client per ser coherent amb
les observacions XEMA.

## Pendent / decisions obertes

- Freqüència del cron de publicació (proposta: 2 cops/dia, com onofre-viewer).
- Si en el futur el pla de crema necessita direcció de vent, caldrà afegir-la
  fent mitjana **vectorial** (no aritmètica) per evitar l'error de wrap-around
  a prop de 0°/360°.
- Domini/subdomini final per publicar-ho a GitHub Pages.
