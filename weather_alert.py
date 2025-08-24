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


def onecall_hourly(api_key: str, lat: float, lon: float, units: str = "metric"):
    url = "https://api.openweathermap.org/data/3.0/onecall"
    params = {"lat": lat, "lon": lon, "exclude": "minutely,daily,alerts", "appid": api_key, "units": units}
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
    parser = argparse.ArgumentParser(description="Send an SMS if rain is expected in the next 12 hours.")
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", required=True, help="Two-letter country code, e.g. US")
    parser.add_argument("--units", choices=["metric", "imperial", "standard"], default="metric")
    parser.add_argument("--threshold", type=float, default=0.2, help="Probability threshold for precipitation (0-1)")
    args = parser.parse_args()

    api_key = os.getenv("OWM_API_KEY")
    if not api_key:
        raise SystemExit("Set OWM_API_KEY in environment or .env file.")

    lat, lon = geocode_city(api_key, args.city, args.country)
    data = onecall_hourly(api_key, lat, lon, args.units)

    hours = data.get("hourly", [])[:12]
    alert_slots = []
    for h in hours:
        dt = datetime.fromtimestamp(h["dt"], tz=timezone.utc)
        pop = h.get("pop", 0.0)
        weather_desc = h.get("weather", [{}])[0].get("description", "")
        if pop >= args.threshold:
            alert_slots.append((dt, pop, weather_desc))

    if not alert_slots:
        print("No precipitation above threshold in next 12 hours; SMS not sent.")
        return

    first = alert_slots[0]
    local = first[0].astimezone()
    prob = int(first[1] * 100)
    desc = first[2]
    body = (
    f"Weather alert: {prob}% chance of {desc} around "
    f"{local.strftime('%I:%M %p')} in {args.city}. "
    f"(Next 12h threshold {args.threshold})"
    sid = send_sms(body)
    print(f"SMS sent. SID: {sid}")


if __name__ == "__main__":
    main()
