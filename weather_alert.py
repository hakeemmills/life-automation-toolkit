#!/usr/bin/env python3
import argparse
import os
from datetime import datetime, timezone
import smtplib
import ssl

import requests
from email.message import EmailMessage
from dotenv import load_dotenv


def need(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise SystemExit(f"Missing env var: {name}")
    return v


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
    host = need("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = need("SMTP_USER")
    pwd = need("SMTP_PASS").replace(" ", "")  # app password often pasted with spaces
    from_addr = os.getenv("EMAIL_FROM", user)
    to_addr = need("EMAIL_TO")

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=20) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.ehlo()
        s.login(user, pwd)
        s.send_message(msg)


def main():
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(
        description="Email an alert if precipitation is expected in the next 12 hours."
    )
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", required=True, help="Two-letter country code, e.g. US")
    parser.add_argument("--units", choices=["metric", "imperial", "standard"], default="metric")
    parser.add_argument("--threshold", type=float, default=0.2, help="Probability threshold (0-1)")
    parser.add_argument(
        "--force", action="store_true",
        help="Always send an email summary even if precipitation is below threshold."
    )
    args = parser.parse_args()

    api_key = need("OWM_API_KEY")
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

    prefix = os.getenv("EMAIL_SUBJECT_PREFIX", "")
    if alert_slots:
        first = alert_slots[0]
        local = first[0].astimezone()
        prob = int(first[1] * 100)
        desc = first[2]

        subject = f"{prefix} Rain alert for {args.city}".strip()
        body = (
            f"Weather alert for {args.city}, {args.country}\n\n"
            f"- Chance: {prob}%\n"
            f"- Condition: {desc}\n"
            f"- Around: {local.strftime('%I:%M %p %Z')}\n"
            f"- Threshold: {args.threshold}\n"
            f"- Units: {args.units}\n"
        )
        send_email(subject, body)
        print("Email sent.")
    else:
        if args.force:
            # Send a summary email even if no precipitation meets the threshold
            subject = f"{prefix} No rain expected (summary) for {args.city}".strip()
            lines = [f"Next 12h summary for {args.city}, {args.country} (no hour >= {args.threshold} pop)\n"]
            if hours:
                for h in hours:
                    dt_local = datetime.fromtimestamp(h["dt"], tz=timezone.utc).astimezone()
                    pop = int(h.get("pop", 0.0) * 100)
                    desc = h.get("weather", [{}])[0].get("description", "")
                    lines.append(f"- {dt_local.strftime('%I:%M %p %Z')}: {pop}% — {desc}")
            else:
                lines.append("- No hourly data returned.")
            send_email(subject, "\n".join(lines))
            print("Email summary sent (force).")
        else:
            print("No precipitation above threshold in next 12 hours; email not sent.")


if __name__ == "__main__":
    main()
