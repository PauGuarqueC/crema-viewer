#!/usr/bin/env python3
"""
compute_backfill_cremes.py

Omple les observacions reals del "dia 0" (el dia de la crema) per a les
cremes marcades des del visor amb "Avui s'ha cremat aquí" -- en el moment de
marcar-ho, aquestes dades encara no existeixen (les observacions es
publiquen amb retard, mai el mateix dia sencer). Aquest script, corregut
diariament, repassa el registre (data/cremes_realitzades.json) i omple
qualsevol crema que encara tingui "observat_dia_0: null" i que ja tingui
prou dies perque les observacions d'aquell dia siguin completes.

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
        if c.get("observat_dia_0") is not None:
            continue  # ja omplert en una execucio anterior

        data_crema = datetime.strptime(c["data_crema"], "%Y-%m-%d").date()
        if (today - data_crema).days < MIN_DAYS_OLD:
            continue  # encara massa recent, esperem al proper dia

        lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            print(f"  [AVIS] {c['id_pla']} ({c['data_crema']}): sense lat/lon guardats, no es pot omplir")
            c["observat_dia_0"] = []  # marca com "intentat" per no reintentar-ho cada dia sense sentit
            updated += 1
            continue

        nearby = cps.find_nearby_stations(lat, lon, obs_data)
        if not nearby:
            print(f"  [AVIS] {c['id_pla']} ({c['data_crema']}): cap estacio propera trobada")
            c["observat_dia_0"] = []
            updated += 1
            continue

        regional_obs = cps.build_regional_hourly_obs(nearby, obs_data)
        dia_0_hores = [
            {"time": t, **v} for t, v in sorted(regional_obs.items())
            if t.startswith(c["data_crema"])
        ]

        if len(dia_0_hores) < 18:
            # Encara no hi ha prou hores dins la finestra rodant de 10 dies
            # (per exemple si el servidor ha fallat uns dies, com ja ha
            # passat) -- ho reintentarem el proper dia.
            print(f"  {c['id_pla']} ({c['data_crema']}): nomes {len(dia_0_hores)}h disponibles, esperant...")
            continue

        c["observat_dia_0"] = dia_0_hores
        updated += 1
        print(f"  {c['id_pla']} ({c['data_crema']}): omplert amb {len(dia_0_hores)}h reals")

    if updated == 0:
        print("Cap crema per actualitzar.")
        return

    with open(CREMES_PATH, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
    print(f"\nActualitzades {updated} cremes a {CREMES_PATH}")


if __name__ == "__main__":
    main()
