import os, smtplib, ssl
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("SMTP_HOST")
port = int(os.getenv("SMTP_PORT", "587"))
user = os.getenv("SMTP_USER")
pwd = os.getenv("SMTP_PASS")
from_addr = os.getenv("EMAIL_FROM", user)
to_addr = os.getenv("EMAIL_TO")

msg = EmailMessage()
msg["From"] = from_addr
msg["To"] = to_addr
msg["Subject"] = "SMTP test"
msg.set_content("If you see this, SMTP login worked.")

ctx = ssl.create_default_context()
with smtplib.SMTP(host, port) as s:
    s.starttls(context=ctx)
    s.login(user, pwd)
    s.send_message(msg)
print("Sent!")
