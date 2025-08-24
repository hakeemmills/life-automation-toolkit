#!/usr/bin/env python3
"""
Rich daily weather email (OpenWeather One Call 3.0)

Reads SMTP + OpenWeather config from environment:
  OWM_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
  EMAIL_FROM, EMAIL_TO, EMAIL_SUBJECT_PREFIX

Example local run:
  python weather_alert.py --city "Moncks Corner" --country US --units imperial --hours 12
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Iterable, List, Tuple

import requests
from dotenv import load_dotenv
from zoneinfo import ZoneInfo


# ------------------------- HTTP helpers -------------------------


def geocode_city(api_key: str, city: str, country: str) -> Tuple[float, float, str]:
    url = "https://api.openweathermap.org/geo/1.0/direct"
    params = {"q": f"{city},{country}", "limit": 1, "appid": api_key}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise SystemExit(f"Location not found for {city}, {country}")
    item = data[0]
    pretty = f"{item.get('name')}, {item.get('state') or ''} {item.get('country')}".replace("  ", " ").strip()
    return float(item["lat"]), float(item["lon"]), pretty


def onecall(api_key: str, lat: float, lon: float, units: str = "metric") -> dict:
    # We only exclude "minutely" so we get 'current', 'hourly', 'daily', and 'alerts' if present
    url = "https://api.openweathermap.org/data/3.0/onecall"
    params = {"lat": lat, "lon": lon, "exclude": "minutely", "appid": api_key, "units": units}
    r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    return r.json()


# ------------------------- Formatting helpers -------------------------


def deg_to_compass(deg: float) -> str:
    # 16-point compass
    ix = int((deg / 22.5) + 0.5) % 16
    return ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][ix]


def fmt_temp(v: float, units: str) -> str:
    return f"{round(v):d}°{'F' if units == 'imperial' else 'C' if units == 'metric' else 'K'}"


def fmt_speed(v: float, units: str) -> str:
    return f"{round(v):d} {'mph' if units == 'imperial' else 'm/s' if units == 'standard' else 'm/s'}"


def fmt_dist_mi_km(v_m: float, units: str) -> str:
    if units == "imperial":
        return f"{v_m/1609.344:.1f} mi"
    return f"{v_m/1000:.1f} km"


def fmt_pressure(hpa: float) -> str:
    return f"{int(round(hpa))} hPa"


def pct(p: float) -> str:
    return f"{int(round(p * 100))}%"


def rain_snow_mm(h: dict) -> float:
    # OpenWeather puts these as {"1h": mm} inside 'rain' or 'snow'
    r = (h.get("rain") or {}).get("1h", 0.0)
    s = (h.get("snow") or {}).get("1h", 0.0)
    return float(r) + float(s)


def safe_desc(block: dict) -> str:
    return (block.get("weather") or [{}])[0].get("description", "").capitalize()


def tz_aware(ts: int, tz: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=tz)


# ------------------------- Email body builder -------------------------


@dataclass
class HourRow:
    time: str
    temp: str
    pop: str
    precip: str
    wind: str
    gust: str
    desc: str


def build_hour_rows(hours: List[dict], tz: ZoneInfo, units: str, take: int) -> List[HourRow]:
    rows: List[HourRow] = []
    for h in hours[:take]:
        t = tz_aware(h["dt"], tz).strftime("%I:%M %p").lstrip("0")
        temp = fmt_temp(h.get("temp", 0.0), units)
        pop = pct(h.get("pop", 0.0))
        pr = rain_snow_mm(h)
        precip = f"{pr:.1f} mm" if pr > 0 else "—"
        wind = f"{fmt_speed(h.get('wind_speed', 0.0), units)} {deg_to_compass(h.get('wind_deg', 0))}"
        gust_val = h.get("wind_gust")
        gust = fmt_speed(gust_val, units) if gust_val else "—"
        desc = safe_desc(h)
        rows.append(HourRow(t, temp, pop, precip, wind, gust, desc))
    return rows


def summarize_highlights(hours: Iterable[dict], units: str) -> List[str]:
    """Quick highlights for the next period."""
    hi: List[str] = []

    # Winds
    wmax = 0.0
    gmax = 0.0
    for h in hours:
        wmax = max(wmax, float(h.get("wind_speed", 0.0)))
        gmax = max(gmax, float(h.get("wind_gust", 0.0) or 0.0))
    if wmax >= (25.0 if units == "imperial" else 11.0):
        hi.append(f"Windy (sustained up to {fmt_speed(wmax, units)})")
    if gmax >= (35.0 if units == "imperial" else 16.0):
        hi.append(f"Gusts up to {fmt_speed(gmax, units)}")

    # Precip risk
    popmax = max(float(h.get("pop") or 0.0) for h in hours)
    if popmax >= 0.6:
        hi.append(f"High precip chance ({pct(popmax)})")

    # Heat / cold feel
    if any((h.get("feels_like") or 0.0) >= 95 for h in hours) and units == "imperial":
        hi.append("Very hot (feels ≥ 95°F)")
    if any((h.get("feels_like") or 999) <= 25 for h in hours) and units == "imperial":
        hi.append("Very cold (feels ≤ 25°F)")

    return hi


def build_subject(prefix: str, place: str, daily: dict, units: str) -> str:
    hi = fmt_temp((daily.get("temp") or {}).get("max", 0.0), units)
    lo = fmt_temp((daily.get("temp") or {}).get("min", 0.0), units)
    desc = safe_desc(daily)
    rain = (daily.get("rain") or 0.0) + (daily.get("snow") or 0.0)
    rain_txt = f", {rain:.1f} mm precip" if rain else ""
    pfx = (prefix or "").strip()
    if pfx and not pfx.endswith(" "):
        pfx += " "
    return f"{pfx}{place}: {desc} — High {hi}, Low {lo}{rain_txt}"


def build_text(place: str, tz: ZoneInfo, units: str, data: dict, hours: int) -> str:
    cur = data["current"]
    today = data["daily"][0]

    now_time = tz_aware(cur["dt"], tz).strftime("%a %b %d, %I:%M %p").lstrip("0")
    sunrise = tz_aware(today["sunrise"], tz).strftime("%I:%M %p").lstrip("0")
    sunset = tz_aware(today["sunset"], tz).strftime("%I:%M %p").lstrip("0")

    lines: List[str] = []
    lines.append(f"{place} — {now_time}")
    lines.append("=" * 60)
    lines.append(f"Now: {safe_desc(cur)} | Temp {fmt_temp(cur['temp'], units)} "
                 f"(feels {fmt_temp(cur.get('feels_like', cur['temp']), units)})")
    lines.append(f"Wind: {fmt_speed(cur.get('wind_speed', 0.0), units)} "
                 f"{deg_to_compass(cur.get('wind_deg', 0))} "
                 f"(gust {fmt_speed(cur.get('wind_gust', 0.0), units) if cur.get('wind_gust') else '—'})")
    lines.append(f"Humidity {int(cur.get('humidity', 0))}%, Dew {fmt_temp(cur.get('dew_point', 0.0), units)}, "
                 f"Pressure {fmt_pressure(cur.get('pressure', 0))}, "
                 f"Visibility {fmt_dist_mi_km(cur.get('visibility', 0.0), units)}")
    lines.append("")
    lines.append(f"Today: {safe_desc(today)} | High {fmt_temp(today['temp']['max'], units)} "
                 f"Low {fmt_temp(today['temp']['min'], units)}")
    lines.append(f"Sunrise {sunrise}, Sunset {sunset}, UV {today.get('uvi', 0)}, "
                 f"Clouds {today.get('clouds', 0)}%")
    if (today.get("rain") or 0) or (today.get("snow") or 0):
        lines.append(f"Precip: rain {today.get('rain', 0):.1f} mm, snow {today.get('snow', 0):.1f} mm")
    lines.append("")

    # Highlights + next hours
    hrs = data["hourly"]
    hi = summarize_highlights(hrs[:hours], units)
    if hi:
        lines.append("Highlights:")
        for h in hi:
            lines.append(f"  • {h}")
        lines.append("")

    rows = build_hour_rows(hrs, tz, units, hours)
    lines.append(f"Next {hours} hours:")
    lines.append("  Time   Temp  POP  Precip  Wind        Gust   Conditions")
    for r in rows:
        lines.append(
            f"  {r.time:<6} {r.temp:>5} {r.pop:>4} {r.precip:>7}  {r.wind:<10} {r.gust:<6} {r.desc}"
        )
    lines.append("")

    # Alerts
    alerts = data.get("alerts") or []
    if alerts:
        lines.append("Alerts:")
        for a in alerts:
            start = tz_aware(a["start"], tz).strftime("%a %I:%M %p").lstrip("0")
            end = tz_aware(a["end"], tz).strftime("%a %I:%M %p").lstrip("0")
            lines.append(f"  • {a.get('event', 'Alert')} ({start} → {end}) — {a.get('sender_name', '')}")
            lines.append(f"    {a.get('description', '').strip().replace(chr(10), ' ')}")
        lines.append("")

    return "\n".join(lines)


def build_html(place: str, tz: ZoneInfo, units: str, data: dict, hours: int) -> str:
    cur = data["current"]
    today = data["daily"][0]
    now_time = tz_aware(cur["dt"], tz).strftime("%a %b %d, %I:%M %p").lstrip("0")
    sunrise = tz_aware(today["sunrise"], tz).strftime("%I:%M %p").lstrip("0")
    sunset = tz_aware(today["sunset"], tz).strftime("%I:%M %p").lstrip("0")

    rows = build_hour_rows(data["hourly"], tz, units, hours)
    highlights = summarize_highlights(data["hourly"][:hours], units)

    # Basic inline styles to keep email safe
    css = (
        "body{font:14px system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#111}"
        "h1{font-size:18px;margin:0 0 6px}"
        ".muted{color:#666}"
        "table{border-collapse:collapse;width:100%;max-width:780px}"
        "th,td{border:1px solid #eee;padding:6px 8px;text-align:left;font-size:13px}"
        "th{background:#fafafa}"
        "ul{margin:6px 0 12px}"
    )

    def td(s: str) -> str:
        return f"<td>{s}</td>"

    hours_html = "".join(
        f"<tr>"
        f"{td(r.time)}{td(r.temp)}{td(r.pop)}{td(r.precip)}{td(r.wind)}{td(r.gust)}{td(r.desc)}"
        f"</tr>"
        for r in rows
    )

    hi_html = "".join(f"<li>{h}</li>" for h in highlights)

    precip_line = ""
    if (today.get("rain") or 0) or (today.get("snow") or 0):
        precip_line = (
            f"<div>Precip: rain {today.get('rain', 0):.1f} mm, "
            f"snow {today.get('snow', 0):.1f} mm</div>"
        )

    alerts = data.get("alerts") or []
    alerts_html = ""
    if alerts:
        al = []
        for a in alerts:
            start = tz_aware(a["start"], tz).strftime("%a %I:%M %p").lstrip("0")
            end = tz_aware(a["end"], tz).strftime("%a %I:%M %p").lstrip("0")
            al.append(
                f"<p><strong>{a.get('event','Alert')}</strong> "
                f"({start} → {end}) – {a.get('sender_name','')}<br>"
                f"{(a.get('description','').strip()).replace(chr(10), '<br>')}</p>"
            )
        alerts_html = "<h2>Alerts</h2>" + "".join(al)

    return f"""\
