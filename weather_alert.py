#!/usr/bin/env python3
import argparse
import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from twilio.rest import Client


def geocode_city(api_key: str, city: str, country: str):
    url = "https://api.openweathermap.org/geo/1.0/direct"
    params = {"q": f"{city},{country}", "limit": 1, "appid": api_key}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise SystemExit(f"Location not found for {city}, {country}")
    return data[0]["lat"], data[0]["lon"]


def forecast_3h(api_key: str, lat: float, lon: float, units: str = "metric"):
    """
    Use the FREE 5-day/3-hour forecast endpoint.
    Docs: /data/2.5/forecast
    """
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {"lat": lat, "lon": lon, "appid": api_key, "units": units}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def send_sms(body: str):
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_ = os.getenv("TWILIO_FROM")
    to = os.getenv("ALERT_TO")
    if not all([sid, token, from_, to]):
        raise SystemExit("Missing Twilio config in environment variables.")
    client = Client(sid, token)
    msg = client.messages.create(body=body, from_=from_, to=to)
    return msg.sid


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="SMS if rain is expected in the next ~12 hours.")
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", required=True, help="Two-letter country code, e.g. US")
    parser.add_argument("--units", choices=["metric", "imperial", "standard"], default="metric")
    parser.add_argument("--threshold", type=float, default=0.2, help="Probability threshold for precipitation (0-1)")
    args = parser.parse_args()

    api_key = os.getenv("OWM_API_KEY")
    if not api_key:
        raise SystemExit("Set OWM_API_KEY in environment or .env file.")

    lat, lon = geocode_city(api_key, args.city, args.country)
    data = forecast_3h(api_key, lat, lon, args.units)

    # Next ~12 hours = first 4 forecast entries (each is 3 hours)
    items = (data.get("list") or [])[:4]
    if not items:
        print("No forecast data returned; SMS not sent.")
        return

    alert_slots = []
    for it in items:
        dt = datetime.fromtimestamp(it["dt"], tz=timezone.utc)
        pop = float(it.get("pop", 0.0) or 0.0)  # 0..1
        wdesc = ""
        w = it.get("weather") or []
        if w and isinstance(w, list):
            wdesc = w[0].get("description", "")
        # Also consider explicit rain/snow volume as a hint
        rain_mm = (it.get("rain") or {}).get("3h", 0.0) or 0.0
        snow_mm = (it.get("snow") or {}).get("3h", 0.0) or 0.0
        if pop >= args.threshold or rain_mm > 0 or snow_mm > 0:
            alert_slots.append((dt, pop, wdesc, rain_mm, snow_mm))

    if not alert_slots:
        print("No precipitation above threshold in next 12 hours; SMS not sent.")
        return

    first = alert_slots[0]
    local = first[0].astimezone()
    prob = int(first[1] * 100)
    desc = first[2] or "precipitation"
    rain_mm, snow_mm = first[3], first[4]

    extras = []
    if rain_mm:
        extras.append(f"rain ~{rain_mm:.1f}mm")
    if snow_mm:
        extras.append(f"snow ~{snow_mm:.1f}mm")
    extra_text = f" ({', '.join(extras)})" if extras else ""

    body = (
        f"Weather alert: {prob}% chance of {desc}{extra_text} "
        f"around {local.strftime('%I:%M %p')} in {args.city}. "
        f"(Next ~12h, threshold {args.threshold})"
    )
    sid = send_sms(body)
    print(f"SMS sent. SID: {sid}")


if __name__ == "__main__":
    main()
