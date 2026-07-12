from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh) Briefcast/0.1 (private personal podcast digest)"


def run_ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd)}\n{proc.stderr[-2000:]}")


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise RuntimeError(f"ffprobe could not read duration of {path}: {proc.stderr[-500:]}")


def download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        log.info("cached: %s", dest.name)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, headers={"User-Agent": UA},
                      follow_redirects=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes(1 << 16):
                f.write(chunk)
    tmp.rename(dest)
    log.info("downloaded %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
    return dest


def to_transcribe_format(src: Path, dest: Path) -> Path:
    """Compress to 16 kHz mono for cheap upload to the transcription API."""
    if not dest.exists():
        run_ffmpeg(["-i", str(src), "-ac", "1", "-ar", "16000", "-b:a", "32k", str(dest)])
    return dest


def split_chunks(src: Path, workdir: Path, chunk_s: int = 1200) -> list[tuple[Path, float]]:
    """Split into chunks for the 25 MB API limit. Returns (path, start_offset_s)."""
    dur = probe_duration(src)
    if dur <= chunk_s:
        return [(src, 0.0)]
    out = []
    i = 0
    start = 0.0
    while start < dur:
        chunk = workdir / f"{src.stem}.chunk{i:03d}.mp3"
        if not chunk.exists():
            run_ffmpeg(["-ss", str(start), "-t", str(chunk_s), "-i", str(src),
                        "-ac", "1", "-ar", "16000", "-b:a", "32k", str(chunk)])
        out.append((chunk, start))
        start += chunk_s
        i += 1
    return out


def extract_clip(src: Path, start: float, end: float, dest: Path, pad: float = 0.25) -> Path:
    """Cut a sound bite and normalize it to standard loudness/format (44.1k stereo wav).

    Uses atrim (accurate decode-based trimming) rather than -ss input seeking:
    podcast MP3s are frequently VBR, where fast seeking can land seconds off."""
    s = max(0.0, start - pad)
    e = end + pad
    fade_out_start = max(0.0, (e - s) - 0.15)
    run_ffmpeg([
        "-i", str(src),
        "-af",
        f"atrim=start={s:.2f}:end={e:.2f},asetpts=PTS-STARTPTS,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,"
        f"afade=t=in:d=0.05,afade=t=out:st={fade_out_start:.2f}:d=0.15",
        "-ac", "2", "-ar", "44100", str(dest),
    ])
    return dest


def normalize_segment(src: Path, dest: Path) -> Path:
    """Normalize any narration/TTS audio to the same loudness/format as clips."""
    run_ffmpeg([
        "-i", str(src),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ac", "2", "-ar", "44100", str(dest),
    ])
    return dest


def silence(dest: Path, seconds: float) -> Path:
    if not dest.exists():
        run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", f"{seconds:.2f}", str(dest)])
    return dest


def concat(wavs: list[Path], dest_mp3: Path, bitrate: str = "96k") -> Path:
    listfile = dest_mp3.with_suffix(".txt")
    listfile.write_text("".join(f"file '{p.resolve()}'\n" for p in wavs))
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listfile),
                "-c:a", "libmp3lame", "-b:a", bitrate, "-ac", "2", "-ar", "44100",
                str(dest_mp3)])
    listfile.unlink()
    return dest_mp3
