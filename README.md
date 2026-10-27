# channel-render

An open-source video renderer for history documentaries built from public-domain archives:
newspaper scans (with smooth camera moves and a highlighter on the quoted lines), archival film,
synthetic narration (Kokoro TTS), music beds, burned-in captions and vertical Shorts.

`render/` holds the renderer:
- `build.py` turns a project's `timeline.json` into a finished long video, captions, chapters and thumbnails
- `motion.py` sub-pixel camera moves over stills, highlighter sweeps, light film look
- `graphics.py` title, chapter, clock, price and caption cards (headless Chromium)
- `shorts.py` 1080x1920 Shorts with captions
- `metadata.py` YouTube descriptions with chapters and sources

## Running it

The `Render` workflow renders a project that lives in a separate (private) studio repository.
It only runs when started by the repository owner (`workflow_dispatch`); there are no triggers from
pull requests or forks. Project content, music and finished files never live in this repository.
