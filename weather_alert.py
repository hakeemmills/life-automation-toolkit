#!/usr/bin/env python3
"""
Daily weather email: rich HTML + text forecast from OpenWeather One Call 3.0.

Env vars (same as your repo/GitHub Actions secrets):
  OWM_API_KEY
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS
  EMAIL_FROM, EMAIL_TO
  EMAIL_SUBJECT_PREFIX   (optional, e.g., "[Weather Alert] ")
Usage examples:
  python daily_weather_email.py --city "Moncks Corner" --country US --units imperial
  python daily_weather_email.py --city "Moncks Corner" --country US --units imperial --hours 18 --days 6
  python daily_weather_email.py --city "Moncks Corner" --country US --units imperial --with-air
"""
from __future__ import annotations

import argparse
import html
import math
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from textwrap import dedent

import requests
from dotenv import load_dotenv


# ---------- helpers ----------

def load_env() -> None:
    # override=True so CI secrets or local .env always win
    load_dotenv(override=True)


def deg_to_compass(deg: float) -> str:
    # 16-point compass
    pts = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    ]
    i = int((deg / 22.5) + 0.5) % 16
    return pts[i]


def fmt_units(units: str) -> dict:
    if units == "imperial":
        return {"temp": "°F", "speed": "mph", "precip": "in"}
    if units == "metric":
        return {"temp": "°C", "speed": "m/s", "precip": "mm"}
    return {"temp": "K", "speed": "m/s", "precip": "mm"}


def safe_get(d: dict, path: list, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def pop_pct(x: float | None) -> str:
    return f"{int(round((x or 0) * 100)):>3d}%"


def mm_to_inches(mm: float) -> float:
    return mm / 25.4


def moon_label(phase: float) -> str:
    # openweather phase 0..1
    steps = [
        (0.00, "🌑 New"), (0.125, "🌒 Waxing crescent"), (0.25, "🌓 First quarter"),
        (0.375, "🌔 Waxing gibbous"), (0.50, "🌕 Full"), (0.625, "🌖 Waning gibbous"),
        (0.75, "🌗 Last quarter"), (0.875, "🌘 Waning crescent"), (1.00, "🌑 New"),
    ]
    for edge, label in steps:
        if phase <= edge:
            return label
    return "🌑 New"


def weather_emoji(code: int) -> str:
    # group by weather condition ID
    if 200 <= code < 300:
        return "⛈️"
    if 300 <= code < 600:
        return "🌦️"
    if 600 <= code < 700:
        return "❄️"
    if 700 <= code < 800:
        return "🌫️"
    if code == 800:
        return "☀️"
    if 801 <= code <= 804:
        return "⛅"
    return "🌡️"


def aqi_label(aqi: int | None) -> str:
    # 1..5 per OpenWeather docs
    mapping = {
        1: "Good",
        2: "Fair",
        3: "Moderate",
        4: "Poor",
        5: "Very Poor",
    }
    return mapping.get(int(aqi or 0), "n/a")


def as_local(ts: int, tz_name: str) -> datetime:
    # OpenWeather returns timezone string (e.g., "America/New_York")
    # We keep things simple using offset-aware conversion via tzinfo in payload
    # but the name is handy for human context in the email.
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()


@dataclass
class Geo:
    lat: float
    lon: float
    name: str


# ---------- OpenWeather API calls ----------

def geocode_city(api_key: str, city: str, country: str) -> Geo:
    url = "https://api.openweathermap.org/geo/1.0/direct"
    r = requests.get(
        url,
        params={"q": f"{city},{country}", "limit": 1, "appid": api_key},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        raise SystemExit(f"Location not found for: {city}, {country}")
    j = data[0]
    pretty = j.get("name") or city
    state = j.get("state")
    if state:
        pretty = f"{pretty}, {state}"
    return Geo(lat=j["lat"], lon=j["lon"], name=pretty)


def onecall(api_key: str, lat: float, lon: float, units: str) -> dict:
    url = "https://api.openweathermap.org/data/3.0/onecall"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "units": units,
        "exclude": "minutely",  # we want current/hourly/daily/alerts
    }
    r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def air_pollution(api_key: str, lat: float, lon: float) -> dict | None:
    url = "https://api.openweathermap.org/data/2.5/air_pollution"
    r = requests.get(url, params={"lat": lat, "lon": lon, "appid": api_key}, timeout=15)
    if r.status_code != 200:
        return None
    return r.json()


# ---------- Email compose & send ----------

def send_email(
    subject: str,
    html_body: str,
    text_body: str,
) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pwd = os.getenv("SMTP_PASS")
    from_addr = os.getenv("EMAIL_FROM", user or "")
    to_addr = os.getenv("EMAIL_TO")

    if not all([host, port, user, pwd, from_addr, to_addr]):
        raise SystemExit("Missing SMTP/Email env vars")

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    # TLS (STARTTLS) path — works with Gmail 587 + app password
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ctx)
        s.login(user, pwd)
        s.send_message(msg)


