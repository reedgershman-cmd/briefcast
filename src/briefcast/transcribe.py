from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from . import audio
from .config import is_mock

log = logging.getLogger(__name__)


@dataclass
class Segment:
    start: float
    end: float
    text: str


def transcribe(source_mp3: Path, workdir: Path, model: str = "whisper-1") -> list[Segment]:
    """Transcribe a full episode with segment timestamps. Caches to JSON."""
    cache = workdir / f"{source_mp3.stem}.transcript.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        return [Segment(**s) for s in data]

    if is_mock():
        segments = _mock_segments(source_mp3)
    else:
        segments = _whisper_api(source_mp3, workdir, model)

    cache.write_text(json.dumps([asdict(s) for s in segments]))
    return segments


def _whisper_api(source_mp3: Path, workdir: Path, model: str) -> list[Segment]:
    from openai import OpenAI

    client = OpenAI()
    small = audio.to_transcribe_format(source_mp3, workdir / f"{source_mp3.stem}.16k.mp3")
    chunks = audio.split_chunks(small, workdir)
    segments: list[Segment] = []
    for path, offset in chunks:
        log.info("transcribing %s (offset %.0fs)", path.name, offset)
        with open(path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model=model,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        for seg in resp.segments or []:
            segments.append(Segment(start=seg.start + offset, end=seg.end + offset,
                                    text=seg.text.strip()))
    return segments


def _mock_segments(source_mp3: Path) -> list[Segment]:
    """Fake transcript spread over the real audio duration, so downstream clip
    extraction cuts real audio at real timestamps."""
    dur = audio.probe_duration(source_mp3)
    segments = []
    t = 30.0
    i = 0
    while t < dur - 60 and i < 200:
        segments.append(Segment(
            start=t, end=t + 20,
            text=f"[mock segment {i}] A substantive point about markets, AI, and "
                 f"institutions made around {int(t)}s into this episode.",
        ))
        t += max(20.0, (dur - 90) / 200)
        i += 1
    return segments


def to_timestamped_text(segments: list[Segment], para_s: float = 30.0) -> str:
    """Merge segments into [MM:SS] paragraphs for the LLM."""
    out, cur, cur_start = [], [], 0.0
    for seg in segments:
        if not cur:
            cur_start = seg.start
        cur.append(seg.text)
        if seg.end - cur_start >= para_s:
            out.append(f"[{_ts(cur_start)}-{_ts(seg.end)}] " + " ".join(cur))
            cur = []
    if cur:
        out.append(f"[{_ts(cur_start)}] " + " ".join(cur))
    return "\n".join(out)


def _ts(s: float) -> str:
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