<!doctype html>
<meta name="color-scheme" content="light dark">
<style>{css}</style>
<body>
  <h1>{place}</h1>
  <div class="muted">{now_time}</div>

  <p><strong>Now:</strong> {safe_desc(cur)} — Temp {fmt_temp(cur['temp'], units)}
     (feels {fmt_temp(cur.get('feels_like', cur['temp']), units)})</p>
  <div>Wind: {fmt_speed(cur.get('wind_speed', 0.0), units)} {deg_to_compass(cur.get('wind_deg', 0))}
     (gust {fmt_speed(cur.get('wind_gust', 0.0), units) if cur.get('wind_gust') else '—'})</div>
  <div>Humidity {int(cur.get('humidity', 0))}%, Dew {fmt_temp(cur.get('dew_point', 0.0), units)},
     Pressure {fmt_pressure(cur.get('pressure', 0))},
     Visibility {fmt_dist_mi_km(cur.get('visibility', 0.0), units)}</div>

  <p><strong>Today:</strong> {safe_desc(today)} — High {fmt_temp(today['temp']['max'], units)}
     Low {fmt_temp(today['temp']['min'], units)}</p>
  <div>Sunrise {sunrise}, Sunset {sunset}, UV {today.get('uvi', 0)}, Clouds {today.get('clouds', 0)}%</div>
  {precip_line}

  {"<h2>Highlights</h2><ul>"+hi_html+"</ul>" if highlights else ""}

  <h2>Next {hours} hours</h2>
  <table>
    <thead>
      <tr>
        <th>Time</th><th>Temp</th><th>POP</th><th>Precip</th><th>Wind</th><th>Gust</th><th>Conditions</th>
      </tr>
    </thead>
    <tbody>
      {hours_html}
    </tbody>
  </table>

  {alerts_html}
