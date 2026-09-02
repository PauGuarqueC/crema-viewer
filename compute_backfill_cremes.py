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
from datetime import datetime, timedelta
from pathlib import Path

import compute_plans_status as cps

REPO_DIR = Path(__file__).resolve().parent
CREMES_PATH = REPO_DIR / "data" / "cremes_realitzades.json"
OBSERVACIONS_PATH = REPO_DIR / "data" / "observacions_10d.json"

MIN_DAYS_OLD = 2  # com a compute_historic_stats.py: cal aquest marge perque les observacions siguin completes


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

        nearby = cps.find_nearby_stations(lat, lon, obs_data)
        if not nearby:
            print(f"  [AVIS] {c['id_pla']} ({c['data_crema']}): cap estacio propera trobada")
            c["observat_complet"] = True
            updated += 1
            continue

        regional_obs = cps.build_regional_hourly_obs(nearby, obs_data)
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

