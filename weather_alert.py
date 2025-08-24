#!/usr/bin/env python3
"""
Morning Weather Email
- Geocodes a city/country via OpenWeather Geocoding.
- Fetches One Call 3.0 (current, hourly, daily, alerts).
- Builds a detailed HTML + plain-text email.
- Sends via SMTP (TLS) using env vars.

Env required:
  OWM_API_KEY
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS
  EMAIL_FROM, EMAIL_TO
Optional:
  EMAIL_SUBJECT_PREFIX (e.g., "[Weather]")
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Dict, List, Tuple

import requests
from dotenv import load_dotenv

# ---------- Utilities ----------

def emoji_for(icon: str) -> str:
    """Map OpenWeather icon code → emoji."""
    m = {
        "01d": "☀️", "01n": "🌑",
        "02d": "🌤️", "02n": "☁️",
        "03d": "⛅",  "03n": "⛅",
        "04d": "☁️", "04n": "☁️",
        "09d": "🌧️", "09n": "🌧️",
        "10d": "🌦️", "10n": "🌧️",
        "11d": "⛈️", "11n": "⛈️",
        "13d": "❄️", "13n": "❄️",
        "50d": "🌫️", "50n": "🌫️",
    }
    return m.get(icon, "")

def safe_get(d: Dict, *path, default=None):
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return default
        d = d[key]
    return d

def fmt_temp(v: float, units: str) -> str:
    return f"{round(v)}°{'F' if units=='imperial' else 'C' if units=='metric' else 'K'}"

def fmt_wind(speed: float, gust: float | None, units: str) -> str:
    if units == "imperial":
        unit = "mph"
    elif units == "metric":
        unit = "m/s"
    else:
        unit = "m/s"
    base = f"{round(speed)} {unit}"
    if gust and gust > speed:
        base += f" (gust {round(gust)} {unit})"
    return base

def fmt_pop(pop: float | None) -> str:
    if pop is None:
        return "—"
    return f"{int(round(pop * 100))}%"

def dewpoint_note(dp_f: float | None, units: str) -> str:
    if dp_f is None:
        return ""
    # Normalize to Fahrenheit to keep thresholds intuitive.
    if units != "imperial":
        # dp in C → F
        dp_f = dp_f * 9/5 + 32
    if dp_f < 50:
        return "Dry/comfortable"
    if dp_f < 60:
        return "A bit humid"
    if dp_f < 70:
        return "Humid"
    return "Oppressive"

def uv_note(uvi: float | None) -> str:
    if uvi is None:
        return ""
    if uvi >= 8:
        return "Very high UV – sunscreen strongly advised"
    if uvi >= 6:
        return "High UV – use sunscreen"
    if uvi >= 3:
        return "Moderate UV – consider protection"
    return "Low UV"

def pick_best_dry_window(hours: List[Dict], tz: timezone, span=3) -> Tuple[datetime, float]:
    """
    Find a consecutive `span`-hour window in next 12h with the smallest max POP.
    Returns (window_start_local, max_pop_in_window)
    """
    hours = hours[:12]
    best_i, best_pop = 0, 1.0
    for i in range(0, len(hours) - span + 1):
        max_pop = max(h.get("pop", 0) for h in hours[i:i+span])
        if max_pop < best_pop:
            best_pop, best_i = max_pop, i
    start = dt_local(hours[best_i]["dt"], tz)
    return start, best_pop

def dt_local(ts: int, tz: timezone) -> datetime:
    return datetime.fromtimestamp(ts, tz)

def format_clock(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p") if os.name != "nt" else dt.strftime("%I:%M %p").lstrip("0")

# ---------- API ----------

@dataclass
class Geo:
    name: str
    lat: float
    lon: float
    state: str | None
    country: str

def geocode_city(api_key: str, city: str, country: str) -> Geo:
    url = "https://api.openweathermap.org/geo/1.0/direct"
    r = requests.get(url, params={"q": f"{city},{country}", "limit": 1, "appid": api_key}, timeout=20)
    r.raise_for_status()
    arr = r.json()
    if not arr:
        raise SystemExit(f"Location not found for {city}, {country}")
    g = arr[0]
    return Geo(
        name=g.get("name", city),
        lat=g["lat"],
        lon=g["lon"],
        state=g.get("state"),
        country=g["country"],
    )

def onecall(api_key: str, lat: float, lon: float, units: str) -> Dict:
    url = "https://api.openweathermap.org/data/3.0/onecall"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "units": units,
        "exclude": "minutely",
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

# ---------- Email ----------

def send_email(subject: str, html: str, text: str) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")
    from_addr = os.getenv("EMAIL_FROM", user)
    to_addr   = os.getenv("EMAIL_TO")
    if not all([host, port, user, pwd, from_addr, to_addr]):
        raise SystemExit("Missing SMTP / EMAIL_* environment variables.")

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ctx)
        s.login(user, pwd)
        s.send_message(msg)

# ---------- Renderers ----------

def build_email(city_display: str, tz: timezone, units: str, data: Dict) -> Tuple[str, str]:
    now = safe_get(data, "current", default={})
    hourly = data.get("hourly", []) or []
    daily  = data.get("daily", []) or []
    alerts = data.get("alerts", []) or []

    # Current section
    cur_dt     = dt_local(now.get("dt", 0), tz)
    desc       = safe_get(now, "weather", 0, "description", default="—").title()
    icon       = safe_get(now, "weather", 0, "icon", default="")
    temp       = now.get("temp")
    feels      = now.get("feels_like")
    humidity   = now.get("humidity")
    dew_point  = now.get("dew_point")
    pressure   = now.get("pressure")
    wind_speed = now.get("wind_speed")
    wind_gust  = now.get("wind_gust")
    uvi        = now.get("uvi")
    visibility = now.get("visibility")
    clouds     = now.get("clouds")

    today = daily[0] if daily else {}
    tmin = safe_get(today, "temp", "min", default=None)
    tmax = safe_get(today, "temp", "max", default=None)
    sunrise = dt_local(today.get("sunrise", now.get("sunrise", 0)), tz)
    sunset  = dt_local(today.get("sunset",  now.get("sunset",  0)), tz)
    pop_day = today.get("pop", 0.0)
    rain_d  = today.get("rain", 0.0) or 0.0
    snow_d  = today.get("snow", 0.0) or 0.0

    # Tips
    tips: List[str] = []
    # Any rainy hour next 12h?
    if any((h.get("pop", 0) >= 0.5) for h in hourly[:12]):
        tips.append("☔ Umbrella recommended")
    if (uvi or 0) >= 6:
        tips.append("🧴 High UV – sunscreen")
    if (wind_gust or 0) >= (30 if units == "imperial" else 13.5):
        tips.append("💨 Windy – secure loose items")
    if dew_point is not None:
        tips.append(f"💧 {dewpoint_note(dew_point, units)}")

    # Best 3-hour dry window next 12h
    tzinfo = tz
    best_start, best_pop = pick_best_dry_window(hourly, tzinfo)
    best_window_note = f"Best 3-hour dry window (next 12h): {format_clock(best_start)} – POP {int(round(best_pop*100))}%"

    # Next 12 hours rows
    rows = []
    for h in hourly[:12]:
        hdt  = dt_local(h["dt"], tzinfo)
        ic   = safe_get(h, "weather", 0, "icon", default="")
        row = {
            "time": format_clock(hdt),
            "emoji": emoji_for(ic),
            "temp": fmt_temp(h.get("temp"), units),
            "pop": fmt_pop(h.get("pop")),
            "wind": fmt_wind(h.get("wind_speed", 0), h.get("wind_gust"), units),
            "desc": safe_get(h, "weather", 0, "description", default="").title()
        }
        rows.append(row)

    # Alerts
    alert_blocks = []
    for a in alerts:
        start = dt_local(a.get("start", 0), tzinfo)
        end   = dt_local(a.get("end", 0), tzinfo)
        alert_blocks.append(
            f"<li><b>{a.get('event','Alert')}</b> – {a.get('sender_name','')}"
            f" ({format_clock(start)}–{format_clock(end)}): {a.get('description','').splitlines()[0]}</li>"
        )

    # Units suffixes
    temp_fmt = "F" if units == "imperial" else "C" if units == "metric" else "K"
    vis_km = None
    if visibility is not None:
        # OWM visibility in meters
        vis_km = visibility / 1609.34 if units == "imperial" else visibility / 1000

    # Plain text
    lines = []
    lines.append(f"{city_display} — {cur_dt.strftime('%a, %b %d %I:%M %p')}")
    lines.append("-" * 60)
    lines.append(f"Now: {emoji_for(icon)} {desc}, {fmt_temp(temp, units)} (feels {fmt_temp(feels, units)})")
    lines.append(f"Humidity {humidity}% | Clouds {clouds}% | Pressure {pressure} hPa")
    if vis_km is not None:
        lines.append(f"Visibility ~{vis_km:.1f} {'mi' if units=='imperial' else 'km'}")
    lines.append(f"Wind {fmt_wind(wind_speed or 0, wind_gust, units)} | UV {uvi} ({uv_note(uvi)})")
    lines.append("")
    lines.append(f"Today: High {fmt_temp(tmax, units)}, Low {fmt_temp(tmin, units)}, POP {fmt_pop(pop_day)}")
    if rain_d:
        lines.append(f"Expected rain: {rain_d:.1f} {'in' if units=='imperial' else 'mm'}")
    if snow_d:
        lines.append(f"Snow: {snow_d:.1f} {'in' if units=='imperial' else 'mm'}")
    lines.append(f"Sunrise {format_clock(sunrise)} | Sunset {format_clock(sunset)}")
    lines.append("")
    lines.append(best_window_note)
    if tips:
        lines.append("Tips: " + " · ".join(tips))
    lines.append("")
    lines.append("Next 12 hours:")
    lines.append("Time  Temp  POP  Wind        Summary")
    for r in rows:
        lines.append(f"{r['time']:>5} {r['temp']:>5} {r['pop']:>4} {r['wind']:<11} {r['desc']}")
    if alert_blocks:
        lines.append("")
        lines.append("ALERTS:")
        for a in alert_blocks:
            # Strip basic tags for text version
            lines.append(" - " + a.replace("<li>", "").replace("</li>", "").replace("<b>", "").replace("</b>", ""))

    text = "\n".join(lines)

    # HTML
    def th(s): return f"<th style='padding:6px 8px;border-bottom:1px solid #eee;text-align:left'>{s}</th>"
    def td(s): return f"<td style='padding:6px 8px;border-bottom:1px solid #f3f3f3'>{s}</td>"

    html_rows = "\n".join(
        f"<tr>{td(r['time'])}{td(r['emoji'] + ' ' + r['temp'])}{td(r['pop'])}"
        f"{td(r['wind'])}{td(r['desc'])}</tr>"
        for r in rows
    )

    alerts_html = f"<ul>{''.join(alert_blocks)}</ul>" if alert_blocks else "<p>No active alerts.</p>"

    html = f"""