# ---------- HTML/Text builders ----------

def build_now_block(cur: dict, units: str) -> tuple[str, str]:
    u = fmt_units(units)
    desc = safe_get(cur, ["weather", 0, "description"], "").title()
    code = safe_get(cur, ["weather", 0, "id"], 800)
    emoji = weather_emoji(int(code))

    temp = f"{round(cur.get('temp', 0))}{u['temp']}"
    feels = f"{round(cur.get('feels_like', 0))}{u['temp']}"
    humidity = f"{cur.get('humidity', 0)}%"
    clouds = f"{cur.get('clouds', 0)}%"
    uv = f"{cur.get('uvi', 0)}"

    wind_spd = cur.get("wind_speed", 0.0)
    wind_deg = cur.get("wind_deg", 0)
    wind = f"{round(wind_spd)} {u['speed']} {deg_to_compass(wind_deg)}"

    line = (
        f"{emoji} {desc}. Temp {temp}, feels {feels}. "
        f"Wind {wind}. Humidity {humidity}. UV {uv}. Clouds {clouds}."
    )

    html_block = dedent(f"""
        <h3 style="margin:0.2rem 0">Now</h3>
        <p style="margin:0.2rem 0">
          {html.escape(line)}
        </p>
    """).strip()

    return html_block, line


def build_today_block(today: dict, units: str, tz_name: str) -> tuple[str, str]:
    u = fmt_units(units)
    desc = safe_get(today, ["weather", 0, "description"], "").title()
    code = safe_get(today, ["weather", 0, "id"], 800)
    emoji = weather_emoji(int(code))
    tmin = f"{round(safe_get(today, ['temp', 'min'], 0))}{u['temp']}"
    tmax = f"{round(safe_get(today, ['temp', 'max'], 0))}{u['temp']}"
    pop = pop_pct(today.get("pop"))
    rain_mm = today.get("rain", 0.0) or 0.0
    snow_mm = today.get("snow", 0.0) or 0.0

    if u["precip"] == "in":
        rain_txt = f"{mm_to_inches(rain_mm):.2f} in"
        snow_txt = f"{mm_to_inches(snow_mm):.2f} in"
    else:
        rain_txt = f"{rain_mm:.1f} mm"
        snow_txt = f"{snow_mm:.1f} mm"

    sunrise = as_local(int(today.get("sunrise", 0)), tz_name).strftime("%-I:%M %p")
    sunset = as_local(int(today.get("sunset", 0)), tz_name).strftime("%-I:%M %p")
    moon = moon_label(today.get("moon_phase", 0.0))

    line = (
        f"{emoji} Today: {desc}. High {tmax}, low {tmin}. POP {pop}; "
        f"rain {rain_txt}, snow {snow_txt}. "
        f"Sunrise {sunrise}, sunset {sunset}. {moon}."
    )

    html_block = dedent(f"""
        <h3 style="margin:0.8rem 0 0.2rem 0">Today</h3>
        <p style="margin:0.2rem 0">
          {html.escape(line)}
        </p>
    """).strip()

    return html_block, line


