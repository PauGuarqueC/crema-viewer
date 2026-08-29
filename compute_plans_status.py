#!/usr/bin/env python3
"""
compute_plans_status.py

Calcula la disponibilitat (verd/groc/vermell) de tots els plans de crema
actius (data/plans_llindars.json) contra els propers 5 dies de previsio,
consultant els 4 models comparatius (ICON-EU, ECMWF IFS HRES, GFS, AROME
France) i aplicant la mateixa logica d'avaluacio que el visor (index.html),
portada aqui a Python.

IMPORTANT: si mai es canvia algun calcul al visor (index.html: calcFMC1h,
evaluatePlan, evaluateDayContext, planToThresholds...), cal actualitzar
TAMBE la funcio corresponent aqui, o els resultats divergiran.

Ordre d'execucio dins publish.sh: DESPRES de publish_xema_10d.py (necessita
el fitxer data/observacions_10d.json ja generat).

Escriu: data/plans_status.json
"""

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

REPO_DIR = Path(__file__).resolve().parent
PLANS_LLINDARS_PATH = REPO_DIR / "data" / "plans_llindars.json"
OBSERVACIONS_PATH = REPO_DIR / "data" / "observacions_10d.json"
OUTPUT_PATH = REPO_DIR / "data" / "plans_status.json"

RADIUS_KM = 20  # mateix per defecte que el visor
COMPARISON_MODELS = ["icon_eu", "ecmwf_ifs", "gfs_seamless", "arome_france"]
MIN_CONSECUTIVE_HOURS = 6

DEFAULT_CONTEXT = {
    "precip_max": 5.0,
    "recovery_hr": 65.0,
    "recovery_nights_max": 2,
    "wind_strong": 8.0,
    "wind_strong_days_max": 1,
}

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


# ---------------------------------------------------------------------------
# UTM -> lat/lon (ETRS89 zona 31N per defecte)
# ---------------------------------------------------------------------------

def utm_to_latlon(easting, northing, zone=31, northern=True):
    from pyproj import Transformer
    epsg = 25800 + zone if northern else 25700 + zone  # ETRS89 / UTM zone N
    transformer = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(easting, northing)
    return lat, lon


# ---------------------------------------------------------------------------
# Haversine
# ---------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Estacions properes + mitjana regional horaria (equival a
# findNearbyStations + buildRegionalHourlyObs al JS)
# ---------------------------------------------------------------------------

def find_nearby_stations(lat, lon, obs_data, radius_km=RADIUS_KM):
    nearby = []
    for code, meta in obs_data["estacions"].items():
        d = haversine_km(lat, lon, meta["lat"], meta["lon"])
        if d <= radius_km:
            nearby.append(code)
    return nearby


def build_regional_hourly_obs(station_codes, obs_data):
    buckets = {}  # time -> {temp:[...], hr:[...], wind:[...], rad:[...], precip:[...]}
    for code in station_codes:
        series = obs_data["series"].get(code)
        if not series:
            continue
        for i, t in enumerate(series["t"]):
            b = buckets.setdefault(t, {"temp": [], "hr": [], "wind": [], "rad": [], "precip": []})
            for key in ("temp", "hr", "vent", "rad", "precip"):
                out_key = "wind" if key == "vent" else key
                arr = series.get(key)
                if arr is None:
                    continue
                v = arr[i]
                if v is not None:
                    buckets[t][out_key].append(v)

    result = {}
    for t, b in buckets.items():
        result[t] = {
            "temp": sum(b["temp"]) / len(b["temp"]) if b["temp"] else None,
            "hr": sum(b["hr"]) / len(b["hr"]) if b["hr"] else None,
            "wind": sum(b["wind"]) / len(b["wind"]) if b["wind"] else None,
            "rad": max(sum(b["rad"]) / len(b["rad"]), 0) if b["rad"] else None,
            "precip": sum(b["precip"]) / len(b["precip"]) if b["precip"] else None,
        }
    return result  # dict time -> {temp,hr,wind,rad,precip}


# ---------------------------------------------------------------------------
# Model FMC1h (equivalent EXACTE de calcFMC1h al JS)
# ---------------------------------------------------------------------------

