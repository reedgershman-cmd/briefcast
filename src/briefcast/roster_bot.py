"""Email-driven roster management.

The listener replies to the weekly email (or writes a new one) in plain English:
"add the All-In podcast", "drop the physics one", "swap EconTalk for Odd Lots".
This bot polls the inbox, parses intent, resolves shows to RSS feeds via Apple's
public podcast search, edits podcasts.yaml, and replies with a confirmation.
No GitHub, no YAML, no URLs ever touch the listener.
"""
from __future__ import annotations

import email
import email.header
import imaplib
import json
import logging
import os
import re
from pathlib import Path

import httpx
import yaml

from . import feeds, notify
from .config import ROOT, Config, Podcast

log = logging.getLogger(__name__)

ROSTER_HEADER = """# The podcast roster. To ADD a show: add a block with a name and its RSS feed url.
# To PAUSE a show without deleting it: set active: false.
# To SUBSTITUTE: pause one, add another. The pipeline picks up changes on the next weekly run.
# Listeners never edit this by hand — they email the bot (see roster_bot.py).
#
# priority: 1 = always include if it published this week; 2 = include if room; 3 = only on slow weeks.

"""

PARSE_SYSTEM = """You manage the podcast lineup for a private weekly podcast digest. \
You read an email from the listener and decide what roster changes they want, if any."""

PARSE_PROMPT = """CURRENT ROSTER (shows marked active: false are paused):
{roster}

EMAIL FROM THE LISTENER:
Subject: {subject}
{body}

Decide what roster changes the listener wants. "The physics one" or similar vague
references should be matched to the roster show they most plausibly mean. If the email
is not about roster changes (thanks, questions, comments), return no actions.

Return ONLY a JSON object:
{{
  "actions": [
    {{"op": "add", "show": "name to search for"}} |
    {{"op": "remove", "show": "exact roster show name"}}
  ],
  "note": "one friendly sentence to include in the reply, or empty string"
}}"""


def resolve_feed(show_name: str) -> tuple[str, str] | None:
    """Look up a show on Apple's podcast directory. Returns (canonical_name, feed_url)."""
    resp = httpx.get(
        "https://itunes.apple.com/search",
        params={"media": "podcast", "limit": 3, "term": show_name},
        timeout=20,
    )
    resp.raise_for_status()
    for hit in resp.json().get("results", []):
        feed_url = hit.get("feedUrl")
        if not feed_url:
            continue
        name = hit.get("collectionName", show_name)
        try:
            podcast = Podcast(name=name, feed=feed_url)
            parsed = feeds.fetch_feed(podcast)
            if feeds.episodes_from_feed(podcast, parsed):
                return name, feed_url
        except Exception as e:
            log.warning("feed for %r failed validation: %s", name, e)
    return None


def load_roster_raw(path: Path) -> list[dict]:
    with open(path) as f:
        return yaml.safe_load(f)["podcasts"]


def save_roster(path: Path, roster: list[dict]) -> None:
    body = yaml.safe_dump({"podcasts": roster}, sort_keys=False,
                          allow_unicode=True, width=100)
    path.write_text(ROSTER_HEADER + body)


def parse_email(subject: str, body: str, roster: list[dict]) -> dict:
    roster_txt = "\n".join(
        f"- {p['name']} (active: {p.get('active', True)})" for p in roster)
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic

            msg = anthropic.Anthropic().messages.create(
                model="claude-sonnet-5",
                max_tokens=1000,
                system=PARSE_SYSTEM,
                messages=[{"role": "user", "content": PARSE_PROMPT.format(
                    roster=roster_txt, subject=subject, body=body[:4000])}],
            )
            text = "".join(b.text for b in msg.content if b.type == "text")
            return json.loads(text[text.find("{"): text.rfind("}") + 1])
        except Exception as e:
            log.warning("LLM parse failed, falling back to regex: %s", e)
    return _regex_parse(subject + "\n" + body)


def _regex_parse(text: str) -> dict:
    actions = []
    for m in re.finditer(r"\b(?:add|follow|include)\s+(?:the\s+)?([^\n.,;!?]{3,60})",
                         text, re.I):
        actions.append({"op": "add", "show": m.group(1).strip()})
    for m in re.finditer(r"\b(?:remove|drop|delete|unsubscribe(?:\s+from)?|pause)\s+"
                         r"(?:the\s+)?([^\n.,;!?]{3,60})", text, re.I):
        actions.append({"op": "remove", "show": m.group(1).strip()})
    for m in re.finditer(r"\b(?:swap|substitute|replace)\s+(?:the\s+)?([^\n.,;!?]{3,60}?)"
                         r"\s+(?:for|with)\s+(?:the\s+)?([^\n.,;!?]{3,60})", text, re.I):
        actions.append({"op": "remove", "show": m.group(1).strip()})
        actions.append({"op": "add", "show": m.group(2).strip()})
    return {"actions": actions, "note": ""}


