from __future__ import annotations

import logging
from pathlib import Path

from . import audio, tts
from .config import Config
from .feeds import SourceEpisode

log = logging.getLogger(__name__)


def assemble(script: dict, episodes: list[SourceEpisode], digests: list[dict],
             cfg: Config, workdir: Path, out_mp3: Path) -> dict:
    """Render the script to a single MP3. Returns metadata incl. duration and
    the plain-text 'podcast text' (narration + quoted clips) for the knowledge base."""
    by_id: dict[str, tuple[SourceEpisode, dict]] = {}
    for d in digests:
        ep = next((e for e in episodes if e.episode_id == d["episode_id"]), None)
        if not ep:
            continue
        for sb in d.get("soundbites", []):
            by_id[sb["id"]] = (ep, sb)

    clip_min = cfg.get("audio", "clip_min_s", default=8)
    clip_max = cfg.get("audio", "clip_max_s", default=75)
    pad = cfg.get("audio", "clip_pad_s", default=0.25)

    segdir = workdir / "render"
    segdir.mkdir(parents=True, exist_ok=True)
    gap = audio.silence(segdir / "gap.wav", 0.45)

    wavs: list[Path] = []
    text_lines: list[str] = []
    used_clips = 0

    for i, seg in enumerate(script.get("segments", [])):
        kind = seg.get("type")
        if kind == "narration":
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            raw = tts.narrate(text, segdir / f"seg{i:03d}.narration.mp3", cfg)
            norm = audio.normalize_segment(raw, segdir / f"seg{i:03d}.narration.wav")
            wavs += [norm, gap]
            text_lines.append(f"NARRATOR: {text}")
        elif kind == "clip":
            sbid = seg.get("soundbite_id", "")
            if sbid not in by_id:
                log.warning("script references unknown soundbite %r — skipping", sbid)
                continue
            ep, sb = by_id[sbid]
            try:
                start, end = float(sb["start"]), float(sb["end"])
            except (KeyError, TypeError, ValueError):
                log.warning("bad timestamps on %s — skipping", sbid)
                continue
            if end - start < clip_min:
                end = start + clip_min
            if end - start > clip_max:
                end = start + clip_max
            src = Path(ep.audio_path)
            dur = audio.probe_duration(src)
            if start >= dur:
                log.warning("clip %s starts beyond episode end — skipping", sbid)
                continue
            end = min(end, dur - 0.1)
            clip = audio.extract_clip(src, start, end,
                                      segdir / f"seg{i:03d}.clip.wav", pad=pad)
            wavs += [clip, gap]
            used_clips += 1
            text_lines.append(
                f"[CLIP — {sb.get('speaker', 'speaker')} on {ep.podcast}, "
                f"\"{ep.title}\"]: \"{sb.get('quote', '')}\""
            )
        else:
            log.warning("unknown segment type %r — skipping", kind)

    if wavs and wavs[-1] == gap:
        wavs = wavs[:-1]
    if not wavs:
        raise RuntimeError("script produced no audio segments")

    audio.concat(wavs, out_mp3, bitrate=cfg.get("audio", "bitrate", default="96k"))
    duration = audio.probe_duration(out_mp3)
    log.info("assembled %s: %.1f min, %d clips", out_mp3.name, duration / 60, used_clips)
    return {
        "duration_s": int(duration),
        "clips_used": used_clips,
        "podcast_text": "\n\n".join(text_lines),
    }