def calc_fmc1h(hourly_series):
    """hourly_series: llista ordenada de dicts {time,temp,hr,wind,rad,precip}."""
    TAU_HOURS = 1.0
    decay = math.exp(-1 / TAU_HOURS)
    m = None
    out = []
    for h in hourly_series:
        temp, hr, rad, precip = h.get("temp"), h.get("hr"), h.get("rad"), h.get("precip")
        emc = None
        if temp is not None and hr is not None:
            hr_c = min(max(hr, 0), 100)
            t = temp
            if hr_c < 10:
                emc_base = 0.03229 + 0.281073 * hr_c - 0.000578 * hr_c * t
            elif hr_c < 50:
                emc_base = 2.22749 + 0.160107 * hr_c - 0.014784 * t
            else:
                emc_base = 21.0606 + 0.005565 * hr_c * hr_c - 0.00035 * hr_c * t - 0.483199 * hr_c
            rad_v = max(rad, 0) if rad is not None else 0
            rad_corr = min(rad_v / 1000, 1) * 2.0
            emc = max(emc_base - rad_corr, 1)

        if m is None:
            m = emc
        elif emc is not None:
            m = emc + (m - emc) * decay

        if precip and precip > 0 and m is not None:
            m = min(m + precip * 4, 35)

        out.append(None if m is None else round(m, 1))
    return out


# ---------------------------------------------------------------------------
# Series combinada (obs regional + previsio d'un model), equivalent a
# buildCombinedSeries al JS
# ---------------------------------------------------------------------------

def build_combined_series(regional_obs, forecast):
    """regional_obs: dict time->{...}. forecast: dict amb 'time' (llista) i
    'temp'/'hr'/'wind'/'rad'/'precip' (llistes paral·leles)."""
    merged = {}
    for t, v in regional_obs.items():
        merged[t] = {**v, "time": t, "is_forecast": False}
    for i, t in enumerate(forecast["time"]):
        merged[t] = {
            "time": t,
            "temp": forecast["temp"][i], "hr": forecast["hr"][i], "wind": forecast["wind"][i],
            "rad": forecast["rad"][i], "precip": forecast["precip"][i], "is_forecast": True,
        }
    times_sorted = sorted(merged.keys())
    series = [merged[t] for t in times_sorted]
    fmc_vals = calc_fmc1h(series)
    for h, fmc in zip(series, fmc_vals):
        h["fmc"] = fmc
    return series


# ---------------------------------------------------------------------------
# Agregats diaris + finestra marc + context (equivalent a dailyAggregates /
# evaluateDayContext al JS)
# ---------------------------------------------------------------------------

def daily_aggregates(hourly_series):
    by_day = {}
    for h in hourly_series:
        day = h["time"][:10]
        hour = int(h["time"][11:13])
        b = by_day.setdefault(day, {
            "tmax": None, "hrmin": None, "windmax": None,
            "precipsum": 0.0, "precip_n": 0, "dawn_hr_sum": 0.0, "dawn_hr_n": 0,
        })
        if h["temp"] is not None:
            b["tmax"] = h["temp"] if b["tmax"] is None else max(b["tmax"], h["temp"])
        if h["hr"] is not None:
            b["hrmin"] = h["hr"] if b["hrmin"] is None else min(b["hrmin"], h["hr"])
        if h["wind"] is not None:
            b["windmax"] = h["wind"] if b["windmax"] is None else max(b["windmax"], h["wind"])
        if h["precip"] is not None:
            b["precipsum"] += h["precip"]
            b["precip_n"] += 1
        # Recuperacio d'humitat nocturna: mitjana (no maxim) entre les 2:00 i
        # les 6:00 locals (5 lectures: 2h,3h,4h,5h,6h), la franja mes
        # freda/humida de la nit (abans de l'alba).
        if 2 <= hour <= 6 and h["hr"] is not None:
            b["dawn_hr_sum"] += h["hr"]
            b["dawn_hr_n"] += 1
    for day, b in by_day.items():
        b["dawn_hr_max"] = (b["dawn_hr_sum"] / b["dawn_hr_n"]) if b["dawn_hr_n"] else None  # nom mantingut, ara es la mitjana
    return by_day


def iso_days_before(day_str, n):
    d = datetime.strptime(day_str, "%Y-%m-%d") - timedelta(days=n)
    return d.strftime("%Y-%m-%d")


def iso_days_after(day_str, n):
    return iso_days_before(day_str, -n)


def day_check(agg, thresholds):
    if not agg:
        return True
    if thresholds.get("tmax") is not None and agg["tmax"] is not None and agg["tmax"] > thresholds["tmax"]:
        return False
    if thresholds.get("hrmin") is not None and agg["hrmin"] is not None and agg["hrmin"] < thresholds["hrmin"]:
        return False
    if thresholds.get("windmax") is not None and agg["windmax"] is not None and agg["windmax"] > thresholds["windmax"]:
        return False
    return True


