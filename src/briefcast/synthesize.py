from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import Config, is_mock
from .feeds import SourceEpisode
from .transcribe import Segment, to_timestamped_text

log = logging.getLogger(__name__)

DIGEST_SYSTEM = """You are an expert podcast analyst preparing raw material for a weekly \
intelligence brief for one specific listener. You extract the biggest ideas and the most \
quotable moments, with exact timestamps, from a podcast transcript."""

DIGEST_PROMPT = """LISTENER PROFILE:
{profile}

PODCAST: {podcast}
EPISODE: {title}
PUBLISHED: {published}
SHOW NOTES: {summary}

TRANSCRIPT (timestamped [H:MM:SS] or [MM:SS] paragraph ranges):
{transcript}

Produce a JSON object with exactly these keys:
- "episode_summary": 3-5 sentence summary of what the episode covers and who speaks.
- "big_ideas": array of 3-6 objects, each {{"idea": short headline, "detail": 2-4 sentences, \
"why_it_matters": 1-2 sentences framed for this listener}}.
- "soundbites": array of 8-14 candidate audio quotes. Each: {{"id": string (use "{eid}-sb" + index, \
e.g. "{eid}-sb0"), "start": seconds (number), "end": seconds (number), "speaker": who is talking, \
"quote": the approximate words spoken, "context": one sentence on what prompted it, \
"idea_link": which big idea it supports}}. Rules: each 10-60 seconds long; pick moments that are \
self-contained and compelling out of context; timestamps MUST fall inside the transcript paragraph \
ranges containing those words; prefer the guest's voice over the host's; spread picks across the episode.
- "rating": letter grade A+ through C for how valuable a full listen is for this listener.
- "listen_recommendation": one short sentence (e.g. "Yes, highest priority" / "Skim" / "Skip unless...").

Return ONLY the JSON object, no markdown fences, no commentary."""

BRIEF_SYSTEM = """You write a weekly intelligence brief for one sophisticated reader, \
synthesizing the best podcast episodes of the week. Your style: dense with insight, zero fluff, \
explicitly connects ideas ACROSS different thinkers and to the reader's own work. You are not a \
summarizer; you are a synthesist. When a point from one podcast reinforces or contradicts another, \
you say so explicitly."""

BRIEF_PROMPT = """LISTENER PROFILE:
{profile}

WEEK OF: {week}

EPISODE DIGESTS (JSON):
{digests}

Write this week's brief in Markdown with exactly this structure:

# The Weekly Signal — {week}

## The Big Picture
One tight paragraph naming the strongest cross-cutting theme of the week and which episodes drive it.

## The {n_ideas} Biggest Ideas
Numbered sections. For each: a bold headline, 2-4 sentences of substance (name the podcast and \
speaker), a "**Why it matters:**" line framed for this listener, and where genuinely relevant a \
"**Relevance:**" line tying it to the listener's work. If ideas from different episodes connect, \
connect them explicitly.

## Connections & Contradictions
2-4 bullets linking ideas across episodes (X reinforces Y because...; A would push back on B's claim that...).

## Episode Scorecard
A Markdown table: Podcast | Episode | Rating | Worth a full listen?

## Must-Listen This Week
Top 2-3 with one-line reasons.

Return ONLY the Markdown document."""

SCRIPT_SYSTEM = """You are the head writer and host of a private, personalized weekly podcast \
produced for exactly one listener. The show weaves an AI narrator's synthesis together with real \
sound bites from the week's source podcasts. The narrator sets up an idea or poses a question, a \
real clip from the source podcast plays as the answer, and the narrator reacts, builds, and bridges. \
The tone: a sharp, warm briefing from a brilliant chief-of-staff — conversational, direct, \
second-person ("you"), never sycophantic, never padded."""

SCRIPT_PROMPT = """LISTENER PROFILE:
{profile}

THIS WEEK'S WRITTEN BRIEF (the episode should track its ideas and structure):
{brief}

AVAILABLE SOUND BITES (the ONLY clips you may use; "dur" = seconds of audio):
{soundbites}

TARGET LENGTH: {tmin}-{tmax} minutes is a guide, not a hard rule — run longer if the week is rich, \
shorter if thin. Runtime math: narration plays at ~150 words/minute; each clip adds its "dur" seconds. \
Budget accordingly and aim for roughly 40-60% narration / 40-60% clips.

STRUCTURE REQUIREMENTS:
1. Cold open: narrator hooks with the week's big theme (30-45s), addressing {listener} directly.
2. Work through the week's biggest ideas. For each: narrator frames it — often as a direct question — \
then a clip answers, then the narrator reacts and connects it to other episodes or to {listener}'s world.
3. Use 6-12 clips total, from as many different source podcasts as possible, so the listener hears \
many voices, not one narrator droning on.
4. Every clip needs a spoken lead-in that names the podcast and speaker (e.g. "Here's Bill Gurley on \
the Knowledge Project:") so the listener always knows whose voice they're hearing.
5. Close: 60-90s synthesis — what to watch next week, plus the top listen-in-full recommendations.

Return ONLY a JSON object:
{{
  "episode_title": "string — punchy, specific to this week",
  "segments": [
    {{"type": "narration", "text": "exact words the narrator speaks"}},
    {{"type": "clip", "soundbite_id": "id from the list above"}}
  ]
}}
Narration segments must contain only speakable prose — no headings, no stage directions, no markdown. \
Never place two clips back-to-back without narration between them."""


