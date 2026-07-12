from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from . import audio
from .config import Config, is_mock

log = logging.getLogger(__name__)

TTS_CHAR_LIMIT = 4000

NARRATOR_STYLE = (
    "Speak as a sharp, warm podcast host giving a private briefing to one person. "
    "Conversational pace, natural emphasis, genuine curiosity. Slow slightly on key "
    "numbers and names. No radio-announcer affect."
)


def narrate(text: str, dest: Path, cfg: Config) -> Path:
    """Render one narration block to audio at dest (any ffmpeg-readable format)."""
    if dest.exists():
        return dest
    if is_mock():
        return _mock_narrate(text, dest)

    from openai import OpenAI

    client = OpenAI()
    parts = _split(text, TTS_CHAR_LIMIT)
    part_files = []
    for i, part in enumerate(parts):
        pf = dest.with_suffix(f".part{i}.mp3")
        if not pf.exists():
            with client.audio.speech.with_streaming_response.create(
                model=cfg.get("models", "tts", default="gpt-4o-mini-tts"),
                voice=cfg.get("models", "tts_voice", default="onyx"),
                input=part,
                instructions=NARRATOR_STYLE,
                response_format="mp3",
            ) as resp:
                resp.stream_to_file(pf)
        part_files.append(pf)

    if len(part_files) == 1:
        part_files[0].rename(dest)
    else:
        wavs = []
        for pf in part_files:
            w = pf.with_suffix(".wav")
            audio.run_ffmpeg(["-i", str(pf), "-ac", "2", "-ar", "44100", str(w)])
            wavs.append(w)
        audio.concat(wavs, dest)
    return dest


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    parts, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) + 1 > limit:
            parts.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        parts.append(cur)
    return parts


def _mock_narrate(text: str, dest: Path) -> Path:
    """Use macOS `say` when available so mock runs are actually listenable."""
    say = shutil.which("say")
    if say:
        aiff = dest.with_suffix(".aiff")
        subprocess.run([say, "-o", str(aiff), text[:1000]], check=True)
        audio.run_ffmpeg(["-i", str(aiff), str(dest)])
        aiff.unlink()
    else:
        secs = max(1.0, len(text.split()) / 2.5)
        audio.run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=440:duration={secs:.1f}",
                          "-ac", "2", "-ar", "44100", str(dest)])
    return dest