def evaluate_day_context(day, by_day_agg, marc, ctx):
    m1 = by_day_agg.get(iso_days_before(day, 1))
    d0 = by_day_agg.get(day)
    p1 = by_day_agg.get(iso_days_after(day, 1))

    marc_ok = day_check(m1, marc["m1"]) and day_check(d0, marc["d0"]) and day_check(p1, marc["p1"])

    precip_sum, precip_has_data = 0.0, False
    for i in range(1, 11):
        a = by_day_agg.get(iso_days_before(day, i))
        if a and a["precip_n"]:
            precip_sum += a["precipsum"]
            precip_has_data = True
    precip_ok = (not precip_has_data) or precip_sum <= ctx["precip_max"]

    no_recovery_streak = 0
    for i in range(1, 11):
        a = by_day_agg.get(iso_days_before(day, i))
        if not a or a["dawn_hr_max"] is None:
            break
        if a["dawn_hr_max"] < ctx["recovery_hr"]:
            no_recovery_streak += 1
        else:
            break
    recovery_ok = no_recovery_streak <= ctx["recovery_nights_max"]

    wind_strong_days = 0
    for i in range(1, 8):
        a = by_day_agg.get(iso_days_before(day, i))
        if a and a["windmax"] is not None and a["windmax"] > ctx["wind_strong"]:
            wind_strong_days += 1
    wind_trend_ok = wind_strong_days <= ctx["wind_strong_days_max"]

    return marc_ok and precip_ok and recovery_ok and wind_trend_ok


# ---------------------------------------------------------------------------
# Avaluacio horaria + ratxa consecutiva (equivalent a evaluatePlan +
# maxConsecutiveRun al JS)
# ---------------------------------------------------------------------------

def hour_step_iso(iso, delta_hours):
    d = datetime.strptime(iso, "%Y-%m-%dT%H:%M") + timedelta(hours=delta_hours)
    return d.strftime("%Y-%m-%dT%H:%M")


NIGHT_RAD_THRESHOLD = 5  # W/m2 -- per sota d'aixo es considera hora nocturna (mateix criteri que compute_historic_stats.py)


def evaluate_plan(forecast_slice, thresholds, day_contexts):
    results = []
    for h in forecast_slice:
        t, hr, w, fmc = h["temp"], h["hr"], h["wind"], h["fmc"]
        day = h["time"][:10]
        ctx_ok = day_contexts.get(day, True)
        hour_ok = (
            t is not None and hr is not None and w is not None and
            thresholds["temp_min"] <= t <= thresholds["temp_max"] and
            thresholds["hr_min"] <= hr <= thresholds["hr_max"] and
            w <= thresholds["wind_max"] and
            (fmc is None or thresholds["fmc_min"] <= fmc <= thresholds["fmc_max"])
        )
        results.append({"time": h["time"], "rad": h.get("rad"), "hour_ok": hour_ok, "ctx_ok": ctx_ok, "match": hour_ok and ctx_ok})
    return results


def max_consecutive_run(results, predicate):
    max_run, cur, prev_time = 0, 0, None
    for r in results:
        ok = predicate(r)
        contiguous = prev_time is None or hour_step_iso(prev_time, 1) == r["time"]
        if ok and contiguous:
            cur += 1
        elif ok:
            cur = 1
        else:
            cur = 0
        max_run = max(max_run, cur)
        prev_time = r["time"]
    return max_run


def max_consecutive_run_with_daynight(results, predicate):
    """Igual que max_consecutive_run, pero a mes indica si la ratxa maxima
    trobada es majoritariament NOCTURNA (radiacio quasi nul·la). Nomes canvia
    la INFORMACIO retornada, mai el calcul del verd/groc/vermell en si."""
    max_run, cur, cur_night = 0, 0, 0
    best_night = 0
    prev_time = None
    for r in results:
        ok = predicate(r)
        contiguous = prev_time is None or hour_step_iso(prev_time, 1) == r["time"]
        is_night_hour = r.get("rad") is None or r["rad"] <= NIGHT_RAD_THRESHOLD
        if ok and contiguous:
            cur += 1
            cur_night += 1 if is_night_hour else 0
        elif ok:
            cur = 1
            cur_night = 1 if is_night_hour else 0
        else:
            cur = 0
            cur_night = 0
        if cur > max_run:
            max_run = cur
            best_night = cur_night
        prev_time = r["time"]
    is_night = max_run > 0 and (best_night / max_run) >= 0.5
    return max_run, is_night


