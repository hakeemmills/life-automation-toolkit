# email_test.py
import os, ssl, smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv(override=True)

HOST = os.getenv("SMTP_HOST")
PORT = int(os.getenv("SMTP_PORT", "587"))
USER = os.getenv("SMTP_USER")
PWD  = os.getenv("SMTP_PASS")
FROM = os.getenv("EMAIL_FROM", USER)
TO   = os.getenv("EMAIL_TO", USER)
SUBJ = os.getenv("EMAIL_SUBJECT_PREFIX", "[Weather Alert]") + " SMTP test"

msg = EmailMessage()
msg["From"] = FROM
msg["To"] = TO
msg["Subject"] = SUBJ
msg.set_content("Hello from Life Automation Toolkit! This is a connectivity/auth test.")

context = ssl.create_default_context()

print(f"Connecting to {HOST}:{PORT} ...")
if PORT == 465:
    with smtplib.SMTP_SSL(HOST, PORT, context=context, timeout=20) as s:
        s.set_debuglevel(1)
        s.login(USER, PWD)
        s.send_message(msg)
else:
    with smtplib.SMTP(HOST, PORT, timeout=20) as s:
        s.set_debuglevel(1)
        s.ehlo()
        s.starttls(context=context)
        s.ehlo()
        s.login(USER, PWD)
        s.send_message(msg)

print("Sent!")
