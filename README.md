# InterviewForge — AI Interview Practice for Students

A full website (Flask backend + HTML/CSS/JS frontend) version of the interview
prep assistant, for when you want a real webpage instead of a Streamlit app.

## Folder structure

```
.
├── app.py                 # Flask server + API routes
├── requirements.txt
├── templates/
│   └── index.html         # The single-page app shell
├── style.css               # Design system and responsive layout
└── script.js               # All frontend logic (fetch calls, state, DOM updates)
```

## Features

The MVP includes real account registration and login (Werkzeug password
hashes, secure sessions, CSRF checks), a persistent SQLite data model, and a
responsive multi-page foundation. Official Round 1 uses exactly 15 persisted
MCQs and Round 2 uses exactly 10 technical MCQs; both work without an API key,
persist the selected question set across refreshes, calculate scores, and
enforce backend round locking. Round 3 requires camera and microphone
permissions. Round 4 shows an explicit AI configuration message until
`ANTHROPIC_API_KEY` is configured. Independent mock setup, practice, resume,
coach, roadmap, progress, leaderboard, and rewards routes are included.

- AI-generated role and level-specific interview questions
- Voice Q&A using the browser Web Speech API
- Optional 90-second timed answers
- Resume-based question tailoring for PDF resumes
- Browser JavaScript Code Lab for technical questions
- Downloadable PDF session reports
- Instant feedback and end-of-session readiness summary

The SQLite database is created automatically as `interviewforge.sqlite3` on
first run. Change `SECRET_KEY` in production and serve behind HTTPS. For a
production host, configure a persistent disk for the SQLite database and
uploads, then set `HOST=0.0.0.0`, `PORT` (provided by most platforms),
`SECRET_KEY`, and optionally `COOKIE_SECURE=1`.

## How it works

- **Backend (`app.py`)** exposes three JSON endpoints that call Claude:
  - `POST /api/questions` — generates role-specific interview questions
  - `POST /api/evaluate` — scores a single answer with feedback
  - `POST /api/summary` — writes the end-of-session performance summary
-   Official Round 1 and Round 2 each use a seeded SQLite question bank with
  placement-standard MCQs. Each official assessment serves exactly 20 questions,
  supports fresh retests until the available bank is exhausted, and records
  scores; passing a round unlocks the next round. Direct requests for locked
  rounds redirect to the placement page.
- Registration is intentionally separate from login: after a successful
  registration you are redirected to `/login` with a confirmation message.
- **Frontend** is a single HTML page with three screens (setup → interview →
  report) shown/hidden with plain JavaScript — no build step, no frameworks.
  `script.js` keeps all interview state in memory and calls the backend with
  `fetch()`.

## Setup & run

```bash
cd "AI interview preparartion assistant"
pip install -r requirements.txt

export ANTHROPIC_API_KEY="your-key-here"      # macOS/Linux
setx ANTHROPIC_API_KEY "your-key-here"        # Windows (new terminal after)

python app.py
```

Open **https://interview-forge-x9g8.onrender.com** in your browser.

For production WSGI hosting, use the included `Procfile` (or run the
equivalent command):

```bash
gunicorn --bind 0.0.0.0:$PORT app:app
```

## Design notes (useful for your assignment writeup)

- **Visual concept**: a calm "briefing room" rather than a generic dashboard —
  warm paper background, a single brass accent, a serif (Fraunces) for the
  interview questions themselves to give them weight, and a plain sans
  (IBM Plex Sans) for all interface chrome.
- **Interaction model**: one question at a time, feedback revealed in place
  before advancing — mirrors the actual rhythm of an interview rather than a
  form you fill out and submit all at once.
- **Numbering**: question numbers (01, 02…) are used because the questions
  genuinely are a sequence — not decorative.

## Relationship to the Streamlit version

Both versions share the same three prompts (question generation, answer
evaluation, session summary) — only the delivery layer differs. If your
assignment wants a "real website" deliverable, submit this version; if it
wants a fast interactive demo you can iterate on in one file, the Streamlit
version (`app.py` in the project root) is quicker to run.
