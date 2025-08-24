import os, ssl, smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("SMTP_HOST", "smtp.gmail.com")
port = int(os.getenv("SMTP_PORT", "587"))
user = os.getenv("SMTP_USER")
pwd  = os.getenv("SMTP_PASS")
from_addr = os.getenv("EMAIL_FROM", user)
to_addr   = os.getenv("EMAIL_TO", user)

msg = EmailMessage()
msg["From"] = from_addr
msg["To"] = to_addr
msg["Subject"] = "Gmail SMTP test"
msg.set_content("If you received this, Gmail SMTP + App Password works!")

ctx = ssl.create_default_context()
if port == 465:
    with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
        s.login(user, pwd)
        s.send_message(msg)
else:
    with smtplib.SMTP(host, port, timeout=20) as s:
        s.ehlo(); s.starttls(context=ctx); s.ehlo()
        s.login(user, pwd)
        s.send_message(msg)

print("Sent!")
