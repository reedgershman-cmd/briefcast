from __future__ import annotations

import html
import logging
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from .config import ROOT, Config

log = logging.getLogger(__name__)

DOCS = ROOT / "docs"

FEED_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{title}</title>
  <link>{base}/p/{token}/</link>
  <language>en-us</language>
  <description>{description}</description>
  <itunes:author>{author}</itunes:author>
  <itunes:block>Yes</itunes:block>
  <itunes:explicit>false</itunes:explicit>
  <atom:link href="{base}/p/{token}/feed.xml" rel="self" type="application/rss+xml"/>
{items}
</channel>
</rss>
"""

ITEM_TEMPLATE = """  <item>
    <title>{title}</title>
    <guid isPermaLink="false">{guid}</guid>
    <pubDate>{pubdate}</pubDate>
    <link>{page_url}</link>
    <description>{description}</description>
    <enclosure url="{mp3_url}" length="{bytes}" type="audio/mpeg"/>
    <itunes:duration>{duration}</itunes:duration>
    <itunes:episodeType>full</itunes:episodeType>
  </item>
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="robots" content="noindex,nofollow">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
 body{{font-family:Georgia,serif;max-width:720px;margin:2rem auto;padding:0 1rem;line-height:1.6;color:#1a1a1a}}
 audio{{width:100%;margin:1rem 0}}
 table{{border-collapse:collapse;width:100%;font-size:.9em}}
 td,th{{border:1px solid #ddd;padding:.4rem .6rem;text-align:left}}
 a{{color:#0a5}}  h1{{font-size:1.5rem}}
 .dl{{background:#f5f5f2;padding:.8rem 1rem;border-radius:8px;margin:1rem 0}}
</style></head><body>
<h1>{title}</h1>
<p><em>{date} · {minutes} min · {clips} sound bites</em></p>
<audio controls preload="none" src="{mp3}"></audio>
<div class="dl">Downloads:
 <a href="{mp3}">episode MP3</a> ·
 <a href="{brief_md}">written brief (Markdown)</a> ·
 <a href="{text_txt}">full podcast text</a> — paste the brief and text into ChatGPT/Claude for the knowledge base.</div>
<hr>
{brief_html}
</body></html>
"""


def publish_episode(
    cfg: Config,
    state: dict,
    date_str: str,
    episode_title: str,
    mp3_src: Path,
    brief_md: str,
    podcast_text: str,
    duration_s: int,
    clips_used: int,
    description: str,
) -> dict:
    token = cfg.get("publish", "feed_token")
    base = cfg.get("publish", "base_url", default="").rstrip("/")
    pdir = DOCS / "p" / token
    (pdir / "episodes").mkdir(parents=True, exist_ok=True)
    (pdir / "briefs").mkdir(parents=True, exist_ok=True)
    (pdir / "text").mkdir(parents=True, exist_ok=True)

    mp3_dest = pdir / "episodes" / f"{date_str}.mp3"
    shutil.copy2(mp3_src, mp3_dest)
    (pdir / "briefs" / f"{date_str}.md").write_text(brief_md)
    (pdir / "text" / f"{date_str}.txt").write_text(podcast_text)

    entry = {
        "date": date_str,
        "title": episode_title,
        "bytes": mp3_dest.stat().st_size,
        "duration_s": duration_s,
        "clips_used": clips_used,
        "description": description,
    }
    state["published"] = [e for e in state["published"] if e["date"] != date_str]
    state["published"].append(entry)
    state["published"].sort(key=lambda e: e["date"])

    _write_page(cfg, pdir, entry, brief_md)
    _write_feed(cfg, state, pdir, base, token)
    _write_site_chrome(pdir)
    log.info("published episode %s -> %s", date_str, mp3_dest)
    return entry


def _md_to_html(md: str) -> str:
    """Small dependency-free Markdown renderer covering what the brief uses:
    headers, bold, tables, numbered/bulleted lists, paragraphs."""
    import re

    out, in_table, in_list = [], False, False
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            tag = "th" if not in_table else "td"
            if not in_table:
                out.append("<table>")
                in_table = True
            out.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        m = re.match(r"^(#{1,4})\s+(.*)", stripped)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue
        if re.match(r"^[-*]\s+", stripped):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(re.sub(r'^[-*]\\s+', '', stripped))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if stripped:
            out.append(f"<p>{_inline(stripped)}</p>")
    if in_table:
        out.append("</table>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _inline(text: str) -> str:
    import re

    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    return text


def _write_page(cfg: Config, pdir: Path, entry: dict, brief_md: str) -> None:
    page = PAGE_TEMPLATE.format(
        title=html.escape(entry["title"]),
        date=entry["date"],
        minutes=round(entry["duration_s"] / 60),
        clips=entry["clips_used"],
        mp3=f"episodes/{entry['date']}.mp3",
        brief_md=f"briefs/{entry['date']}.md",
        text_txt=f"text/{entry['date']}.txt",
        brief_html=_md_to_html(brief_md),
    )
    (pdir / f"{entry['date']}.html").write_text(page)


def _write_feed(cfg: Config, state: dict, pdir: Path, base: str, token: str) -> None:
    keep = cfg.get("publish", "feed_keep", default=26)
    items = []
    for e in reversed(state["published"][-keep:]):
        h, rem = divmod(e["duration_s"], 3600)
        m, s = divmod(rem, 60)
        pub = datetime.strptime(e["date"], "%Y-%m-%d").replace(
            hour=11, tzinfo=timezone.utc)
        items.append(ITEM_TEMPLATE.format(
            title=html.escape(e["title"]),
            guid=f"briefcast-{e['date']}",
            pubdate=format_datetime(pub),
            page_url=f"{base}/p/{token}/{e['date']}.html",
            description=html.escape(e["description"][:900]),
            mp3_url=f"{base}/p/{token}/episodes/{e['date']}.mp3",
            bytes=e["bytes"],
            duration=f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}",
        ))
    feed = FEED_TEMPLATE.format(
        title=html.escape(cfg.get("episode", "show_title", default="The Weekly Signal")),
        base=base, token=token,
        description=html.escape(cfg.get("episode", "show_description", default="").strip()),
        author=html.escape(cfg.get("episode", "show_author", default="Briefcast")),
        items="".join(items),
    )
    (pdir / "feed.xml").write_text(feed)


def _write_site_chrome(pdir: Path) -> None:
    (DOCS / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (DOCS / "index.html").write_text(
        "<!DOCTYPE html><meta name='robots' content='noindex'><title>·</title>")
    latest_links = "\n".join(
        f"<li><a href='{p.name}'>{p.stem}</a></li>"
        for p in sorted(pdir.glob("*.html"), reverse=True)
        if p.name != "index.html"
    )
    (pdir / "index.html").write_text(
        "<!DOCTYPE html><meta charset='utf-8'><meta name='robots' content='noindex,nofollow'>"
        "<title>The Weekly Signal</title><body style='font-family:Georgia,serif;max-width:720px;"
        f"margin:2rem auto'><h1>The Weekly Signal — all episodes</h1><ul>{latest_links}</ul></body>")
