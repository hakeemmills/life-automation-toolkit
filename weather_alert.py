#!/usr/bin/env python3
"""
Morning weather email with rich details:
- Current conditions
- Today's summary (sunrise/sunset, hi/lo, precip)
- 12-hour timeline
- 7-day outlook
- Government alerts (if any)

Env (.env):
  OWM_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
  EMAIL_FROM, EMAIL_TO, EMAIL_SUBJECT_PREFIX

Example:
  python weather_alert.py --city "Moncks Corner" --country US --units imperial --threshold 0.2
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from typing import Dict, List, Tuple

import requests
from dotenv import load_dotenv


# ---------- Helpers -----------------------------------------------------------

def load_env() -> None:
    # Make sure CI or local overrides pick up the latest values
    load_dotenv(override=True)


def geocode_city(api_key: str, city: str, country: str) -> Tuple[float, float]:
    url = "https://api.openweathermap.org/geo/1.0/direct"
    params = {"q": f"{city},{country}", "limit": 1, "appid": api_key}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise SystemExit(f"Location not found for {city}, {country}")
    return float(data[0]["lat"]), float(data[0]["lon"])


def onecall(api_key: str, lat: float, lon: float, units: str) -> Dict:
    # We exclude only "minutely" to keep current/hourly/daily/alerts
    url = "https://api.openweathermap.org/data/3.0/onecall"
    params = {
        "lat": lat,
        "lon": lon,
        "exclude": "minutely",
        "appid": api_key,
        "units": units,
    }
    r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def deg_to_compass(deg: float) -> str:
    dirs = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    ]
    idx = int((deg / 22.5) + 0.5) % 16
    return dirs[idx]


def fmt_temp(v: float, units: str) -> str:
    unit = "°F" if units == "imperial" else ("°C" if units == "metric" else "K")
    return f"{round(v):d}{unit}"


def fmt_speed(v: float, units: str) -> str:
    # OpenWeather: imperial=mi/h, metric=meters/sec by default; API returns m/s in metric
    if units == "imperial":
        return f"{round(v):d} mph"
    # Convert m/s → km/h for readability
    return f"{round(v * 3.6):d} km/h"


def fmt_length_mm_to_unit(mm: float, units: str) -> str:
    if units == "imperial":
        inches = mm / 25.4
        return f"{inches:.2f} in"
    return f"{mm:.1f} mm"


def local_ts(ts: int, tz_offset: int) -> datetime:
    # One Call returns `timezone_offset` seconds; convert to a tz-aware datetime
    return datetime.utcfromtimestamp(ts).replace(tzinfo=timezone.utc) + timedelta(seconds=tz_offset)


def first_precip_hour(hours: List[Dict], tz_off: int, threshold: float) -> str | None:
    for h in hours:
        pop = float(h.get("pop", 0.0))
        has_rain = "rain" in h and h["rain"].get("1h", 0) > 0
        has_snow = "snow" in h and h["snow"].get("1h", 0) > 0
        if pop >= threshold or has_rain or has_snow:
            return local_ts(h["dt"], tz_off).strftime("%I:%M %p").lstrip("0")
    return None


# ---------- Email builder -----------------------------------------------------

def build_plain_text(
    city: str, country: str, units: str, data: Dict, threshold: float, hours_window: int
) -> str:
    tz_off = int(data.get("timezone_offset", 0))
    cur = data["current"]
    hour = data["hourly"]
    daily = data["daily"]

    now = local_ts(cur["dt"], tz_off).strftime("%a %b %d, %I:%M %p").lstrip("0")
    desc = cur["weather"][0]["description"].capitalize()

    wind_spd = fmt_speed(cur.get("wind_speed", 0.0), units)
    wind_gust = cur.get("wind_gust")
    wind_dir = deg_to_compass(cur.get("wind_deg", 0))
    gust_txt = f", gusts {fmt_speed(wind_gust, units)}" if wind_gust else ""

    vis_km = cur.get("visibility", 0) / 1000.0
    uv = cur.get("uvi", 0.0)

    # Today's block
    tdy = daily[0]
    sr = local_ts(tdy["sunrise"], tz_off)
    ss = local_ts(tdy["sunset"], tz_off)
    daylight = ss - sr

    hi = fmt_temp(tdy["temp"]["max"], units)
    lo = fmt_temp(tdy["temp"]["min"], units)
    tdy_desc = tdy["weather"][0]["description"].capitalize()
    tdy_pop = int(round(tdy.get("pop", 0.0) * 100))
    tdy_rain_mm = float(tdy.get("rain", 0.0))
    tdy_snow_mm = float(tdy.get("snow", 0.0))

    # First precip hour (next hours_window)
    first_hit = first_precip_hour(hour[:hours_window], tz_off, threshold)

    # 12h timeline rows
    timeline_lines = []
    for h in hour[:hours_window]:
        hts = local_ts(h["dt"], tz_off).strftime("%I %p").lstrip("0")
        pop = int(round(h.get("pop", 0.0) * 100))
        t = fmt_temp(h["temp"], units)
        fl = fmt_temp(h.get("feels_like", h["temp"]), units)
        wspd = fmt_speed(h.get("wind_speed", 0.0), units)
        wgst = h.get("wind_gust")
        gust = f"/{fmt_speed(wgst, units)}" if wgst else ""
        rain_mm = h.get("rain", {}).get("1h", 0.0)
        snow_mm = h.get("snow", {}).get("1h", 0.0)
        precip = ""
        if rain_mm:
            precip = f"rain {fmt_length_mm_to_unit(rain_mm, units)}"
        if snow_mm:
            comma = ", " if precip else ""
            precip += f"{comma}snow {fmt_length_mm_to_unit(snow_mm, units)}"
        precip = precip or "-"
        timeline_lines.append(
            f"{hts:>5} | {pop:>3}% | {t:>5} (feels {fl:>5}) | wind {wspd}{gust:<7} | {precip}"
        )

    # 7-day outlook
    days_lines = []
    for d in daily[:7]:
        dts = local_ts(d["dt"], tz_off).strftime("%a")
        dhi = fmt_temp(d["temp"]["max"], units)
        dlo = fmt_temp(d["temp"]["min"], units)
        dd = d["weather"][0]["description"].capitalize()
        dpop = int(round(d.get("pop", 0.0) * 100))
        days_lines.append(f"{dts:>3}: {dhi}/{dlo}, {dpop:>3}% - {dd}")

    # Alerts
    alerts = data.get("alerts", [])
    alerts_lines = []
    for a in alerts:
        start = local_ts(a["start"], tz_off).strftime("%a %I:%M %p").lstrip("0")
        end = local_ts(a["end"], tz_off).strftime("%a %I:%M %p").lstrip("0")
        alerts_lines.append(
            f"- {a['event']} ({a.get('sender_name','')}) — {start} to {end}"
        )

    # Compose plain text
    out = []
    out.append(f"{city}, {country} — {now}")
    out.append(f"Current: {desc}, {fmt_temp(cur['temp'], units)} "
               f"(feels {fmt_temp(cur.get('feels_like', cur['temp']), units)})")
    out.append(
        f"Wind: {wind_dir} {wind_spd}{gust_txt} | Humidity: {cur.get('humidity', 0)}% | "
        f"Dew: {fmt_temp(cur.get('dew_point', 0.0), units)} | "
        f"Pressure: {cur.get('pressure', 0)} hPa | Visibility: {vis_km:.1f} km | UV: {uv:.1f}"
    )
    out.append("")
    out.append("Today")
    out.append(
        f"  {tdy_desc}. High {hi}, low {lo}. "
        f"POP {tdy_pop}%. "
        f"Sunrise {sr.strftime('%I:%M %p').lstrip('0')}, "
        f"Sunset {ss.strftime('%I:%M %p').lstrip('0')} "
        f"(Daylight {str(daylight).split('.')[0]})."
    )
    if tdy_rain_mm or tdy_snow_mm:
        extra = []
        if tdy_rain_mm:
            extra.append(f"rain {fmt_length_mm_to_unit(tdy_rain_mm, units)}")
        if tdy_snow_mm:
            extra.append(f"snow {fmt_length_mm_to_unit(tdy_snow_mm, units)}")
        out.append(f"  Expected precip: {', '.join(extra)}.")
    if first_hit:
        out.append(f"  Next precip near: {first_hit} (threshold {int(threshold * 100)}%).")
    else:
        out.append("  No significant precip in the next hours window.")

    out.append("")
    out.append(f"Next {hours_window} hours (local)")
    out.append("  Time | POP | Temp (feels) | Wind        | Precip")
    out.append("  -----+-----+-------------+-------------+----------------")
    out.extend(f"  {ln}" for ln in timeline_lines)

    out.append("")
    out.append("7-day outlook")
    out.extend(f"  {ln}" for ln in days_lines)

    if alerts_lines:
        out.append("")
        out.append("Alerts")
        out.extend(f"  {ln}" for ln in alerts_lines)

    return "\n".join(out)


def build_html(
    city: str, country: str, units: str, data: Dict, threshold: float, hours_window: int
) -> str:
    # Simple, inline styles to be email-client friendly
    tz_off = int(data.get("timezone_offset", 0))
    cur = data["current"]
    hour = data["hourly"]
    daily = data["daily"]

    def t(ts: int, fmt: str) -> str:
        return local_ts(ts, tz_off).strftime(fmt).lstrip("0")

    def safe_precip(h: Dict) -> str:
        rain_mm = h.get("rain", {}).get("1h", 0.0)
        snow_mm = h.get("snow", {}).get("1h", 0.0)
        parts = []
        if rain_mm:
            parts.append(f"rain {fmt_length_mm_to_unit(rain_mm, units)}")
        if snow_mm:
            parts.append(f"snow {fmt_length_mm_to_unit(snow_mm, units)}")
        return ", ".join(parts) if parts else "—"

    sr = t(daily[0]["sunrise"], "%I:%M %p")
    ss = t(daily[0]["sunset"], "%I:%M %p")
    daylight = (
        local_ts(daily[0]["sunset"], tz_off) - local_ts(daily[0]["sunrise"], tz_off)
    )
    first_hit = first_precip_hour(hour[:hours_window], tz_off, threshold) or "None"

    # Hourly rows
    hourly_rows = []
    for h in hour[:hours_window]:
        hourly_rows.append(
            f"<tr>"
            f"<td>{t(h['dt'], '%I %p')}</td>"
            f"<td style='text-align:right'>{int(round(h.get('pop', 0)*100))}%</td>"
            f"<td>{fmt_temp(h['temp'], units)} (feels {fmt_temp(h.get('feels_like', h['temp']), units)})</td>"
            f"<td> {fmt_speed(h.get('wind_speed', 0.0), units)}"
            f"{' / ' + fmt_speed(h['wind_gust'], units) if h.get('wind_gust') else ''}</td>"
            f"<td>{safe_precip(h)}</td>"
            f"</tr>"
        )

    # 7-day rows
    daily_rows = []
    for d in daily[:7]:
        daily_rows.append(
            f"<tr>"
            f"<td>{t(d['dt'], '%a')}</td>"
            f"<td>{fmt_temp(d['temp']['max'], units)} / {fmt_temp(d['temp']['min'], units)}</td>"
            f"<td style='text-align:right'>{int(round(d.get('pop', 0)*100))}%</td>"
            f"<td>{d['weather'][0]['description'].capitalize()}</td>"
            f"</tr>"
        )

    alerts_html = ""
    if data.get("alerts"):
        parts = []
        for a in data["alerts"]:
            parts.append(
                f"<li><b>{a['event']}</b> "
                f"({a.get('sender_name','')}) — {t(a['start'], '%a %I:%M %p')} to "
                f"{t(a['end'], '%a %I:%M %p')}</li>"
            )
        alerts_html = "<h3>Alerts</h3><ul>" + "".join(parts) + "</ul>"

    desc = cur["weather"][0]["description"].capitalize()
    wind_dir = deg_to_compass(cur.get("wind_deg", 0))
    wind_gust = (
        f" / gusts {fmt_speed(cur['wind_gust'], units)}" if cur.get("wind_gust") else ""
    )

    html = f"""