def build_hours_table(hours: list[dict], units: str, tz_name: str, limit: int) -> tuple[str, str]:
    u = fmt_units(units)
    rows_html: list[str] = []
    rows_text: list[str] = []
    header_html = (
        "<tr>"
        "<th align='left'>Time</th>"
        "<th align='right'>Temp</th>"
        "<th align='right'>POP</th>"
        "<th align='right'>Rain</th>"
        "<th align='right'>Snow</th>"
        "<th align='left'>Desc</th>"
        "</tr>"
    )

    for h in hours[:limit]:
        t = as_local(int(h["dt"]), tz_name).strftime("%-I %p")
        temp = f"{round(h.get('temp', 0))}{u['temp']}"
        pop = pop_pct(h.get("pop"))
        wdesc = safe_get(h, ["weather", 0, "description"], "").title()
        rain = (safe_get(h, ["rain", "1h"], 0.0) or 0.0)
        snow = (safe_get(h, ["snow", "1h"], 0.0) or 0.0)

        if u["precip"] == "in":
            rain_txt = f"{mm_to_inches(rain):.2f}"
            snow_txt = f"{mm_to_inches(snow):.2f}"
        else:
            rain_txt = f"{rain:.1f}"
            snow_txt = f"{snow:.1f}"

        rows_html.append(
            "<tr>"
            f"<td>{html.escape(t)}</td>"
            f"<td align='right'>{html.escape(temp)}</td>"
            f"<td align='right'>{html.escape(pop)}</td>"
            f"<td align='right'>{html.escape(rain_txt)}</td>"
            f"<td align='right'>{html.escape(snow_txt)}</td>"
            f"<td>{html.escape(wdesc)}</td>"
            "</tr>"
        )
        rows_text.append(
            f"{t:>4}  {temp:>6}  {pop:>4}  "
            f"rain {rain_txt:>5}  snow {snow_txt:>5}  {wdesc}"
        )

    table = (
        "<table cellpadding='4' cellspacing='0' "
        "style='border-collapse:collapse;border:1px solid #ddd;font-family:system-ui'>"
        f"{header_html}"
        f"{''.join(rows_html)}"
        "</table>"
    )
    txt = "Time  Temp   POP   Rain   Snow  Description\n" + "\n".join(rows_text)
    html_block = f"<h3 style='margin:0.8rem 0 0.2rem 0'>Next {limit} hours</h3>{table}"
    return html_block, txt


def build_days_list(days: list[dict], units: str, tz_name: str, limit: int) -> tuple[str, str]:
    u = fmt_units(units)
    items_html: list[str] = []
    items_text: list[str] = []
    for d in days[:limit]:
        name = as_local(int(d["dt"]), tz_name).strftime("%a")
        tmin = f"{round(safe_get(d, ['temp', 'min'], 0))}{u['temp']}"
        tmax = f"{round(safe_get(d, ['temp', 'max'], 0))}{u['temp']}"
        pop = pop_pct(d.get("pop"))
        desc = safe_get(d, ["weather", 0, "description"], "").title()
        code = safe_get(d, ["weather", 0, "id"], 800)
        emoji = weather_emoji(int(code))
        items_html.append(
            f"<li><b>{html.escape(name)}</b>: {html.escape(emoji)} "
            f"{html.escape(desc)} — {html.escape(tmin)} / {html.escape(tmax)} "
            f"(POP {html.escape(pop)})</li>"
        )
        items_text.append(f"{name}: {desc} — {tmin}/{tmax} (POP {pop})")
    html_block = (
        f"<h3 style='margin:0.8rem 0 0.2rem 0'>Next {limit} days</h3>"
        f"<ul style='margin:0.2rem 0 0 1rem'>{''.join(items_html)}</ul>"
    )
    txt = "Next days:\n- " + "\n- ".join(items_text)
    return html_block, txt


def build_alerts_section(alerts: list[dict] | None, tz_name: str) -> tuple[str, str]:
    if not alerts:
        return "", ""
    items_html: list[str] = []
    items_text: list[str] = []
    for a in alerts:
        event = a.get("event", "Alert")
        start = as_local(int(a.get("start", 0)), tz_name).strftime("%a %-I:%M %p")
        end = as_local(int(a.get("end", 0)), tz_name).strftime("%a %-I:%M %p")
        desc = (a.get("description") or "").strip()
        short = desc[:300].replace("\n", " ")
        items_html.append(
            f"<li><b>{html.escape(event)}</b> "
            f"({html.escape(start)} → {html.escape(end)}): "
            f"{html.escape(short)}...</li>"
        )
        items_text.append(f"{event} ({start}→{end}): {short}...")
    html_block = (
        "<h3 style='margin:1rem 0 0.2rem 0'>⚠️ Alerts</h3>"
        f"<ul style='margin:0.2rem 0 0 1rem'>{''.join(items_html)}</ul>"
    )
    txt = "ALERTS:\n- " + "\n- ".join(items_text)
    return html_block, txt


