#!/usr/bin/env python3
"""
publish_xema_10d.py

Llegeix els darrers N dies de dades XEMA (parquet per estacio/dia a
/home/labfire/data/SMC-STATIONS/{YYYY}/{MM}/station={CODI}/data_{YYYY-MM-DD}.parquet)
i genera un JSON consolidat (observacions_10d.json) amb series horaries de
temperatura, humitat relativa i vent per a totes les estacions actives.

Pensat per cron diari + git push cap al repo crema-viewer (GitHub Pages).
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

DATA_ROOT = Path("/home/labfire/data/SMC-STATIONS")
METADATA_ROOT = Path("/home/labfire/data/SMC-STATIONS/metadata")
OUTPUT_PATH = Path("/home/pguarque/cremes_viewer/data/observacions_10d.json")

N_DAYS = 10

# Codis de variable (taula oficial confirmada)
VAR_TEMP = 32          # Temp instantania
VAR_HR = 33            # HR instantania
VAR_WIND_SPEED_PRIORITY = [30, 48, 46]   # 10m, 6m, 2m (escalar)
VAR_WIND_DIR_PRIORITY = [31, 49, 47]     # 10m, 6m, 2m (m1, parella de l'escalar)
VAR_PRECIP = 35        # Precipitacio (mm, acumulat interval 30min)
VAR_RAD = 36           # Radiacio global (W/m2) -- necessaria pel model Nelson 1h (FMC1h)

ALL_NEEDED_VARS = {VAR_TEMP, VAR_HR, VAR_PRECIP, VAR_RAD, *VAR_WIND_SPEED_PRIORITY, *VAR_WIND_DIR_PRIORITY}


def find_latest_metadata_file() -> Path:
    """
    Les metadades son fitxers mensuals versionats:
    /home/labfire/data/SMC-STATIONS/metadata/{YYYY}/{MM}/stations_metadata_{YYYY-MM-DD}.parquet
    Agafem el fitxer amb la data mes recent (<= avui) disponible.
    """
    candidates = sorted(METADATA_ROOT.glob("*/*/stations_metadata_*.parquet"))
    if not candidates:
        raise FileNotFoundError(f"Cap fitxer de metadades trobat sota {METADATA_ROOT}")
    return candidates[-1]  # ordre lexicografic == ordre cronologic amb aquest naming


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def daterange(end_date: datetime, n_days: int):
    """Retorna una llista de n_days dates (date objects), de la mes antiga a la mes recent, incloent end_date."""
    return [(end_date - timedelta(days=i)).date() for i in range(n_days - 1, -1, -1)]


def station_day_path(station_code: str, day) -> Path:
    return (
        DATA_ROOT
        / f"{day.year:04d}"
        / f"{day.month:02d}"
        / f"station={station_code}"
        / f"data_{day.isoformat()}.parquet"
    )


def load_station_period(station_code: str, days) -> pd.DataFrame | None:
    """Concatena els parquet disponibles pels dies indicats. Retorna None si no hi ha cap fitxer."""
    frames = []
    for day in days:
        p = station_day_path(station_code, day)
        if p.exists():
            try:
                df = pd.read_parquet(p, columns=["variable_code", "date", "value"])
                frames.append(df)
            except Exception as e:
                print(f"  [WARN] {station_code} {day}: error llegint {p.name}: {e}", file=sys.stderr)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def pick_first_available(df: pd.DataFrame, priority_codes: list[int]) -> pd.Series | None:
    """Donat un df pivotat (columnes = variable_code), retorna la primera serie disponible segons prioritat."""
    for code in priority_codes:
        if code in df.columns and df[code].notna().any():
            return df[code]
    return None


def pick_first_available_with_index(df: pd.DataFrame, priority_codes: list[int]):
    """Igual que pick_first_available, pero tambe retorna l'index dins la llista
    de prioritat (per poder aparellar amb el codi de direccio corresponent)."""
    for i, code in enumerate(priority_codes):
        if code in df.columns and df[code].notna().any():
            return df[code], i
    return None, None


def circular_mean_hourly(raw_long: pd.DataFrame, dir_code: int) -> pd.Series | None:
    """Mitjana vectorial (no aritmetica) de la direccio del vent (graus), per
    evitar l'error de wrap-around a prop de 0/360 graus quan es fa mitjana
    horaria dels dos valors de 30 min."""
    dir_raw = raw_long[raw_long["variable_code"] == dir_code]
    if dir_raw.empty:
        return None
    dir_wide = dir_raw.pivot_table(index="date", columns="variable_code", values="value", aggfunc="mean")
    if dir_code not in dir_wide.columns:
        return None
    rad = np.radians(dir_wide[dir_code])
    sin_s = pd.Series(np.sin(rad), index=dir_wide.index).resample("1h").mean()
    cos_s = pd.Series(np.cos(rad), index=dir_wide.index).resample("1h").mean()
    deg = (np.degrees(np.arctan2(sin_s, cos_s)) + 360) % 360
    return deg


def process_station(station_code: str, days) -> dict | None:
    raw = load_station_period(station_code, days)
    if raw is None:
        return None

    raw = raw[raw["variable_code"].isin(ALL_NEEDED_VARS)]
    if raw.empty:
        return None

    # Les dades cru del SMC son UTC (confirmat comparant el pic de radiacio
    # amb el migdia solar real). Les convertim a hora local (Europe/Madrid)
    # AQUI, abans de qualsevol pivot/resample, perque coincideixi exactament
    # amb el format que ja fa servir Open-Meteo (timezone=Europe/Madrid) al
    # visor -- evita un desajust sistematic de 1-2h (segons horari d'estiu)
    # entre observacions i previsio quan es combinen al client.
    raw = raw.copy()
    raw["date"] = raw["date"].dt.tz_localize("UTC").dt.tz_convert("Europe/Madrid").dt.tz_localize(None)

    # Pivot: index = timestamp (30 min), columns = variable_code
    wide = raw.pivot_table(index="date", columns="variable_code", values="value", aggfunc="mean")

    # Resample horari: mitjana pels valors instantanis, SUMA pel precipitat
    # (precip es un acumulat per interval de 30min, no una mitjana te sentit)
    hourly_mean = wide.resample("1h").mean()
    hourly_sum = wide.resample("1h").sum(min_count=1)

    temp = hourly_mean[VAR_TEMP] if VAR_TEMP in hourly_mean.columns else None
    hr = hourly_mean[VAR_HR] if VAR_HR in hourly_mean.columns else None
    wind_speed, speed_idx = pick_first_available_with_index(hourly_mean, VAR_WIND_SPEED_PRIORITY)
    rad = hourly_mean[VAR_RAD] if VAR_RAD in hourly_mean.columns else None
    precip = hourly_sum[VAR_PRECIP] if VAR_PRECIP in hourly_sum.columns else None

    wind_dir = None
    if speed_idx is not None:
        dir_code = VAR_WIND_DIR_PRIORITY[speed_idx]
        wind_dir = circular_mean_hourly(raw, dir_code)

    if temp is None and hr is None and wind_speed is None:
        return None

    # Elimina hores on tot es NaN
    combined = pd.DataFrame({
        "temp": temp,
        "hr": hr,
        "vent": wind_speed,
        "ventdir": wind_dir,
        "rad": rad,
        "precip": precip,
    })
    # Coerceix a numeric (columnes que venien de None -> NaN, no None)
    combined = combined.apply(pd.to_numeric, errors="coerce")
    combined = combined.dropna(how="all", subset=["temp", "hr", "vent"])
    if combined.empty:
        return None

    combined[["temp", "hr", "vent", "rad"]] = combined[["temp", "hr", "vent", "rad"]].round(1)
    combined["precip"] = combined["precip"].round(2)
    if "ventdir" in combined.columns:
        combined["ventdir"] = combined["ventdir"].round(0)

    return {
        "t": [ts.strftime("%Y-%m-%dT%H:%M") for ts in combined.index],
        "temp": [None if pd.isna(v) else v for v in combined["temp"]],
        "hr": [None if pd.isna(v) else v for v in combined["hr"]],
        "vent": [None if pd.isna(v) else v for v in combined["vent"]],
        "ventdir": [None if pd.isna(v) else v for v in combined["ventdir"]],
        "rad": [None if pd.isna(v) else v for v in combined["rad"]],
        "precip": [None if pd.isna(v) else v for v in combined["precip"]],
    }


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    now = datetime.now(timezone.utc)
    days = daterange(now, N_DAYS)

    metadata_path = find_latest_metadata_file()
    print(f"Metadades: {metadata_path}")
    meta = pd.read_parquet(metadata_path)
    meta_active = meta[meta["deactivation_date"].isna()].copy()

    estacions = {}
    series = {}

    print(f"Processant {len(meta_active)} estacions actives, {len(days)} dies ({days[0]} -> {days[-1]})...")

    for _, row in meta_active.iterrows():
        code = row["station_code"]
        result = process_station(code, days)
        if result is None:
            continue
        series[code] = result
        estacions[code] = {
            "nom": row["station_name"],
            "lat": round(float(row["latitude"]), 5),
            "lon": round(float(row["longitude"]), 5),
            "alt": int(row["altitude_m"]),
        }

    print(f"Estacions amb dades: {len(series)} / {len(meta_active)}")

    output = {
        "generat": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dies": [d.isoformat() for d in days],
        "estacions": estacions,
        "series": series,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = OUTPUT_PATH.stat().st_size / 1_000_000
    print(f"Escrit {OUTPUT_PATH} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
