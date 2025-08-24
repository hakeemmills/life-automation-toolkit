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
    """OpenWeather One Call 3.0 hourly. Raises on 401 if plan not enabled."""
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
    data = r.json()
    hours = data.get("hourly", [])
    out = []
    for h in hours[:12]:
        dt = datetime.fromtimestamp(h["dt"], tz=timezone.utc)
        pop = h.get("pop", 0.0)
        desc = h.get("weather", [{}])[0].get("description", "")
        out.append((dt, pop, desc))
    print("[info] using One Call 3.0 hourly")
    return out


def forecast_fallback(api_key: str, lat: float, lon: float, units: str = "metric"):
    """Free 3-hour forecast (first 4 slots ≈ next 12h)."""
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {"lat": lat, "lon": lon, "appid": api_key, "units": units}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    slots = data.get("list", [])[:4]  # 4 x 3h = 12h
    out = []
    for it in slots:
        dt = datetime.fromtimestamp(it["dt"], tz=timezone.utc)
        pop = it.get("pop", 0.0)
        desc = it.get("weather", [{}])[0].get("description", "")
        out.append((dt, pop, desc))
    print("[info] using 2.5 forecast fallback (3-hour bins)")
    return out


def send_email(subject: str, body: str):
    host = need("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = need("SMTP_USER")
    pwd = need("SMTP_PASS").replace(" ", "")  # Gmail app passwords are often pasted with spaces
    from_addr = os.getenv("EMAIL_FROM", user)
    to_addr = need("EMAIL_TO")

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
            s.login(user, pwd)
            s.send_message(msg)
    else:
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
        "--force",
        action="store_true",
        help="Always send an email summary even if precipitation is below threshold.",
    )
    args = parser.parse_args()

    api_key = need("OWM_API_KEY")
    lat, lon = geocode_city(api_key, args.city, args.country)

    # Get next-12h slots from One Call 3.0, or fall back to free 2.5 forecast
    try:
        slots = onecall_hourly(api_key, lat, lon, args.units)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            slots = forecast_fallback(api_key, lat, lon, args.units)
        else:
            raise

    threshold = args.threshold
    hits = [(dt, pop, desc) for dt, pop, desc in slots if pop >= threshold]

    prefix = os.getenv("EMAIL_SUBJECT_PREFIX", "")
    if hits:
        first_dt, first_pop, first_desc = hits[0]
        local = first_dt.astimezone()
        prob = int(first_pop * 100)
        subject = f"{prefix} Rain alert for {args.city}".strip()
        body = (
            f"Weather alert for {args.city}, {args.country}\n\n"
            f"- Chance: {prob}%\n"
            f"- Condition: {first_desc}\n"
            f"- Around: {local.strftime('%I:%M %p %Z')}\n"
            f"- Threshold: {threshold}\n"
            f"- Units: {args.units}\n"
        )
        send_email(subject, body)
        print("Email sent.")
    else:
        if args.force:
            subject = f"{prefix} No rain expected (summary) for {args.city}".strip()
            lines = [
                f"Next 12h summary for {args.city}, {args.country} (no slot >= {threshold} pop)\n"
            ]
            for dt, pop, desc in slots:
                local = dt.astimezone()
                lines.append(
                    f"- {local.strftime('%I:%M %p %Z')}: {int(pop*100)}% — {desc}"
                )
            send_email(subject, "\n".join(lines))
            print("Email summary sent (force).")
        else:
            print("No precipitation above threshold in next 12 hours; email not sent.")


if __name__ == "__main__":
    main()