def _claude(system: str, prompt: str, model: str, max_tokens: int = 8000) -> str:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model output: {text[:300]}")
    return json.loads(text[start : end + 1])


def digest_episode(ep: SourceEpisode, segments: list[Segment], cfg: Config,
                   workdir: Path) -> dict:
    cache = workdir / f"{ep.episode_id}.digest.json"
    if cache.exists():
        return json.loads(cache.read_text())

    if is_mock():
        digest = _mock_digest(ep, segments)
    else:
        transcript = to_timestamped_text(segments)
        prompt = DIGEST_PROMPT.format(
            profile=cfg.get("listener", "profile"),
            podcast=ep.podcast, title=ep.title,
            published=ep.published.strftime("%Y-%m-%d"),
            summary=ep.summary[:1500], transcript=transcript,
            eid=ep.episode_id,
        )
        digest = _extract_json(_claude(DIGEST_SYSTEM, prompt,
                                       cfg.get("models", "synthesis")))
    digest["episode_id"] = ep.episode_id
    digest["podcast"] = ep.podcast
    digest["title"] = ep.title
    cache.write_text(json.dumps(digest, indent=1))
    return digest


def write_brief(digests: list[dict], cfg: Config, week: str, workdir: Path) -> str:
    cache = workdir / "brief.md"
    if cache.exists():
        return cache.read_text()
    if is_mock():
        brief = _mock_brief(digests, week)
    else:
        n_ideas = min(6, max(4, len(digests)))
        prompt = BRIEF_PROMPT.format(
            profile=cfg.get("listener", "profile"), week=week,
            digests=json.dumps(digests, indent=1), n_ideas=n_ideas,
        )
        brief = _claude(BRIEF_SYSTEM, prompt, cfg.get("models", "synthesis"),
                        max_tokens=10000).strip()
    cache.write_text(brief)
    return brief


def write_script(digests: list[dict], brief: str, cfg: Config, workdir: Path) -> dict:
    cache = workdir / "script.json"
    if cache.exists():
        return json.loads(cache.read_text())

    bites = []
    for d in digests:
        for sb in d.get("soundbites", []):
            try:
                dur = round(float(sb["end"]) - float(sb["start"]), 1)
            except (KeyError, TypeError, ValueError):
                continue
            if dur <= 0:
                continue
            bites.append({
                "id": sb["id"], "podcast": d["podcast"], "episode": d["title"],
                "speaker": sb.get("speaker", ""), "dur": dur,
                "quote": sb.get("quote", ""), "context": sb.get("context", ""),
            })

    if is_mock():
        script = _mock_script(digests, bites)
    else:
        prompt = SCRIPT_PROMPT.format(
            profile=cfg.get("listener", "profile"), brief=brief,
            soundbites=json.dumps(bites, indent=1),
            tmin=cfg.get("episode", "target_minutes_min", default=18),
            tmax=cfg.get("episode", "target_minutes_max", default=22),
            listener=cfg.get("listener", "name", default="the listener"),
        )
        script = _extract_json(_claude(SCRIPT_SYSTEM, prompt,
                                       cfg.get("models", "synthesis"),
                                       max_tokens=16000))
    cache.write_text(json.dumps(script, indent=1))
    return script


# ---------------- mock implementations (keyless end-to-end testing) ----------------

def _mock_digest(ep: SourceEpisode, segments: list[Segment]) -> dict:
    picks = segments[len(segments) // 4 :: max(1, len(segments) // 4)][:3]
    return {
        "episode_summary": f"Mock digest of {ep.title} ({ep.podcast}).",
        "big_ideas": [{"idea": f"Mock idea from {ep.podcast}",
                       "detail": "Mock detail.", "why_it_matters": "Mock relevance."}],
        "soundbites": [
            {"id": f"{ep.episode_id}-sb{i}", "start": s.start, "end": min(s.end + 15, s.start + 30),
             "speaker": "Guest", "quote": s.text[:80], "context": "Mock context.",
             "idea_link": "Mock idea"}
            for i, s in enumerate(picks)
        ],
        "rating": "A",
        "listen_recommendation": "Mock: yes.",
    }


def _mock_brief(digests: list[dict], week: str) -> str:
    rows = "\n".join(f"| {d['podcast']} | {d['title'][:40]} | {d['rating']} | Yes |"
                     for d in digests)
    return (f"# The Weekly Signal — {week}\n\n## The Big Picture\nMock brief.\n\n"
            f"## Episode Scorecard\n| Podcast | Episode | Rating | Worth a full listen? |\n"
            f"|---|---|---|---|\n{rows}\n")


def _mock_script(digests: list[dict], bites: list[dict]) -> dict:
    segments = [{"type": "narration",
                 "text": "Welcome to your Weekly Signal. This is a mock episode "
                         "testing the full audio pipeline end to end."}]
    for b in bites[:4]:
        segments.append({"type": "narration",
                         "text": f"Here is a moment from {b['podcast']}:"})
        segments.append({"type": "clip", "soundbite_id": b["id"]})
    segments.append({"type": "narration", "text": "That is the mock brief. See you next week."})
    return {"episode_title": "Mock Weekly Signal", "segments": segments}
