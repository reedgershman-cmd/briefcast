from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from .config import Podcast

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh) Briefcast/0.1 (private personal podcast digest)"


@dataclass
class SourceEpisode:
    podcast: str
    podcast_slug: str
    title: str
    audio_url: str
    published: datetime
    guid: str
    link: str = ""
    summary: str = ""
    duration_s: int | None = None
    # Filled in by later pipeline stages:
    episode_id: str = ""
    audio_path: str = ""
    segments: list = field(default_factory=list)

    def __post_init__(self):
        if not self.episode_id:
            digest = hashlib.md5(self.guid.encode()).hexdigest()[:8]
            self.episode_id = f"{self.podcast_slug}--{digest}"


def _parse_duration(entry) -> int | None:
    d = entry.get("itunes_duration")
    if not d:
        return None
    try:
        if ":" in str(d):
            parts = [int(p) for p in str(d).split(":")]
            while len(parts) < 3:
                parts.insert(0, 0)
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return int(d)
    except (ValueError, TypeError):
        return None


def fetch_feed(podcast: Podcast) -> feedparser.FeedParserDict:
    resp = httpx.get(
        podcast.feed,
        headers={"User-Agent": UA},
        follow_redirects=True,
        timeout=30,
    )
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def episodes_from_feed(podcast: Podcast, parsed) -> list[SourceEpisode]:
    out = []
    for entry in parsed.entries:
        ts = entry.get("published_parsed") or entry.get("updated_parsed")
        if not ts:
            continue
        published = datetime(*ts[:6], tzinfo=timezone.utc)
        audio_url = ""
        for enc in entry.get("enclosures", []):
            if "audio" in enc.get("type", "") or enc.get("href", "").split("?")[0].endswith((".mp3", ".m4a")):
                audio_url = enc["href"]
                break
        if not audio_url:
            for lnk in entry.get("links", []):
                if "audio" in lnk.get("type", ""):
                    audio_url = lnk["href"]
                    break
        if not audio_url:
            continue
        out.append(
            SourceEpisode(
                podcast=podcast.name,
                podcast_slug=podcast.slug,
                title=entry.get("title", "Untitled"),
                audio_url=audio_url,
                published=published,
                guid=entry.get("id", audio_url),
                link=entry.get("link", ""),
                summary=(entry.get("summary", "") or "")[:2000],
                duration_s=_parse_duration(entry),
            )
        )
    return out


def select_episodes(
    roster: list[Podcast],
    lookback_days: int,
    max_total: int,
    already_processed: set[str],
    now: datetime | None = None,
) -> list[SourceEpisode]:
    """Newest unprocessed episode per show within the lookback window; if room
    remains, second-newest from priority-1 shows. Very short episodes (<10 min,
    usually trailers) are skipped."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    per_show: dict[str, list[SourceEpisode]] = {}

    for podcast in roster:
        try:
            parsed = fetch_feed(podcast)
        except Exception as e:
            log.warning("feed failed for %s: %s", podcast.name, e)
            continue
        eps = [
            e
            for e in episodes_from_feed(podcast, parsed)
            if e.published >= cutoff
            and e.guid not in already_processed
            and (e.duration_s is None or e.duration_s >= 600)
        ]
        eps.sort(key=lambda e: e.published, reverse=True)
        if eps:
            per_show[podcast.name] = eps
        else:
            log.info("no new episodes for %s", podcast.name)

    prio = {p.name: p.priority for p in roster}
    firsts = sorted(
        (eps[0] for eps in per_show.values()),
        key=lambda e: (prio.get(e.podcast, 9), -e.published.timestamp()),
    )
    selected = firsts[:max_total]

    if len(selected) < max_total:
        seconds = [
            eps[1]
            for name, eps in per_show.items()
            if len(eps) > 1 and prio.get(name, 9) == 1
        ]
        seconds.sort(key=lambda e: e.published, reverse=True)
        selected += seconds[: max_total - len(selected)]

    return selected