# ---------------------------------------------------------------------------
# Conversio dels llindars d'un pla (baix/desitjat/alt) al format pla, igual
# que planToThresholds/planToMarc al JS (incloent el cas "nomes un definit"
# i el cas invertit de la HR)
# ---------------------------------------------------------------------------

def _range(obj):
    if not obj:
        return None, None
    baix, alt = obj.get("baix"), obj.get("alt")
    has_baix, has_alt = baix is not None, alt is not None
    if not has_baix and not has_alt:
        return None, None
    if has_baix and has_alt:
        return min(baix, alt), max(baix, alt)
    return (baix, None) if has_baix else (None, alt)


def plan_to_thresholds(plan):
    f = plan.get("finestra", {})
    temp_min, temp_max = _range(f.get("temp"))
    hr_min, hr_max = _range(f.get("hr"))
    _, wind_max_kmh = _range(f.get("vent_kmh"))
    fmc_min, fmc_max = _range(f.get("fmc1h"))
    return {
        "temp_min": temp_min if temp_min is not None else float("-inf"),
        "temp_max": temp_max if temp_max is not None else float("inf"),
        "hr_min": hr_min if hr_min is not None else float("-inf"),
        "hr_max": hr_max if hr_max is not None else float("inf"),
        "wind_max": (wind_max_kmh / 3.6) if wind_max_kmh is not None else float("inf"),
        "fmc_min": fmc_min if fmc_min is not None else float("-inf"),
        "fmc_max": fmc_max if fmc_max is not None else float("inf"),
    }


def plan_to_marc(plan):
    m = plan.get("marc", {})

    def day(d):
        if not d:
            return {"tmax": None, "hrmin": None, "windmax": None}
        vk = d.get("vent_kmh_max")
        return {
            "tmax": d.get("tmax"),
            "hrmin": d.get("hrmin"),
            "windmax": (vk / 3.6) if vk is not None else None,
        }

    return {"m1": day(m.get("dia_m1")), "d0": day(m.get("dia_0")), "p1": day(m.get("dia_p1"))}


# ---------------------------------------------------------------------------
# Open-Meteo (4 models alhora, mateixa crida que fa el visor)
# ---------------------------------------------------------------------------

def fetch_multimodel_forecast(lat, lon):
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation,precipitation",
        "models": ",".join(COMPARISON_MODELS),
        "forecast_days": 5,
        "timezone": "Europe/Madrid",
    }
    res = requests.get(OPEN_METEO_URL, params=params, timeout=30)
    res.raise_for_status()
    data = res.json()
    hourly = data["hourly"]
    time_arr = hourly["time"]

    def find_key(prefix, model_id):
        for k in hourly.keys():
            if k.startswith(prefix) and k.endswith("_" + model_id):
                return k
        return None

    models_out = {}
    for model_id in COMPARISON_MODELS:
        temp_k = find_key("temperature_2m", model_id)
        hr_k = find_key("relative_humidity_2m", model_id)
        wind_k = find_key("wind_speed_10m", model_id)
        rad_k = find_key("shortwave_radiation", model_id)
        precip_k = find_key("precipitation", model_id)
        models_out[model_id] = {
            "time": time_arr,
            "temp": hourly.get(temp_k, [None] * len(time_arr)) if temp_k else [None] * len(time_arr),
            "hr": hourly.get(hr_k, [None] * len(time_arr)) if hr_k else [None] * len(time_arr),
            "wind": [v / 3.6 if v is not None else None for v in hourly[wind_k]] if wind_k else [None] * len(time_arr),
            "rad": hourly.get(rad_k, [None] * len(time_arr)) if rad_k else [None] * len(time_arr),
            "precip": hourly.get(precip_k, [None] * len(time_arr)) if precip_k else [None] * len(time_arr),
        }
    return models_out


# ---------------------------------------------------------------------------
# Estat de consens d'un pla (equivalent a computePlanStatus al JS)
# ---------------------------------------------------------------------------