<html>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, 'Helvetica Neue', Arial, sans-serif; color:#111">
    <h2 style="margin:0">{city_display}</h2>
    <div style="color:#666; margin-bottom:10px">{cur_dt.strftime('%A, %B %d · %I:%M %p')}</div>

    <table role="presentation" cellspacing="0" cellpadding="0" style="border-collapse:collapse">
      <tr><td style="padding-right:16px;font-size:40px">{emoji_for(icon)}</td>
          <td>
            <div style="font-size:18px">{desc}</div>
            <div style="font-size:24px; font-weight:600">{fmt_temp(temp, units)} <span style="color:#666;font-weight:400">feels {fmt_temp(feels, units)}</span></div>
            <div style="color:#666">Humidity {humidity}% · Clouds {clouds}% · Pressure {pressure} hPa</div>
            <div style="color:#666">Wind {fmt_wind(wind_speed or 0, wind_gust, units)} · UV {uvi} ({uv_note(uvi)})</div>
          </td>
      </tr>
    </table>

    <h3 style="margin:18px 0 6px 0">Today</h3>
    <div>High {fmt_temp(tmax, units)}, Low {fmt_temp(tmin, units)}, POP {fmt_pop(pop_day)}</div>
    <div>Sunrise {format_clock(sunrise)} · Sunset {format_clock(sunset)}</div>
    {"<div>Expected rain: " + f"{rain_d:.1f} " + ("in" if units=='imperial' else "mm") + "</div>" if rain_d else ""}
    {"<div>Snow: " + f"{snow_d:.1f} " + ("in" if units=='imperial' else "mm") + "</div>" if snow_d else ""}

    <div style="margin-top:10px; color:#0a7">✅ {best_window_note}</div>
    {"<div style='margin-top:8px'>" + " · ".join(tips) + "</div>" if tips else ""}

    <h3 style="margin:18px 0 8px 0">Next 12 hours</h3>
    <table role="presentation" cellspacing="0" cellpadding="0" style="border-collapse:collapse; width:100%; max-width:680px;">
      <thead>
        <tr>{th('Time')}{th('Temp')}{th('POP')}{th('Wind')}{th('Summary')}</tr>
      </thead>
      <tbody>
        {html_rows}
      </tbody>
    </table>

    <h3 style="margin:18px 0 8px 0">Alerts</h3>
    {alerts_html}

    <div style="margin-top:18px;color:#888;font-size:12px">
      Data: OpenWeather One Call 3.0 · Units: {temp_fmt}
    </div>
  </body>
