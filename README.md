# Briefcast — "The Weekly Signal"

A fully automated weekly pipeline that turns the week's best podcast episodes into:

1. **A written intelligence brief** (Markdown) — Big Picture, the week's biggest ideas with
   "why it matters," cross-episode connections, an episode scorecard, and must-listens.
2. **A custom ~18–22 minute podcast episode** — an AI narrator synthesizes the week and poses
   questions; **real sound bites from the source podcasts answer them**, so it's many voices,
   not one narrator droning on. Length is a guide, not a hard rule.
3. **The full text of the generated episode** — ready to paste into ChatGPT/Claude to build
   the ongoing knowledge base.

Delivered as a **private podcast feed**: subscribe once in Apple Podcasts/Overcast/Spotify and a
new episode simply appears every Monday morning, alongside a web page with the brief and downloads.

## How it works

```
podcasts.yaml (roster)                          config.yaml (listener profile, length, models)
      │                                                │
      ▼                                                ▼
RSS feeds ─► pick this week's episodes ─► download MP3s ─► transcribe (Whisper, timestamps)
      ─► Claude digests each episode (big ideas + candidate sound bites w/ timestamps)
      ─► Claude writes the weekly brief (Markdown)
      ─► Claude writes the episode script (narration blocks + which sound bites to play)
      ─► OpenAI TTS voices the narrator ─► ffmpeg cuts the real clips, normalizes loudness,
         stitches everything into one MP3
      ─► publishes MP3 + brief + text + RSS feed to docs/ (served by GitHub Pages)
```

Runs weekly on GitHub Actions (Mondays 5:30am ET); no computer needs to be on.

## One-time setup

1. **Create API keys**
   - Anthropic: <https://console.anthropic.com> → API keys
   - OpenAI: <https://platform.openai.com/api-keys> (used for Whisper transcription + narrator TTS)

2. **GitHub repository secrets** — repo → Settings → Secrets and variables → Actions:
   - `ANTHROPIC_API_KEY`
   - `OPENAI_API_KEY`

3. **Enable GitHub Pages** — repo → Settings → Pages → Source: *Deploy from a branch* →
   Branch `main`, folder `/docs`.

4. **Configure `config.yaml`**
   - `publish.base_url`: the Pages URL, e.g. `https://<user>.github.io/briefcast`
   - `publish.feed_token`: a long random string (`openssl rand -hex 16`). This makes the feed
     URL unguessable — treat the URL like a password.

5. **Subscribe** — in Apple Podcasts: Library → ⋯ → *Follow a Show by URL…* and paste
   `https://<user>.github.io/briefcast/p/<token>/feed.xml`. (Overcast/Pocket Casts: *Add URL*.)

6. First episode: Actions tab → *Weekly Signal* → *Run workflow* (or wait for Monday).

## Weekly cost (approximate)

| Item | Cost |
|---|---|
| Whisper transcription (~8 episodes × ~90 min) | ~$4 |
| Claude synthesis (digests + brief + script) | ~$1–3 |
| OpenAI TTS narrator (~10–12 min narration) | ~$0.20 |
| GitHub Actions + Pages | free |
| **Total** | **~$5–7/week** |

## Changing the podcast lineup

Edit `podcasts.yaml` — add a block with the show's RSS feed URL, or set `active: false` to pause
one. `priority: 1` shows get first claim on the weekly episode slots. Find any show's RSS URL at
<https://podnews.net> (search the show, copy "RSS feed"). Then run
`uv run briefcast validate-feeds` to confirm the feed parses.

Episode length, the listener profile the AI writes for, models, and loudness are all in `config.yaml`.

## Local development

```bash
uv sync
uv run briefcast validate-feeds        # check all RSS feeds (no keys needed)
uv run briefcast run --dry-run         # show what would be processed (no keys needed)
BRIEFCAST_MOCK=1 uv run briefcast run  # full pipeline with fake AI (no keys; tests audio end-to-end)
uv run briefcast run                   # real run (needs ANTHROPIC_API_KEY + OPENAI_API_KEY in env)
```

Intermediate artifacts cache in `.cache/run-<date>/`; a crashed run resumes where it left off.
Re-running for the same date reuses transcripts/digests/TTS already produced (delete the run
folder to force fresh).

## Notes

- **Keep the feed URL private.** Episodes contain short excerpts of copyrighted podcasts,
  fine for a personal one-listener brief, not for public distribution. The repo's `docs/` is
  technically public (GitHub Pages), protected by the unguessable token path and `noindex`;
  don't share the link beyond the intended listener.
- The knowledge-base loop: each week, paste `briefs/<date>.md` and `text/<date>.txt` (both
  linked on the episode page) into the "My Knowledge Base" ChatGPT project.