def build_aqi_section(aqi_json: dict | None) -> tuple[str, str]:
    if not aqi_json:
        return "", ""
    first = safe_get(aqi_json, ["list", 0], {})
    aqi_val = safe_get(first, ["main", "aqi"], None)
    lbl = aqi_label(aqi_val)
    comps = first.get("components", {})
    fine = comps.get("pm2_5", None)
    coarse = comps.get("pm10", None)

    html_block = dedent(f"""
        <h3 style="margin:0.8rem 0 0.2rem 0">Air quality</h3>
        <p style="margin:0.2rem 0">
          AQI: <b>{html.escape(lbl)}</b> ({html.escape(str(aqi_val))}).
          PM2.5: {html.escape(str(fine))} µg/m³; PM10: {html.escape(str(coarse))} µg/m³.
        </p>
    """).strip()
    txt = f"AQI {lbl} ({aqi_val}). PM2.5 {fine} µg/m³; PM10 {coarse} µg/m³."
    return html_block, txt


# ---------- main ----------

def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(
        description="Send a detailed daily weather email (OpenWeather One Call 3.0)."
    )
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", required=True, help="Two-letter code, e.g., US")
    parser.add_argument(
        "--units",
        choices=["metric", "imperial", "standard"],
        default="imperial",
    )
    parser.add_argument("--hours", type=int, default=12, help="Hours to include")
    parser.add_argument("--days", type=int, default=5, help="Days to include")
    parser.add_argument(
        "--with-air",
        action="store_true",
        help="Include Air Quality (OpenWeather Air Pollution API).",
    )
    args = parser.parse_args()

    api_key = os.getenv("OWM_API_KEY")
    if not api_key:
        raise SystemExit("Set OWM_API_KEY in env/secrets")

    geo = geocode_city(api_key, args.city, args.country)
    data = onecall(api_key, geo.lat, geo.lon, args.units)
    tz_name = data.get("timezone", "local time")

    # Compose blocks
    now_html, now_txt = build_now_block(data.get("current", {}), args.units)
    today_html, today_txt = build_today_block(data["daily"][0], args.units, tz_name)
    hours_html, hours_txt = build_hours_table(
        data.get("hourly", []), args.units, tz_name, args.hours
    )
    days_html, days_txt = build_days_list(
        data.get("daily", [])[1:], args.units, tz_name, args.days
    )
    alerts_html, alerts_txt = build_alerts_section(data.get("alerts"), tz_name)

    aqi_html = ""
    aqi_txt = ""
    if args.with_air:
        aqi_json = air_pollution(api_key, geo.lat, geo.lon)
        aqi_html, aqi_txt = build_aqi_section(aqi_json)

    # Subject
    prefix = os.getenv("EMAIL_SUBJECT_PREFIX", "")
    today_str = as_local(int(data.get("current", {}).get("dt", 0)), tz_name).strftime("%a %b %-d")
    subject = f"{prefix}{geo.name} — Daily Outlook ({today_str})"

    # HTML body (safe, short inline styles)
    header = dedent(f"""
        <div style="font:14px/1.4 system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
                    color:#111; max-width:780px">
          <h2 style="margin:0.2rem 0 0.8rem 0">
            {html.escape(geo.name)} — Daily Weather Outlook
          </h2>
          <p style="margin:0 0 0.6rem 0; color:#666">
            Source: OpenWeather One Call 3.0 · Units: {html.escape(args.units)}
          </p>
    """).strip()

    html_body = (
        header
        + "\n"
        + now_html
        + "\n"
        + today_html
        + "\n"
        + hours_html
        + "\n"
        + days_html
        + ("\n" + alerts_html if alerts_html else "")
        + ("\n" + aqi_html if aqi_html else "")
        + "\n</div>"
    )

    # Plain text fallback
    text_body = "\n".join(
        [
            f"{geo.name} — Daily Weather Outlook",
            f"Units: {args.units}",
            "",
            now_txt,
            today_txt,
            "",
            hours_txt,
            "",
            days_txt,
            "",
            alerts_txt if alerts_txt else "",
            aqi_txt if aqi_txt else "",
        ]
    )

    send_email(subject=subject, html_body=html_body, text_body=text_body)
    print("Email sent.")


if __name__ == "__main__":
    main()
