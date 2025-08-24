#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Daily weather email with detailed summary, hazards, and hourly/daily tables.

Env (.env or CI secrets):
  OWM_API_KEY
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS
  EMAIL_FROM, EMAIL_TO
  EMAIL_SUBJECT_PREFIX  (optional, e.g., "[Weather] ")

Examples:
  python morning_report.py --city "Moncks Corner" --country US --units imperial
"""

from __future__ import annotations

import argparse
import os
import textwrap
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Iterable, List, Tuple

import requests
from dotenv import load_dotenv


# ----------------------------- API helpers --------------------------------- #
def geocode_city(api_key: str, city: str, country: str) -> Tuple[float, float, str]:
    url = "https://api.openweathermap.org/geo/1.0/direct"
    params = {"q": f"{city},{country}", "limit": 1, "appid": api_key}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise SystemExit(f"Location not found for {city}, {country}")
    place = ", ".join(x for x in [data[0].get("name"), data[0].get("state")] if x)
    return float(data[0]["lat"]), float(data[0]["lon"]), place or city


def onecall(api_key: str, lat: float, lon: float, units: str) -> dict:
    url = "https://api.openweathermap.org/data/3.0/onecall"
    params = {
        "lat": lat,
        "lon": lon,
        "units": units,
        "exclude": "minutely,alerts",  # hourly + daily + current
        "appid": api_key,
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


# ----------------------------- Formatting utils ---------------------------- #
def tzfmt(ts: int, tz_offset: int, fmt: str = "%I:%M %p") -> str:
    return datetime.utcfromtimestamp(ts + tz_offset).strftime(fmt).lstrip("0")


def cardinal(deg: float | None) -> str:
    if deg is None:
        return "—"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((deg / 22.5) + 0.5) % 16]


def unit_labels(units: str) -> dict:
    if units == "imperial":
        return {"temp": "°F", "spd": "mph", "mm_to": "in", "mm_div": 25.4}
    if units == "metric":
        return {"temp": "°C", "spd": "m/s", "mm_to": "mm", "mm_div": 1.0}
    return {"temp": "K", "spd": "m/s", "mm_to": "mm", "mm_div": 1.0}


def mm_display(mm: float | None, labels: dict) -> str:
    if not mm or mm <= 0:
        return "—"
    if labels["mm_to"] == "in":
        return f"{mm / labels['mm_div']:.2f} in"
    return f"{mm:.1f} mm"


def pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{int(round(x * 100))}%"


def clamp(n: float, lo: float, hi: float) -> float:
    return max(lo, min(n, hi))


# ----------------------------- Hazards ------------------------------------- #
def hazards(data: dict, units: str, threshold: float, hours: int) -> List[str]:
    lab = unit_labels(units)
    now = data["current"]
    hourly = data.get("hourly", [])[:hours]
    daily0 = data.get("daily", [{}])[0]

    msgs: List[str] = []

    # Rain window & heavy precip detection
    rain_hours = [h for h in hourly if (h.get("pop", 0) or 0) >= threshold]
    if rain_hours:
        first = rain_hours[0]
        tzo = data.get("timezone_offset", 0)
        msgs.append(
            f"Rain chance ≥ {int(threshold * 100)}% around {tzfmt(first['dt'], tzo)}."
        )
        one_h = first.get("rain", {}).get("1h") or first.get("snow", {}).get("1h")
        if one_h and one_h >= (6 if lab["mm_to"] == "mm" else 6):  # ~6 mm/hr threshold
            msgs.append(f"Heavy precip rate: {mm_display(one_h, lab)}.")

    # Wind
    gust = (now.get("wind_gust") or 0) or max((h.get("wind_gust") or 0) for h in hourly[:6] or [0])
    gust_mph_equiv = gust if units == "imperial" else gust * 2.237
    if gust_mph_equiv >= 30:
        msgs.append(f"Windy: gusts up to ~{gust_mph_equiv:.0f} mph.")

    # Heat / Freeze
    tmax = daily0.get("temp", {}).get("max")
    tmin = daily0.get("temp", {}).get("min")
    if tmax is not None:
        hot = tmax >= (90 if units == "imperial" else 32)
        if hot:
            msgs.append("Heat risk: plan hydration/shade.")
    if tmin is not None:
        freezing = tmin <= (32 if units == "imperial" else 0)
        if freezing:
            msgs.append("Freeze risk overnight.")

    # UV index
    uvi_peak = max(int(round((h.get("uvi") or 0))) for h in hourly or [0])
    if uvi_peak >= 6:
        msgs.append(f"High UV (peak ~{uvi_peak}). Use sunscreen.")

    # Fog/low vis
    low_vis = [h for h in hourly[:6] if (h.get("visibility") or 10_000) < 2000]
    if low_vis:
        msgs.append("Fog possible (low visibility early).")

    return msgs


# ----------------------------- Email builders ------------------------------- #
def build_subject(prefix: str, place: str, data: dict, units: str) -> str:
    lab = unit_labels(units)
    cur = data["current"]
    temp = f"{round(cur['temp'])}{lab['temp']}"
    hi = data["daily"][0]["temp"]["max"]
    lo = data["daily"][0]["temp"]["min"]
    hi_lo = f"H {round(hi)}° / L {round(lo)}°"
    desc = (cur.get("weather") or [{}])[0].get("description", "weather").title()
    day = datetime.utcfromtimestamp(cur["dt"] + data["timezone_offset"]).strftime("%a %b %d")
    prefix = prefix or ""
    return f"{prefix}{place} — {day} — {temp}, {hi_lo}, {desc}"


def _html_table(headers: List[str], rows: Iterable[List[str]]) -> str:
    ths = "".join(f"<th style='padding:4px 8px;text-align:left'>{h}</th>" for h in headers)
    trs = []
    for r in rows:
        tds = "".join(f"<td style='padding:4px 8px'>{c}</td>" for c in r)
        trs.append(f"<tr>{tds}</tr>")
    body = "\n".join(trs)
    return (
        "<table border='1' cellpadding='0' cellspacing='0' "
        "style='border-collapse:collapse;font-family:system-ui,Segoe UI,Arial'>"
        f"<thead><tr style='background:#f3f4f6'>{ths}</tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def build_email(data: dict, place: str, units: str, threshold: float, hours: int) -> Tuple[str, str]:
    lab = unit_labels(units)
    tzo = data.get("timezone_offset", 0)
    cur = data["current"]
    daily = data.get("daily", [])[:3]
    hourly = data.get("hourly", [])[:hours]

    # Current summary bits
    now_desc = (cur.get("weather") or [{}])[0].get("description", "").title()
    feels = f"{round(cur.get('feels_like'))}{lab['temp']}"
    now_temp = f"{round(cur['temp'])}{lab['temp']}"
    wind = f"{round(cur.get('wind_speed', 0))} {lab['spd']} {cardinal(cur.get('wind_deg'))}"
    gust = cur.get("wind_gust")
    gust_s = f", gust {round(gust)} {lab['spd']}" if gust else ""
    humidity = f"{cur.get('humidity', 0)}%"
    pressure = f"{cur.get('pressure', 0)} hPa"
    clouds = f"{cur.get('clouds', 0)}%"
    vis_km = (cur.get("visibility") or 0) / 1000
    vis = f"{vis_km:.1f} km" if units != "imperial" else f"{vis_km * 0.621:.1f} mi"

    sr = tzfmt(daily[0]["sunrise"], tzo)
    ss = tzfmt(daily[0]["sunset"], tzo)

    # Hazards
    hz = hazards(data, units, threshold, hours)
    hz_lines = "\n".join(f"• {h}" for h in hz) if hz else "• No notable hazards expected."

    # Hourly rows
    h_rows: List[List[str]] = []
    for h in hourly:
        dt = tzfmt(h["dt"], tzo, "%I %p")
        t = f"{round(h['temp'])}{lab['temp']}"
        p = pct(h.get("pop"))
        rn = mm_display((h.get("rain") or {}).get("1h"), lab)
        sn = mm_display((h.get("snow") or {}).get("1h"), lab)
        wx = (h.get("weather") or [{}])[0].get("main", "—")
        ws = f"{round(h.get('wind_speed', 0))} {lab['spd']}"
        gu = f"{round(h.get('wind_gust', 0))}" if h.get("wind_gust") else "—"
        cl = f"{h.get('clouds', 0)}%"
        uv = f"{round(h.get('uvi', 0))}"
        h_rows.append([dt, t, wx, p, rn, sn, ws, gu, cl, uv])

    h_html = _html_table(
        ["Time", "Temp", "Wx", "POP", "Rain(1h)", "Snow(1h)", "Wind", "Gust", "Cloud", "UVI"],
        h_rows,
    )

    # Daily rows (next 3 days)
    d_rows: List[List[str]] = []
    for d in daily:
        day = tzfmt(d["dt"], tzo, "%a %b %d")
        hi = f"{round(d['temp']['max'])}{lab['temp']}"
        lo = f"{round(d['temp']['min'])}{lab['temp']}"
        wx = (d.get("weather") or [{}])[0].get("description", "—").title()
        pr = pct(d.get("pop"))
        wspd = f"{round(d.get('wind_speed', 0))} {lab['spd']}"
        gust_d = d.get("wind_gust")
        g_s = f"{round(gust_d)} {lab['spd']}" if gust_d else "—"
        rain = mm_display(d.get("rain"), lab)
        snow = mm_display(d.get("snow"), lab)
        d_rows.append([day, hi, lo, wx, pr, rain, snow, wspd, g_s])

    d_html = _html_table(
        ["Day", "High", "Low", "Wx", "POP", "Rain", "Snow", "Wind", "Gust"],
        d_rows,
    )

    # HTML email
    html_lines = [
        "<div style='font-family:system-ui,Segoe UI,Arial;color:#111'>",
        f"<h2 style='margin:0 0 8px'>Daily Weather — {place}</h2>",
        f"<p style='margin:0 0 12px'>Now: <b>{now_temp}</b> (feels {feels}), {now_desc}. "
        f"Wind {wind}{gust_s}. Humidity {humidity}. Clouds {clouds}. "
        f"Pressure {pressure}. Visibility {vis}. Sunrise {sr}, Sunset {ss}.</p>",
        "<h3 style='margin:16px 0 6px'>Heads-up</h3>",
        f"<pre style='margin:0 0 12px;white-space:pre-wrap'>{hz_lines}</pre>",
        "<h3 style='margin:16px 0 6px'>Next hours</h3>",
        h_html,
        "<h3 style='margin:16px 0 6px'>Next 3 days</h3>",
        d_html,
        "<p style='color:#6b7280;font-size:12px;margin-top:12px'>"
        "POP = probability of precipitation. Rain/Snow are model estimates.</p>",
        "</div>",
    ]
    html = "\n".join(html_lines)

    # Plaintext (brief)
    pt = textwrap.dedent(
        f"""
        {place} — Now {now_temp} (feels {feels}), {now_desc}
        Wind {wind}{gust_s} | Humidity {humidity} | Clouds {clouds}
        Sunrise {sr} | Sunset {ss}

        Hazards:
        {hz_lines}

        Hourly (first {len(h_rows)}):
        """  # noqa: E501
    ).strip()

    # Add compact hourly plaintext table
    for r in h_rows[:12]:
        pt += f"\n  {r[0]:>5}  {r[1]:>4}  POP {r[3]:>3}  Wind {r[6]:>6}  Gust {r[7]:>3}"

    return html, pt


# ----------------------------- SMTP ---------------------------------------- #
def send_email(subject: str, html: str, text: str) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pwd = os.getenv("SMTP_PASS")
    from_ = os.getenv("EMAIL_FROM", user)
    to = os.getenv("EMAIL_TO")

    if not all([host, port, user, pwd, from_, to]):
        raise SystemExit("Missing SMTP env: host/port/user/pass/from/to")

    msg = EmailMessage()
    msg["From"] = from_
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    import smtplib, ssl  # noqa: E401

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ctx)
        s.login(user, pwd)
        s.send_message(msg)


# ----------------------------- Main ---------------------------------------- #
def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(description="Send a detailed morning weather email.")
    p.add_argument("--city", required=True)
    p.add_argument("--country", required=True, help="Two-letter country code, e.g. US")
    p.add_argument("--units", default="imperial",
                   choices=["imperial", "metric", "standard"])
    p.add_argument("--threshold", type=float, default=0.3,
                   help="Rain probability threshold (0-1) for hazard note")
    p.add_argument("--hours", type=int, default=18,
                   help="How many future hours to include")
    args = p.parse_args()

    api_key = os.getenv("OWM_API_KEY")
    if not api_key:
        raise SystemExit("Set OWM_API_KEY in env or .env")

    lat, lon, place = geocode_city(api_key, args.city, args.country)
    data = onecall(api_key, lat, lon, args.units)

    prefix = os.getenv("EMAIL_SUBJECT_PREFIX", "")
    subject = build_subject(prefix, place, data, args.units)
    html, text = build_email(data, place, args.units, args.threshold, args.hours)

    send_email(subject, html, text)
    print("Email sent.")


if __name__ == "__main__":
    main()
