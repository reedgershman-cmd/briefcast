from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import assemble, audio, feeds, publish, synthesize, transcribe
from .config import ROOT, Config, is_mock, load_roster
from .state import load_state, save_state

log = logging.getLogger("briefcast")


def cmd_validate_feeds(args) -> int:
    """Fetch every active feed and print its latest episode. No API keys needed."""
    roster = load_roster()
    failures = 0
    for p in roster:
        try:
            parsed = feeds.fetch_feed(p)
            eps = feeds.episodes_from_feed(p, parsed)
            if not eps:
                print(f"  WARN {p.name}: feed parsed but no audio episodes found")
                failures += 1
                continue
            latest = max(eps, key=lambda e: e.published)
            dur = f"{latest.duration_s // 60}min" if latest.duration_s else "?"
            print(f"  OK   {p.name}: \"{latest.title[:70]}\" "
                  f"({latest.published.date()}, {dur})")
        except Exception as e:
            print(f"  FAIL {p.name}: {e}")
            failures += 1
    return 1 if failures else 0


def cmd_run(args) -> int:
    cfg = Config.load()
    roster = load_roster()
    state = load_state()
    now = datetime.now(timezone.utc)
    date_str = args.date or now.strftime("%Y-%m-%d")
    week = now.strftime("Week of %B %d, %Y")
    workdir = ROOT / ".cache" / f"run-{date_str}"
    workdir.mkdir(parents=True, exist_ok=True)

    if is_mock():
        log.info("*** MOCK MODE: no API keys used; narrator is synthetic ***")

    log.info("1/7 selecting episodes (lookback %sd)", cfg.get("episode", "lookback_days", default=8))
    selected = feeds.select_episodes(
        roster,
        lookback_days=cfg.get("episode", "lookback_days", default=8),
        max_total=args.max_episodes or cfg.get("episode", "max_source_episodes", default=8),
        already_processed=set(state["processed_guids"]),
        now=now,
    )
    if not selected:
        log.info("no new episodes this week; nothing to do")
        return 0
    for ep in selected:
        log.info("   %s — %s", ep.podcast, ep.title[:80])
    if args.dry_run:
        return 0

    log.info("2/7 downloading %d episodes", len(selected))
    for ep in selected:
        path = audio.download(ep.audio_url, workdir / "sources" / f"{ep.episode_id}.mp3")
        ep.audio_path = str(path)

    log.info("3/7 transcribing")
    digests = []
    cfg_model = cfg.get("models", "transcription", default="whisper-1")
    for ep in selected:
        segments = transcribe.transcribe(Path(ep.audio_path), workdir, model=cfg_model)
        log.info("   %s: %d segments", ep.podcast, len(segments))
        log.info("4/7 digesting %s", ep.title[:60])
        digests.append(synthesize.digest_episode(ep, segments, cfg, workdir))

    log.info("5/7 writing weekly brief + script")
    brief = synthesize.write_brief(digests, cfg, week, workdir)
    script = synthesize.write_script(digests, brief, cfg, workdir)

    log.info("6/7 rendering audio: \"%s\"", script.get("episode_title", "untitled"))
    out_mp3 = workdir / f"weekly-signal-{date_str}.mp3"
    meta = assemble.assemble(script, selected, digests, cfg, workdir, out_mp3)

    log.info("7/7 publishing")
    sources_line = "Sources this week: " + "; ".join(
        f"{e.podcast} — {e.title}" for e in selected)
    entry = publish.publish_episode(
        cfg, state, date_str,
        episode_title=script.get("episode_title", f"The Weekly Signal — {week}"),
        mp3_src=out_mp3,
        brief_md=brief,
        podcast_text=meta["podcast_text"] + "\n\n" + sources_line,
        duration_s=meta["duration_s"],
        clips_used=meta["clips_used"],
        description=_first_paragraph(brief) + " " + sources_line,
    )

    state["processed_guids"] += [ep.guid for ep in selected]
    save_state(state)

    base = cfg.get("publish", "base_url", default="").rstrip("/")
    token = cfg.get("publish", "feed_token")
    log.info("done: %s min episode, %s clips", round(entry["duration_s"] / 60),
             entry["clips_used"])
    log.info("feed: %s/p/%s/feed.xml", base, token)
    log.info("page: %s/p/%s/%s.html", base, token, date_str)
    return 0


def _first_paragraph(md: str) -> str:
    for line in md.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return ""


def cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(prog="briefcast")
    sub = ap.add_subparsers(dest="cmd", required=True)

    runp = sub.add_parser("run", help="produce this week's brief + episode")
    runp.add_argument("--dry-run", action="store_true",
                      help="only show which episodes would be processed")
    runp.add_argument("--date", help="override episode date (YYYY-MM-DD)")
    runp.add_argument("--max-episodes", type=int, default=None)
    runp.set_defaults(fn=cmd_run)

    vf = sub.add_parser("validate-feeds", help="check every RSS feed in podcasts.yaml")
    vf.set_defaults(fn=cmd_validate_feeds)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    cli()