</html>
"""
    return html, text

# ---------- Main ----------

def main():
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(description="Send a rich morning weather email.")
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", required=True, help="Two-letter country code, e.g. US")
    parser.add_argument("--units", choices=["metric", "imperial", "standard"], default="imperial")
    args = parser.parse_args()

    api_key = os.getenv("OWM_API_KEY")
    if not api_key:
        raise SystemExit("Missing OWM_API_KEY")

    geo = geocode_city(api_key, args.city, args.country)
    data = onecall(api_key, geo.lat, geo.lon, args.units)

    tz_offset = int(data.get("timezone_offset", 0))
    tzinfo = timezone(timedelta(seconds=tz_offset))
    city_display = f"{geo.name}{', ' + geo.state if geo.state else ''} ({geo.country})"

    html, text = build_email(city_display, tzinfo, args.units, data)

    subj_prefix = os.getenv("EMAIL_SUBJECT_PREFIX", "").strip()
    today = dt_local(data.get("current", {}).get("dt", int(datetime.now().timestamp())), tzinfo)
    subject = f"{subj_prefix + ' ' if subj_prefix else ''}{geo.name}: {today.strftime('%a %b %d')} forecast"

    send_email(subject, html, text)
    print("Morning weather email sent.")

if __name__ == "__main__":
    main()
