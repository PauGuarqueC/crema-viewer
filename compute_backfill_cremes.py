#!/usr/bin/env python3
"""
compute_backfill_cremes.py

Recalcula el bloc 'observat' (dia -1 + dia 0 + dia +1) per a les cremes marcades des
del visor amb "Avui s'ha cremat aquí" -- en el moment de marcar-ho, dia 0
encara no te totes les hores publicades (les observacions es publiquen amb
retard, mai el mateix dia sencer). Aquest script, corregut diariament,
repassa el registre (data/cremes_realitzades.json) i recalcula sencer
aquest bloc per a qualsevol crema que encara tingui "observat_complet: false"
i que ja tingui prou dies perque dia 0 sigui complet.

Reaprofita find_nearby_stations / build_regional_hourly_obs de
compute_plans_status.py (mateix radi, mateixa mitjana regional que fa
servir el visor en directe).
"""

import json
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

import compute_plans_status as cps
import publish_xema_10d as pub  # per llegir l'arxiu cru quan la finestra de 10 dies no arriba

REPO_DIR = Path(__file__).resolve().parent
CREMES_PATH = REPO_DIR / "data" / "cremes_realitzades.json"
OBSERVACIONS_PATH = REPO_DIR / "data" / "observacions_10d.json"

MIN_DAYS_OLD = 3  # dia +1 ha d'estar acabat i amb l'arxiu ja complet (dia 0 + dia +1)
ERA5_MIN_DAYS = 6  # ERA5-Land te un retard d'uns 5 dies
ERA5_URL = "https://archive-api.open-meteo.com/v1/archive"
MAX_DAYS_RETRY = 10  # passat aquest temps, si dia +1 no arriba a tenir prou hores es deixa estar


def build_obs_from_archive(lat, lon, first_day, last_day):
    """Quan la crema es mes antiga que la finestra de 10 dies d'observacions_10d.json,
    llegeix directament l'arxiu cru de parquets (el mateix que fa servir
    publish_xema_10d.py) NOMES per a les estacions dins del radi i els dies
    first_day..last_day (+/- 1 dia de marge per la conversio UTC -> local).
    Retorna un dict amb el mateix format que observacions_10d.json."""
    meta = pd.read_parquet(pub.find_latest_metadata_file())
    meta = meta[meta["deactivation_date"].isna()]
    estacions = {
        r["station_code"]: {"nom": r["station_name"], "lat": float(r["latitude"]), "lon": float(r["longitude"]), "alt": int(r["altitude_m"])}
        for _, r in meta.iterrows()
    }
    nearby = cps.stations_in_radius(lat, lon, estacions)  # tot el radi; la seleccio es fa despres
    days = []
    d = first_day - timedelta(days=1)
    while d <= last_day + timedelta(days=1):
        days.append(d)
        d += timedelta(days=1)
    series = {}
    for code in nearby:
        res = pub.process_station(code, days)
        if res is not None:
            series[code] = res
    return {"estacions": {c: estacions[c] for c in series}, "series": series}


def build_30min(codes, sel, dia_m1, dia_p1):
    """Serie de 30 min (dia -1 .. dia +1, hora local) de cada estacio usada, llegida
    de l'arxiu cru. Es desa a la crema (camp 'observat_30min') nomes com a registre:
    el visor segueix fent servir 'observat' i 'observat_estacions' (horaris)."""
    noms = {x["code"]: x.get("nom", x["code"]) for x in sel}
    d0 = datetime.strptime(dia_m1, "%Y-%m-%d").date()
    d1 = datetime.strptime(dia_p1, "%Y-%m-%d").date()
    days = [d0 - timedelta(days=1) + timedelta(days=i) for i in range((d1 - d0).days + 3)]  # +/- 1 dia per UTC -> local
    out = {}
    for code in codes:
        try:
            res = pub.process_station_30min(code, days)
        except Exception as e:
            print(f"  [AVIS] {code}: no s'ha pogut llegir la serie de 30 min ({e})")
            continue
        if not res:
            continue
        idx = [i for i, t in enumerate(res["t"]) if f"{dia_m1}T00:00" <= t <= f"{dia_p1}T23:59"]
        if not idx:
            continue
        entry = {"nom": noms.get(code, code), "t": [res["t"][i] for i in idx]}
        for k in ("temp", "hr", "vent", "ventdir", "rad", "precip"):
            entry[k] = [res[k][i] for i in idx]
        out[code] = entry
    return out