def apply_actions(actions: list[dict], roster: list[dict]) -> tuple[list[dict], list[str]]:
    """Returns (new_roster, human-readable change lines)."""
    changes = []
    for action in actions:
        op, show = action.get("op"), (action.get("show") or "").strip()
        if not show:
            continue
        if op == "remove":
            match = _find(show, roster)
            if match and match.get("active", True):
                match["active"] = False
                changes.append(f"Removed: {match['name']}")
            elif match:
                changes.append(f"{match['name']} was already off the lineup")
            else:
                changes.append(f"Couldn't find \"{show}\" in the current lineup")
        elif op == "add":
            match = _find(show, roster)
            if match:
                if match.get("active", True):
                    changes.append(f"{match['name']} is already in the lineup")
                else:
                    match["active"] = True
                    changes.append(f"Added back: {match['name']}")
                continue
            resolved = resolve_feed(show)
            if resolved:
                name, feed_url = resolved
                roster.append({"name": name, "feed": feed_url,
                               "priority": 2, "active": True})
                changes.append(f"Added: {name}")
            else:
                changes.append(
                    f"Couldn't find a working podcast feed for \"{show}\" — "
                    f"try the exact show name as it appears in Apple Podcasts")
    return roster, changes


def _find(show: str, roster: list[dict]) -> dict | None:
    needle = show.casefold()
    for p in roster:
        name = p["name"].casefold()
        if needle == name or needle in name or name in needle:
            return p
    return None


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    return "".join(
        p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else p
        for p, enc in parts)


def _body_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8",
                                          errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(msg.get_content_charset() or "utf-8",
                          errors="replace") if payload else ""


def check_inbox(cfg: Config, roster_path: Path = ROOT / "podcasts.yaml") -> bool:
    """Poll the inbox once. Returns True if the roster changed."""
    creds = notify.smtp_creds()
    if not creds:
        log.info("roster bot skipped: BRIEFCAST_SMTP_USER/PASS not set")
        return False
    user, pw = creds
    allowed = {a.casefold() for a in
               cfg.get("roster_bot", "allowed_senders", default=[])
               if a and "REPLACE" not in a.upper()}
    if not allowed:
        log.info("roster bot skipped: no allowed_senders configured")
        return False

    changed = False
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(user, pw)
        imap.select("INBOX")
        _, data = imap.search(None, "UNSEEN")
        for num in (data[0].split() if data and data[0] else []):
            _, msg_data = imap.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg_data[0][1])
            sender = email.utils.parseaddr(msg.get("From", ""))[1].casefold()
            subject = _decode(msg.get("Subject"))
            if sender not in allowed:
                log.info("ignoring email from %s", sender)
                imap.store(num, "+FLAGS", "\\Seen")
                continue

            body = _body_text(msg)
            log.info("processing email from %s: %r", sender, subject[:60])
            roster = load_roster_raw(roster_path)
            parsed = parse_email(subject, body, roster)
            actions = parsed.get("actions", [])
            if actions:
                roster, changes = apply_actions(actions, roster)
                save_roster(roster_path, roster)
                changed = True
                lineup = "\n".join(
                    f"  • {p['name']}" for p in roster if p.get("active", True))
                note = parsed.get("note") or ""
                reply = (f"Done!\n\n" + "\n".join(f"• {c}" for c in changes) +
                         (f"\n\n{note}" if note else "") +
                         f"\n\nYour current lineup:\n{lineup}\n\n"
                         f"Changes take effect with next Monday's episode.")
                notify.send_email(
                    [sender], f"Re: {subject}" if subject else "Your podcast lineup",
                    html=f"<div style='{notify.EMAIL_CSS}'><pre style='font-family:inherit;"
                         f"white-space:pre-wrap'>{reply}</pre></div>",
                    text=reply,
                    from_name=cfg.get("delivery", "email_from_name",
                                      default="The Weekly Signal"))
                log.info("roster updated: %s", "; ".join(changes))
            else:
                log.info("no roster actions in email; leaving unanswered")
            imap.store(num, "+FLAGS", "\\Seen")
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return changed
