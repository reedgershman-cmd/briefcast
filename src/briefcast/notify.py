from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from .config import Config

log = logging.getLogger(__name__)

EMAIL_CSS = (
    "font-family:Georgia,serif;max-width:680px;margin:0 auto;line-height:1.6;"
    "color:#1a1a1a;padding:0 12px"
)


def smtp_creds() -> tuple[str, str] | None:
    user = os.environ.get("BRIEFCAST_SMTP_USER", "")
    pw = os.environ.get("BRIEFCAST_SMTP_PASS", "")
    return (user, pw) if user and pw else None


def send_email(to: list[str], subject: str, html: str, text: str,
               attachments: list[Path] | None = None,
               from_name: str = "The Weekly Signal") -> bool:
    creds = smtp_creds()
    if not creds:
        log.info("email skipped: BRIEFCAST_SMTP_USER/PASS not set")
        return False
    user, pw = creds
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{user}>"
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for path in attachments or []:
        msg.add_attachment(path.read_bytes(), maintype="text", subtype="plain",
                           filename=path.name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, pw)
        smtp.send_message(msg)
    log.info("emailed %s: %s", to, subject)
    return True


def send_weekly_email(cfg: Config, entry: dict, brief_md: str,
                      brief_path: Path, text_path: Path) -> bool:
    """Email the listener the written brief + episode link, with the brief and
    podcast text attached for the knowledge-base workflow."""
    if not cfg.get("delivery", "email_enabled", default=False):
        return False
    to = cfg.get("delivery", "email_to", default=[])
    to = [t for t in to if t and "@" in t and "REPLACE" not in t.upper()]
    if not to:
        log.info("email skipped: no listener address configured")
        return False

    from .publish import _md_to_html

    base = cfg.get("publish", "base_url", default="").rstrip("/")
    token = cfg.get("publish", "feed_token")
    page = f"{base}/p/{token}/{entry['date']}.html"
    minutes = round(entry["duration_s"] / 60)

    html = f"""<div style="{EMAIL_CSS}">
<p style="font-size:1.1em"><strong>Your episode is ready</strong> — {minutes} minutes,
{entry['clips_used']} sound bites. It's already in your podcast app, or
<a href="{page}">listen right here</a>.</p>
<p style="color:#555">The two attachments (brief + full episode text) are for your
knowledge base — paste them into your ChatGPT project. To change your podcast lineup,
just reply to this email (e.g. "add the All-In podcast" or "drop the physics one").</p>
<hr>
{_md_to_html(brief_md)}
</div>"""
    text = (f"Your Weekly Signal is ready ({minutes} min).\nListen: {page}\n\n"
            f"Reply to this email to change your podcast lineup.\n\n{brief_md}")
    return send_email(to, f"The Weekly Signal — {entry['title']}", html, text,
                      attachments=[brief_path, text_path],
                      from_name=cfg.get("delivery", "email_from_name",
                                        default="The Weekly Signal"))
