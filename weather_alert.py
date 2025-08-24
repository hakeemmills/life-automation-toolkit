#!/usr/bin/env python3
import argparse
import os
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv


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
    params = {
        "lat": lat,
        "lon": lon,
        "exclude": "minutely,daily,alerts",
        "appid": api_key,
        "units": units,
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def send_email(subject: str, body: str):
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    from_addr = os.getenv("EMAIL_FROM", user)
    to_addr = os.getenv("EMAIL_TO")

    missing = [k for k, v in {
        "SMTP_HOST": host, "SMTP_PORT": port, "SMTP_USER": user,
        "SMTP_PASS": password, "EMAIL_TO": to_addr
    }.items() if not v]
    if missing:
        raise SystemExit(f"Missing email config: {', '.join(missing)} in .env")

    msg = EmailMessage()
    msg["From"] = from_addr or user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    if port == 465:
        # SSL (implicit TLS)
        with smtplib.SMTP_SSL(host, port, context=context) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        # STARTTLS
        with smtplib.SMTP(host, port) as s:
            s.ehlo()
            s.starttls(context=context)
            s.ehlo()
            s.login(user, password)
            s.send_message(msg)


def main():
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(
        description="Email an alert if rain is expected in the next 12 hours."
    )
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", required=True, help="Two-letter country code, e.g. US")
    parser.add_argument("--units", choices=["metric", "imperial", "standard"], default="metric")
    parser.add_argument("--threshold", type=float, default=0.2, help="Probability threshold (0-1)")
    args = parser.parse_args()

    api_key = os.getenv("OWM_API_KEY")
    if not api_key:
        raise SystemExit("Set OWM_API_KEY in your .env file.")

    lat, lon = geocode_city(api_key, args.city, args.country)
    data = onecall_hourly(api_key, lat, lon, args.units)

    hours = data.get("hourly", [])[:12]
    alert_slots = []
    for h in hours:
        dt = datetime.fromtimestamp(h["dt"], tz=timezone.utc)
        pop = h.get("pop", 0.0)  # probability of precipitation
        weather_desc = h.get("weather", [{}])[0].get("description", "").capitalize()
        if pop >= args.threshold:
            alert_slots.append((dt, pop, weather_desc))

    if not alert_slots:
        print("No precipitation above threshold in next 12 hours; email not sent.")
        return

    first = alert_slots[0]
    local_time = first[0].astimezone()
    prob_pct = int(first[1] * 100)
    desc = first[2]

    subject_prefix = os.getenv("EMAIL_SUBJECT_PREFIX", "[Weather Alert]")
    subject = f"{subject_prefix} {args.city}: {prob_pct}% chance of {desc}"

    lines = [
        f"Location: {args.city}, {args.country}",
        f"Units: {args.units}",
        f"Alert threshold: {args.threshold}",
        "",
        f"First precip window: ~{local_time.strftime('%I:%M %p %Z')} ({prob_pct}% chance of {desc})",
        "",
        "Next 12 hours (UTC):",
    ]
    for dt, pop, desc in alert_slots[:6]:
        lines.append(f" - {dt.strftime('%Y-%m-%d %H:%M')}Z: {int(pop*100)}% {desc}")

    body = "\n".join(lines)

    send_email(subject, body)
    print("Email sent.")


if __name__ == "__main__":
    main()