def fetch_era5_land(lat, lon, data_crema):
    """ERA5-Land (reanalisi, ~9 km) per al punt de la crema, hora local, dia -1 .. dia +1.
    Es demana des de 2 dies abans de dia -1 perque el FMC 1h tingui un tram previ
    d'arrencada (mateix criteri que l'observat); despres es retalla.
    Retorna None si Open-Meteo encara no te les dades (retard d'ERA5)."""
    d_ini = (data_crema - timedelta(days=3)).strftime("%Y-%m-%d")
    d_fi = (data_crema + timedelta(days=1)).strftime("%Y-%m-%d")
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "start_date": d_ini, "end_date": d_fi,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,shortwave_radiation,precipitation",
        "models": "era5_land,era5", "wind_speed_unit": "ms", "timezone": "Europe/Madrid",
    }
    res = requests.get(ERA5_URL, params=params, timeout=60)
    res.raise_for_status()
    data = res.json()
    h = data["hourly"]

    fonts = {}

    def col(var, name):
        """Cada variable: ERA5-Land si en te dades; si no (Open-Meteo no serveix vent, radiacio
        ni pluja d'ERA5-Land a l'arxiu), ERA5 (~31 km) com a alternativa. Es desa la font usada."""
        n = len(h["time"])
        for model in ("era5_land", "era5"):
            for k in (f"{var}_{model}", var):
                v = h.get(k)
                if v is not None and any(x is not None for x in v):
                    fonts[name] = model
                    return v
        return [None] * n

    temp, hr, wind = col("temperature_2m", "temp"), col("relative_humidity_2m", "hr"), col("wind_speed_10m", "wind")
    wdir, rad, precip = col("wind_direction_10m", "windDir"), col("shortwave_radiation", "rad"), col("precipitation", "precip")
    serie = [{"time": t, "temp": temp[i], "hr": hr[i], "wind": wind[i], "windDir": wdir[i],
              "rad": rad[i], "precip": precip[i]} for i, t in enumerate(h["time"])]
    for row, fmc in zip(serie, cps.calc_fmc1h(serie)):
        row["fmc"] = fmc
    dia_m1 = (data_crema - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = [r for r in serie if dia_m1 <= r["time"][:10] <= d_fi]
    if len(rows) < 48 or all(r["temp"] is None for r in rows):
        return None  # encara no disponible
    out = {k: [r[k] for r in rows] for k in ("time", "temp", "hr", "wind", "windDir", "rad", "precip", "fmc")}
    out["elevacio_m"] = data.get("elevation")
    out["fonts"] = fonts  # model usat per a cada variable: era5_land o era5
    out["fmc_amb_rad_i_pluja"] = fonts.get("rad") is not None and fonts.get("precip") is not None
    return out


def add_era5(cremes, today):
    """Afegeix el camp 'era5_land' (dia -1, 0 i +1, horari) a les cremes que encara no el tenen."""
    n = 0
    for c in cremes:
        if c.get("era5_v") == 2:
            continue  # v2: ERA5-Land amb alternativa ERA5 per a vent/radiacio/pluja
        lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        data_crema = datetime.strptime(c["data_crema"], "%Y-%m-%d").date()
        if (today - data_crema).days < ERA5_MIN_DAYS:
            continue
        try:
            res = fetch_era5_land(lat, lon, data_crema)
        except Exception as e:
            print(f"  [AVIS] {c['id_pla']} ({c['data_crema']}): ERA5-Land no disponible ({e})")
            continue
        if res is None:
            if (today - data_crema).days > MAX_DAYS_RETRY:
                c["era5_land"] = {}  # no n'hi ha i ja no n'hi haura
                c["era5_v"] = 2
                n += 1
            continue
        c["era5_land"] = res
        c["era5_v"] = 2
        n += 1
        print(f"  {c['id_pla']} ({c['data_crema']}): ERA5-Land afegit ({len(res['time'])}h)")
    return n


def main():
    if not CREMES_PATH.exists():
        print(f"{CREMES_PATH} no existeix encara -- cap crema marcada, res a fer.")
        return

    with open(CREMES_PATH, encoding="utf-8") as f:
        content = json.load(f)
    cremes = content.get("cremes", [])
    if not cremes:
        print("Cap crema registrada.")
        return

    with open(OBSERVACIONS_PATH, encoding="utf-8") as f:
        obs_data = json.load(f)

    today = datetime.now().date()
    updated = 0

    for c in cremes:
        if c.get("observat_complet") and c.get("observat_dia1") and "observat_30min" in c:
            continue  # ja completat (dia -1, dia 0 i dia +1) en una execucio anterior

        data_crema = datetime.strptime(c["data_crema"], "%Y-%m-%d").date()
        if (today - data_crema).days < MIN_DAYS_OLD:
            continue  # dia 0 encara massa recent, esperem al proper dia

        dia_m1 = (data_crema - timedelta(days=1)).strftime("%Y-%m-%d")

        lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            print(f"  [AVIS] {c['id_pla']} ({c['data_crema']}): sense lat/lon guardats, no es pot completar")
            c["observat_complet"] = True  # marca com "intentat" per no reintentar-ho cada dia sense sentit
            c["observat_dia1"] = True
            updated += 1
            continue

        # Si la crema es mes antiga que la finestra de 10 dies (p.ex. el
        # servidor ha estat aturat), llegim l'arxiu cru en lloc de
        # observacions_10d.json. Es deixa 1 dia extra abans de dia -1
        # perque la cadena de FMC1h tingui un tram previ d'arrencada.
        finestra_inici = datetime.strptime(obs_data["dies"][0], "%Y-%m-%d").date()
        if data_crema - timedelta(days=2) < finestra_inici:
            print(f"  {c['id_pla']} ({c['data_crema']}): fora de la finestra de 10 dies, llegint l'arxiu cru...")
            font = build_obs_from_archive(lat, lon, data_crema - timedelta(days=2), data_crema + timedelta(days=1))
        else:
            font = obs_data

        sel = cps.select_stations(lat, lon, font)
        nearby = [x["code"] for x in sel]
        if not nearby:
            print(f"  [AVIS] {c['id_pla']} ({c['data_crema']}): cap estacio propera trobada")
            c["observat_complet"] = True
            c["observat_dia1"] = True
            updated += 1
            continue

        regional_obs = cps.build_regional_hourly_obs(nearby, font)
        # FMC1h calculat sobre TOT l'historic disponible (bona continuitat
        # de la cadena de decaïment), despres es retalla nomes al tram
        # dia_m1..data_crema -- mateix criteri que el JS al moment de marcar.
        serie_completa = [{"time": t, **v} for t, v in sorted(regional_obs.items())]
        fmc_vals = cps.calc_fmc1h(serie_completa)
        for h, fmc in zip(serie_completa, fmc_vals):
            h["fmc"] = fmc
        dia_p1 = (data_crema + timedelta(days=1)).strftime("%Y-%m-%d")
        hores = [h for h in serie_completa if dia_m1 <= h["time"][:10] <= dia_p1]
        hores_dia_0 = [h for h in hores if h["time"].startswith(c["data_crema"])]
        hores_dia_p1 = [h for h in hores if h["time"].startswith(dia_p1)]

        if c.get("observat_complet") and len(hores_dia_p1) < 18:
            # Crema ja completada abans (nomes dia -1 + dia 0): intentem afegir dia +1, pero sense tocar el que hi ha
            if (today - data_crema).days > MAX_DAYS_RETRY:
                c["observat_dia1"] = True  # no n'hi ha i ja no n'hi haura
                c.setdefault("observat_30min", {})
                updated += 1
            continue

        if len(hores_dia_0) < 18 or len(hores_dia_p1) < 18:
            # Dia 0 encara no te prou hores dins la finestra rodant de 10
            # dies (p.ex. si el servidor ha fallat uns dies, com ja ha
            # passat) -- ho reintentarem el proper dia.
            print(f"  {c['id_pla']} ({c['data_crema']}): dia 0 te {len(hores_dia_0)}h i dia +1 {len(hores_dia_p1)}h, esperant...")
            continue

        c["observat"] = hores
        c["estacions_usades"] = sel
        c["observat_estacions"] = cps.station_series_slice(
            nearby, font, f"{dia_m1}T00:00", dia_p1 + "T23:59")
        c["representativitat"] = cps.low_representativity(sel)
        c["sense_dades"] = cps.missing_variables(sel)
        c["dispersio"] = cps.station_dispersion(
            nearby, font, t_from=dia_m1, t_to=dia_p1 + "T23:59")
        c["observat_30min"] = build_30min(nearby, sel, dia_m1, dia_p1)
        c["observat_complet"] = True
        c["observat_dia1"] = True
        updated += 1
        print(f"  {c['id_pla']} ({c['data_crema']}): completat amb {len(hores)}h (dia -1 + dia 0 + dia +1)")

    updated += add_era5(cremes, today)

    if updated == 0:
        print("Cap crema per actualitzar.")
        return

    with open(CREMES_PATH, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
    print(f"\nActualitzades {updated} cremes a {CREMES_PATH}")


if __name__ == "__main__":
    main()