<html>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; color:#111;">
    <h2 style="margin:0 0 8px 0;">{city}, {country}</h2>
    <div style="margin-bottom:12px; color:#555;">{t(cur['dt'], '%a %b %d, %I:%M %p')}</div>

    <div style="margin-bottom:8px;">
      <b>Now:</b> {desc}, {fmt_temp(cur['temp'], units)}
      (feels {fmt_temp(cur.get('feels_like', cur['temp']), units)})
    </div>
    <div style="margin-bottom:16px; color:#333;">
      Wind: {wind_dir} {fmt_speed(cur.get('wind_speed', 0.0), units)}{wind_gust} |
      Humidity: {cur.get('humidity', 0)}% |
      Dew: {fmt_temp(cur.get('dew_point', 0.0), units)} |
      Pressure: {cur.get('pressure', 0)} hPa |
      Visibility: {cur.get('visibility', 0)/1000:.1f} km |
      UV: {cur.get('uvi', 0.0):.1f}
    </div>

    <h3 style="margin:18px 0 6px 0;">Today</h3>
    <div style="margin-bottom:12px;">
      {daily[0]['weather'][0]['description'].capitalize()}.
      High {fmt_temp(daily[0]['temp']['max'], units)}, low {fmt_temp(daily[0]['temp']['min'], units)}.
      POP {int(round(daily[0].get('pop', 0)*100))}%.
      Sunrise {sr}, Sunset {ss} (Daylight {str(daylight).split('.')[0]}).
      Next precip near: {first_hit} (threshold {int(threshold*100)}%).
    </div>

    <h3 style="margin:18px 0 6px 0;">Next {hours_window} hours</h3>
    <table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse; border-color:#ddd;">
      <thead style="background:#f7f7f7;">
        <tr><th>Time</th><th>POP</th><th>Temp (feels)</th><th>Wind</th><th>Precip</th></tr>
      </thead>
      <tbody>
        {''.join(hourly_rows)}
      </tbody>
    </table>

    <h3 style="margin:18px 0 6px 0;">7-day outlook</h3>
    <table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse; border-color:#ddd;">
      <thead style="background:#f7f7f7;">
        <tr><th>Day</th><th>High / Low</th><th>POP</th><th>Summary</th></tr>
      </thead>
      <tbody>
        {''.join(daily_rows)}
      </tbody>
    </table>

    {alerts_html}
  </body>