def compute_plan_status(plan, obs_data):
    lat, lon = plan.get("_lat"), plan.get("_lon")  # ja resolts abans de cridar

    nearby = find_nearby_stations(lat, lon, obs_data)
    regional_obs = build_regional_hourly_obs(nearby, obs_data)

    models_forecast = fetch_multimodel_forecast(lat, lon)
    thresholds = plan_to_thresholds(plan)
    marc = plan_to_marc(plan)

    streaks_by_model = {}
    for model_id in COMPARISON_MODELS:
        combined = build_combined_series(regional_obs, models_forecast[model_id])
        forecast_slice = [h for h in combined if h["is_forecast"]]

        by_day_agg = daily_aggregates(combined)
        forecast_days = sorted(set(h["time"][:10] for h in forecast_slice))
        day_contexts = {d: evaluate_day_context(d, by_day_agg, marc, DEFAULT_CONTEXT) for d in forecast_days}

        results = evaluate_plan(forecast_slice, thresholds, day_contexts)
        match_streak, match_is_night = max_consecutive_run_with_daynight(results, lambda r: r["match"])
        hour_ok_streak, hour_ok_is_night = max_consecutive_run_with_daynight(results, lambda r: r["hour_ok"])
        streaks_by_model[model_id] = {
            "match_streak": match_streak, "hour_ok_streak": hour_ok_streak,
            "match_streak_is_night": match_is_night, "hour_ok_streak_is_night": hour_ok_is_night,
        }

    green_count, yellow_or_green_count = 0, 0
    qualifying_is_night = []  # nomes dels models que realment compten pel verd/groc
    for s in streaks_by_model.values():
        is_green = s["match_streak"] >= MIN_CONSECUTIVE_HOURS
        is_yellow = (not is_green) and s["hour_ok_streak"] >= MIN_CONSECUTIVE_HOURS
        if is_green:
            green_count += 1
            yellow_or_green_count += 1
            qualifying_is_night.append(s["match_streak_is_night"])
        elif is_yellow:
            yellow_or_green_count += 1
            qualifying_is_night.append(s["hour_ok_streak_is_night"])

    if green_count >= 2:
        status = "green"
    elif yellow_or_green_count >= 2:
        status = "yellow"
    else:
        status = "red"

    # El calcul de verd/groc/vermell NO canvia gens; nomes s'hi afegeix
    # informacio de si la ratxa que ho ha fet possible es majoritariament
    # nocturna (per marcar-ho ratllat al mapa) -- nomes es "nocturn" si TOTS
    # els models que compten pel resultat ho son (si n'hi ha algun de diürn,
    # ja hi ha una oportunitat real de dia, no es marca).
    is_night = len(qualifying_is_night) > 0 and all(qualifying_is_night)

    return {"status": status, "isNight": is_night, "streaksByModel": streaks_by_model}


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def resolve_plan_location(plan):
    """Retorna (lat, lon) o None. Nomes lat/lon o UTM directes (sense caure
    al centroide del geojson, com fa el visor per aquesta capa)."""
    lat, lon = plan.get("lat"), plan.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return lat, lon
    utm_x, utm_y = plan.get("utm_x"), plan.get("utm_y")
    if isinstance(utm_x, (int, float)) and isinstance(utm_y, (int, float)):
        zone = plan.get("utm_zone") or 31
        return utm_to_latlon(utm_x, utm_y, zone)
    return None


def main():
    print(f"Llegint {PLANS_LLINDARS_PATH} ...")
    with open(PLANS_LLINDARS_PATH, encoding="utf-8") as f:
        plans_data = json.load(f)
    all_plans = [p for p in plans_data.get("plans", []) if p.get("id_pla", "").strip()]

    print(f"Llegint {OBSERVACIONS_PATH} ...")
    with open(OBSERVACIONS_PATH, encoding="utf-8") as f:
        obs_data = json.load(f)

    out = {}
    n_with_location = 0
    for plan in all_plans:
        loc = resolve_plan_location(plan)
        if not loc:
            continue
        n_with_location += 1
        plan["_lat"], plan["_lon"] = loc
        print(f"  {plan['id_pla']} ({plan.get('nom','')}) @ {loc[0]:.4f},{loc[1]:.4f} ...")
        try:
            status = compute_plan_status(plan, obs_data)
            out[plan["id_pla"]] = status
            # Si TOTS els models donen 0 hores ni tan sols dins del llindar
            # horari pur (sense context), es molt probable que Open-Meteo
            # no hagi tornat dades valides per aquest punt (i no un resultat
            # meteorologic real) -> avisa'n explicitament.
            all_zero = all(s["hour_ok_streak"] == 0 for s in status["streaksByModel"].values())
            if all_zero:
                print(f"    -> {status['status']}  [AVIS: 0h a TOTS els models, revisa si Open-Meteo ha tornat dades per aquest punt]")
            else:
                print(f"    -> {status['status']}")
        except Exception as e:
            print(f"    [ERROR] {e}")

    print(f"\nCalculats {len(out)}/{n_with_location} plans amb ubicació.")

    output = {
        "generat": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plans": out,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Escrit {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
