#!/usr/bin/env python3
"""
compute_backfill_cremes.py

Recalcula el bloc 'observat' (dia -1 + dia 0) per a les cremes marcades des
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
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

import compute_plans_status as cps
import publish_xema_10d as pub  # per llegir l'arxiu cru quan la finestra de 10 dies no arriba

REPO_DIR = Path(__file__).resolve().parent
CREMES_PATH = REPO_DIR / "data" / "cremes_realitzades.json"
OBSERVACIONS_PATH = REPO_DIR / "data" / "observacions_10d.json"

MIN_DAYS_OLD = 2  # com a compute_historic_stats.py: cal aquest marge perque les observacions siguin completes


def build_obs_from_archive(lat, lon, first_day, last_day):
    """Quan la crema es mes antiga que la finestra de 10 dies d'observacions_10d.json,
    llegeix directament l'arxiu cru de parquets (el mateix que fa servir
    publish_xema_10d.py) NOMES per a les estacions dins del radi i els dies
    first_day..last_day (+/- 1 dia de marge per la conversio UTC -> local).
    Retorna un dict amb el mateix format que observacions_10d.json."""
    meta = pd.read_parquet(pub.find_latest_metadata_file())
    meta = meta[meta["deactivation_date"].isna()]
    estacions = {
        r["station_code"]: {"nom": r["station_name"], "lat": float(r["latitude"]), "lon": float(r["longitude"])}
        for _, r in meta.iterrows()
    }
    nearby = cps.find_nearby_stations(lat, lon, {"estacions": estacions})
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
        if c.get("observat_complet"):
            continue  # ja completat en una execucio anterior

        data_crema = datetime.strptime(c["data_crema"], "%Y-%m-%d").date()
        if (today - data_crema).days < MIN_DAYS_OLD:
            continue  # dia 0 encara massa recent, esperem al proper dia

        dia_m1 = (data_crema - timedelta(days=1)).strftime("%Y-%m-%d")

        lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            print(f"  [AVIS] {c['id_pla']} ({c['data_crema']}): sense lat/lon guardats, no es pot completar")
            c["observat_complet"] = True  # marca com "intentat" per no reintentar-ho cada dia sense sentit
            updated += 1
            continue

        # Si la crema es mes antiga que la finestra de 10 dies (p.ex. el
        # servidor ha estat aturat), llegim l'arxiu cru en lloc de
        # observacions_10d.json. Es deixa 1 dia extra abans de dia -1
        # perque la cadena de FMC1h tingui un tram previ d'arrencada.
        finestra_inici = datetime.strptime(obs_data["dies"][0], "%Y-%m-%d").date()
        if data_crema - timedelta(days=2) < finestra_inici:
            print(f"  {c['id_pla']} ({c['data_crema']}): fora de la finestra de 10 dies, llegint l'arxiu cru...")
            font = build_obs_from_archive(lat, lon, data_crema - timedelta(days=2), data_crema)
        else:
            font = obs_data

        nearby = cps.find_nearby_stations(lat, lon, font)
        if not nearby:
            print(f"  [AVIS] {c['id_pla']} ({c['data_crema']}): cap estacio propera trobada")
            c["observat_complet"] = True
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
        hores = [h for h in serie_completa if dia_m1 <= h["time"][:10] <= c["data_crema"]]
        hores_dia_0 = [h for h in hores if h["time"].startswith(c["data_crema"])]

        if len(hores_dia_0) < 18:
            # Dia 0 encara no te prou hores dins la finestra rodant de 10
            # dies (p.ex. si el servidor ha fallat uns dies, com ja ha
            # passat) -- ho reintentarem el proper dia.
            print(f"  {c['id_pla']} ({c['data_crema']}): dia 0 nomes te {len(hores_dia_0)}h, esperant...")
            continue

        c["observat"] = hores
        c["observat_complet"] = True
        updated += 1
        print(f"  {c['id_pla']} ({c['data_crema']}): completat amb {len(hores)}h (dia -1 + dia 0)")

    if updated == 0:
        print("Cap crema per actualitzar.")
        return

    with open(CREMES_PATH, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
    print(f"\nActualitzades {updated} cremes a {CREMES_PATH}")


if __name__ == "__main__":
    main()