</html>
"""
    return html


# ---------- Email sender ------------------------------------------------------

def send_email(subject: str, plain: str, html: str) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pwd = os.getenv("SMTP_PASS")
    from_addr = os.getenv("EMAIL_FROM", user)
    to_addr = os.getenv("EMAIL_TO")

    if not all([host, port, user, pwd, from_addr, to_addr]):
        raise SystemExit("Missing SMTP configuration in environment/.env")

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    import smtplib
    import ssl

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ctx)
        s.login(user, pwd)
        s.send_message(msg)


# ---------- Main --------------------------------------------------------------

def main() -> None:
    load_env()

    parser = argparse.ArgumentParser(
        description="Send a detailed morning weather email."
    )
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", required=True, help="Two-letter country code, e.g. US")
    parser.add_argument("--units", choices=["metric", "imperial", "standard"], default="imperial")
    parser.add_argument("--threshold", type=float, default=0.2,
                        help="Precip probability threshold for 'next precip' marker (0-1).")
    parser.add_argument("--hours", type=int, default=12, help="Timeline hours to include (default: 12).")
    args = parser.parse_args()

    api_key = os.getenv("OWM_API_KEY")
    if not api_key:
        raise SystemExit("Set OWM_API_KEY in environment or .env file.")

    lat, lon = geocode_city(api_key, args.city, args.country)
    data = onecall(api_key, lat, lon, args.units)

    plain = build_plain_text(args.city, args.country, args.units, data, args.threshold, args.hours)
    html = build_html(args.city, args.country, args.units, data, args.threshold, args.hours)

    prefix = os.getenv("EMAIL_SUBJECT_PREFIX", "[Weather]")
    today = datetime.now().strftime("%a %b %d")
    subj = f"{prefix} {args.city} — {today}"

    send_email(subj, plain, html)
    print("Email sent.")


if __name__ == "__main__":
    main()
