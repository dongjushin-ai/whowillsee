# 👁️ WhoWillSee

English · [한국어](README.md)

**Every audience reads your slides differently.**

Upload a presentation, briefing, PPT, PDF, HTML page or image and pick who will see it. WhoWillSee simulates that audience (a *persona*) and draws **red boxes** wherever they would get stuck, misunderstand, feel uneasy or lose trust, each with a note on *why*, *how to fix it* and *what they're thinking*.

- **Personas:** the default is a *general citizen*. Build your own from occupation, organization, role, personality, needs, preferences, expertise, digital literacy, concerns and context, or generate one from a single sentence.
- **Audience groups:** e.g. *town hall = 60% general citizens + 30% seniors + 10% journalists*. You get a weighted score, and problems that trip up several groups come first.
- **Revisions:** upload a revised version to see the score change and which issues were resolved, remain, or are new.
- **Talk script & recordings:** evaluate what you'll *say* (PPTX speaker notes are imported). Upload a rehearsal recording to get a real per-page script, real timing, pace, filler words and long pauses.
- **Video:** upload a rehearsal video to measure how often you face the audience, stay in frame, lighting and distance, plus persona-specific feedback on gaze, expression, posture and gestures.
- **Dashboard, dark mode, Korean/English UI**, annotated PDF, a self-contained shareable HTML report, and JSON export.
- **Bring your own AI:** not tied to any account. Connect Claude, OpenAI, Gemini, OpenRouter, or a local Ollama / LM Studio from the *AI connection* screen. Without an AI it still runs in a rule-based demo mode.

## Quick start

Requires Python 3.10+.

```bash
git clone <this repository>
cd whowillsee
pip install -r requirements.txt
python -m playwright install chromium      # for HTML / web pages
uvicorn app.main:app --port 8000
```

Open http://localhost:8000, switch the language to **English** at the top, and connect your AI under **AI connection**.
Optional: LibreOffice for PPT/DOCX, ffmpeg for audio in videos. Or use Docker:

```bash
docker build -t whowillsee .
docker run -p 8000:8000 -v whowillsee-data:/data whowillsee
```

## Notes

- Results are a **simulation** of how such a person would likely react, not real people's reactions.
- When an AI or speech service is connected, page images, text and audio are sent to that service. Use a local model (Ollama, LM Studio, a local Whisper server) for sensitive material.
- There is no login. If you expose the server to others, put authentication in front of it and set `WHOWILLSEE_LOCK_SETTINGS=1`.
- Built-in demo rules (jargon lists, filler words) are tuned for Korean material; with an AI connected, any language works.

See the Korean [README](README.md) for the full feature guide, environment variables and API reference.