</body>
"""


# ------------------------- Email sender -------------------------


def send_email(
    host: str,
    port: int,
    user: str | None,
    pwd: str | None,
    from_addr: str,
    to_addr: str,
    subject: str,
    text: str,
    html: str,
) -> None:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ctx)
        if user:
            s.login(user, pwd or "")
        s.send_message(msg)


# ------------------------- CLI / main -------------------------


def main() -> None:
    load_dotenv(override=True)

    p = argparse.ArgumentParser(description="Send a rich daily weather email.")
    p.add_argument("--city", required=True)
    p.add_argument("--country", required=True, help="Two-letter code, e.g. US")
    p.add_argument("--units", choices=["metric", "imperial", "standard"], default="imperial")
    p.add_argument("--hours", type=int, default=12, help="How many hours of detail to include (default 12)")
    args = p.parse_args()

    api_key = os.getenv("OWM_API_KEY")
    if not api_key:
        raise SystemExit("Set OWM_API_KEY in environment or .env")

    lat, lon, place = geocode_city(api_key, args.city, args.country)
    data = onecall(api_key, lat, lon, args.units)

    tz_name = data.get("timezone") or "UTC"
    tz = ZoneInfo(tz_name)

    subject_prefix = os.getenv("EMAIL_SUBJECT_PREFIX", "").strip()
    subject = build_subject(subject_prefix, place, data["daily"][0], args.units)
    text = build_text(place, tz, args.units, data, hours=args.hours)
    html = build_html(place, tz, args.units, data, hours=args.hours)

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER") or None
    pwd = os.getenv("SMTP_PASS") or None
    from_addr = os.getenv("EMAIL_FROM") or (user or "")
    to_addr = os.getenv("EMAIL_TO")

    missing = [k for k, v in {
        "SMTP_HOST": host, "SMTP_PORT": port, "EMAIL_TO": to_addr
    }.items() if not v]
    if missing:
        raise SystemExit(f"Missing SMTP config: {', '.join(missing)}")

    send_email(host, port, user, pwd, from_addr, to_addr, subject, text, html)


if __name__ == "__main__":
    main()
