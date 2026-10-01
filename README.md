# InterviewForge — AI Interview Practice for Students

InterviewForge is an interview-preparation website built with a Flask backend
and a server-rendered HTML/CSS/JavaScript frontend.

## Folder structure

```
.
├── app.py                 # Flask server + API routes
├── coding_bank.py         # Local coding question bank and editor starters
├── coding.html            # Sandboxed coding practice interface
├── voice.js               # Browser speech and microphone controls
├── coding.js              # Coding editor, timer, and sandbox client
├── tests/
│   └── test_app_flows.py   # Authentication and coding flow tests
├── requirements.txt
├── *.html                 # Server-rendered pages
├── style.css               # Design system and responsive layout
└── script.js               # All frontend logic (fetch calls, state, DOM updates)
```

## Features

The MVP includes real account registration and login (Werkzeug password
hashes, secure sessions, CSRF checks), a persistent SQLite data model, and a
responsive multi-page foundation. Official Round 1 and Round 2 each use 20
persisted MCQs and work without an API key,
persist the selected question set across refreshes, calculate scores, and
enforce backend round locking. Camera preview is optional; interview answers
support speech recognition where available and always allow typing. Round 4 shows an explicit AI configuration message until
`ANTHROPIC_API_KEY` is configured. Independent mock setup, practice, resume,
coach, roadmap, progress, leaderboard, and rewards routes are included.

- AI-generated role and level-specific interview questions
- Voice Q&A with question playback, speech transcription, a recording timer, and a typed-answer fallback
- Per-answer recording timers and question replay
- Resume-based question tailoring for PDF resumes
- Randomized coding practice across 11 data-structure and algorithm topics, company filters, and C, C++, Java, JavaScript, and Python
- Round 2 combines the technical MCQs with a randomized, sandbox-verified coding task; the coding task contributes to the score and is required to unlock Round 3
- Login cooldowns and OTP-based password recovery with short-lived, single-use reset authorization
- Signup accounts remain inactive until email or (when configured) mobile OTP verification succeeds
- Downloadable PDF session reports
- Instant feedback and end-of-session readiness summary

The SQLite database is created automatically as `interviewforge.sqlite3` on
first run. Change `SECRET_KEY` in production and serve behind HTTPS. For a
production host, configure a persistent disk for the SQLite database and
uploads, then set `HOST=0.0.0.0`, `PORT` (provided by most platforms),
`SECRET_KEY`, and optionally `COOKIE_SECURE=1`.

Coding source is never executed by the InterviewForge Flask server. To enable
Run Code and Submit Code, configure `CODE_SANDBOX_URL` to the base URL of a
trusted, HTTPS Piston-compatible sandbox service; optionally set
`CODE_SANDBOX_API_KEY` if that service requires a bearer token. Without a
sandbox configuration, the coding editor and question bank remain available,
and execution requests fail with a clear setup message.

### Password-reset email configuration

Password recovery uses SMTP over TLS and the Python standard library; no
additional package or credential is required in the repository. Configure
these environment variables on the server (never commit them):

```text
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your-smtp-user
SMTP_PASSWORD=your-smtp-password
SMTP_FROM=InterviewForge <no-reply@example.com>
SMTP_USE_STARTTLS=1
SMTP_USE_SSL=0
```

For implicit TLS on port 465, set `SMTP_USE_SSL=1` and `SMTP_USE_STARTTLS=0`.
When SMTP is missing or unavailable, the UI still returns a generic
anti-enumeration response; password reset email delivery will not succeed
until SMTP is configured. Login failures trigger a 15-minute cooldown after
five account failures or twenty IP failures in a 15-minute window. OTP requests
are limited to three per email per 30 minutes and ten per IP per 30 minutes;
verification allows five attempts.

New signups must also verify their identity before login. Email verification
uses the SMTP settings above and is selected by default. Mobile verification
can be selected at signup when a phone is entered in international `+` format.
For SMS, configure a Twilio account using environment variables:

```text
TWILIO_ACCOUNT_SID=your-account-sid
TWILIO_AUTH_TOKEN=your-auth-token
TWILIO_FROM_NUMBER=+15551234567
```

The account remains inactive if OTP delivery is unavailable; a user can retry
with the resend control once the cooldown has elapsed. Signup codes expire
after 10 minutes, allow five verification attempts, and can be resent no more
than once per minute (with additional per-contact and per-IP request limits).
Existing accounts in databases upgraded from earlier versions are preserved
and treated as already verified; newly registered accounts are explicitly
created unverified. The JSON `POST /api/register` endpoint now returns HTTP 202
and a verification redirect instead of creating an authenticated session.

## How it works

- Flask renders the existing HTML pages directly; there is no frontend build
  step or framework migration.
- `POST /api/assessment` creates role-based practice assessments and
  `POST /api/interview` handles individual interview-answer feedback.
- Official Round 1 and Round 2 use seeded SQLite question banks, persist
  question sets, record scores, and enforce round locking. Round 2 requires
  both a passing technical score and a sandbox-verified coding submission.
- Login failures are rate-limited by account and IP. Password recovery sends a
  six-digit OTP that expires after 10 minutes, has a 60-second resend cooldown,
  and is verified and stored only as an HMAC digest. Set SMTP credentials using
  environment variables; OTPs are never included in frontend responses or logs.
- Registration and login use normalized email addresses, Werkzeug password
  hashes, secure Flask sessions, and explicit duplicate-account feedback.
  Registration creates a pending account and redirects to `/verify-signup`;
  only successful OTP verification activates the account for login.
- Coding practice sessions select unique questions from the local bank.
  Execution is only proxied to the configured sandbox service; code is not
  stored by InterviewForge or run in the Flask process.
- Browser speech controls use Web Speech APIs when supported and retain the
  editable text-answer path when speech recognition is unavailable.

Run the authentication, interview, and coding integration tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Setup & run

From PowerShell on Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open **https://interview-forge-x9g8.onrender.com** in your browser.
The app listens on **http://127.0.0.1:5000** by default. To use a different
port, set `PORT` before starting it, for example:

```powershell
$env:PORT = "5057"
.\.venv\Scripts\python.exe app.py
```

AI question generation and feedback are optional. Set `ANTHROPIC_API_KEY` in
the server environment to enable them; supported practice flows provide
non-AI fallback content.

On macOS or Linux, create and activate a virtual environment, install
`requirements.txt`, and run `python app.py`. For production WSGI hosting, use
the included `Procfile` (or its equivalent):

```bash
gunicorn --bind 0.0.0.0:$PORT app:app
```

## Interface and architecture

- The responsive interface uses a warm, minimal visual theme throughout the
  Flask-rendered pages.
- Interview rounds guide the user through one question at a time and preserve
  progress between page refreshes.
- The application is served by Flask, with local HTML templates, CSS, and
  JavaScript; no frontend build step is required.

## Runtime details

SQLite schema initialization and additive migrations run when the application
starts, preserving existing account data.
