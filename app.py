"""InterviewForge MVP: a small, secure Flask application.

The application deliberately uses SQLite and the Python standard library so it
is easy for a beginner to run and understand.  AI calls are kept behind
``call_claude`` and every response is validated before it reaches the client.
"""

import json
import os
import re
import secrets
import sqlite3
import random
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

from anthropic import Anthropic
from flask import Flask, g, jsonify, render_template, request, session, redirect, url_for, send_from_directory, flash
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pypdf import PdfReader
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "interviewforge.sqlite3"
UPLOAD_DIR = BASE_DIR / "uploads"
app = Flask(__name__, template_folder=".", static_folder=None)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
)

# Deployment platforms inject HOST and PORT; local development keeps the
# familiar localhost binding unless overridden.
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))


@app.template_filter("from_json")
def from_json(value):
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
client = Anthropic(api_key=API_KEY) if API_KEY else None
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
ROUND_QUESTION_COUNTS = {1: 20, 2: 20}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exception=None):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS profiles (
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          display_name TEXT NOT NULL DEFAULT '', level TEXT NOT NULL DEFAULT 'Entry-level',
          resume_text TEXT NOT NULL DEFAULT '', resume_filename TEXT NOT NULL DEFAULT '',
          phone TEXT NOT NULL DEFAULT '', college TEXT NOT NULL DEFAULT '', graduation_year INTEGER,
          preferred_role TEXT NOT NULL DEFAULT '', preferred_company TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS companies (
          id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS roles (
          id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id),
          name TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'Entry-level'
        );
        CREATE TABLE IF NOT EXISTS assessment_attempts (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          role_id INTEGER REFERENCES roles(id), mode TEXT NOT NULL DEFAULT 'official',
          round_number INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'started',
          score REAL, answers_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS interview_sessions (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          role_id INTEGER REFERENCES roles(id), mode TEXT NOT NULL DEFAULT 'practice',
          transcript_json TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
          score REAL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS xp_events (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          amount INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS badges (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', UNIQUE(user_id, name)
        );
        CREATE TABLE IF NOT EXISTS streaks (
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          current_count INTEGER NOT NULL DEFAULT 0, longest_count INTEGER NOT NULL DEFAULT 0,
          last_activity TEXT
        );
        CREATE TABLE IF NOT EXISTS question_bank (
          id INTEGER PRIMARY KEY, round_number INTEGER NOT NULL DEFAULT 1,
          prompt TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',
          options_json TEXT NOT NULL DEFAULT '[]', correct_answer TEXT NOT NULL DEFAULT '',
          explanation TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
          difficulty TEXT NOT NULL DEFAULT 'medium', role_name TEXT NOT NULL DEFAULT '',
          source_type TEXT NOT NULL DEFAULT 'general'
        );
        """
    )
    # Keep the catalogue deterministic and useful even on a brand-new install.
    for name, description in [
        ("Google", "Technology, product, and data roles"),
        ("Microsoft", "Cloud, software, and business roles"),
        ("Amazon", "Technology and operations roles"),
        ("Meta", "Product and software roles"),
        ("Apple", "Hardware, software, and design roles"),
        ("Netflix", "Technology and creative roles"),
        ("TCS", "Technology and consulting roles"),
        ("Infosys", "Technology and consulting roles"),
        ("Wipro", "Technology and consulting roles"),
        ("Accenture", "Technology and consulting roles"),
        ("Cognizant", "Technology and consulting roles"),
        ("Capgemini", "Technology and consulting roles"),
        ("Deloitte", "Consulting and analyst roles"),
        ("IBM", "Technology and consulting roles"),
        ("Oracle", "Cloud and database roles"),
        ("Adobe", "Creative technology roles"),
        ("Flipkart", "Technology and ecommerce roles"),
        ("Paytm", "Fintech and product roles"),
        ("General Placement", "General placement preparation"),
        ("Other", "Choose this for any employer not listed"),
    ]:
        db.execute("INSERT OR IGNORE INTO companies(name, description) VALUES (?, ?)", (name, description))
    catalog_roles = [
        ("Google", "Software Engineer"), ("Google", "Data Analyst"),
        ("Microsoft", "Software Engineer"), ("Microsoft", "Product Manager"),
        ("Amazon", "Software Development Engineer"), ("Amazon", "Business Analyst"),
        ("Meta", "Software Engineer"), ("Meta", "Product Manager"),
        ("Apple", "Software Engineer"), ("Apple", "UX Designer"),
        ("Netflix", "Software Engineer"), ("TCS", "Systems Engineer"),
        ("Infosys", "Systems Engineer"),
        ("Deloitte", "Consultant"), ("Deloitte", "Analyst"),
        ("Other", "General Placement"),
    ]
    for role_name in [
        "Software Developer", "Web Developer", "Frontend Developer",
        "Backend Developer", "Full Stack Developer", "Data Scientist",
        "AI Engineer", "Machine Learning Engineer", "GenAI Engineer",
        "Python Developer", "Java Developer", "Cloud Engineer",
        "QA Engineer", "Cybersecurity Analyst", "DevOps Engineer",
    ]:
        catalog_roles.append(("Other", role_name))
    for company, role in catalog_roles:
        cid = db.execute("SELECT id FROM companies WHERE name=?", (company,)).fetchone()[0]
        exists = db.execute("SELECT 1 FROM roles WHERE company_id=? AND name=? AND level='Entry-level'",
                            (cid, role)).fetchone()
        if not exists:
            db.execute("INSERT INTO roles(company_id, name, level) VALUES (?, ?, 'Entry-level')", (cid, role))
    question_prompts = [
        "Tell me about yourself and your career goals.", "Why are you interested in this role?",
        "What is your greatest strength?", "What is one area you are improving?",
        "Describe a project you are proud of.", "Tell me about a difficult problem you solved.",
        "How do you prioritize competing tasks?", "Describe a time you worked in a team.",
        "How do you respond to constructive feedback?", "Where do you see yourself in three years?",
        "What makes a good software engineer?", "Explain the difference between a list and a tuple in Python.",
        "What is an API?", "What is the purpose of version control?", "Explain a database index.",
        "What is normalization?", "What is the difference between GET and POST?", "What is a unit test?",
        "How would you debug a failing program?", "What is Big O notation?", "What is a primary key?",
        "Explain inheritance in object-oriented programming.", "What is a Git branch?",
        "What is the difference between a process and a thread?", "How would you design a URL shortener?",
        "How would you investigate a slow endpoint?", "How do you keep code maintainable?",
        "What is caching and when can it help?", "How would you handle an ambiguous requirement?",
        "What makes an effective code review?", "How do you learn an unfamiliar technology?",
        "Tell me about a time you met a tight deadline.", "Describe a failure and what you learned.",
        "How do you communicate technical ideas to non-technical people?",
        "What would you do if you disagreed with a teammate?",
        "How do you ensure your work is inclusive and accessible?",
        "What questions would you ask an interviewer?", "Why should we select you?",
        "How do you handle changing priorities?", "What is your approach to technical debt?",
    ]
    if db.execute("SELECT COUNT(*) FROM question_bank WHERE round_number=1").fetchone()[0] < len(question_prompts):
        for prompt in question_prompts:
            if not db.execute("SELECT 1 FROM question_bank WHERE round_number=1 AND prompt=?", (prompt,)).fetchone():
                db.execute("INSERT INTO question_bank(round_number,prompt) VALUES (1,?)", (prompt,))
    # Lightweight migration for databases created by the original MVP.
    columns = {row[1] for row in db.execute("PRAGMA table_info(profiles)")}
    for column, definition in {
        "phone": "TEXT NOT NULL DEFAULT ''", "college": "TEXT NOT NULL DEFAULT ''", "graduation_year": "INTEGER",
        "preferred_role": "TEXT NOT NULL DEFAULT ''", "preferred_company": "TEXT NOT NULL DEFAULT ''",
    }.items():
        if column not in columns:
            db.execute(f"ALTER TABLE profiles ADD COLUMN {column} {definition}")
    question_columns = {row[1] for row in db.execute("PRAGMA table_info(question_bank)")}
    for column, definition in {
        "options_json": "TEXT NOT NULL DEFAULT '[]'",
        "correct_answer": "TEXT NOT NULL DEFAULT ''",
        "explanation": "TEXT NOT NULL DEFAULT ''",
        "category": "TEXT NOT NULL DEFAULT ''",
        "difficulty": "TEXT NOT NULL DEFAULT 'medium'",
        "role_name": "TEXT NOT NULL DEFAULT ''",
        "source_type": "TEXT NOT NULL DEFAULT 'general'",
    }.items():
        if column not in question_columns:
            db.execute(f"ALTER TABLE question_bank ADD COLUMN {column} {definition}")
    _seed_structured_questions(db)
    _seed_placement_standard_questions(db)
    _seed_aptitude_questions(db)
    _seed_expanded_technical_questions(db)
    structured = db.execute(
        "SELECT id FROM question_bank WHERE options_json <> '[]' ORDER BY round_number, id"
    ).fetchall()
    for row in structured:
        question_id = row[0]
        current = db.execute(
            "SELECT difficulty, role_name, source_type FROM question_bank WHERE id=?", (question_id,)
        ).fetchone()
        difficulty = current[0] if current[0] in {"easy", "medium", "hard"} else "medium"
        source_type = current[2] if current[2] != "general" else (
            "role_specific" if current[1] else "general"
        )
        db.execute("UPDATE question_bank SET difficulty=?,source_type=? WHERE id=?",
                   (difficulty, source_type, question_id))
    db.commit()
    db.close()


def _seed_structured_questions(db):
    """Seed deterministic MCQs so official rounds never depend on an AI key."""
    round_one = [
        ("What is 20% of 150?", ["20", "30", "35", "40"], "30", "Percentage"),
        ("A product bought for 800 is sold for 920. What is the profit percentage?", ["10%", "12%", "15%", "20%"], "15%", "Profit & Loss"),
        ("The ratio 3:5 is equivalent to:", ["6:10", "5:3", "9:20", "12:25"], "6:10", "Ratio"),
        ("The average of 10, 20 and 30 is:", ["15", "20", "25", "30"], "20", "Average"),
        ("If a worker completes a task in 10 days, the daily work rate is:", ["1/5", "1/10", "10", "100"], "1/10", "Time & Work"),
        ("A car travels 120 km in 3 hours. Its average speed is:", ["30 km/h", "40 km/h", "60 km/h", "90 km/h"], "40 km/h", "Time & Distance"),
        ("What is the probability of getting heads on a fair coin?", ["0", "1/4", "1/2", "1"], "1/2", "Probability"),
        ("Which is the smallest prime number?", ["0", "1", "2", "3"], "2", "Number System"),
        ("Find the next number: 2, 4, 8, 16, ?", ["20", "24", "32", "36"], "32", "Number Series"),
        ("If CAT is coded as DBU, DOG is coded as:", ["EPH", "CNE", "FPH", "EOG"], "EPH", "Coding-Decoding"),
        ("A is the brother of B. B is the sister of C. A is C's:", ["Sister", "Brother", "Mother", "Father"], "Brother", "Blood Relations"),
        ("You face north, turn right, then turn right again. You face:", ["East", "West", "South", "North"], "South", "Direction Sense"),
        ("Book is to Reading as Fork is to:", ["Writing", "Eating", "Drawing", "Cutting"], "Eating", "Analogy"),
        ("All roses are flowers. Some flowers fade. Which statement is definitely true?", ["All roses fade", "Some roses fade", "All roses are flowers", "No flowers are roses"], "All roses are flowers", "Syllogism"),
        ("If P > Q and Q > R, then:", ["R > P", "P > R", "P = R", "Q < R"], "P > R", "Logical Reasoning"),
        ("What is 15% of 200?", ["15", "20", "30", "35"], "30", "Percentage"),
        ("A 10% discount on 500 gives a selling price of:", ["450", "480", "490", "550"], "450", "Profit & Loss"),
        ("The LCM of 4 and 6 is:", ["2", "8", "12", "24"], "12", "Number System"),
        ("If 5 pens cost 50, one pen costs:", ["5", "10", "15", "20"], "10", "Ratio"),
        ("A train covers 90 km in 2 hours. Speed is:", ["35 km/h", "45 km/h", "60 km/h", "90 km/h"], "45 km/h", "Time & Distance"),
        ("The next number in 5, 10, 15, 20 is:", ["22", "24", "25", "30"], "25", "Number Series"),
        ("If today is Monday, after 3 days it will be:", ["Tuesday", "Wednesday", "Thursday", "Friday"], "Thursday", "Direction Sense"),
        ("Some students are athletes. All athletes train. Therefore:", ["Some students train", "All students train", "No students train", "All trainers are students"], "Some students train", "Syllogism"),
        ("Which number is divisible by 3?", ["14", "22", "27", "31"], "27", "Number System"),
        ("A 25% increase on 80 equals:", ["90", "95", "100", "105"], "100", "Percentage"),
        ("A and B share money in ratio 2:3. If total is 50, B gets:", ["20", "25", "30", "35"], "30", "Ratio"),
        ("The mean of 4 and 8 is:", ["4", "5", "6", "8"], "6", "Average"),
        ("A statement that contradicts itself is called:", ["Analogy", "Paradox", "Ratio", "Series"], "Paradox", "Logical Reasoning"),
        ("If EAST is written as FBTU, WEST is written as:", ["XFTU", "WFTU", "VDRS", "YFTV"], "XFTU", "Coding-Decoding"),
        ("A person walks 3 km east and 3 km west. Distance from start is:", ["0 km", "3 km", "6 km", "9 km"], "0 km", "Direction Sense"),
        ("The opposite of 'ancient' is:", ["Old", "Modern", "Past", "Historic"], "Modern", "Analogy"),
        ("What is 7 squared?", ["14", "21", "42", "49"], "49", "Number System"),
        ("If 2 workers finish in 6 days, more workers generally make the time:", ["Longer", "Shorter", "Equal", "Infinite"], "Shorter", "Time & Work"),
        ("A bag has 2 red and 2 blue balls. Probability of red is:", ["1/4", "1/2", "3/4", "1"], "1/2", "Probability"),
        ("Find the missing term: 3, 6, 12, 24, ?", ["36", "42", "48", "54"], "48", "Number Series"),
        ("If every developer is a learner and Sam is a developer, Sam is a:", ["Manager", "Learner", "Designer", "Tester"], "Learner", "Syllogism"),
        ("Which is the odd one out?", ["Circle", "Square", "Triangle", "Blue"], "Blue", "Analogy"),
        ("A 500 rupee item sold for 450 has a loss of:", ["5%", "10%", "15%", "20%"], "10%", "Profit & Loss"),
        ("If 3x = 21, x equals:", ["6", "7", "8", "9"], "7", "Logical Reasoning"),
        ("A clock shows 3:00. The minute hand points:", ["3", "6", "9", "12"], "12", "Logical Reasoning"),
    ]
    existing = db.execute("SELECT COUNT(*) FROM question_bank WHERE round_number=1 AND options_json <> '[]'").fetchone()[0]
    if existing < len(round_one):
        for prompt, options, answer, category in round_one:
            db.execute(
                """INSERT INTO question_bank(round_number,prompt,answer,options_json,correct_answer,
                   explanation,category,difficulty,role_name) VALUES (1,?,?,?,?,?,?,?,?)""",
                (prompt, answer, json.dumps(options), answer, f"The correct answer is {answer}.", category, "easy", ""),
            )
    technical = [
        ("What does OOP encapsulation achieve?", ["Hiding implementation details", "Sorting arrays", "Compressing files", "Rendering CSS"], "Hiding implementation details", "Software Developer"),
        ("Which SQL clause filters rows?", ["ORDER BY", "WHERE", "GROUP BY", "JOIN"], "WHERE", "Data Analyst"),
        ("Which HTML element creates a link?", ["<div>", "<a>", "<p>", "<img>"], "<a>", "Web Developer"),
        ("What does a Python dictionary store?", ["Only numbers", "Key-value pairs", "Only strings", "HTML nodes"], "Key-value pairs", "Python Developer"),
        ("Which structure follows FIFO?", ["Stack", "Queue", "Tree", "Graph"], "Queue", "Software Engineer"),
        ("What is a primary key?", ["A duplicate field", "A unique row identifier", "A password", "A CSS selector"], "A unique row identifier", "Data Analyst"),
        ("What is overfitting in machine learning?", ["Poor training accuracy", "Memorizing training data", "Deleting data", "Scaling features"], "Memorizing training data", "AI Engineer"),
        ("Which CSS property changes text color?", ["font-size", "color", "display", "padding"], "color", "Frontend Developer"),
        ("What does an HTTP 404 mean?", ["Success", "Unauthorized", "Not found", "Server started"], "Not found", "Web Developer"),
        ("What is the average complexity of binary search?", ["O(1)", "O(log n)", "O(n)", "O(n²)"], "O(log n)", "Software Developer"),
    ]
    technical_count = db.execute("SELECT COUNT(*) FROM question_bank WHERE round_number=2 AND options_json <> '[]'").fetchone()[0]
    if technical_count < len(technical):
        for prompt, options, answer, role_name in technical:
            db.execute(
                """INSERT INTO question_bank(round_number,prompt,answer,options_json,correct_answer,
                   explanation,category,difficulty,role_name) VALUES (2,?,?,?,?,?,?,?,?)""",
                (prompt, answer, json.dumps(options), answer, f"The correct answer is {answer}.", "Technical", "medium", role_name),
            )


def _seed_placement_standard_questions(db):
    """Add final-year engineering and campus-placement level MCQs."""
    questions = [
        (1, "easy", "DSA", "Given an array and a target, which technique finds a pair in O(n) average time?", ["Nested loops", "Hash set", "Insertion sort", "DFS"], "Hash set"),
        (1, "easy", "DSA", "What is the amortized complexity of appending to a dynamic array?", ["O(1)", "O(log n)", "O(n)", "O(n log n)"], "O(1)"),
        (1, "easy", "OOP", "Which OOP principle lets a subtype be used wherever its base type is expected?", ["Encapsulation", "Inheritance", "Polymorphism", "Serialization"], "Polymorphism"),
        (1, "easy", "Machine Learning", "Which technique is primarily used to reduce overfitting?", ["Increasing model variance", "Regularization", "Removing validation data", "Training for one epoch"], "Regularization"),
        (1, "easy", "Machine Learning", "What does a loss function measure during model training?", ["Storage usage", "Prediction error", "Network bandwidth", "Feature count"], "Prediction error"),
        (1, "medium", "DSA", "Why is the two-pointer technique effective for finding a pair sum in a sorted array?", ["It explores every permutation", "It discards impossible ranges using ordering", "It requires a hash table", "It sorts after every comparison"], "It discards impossible ranges using ordering"),
        (1, "medium", "DSA", "A binary search implementation on a rotated sorted array must primarily handle:", ["Duplicate HTML tags", "Which sorted half contains the target", "Only negative numbers", "A graph cycle"], "Which sorted half contains the target"),
        (1, "medium", "OOP", "Which design choice best supports substituting a mock object in a unit test?", ["Concrete global state", "Programming to an interface", "Private constructors only", "Duplicated logic"], "Programming to an interface"),
        (1, "medium", "Machine Learning", "A large training score and much lower validation score most strongly indicates:", ["Underfitting", "Overfitting", "Perfect calibration", "Data compression"], "Overfitting"),
        (1, "medium", "Systems", "What is the main purpose of a database index?", ["Guarantee zero writes", "Speed up selected lookups", "Encrypt every column", "Replace transactions"], "Speed up selected lookups"),
        (1, "hard", "DSA", "Which algorithm computes single-source shortest paths with non-negative edge weights?", ["Kruskal", "Dijkstra", "KMP", "Floyd cycle detection"], "Dijkstra"),
        (1, "hard", "DSA", "What is the usual worst-case time complexity of building a heap from n values?", ["O(1)", "O(log n)", "O(n)", "O(n log n)"], "O(n)"),
        (1, "hard", "Systems", "For a read-heavy globally distributed service, which combination best reduces latency while preserving a source of truth?", ["Only client retries", "CDN/cache with a primary datastore", "One synchronous process", "Disabling replication"], "CDN/cache with a primary datastore"),
        (1, "hard", "Machine Learning", "Why can batch normalization become less reliable with very small batches?", ["It removes all labels", "Batch statistics become noisy", "It disables gradients", "It sorts features alphabetically"], "Batch statistics become noisy"),
        (1, "hard", "Systems", "A queue-backed service is falling behind as traffic spikes. What is the most robust first scaling action?", ["Increase request timeouts", "Add consumers and monitor lag", "Disable acknowledgements", "Drop all queued work"], "Add consumers and monitor lag"),
        (2, "easy", "DSA", "What is the time complexity of traversing every node in a binary tree?", ["O(1)", "O(log n)", "O(n)", "O(n²)"], "O(n)"),
        (2, "easy", "OOP", "Which principle keeps an object's internal state protected behind methods?", ["Encapsulation", "Recursion", "Indexing", "Compilation"], "Encapsulation"),
        (2, "easy", "Machine Learning", "What is the purpose of a validation set?", ["Tune choices before final testing", "Store production logs", "Replace training labels", "Increase CPU clock speed"], "Tune choices before final testing"),
        (2, "easy", "Web", "Which HTTP method is conventionally idempotent for replacing a resource?", ["POST", "PUT", "CONNECT", "PATCH only"], "PUT"),
        (2, "easy", "Databases", "What does a transaction's atomicity guarantee?", ["All operations happen or none do", "Queries are always fast", "Rows are never locked", "Data is automatically replicated"], "All operations happen or none do"),
        (2, "medium", "DSA", "What is the key benefit of memoization in overlapping subproblems?", ["It stores computed results", "It removes base cases", "It forces recursion depth to one", "It sorts inputs"], "It stores computed results"),
        (2, "medium", "Systems", "Why would an API use a circuit breaker?", ["To compress JSON", "To stop repeated calls to an unhealthy dependency", "To replace authentication", "To remove all timeouts"], "To stop repeated calls to an unhealthy dependency"),
        (2, "medium", "Databases", "Under a composite index on (user_id, created_at), which query benefits most directly?", ["Filter by user_id and order by created_at", "Filter only by an unrelated column", "Update every table", "Drop the index"], "Filter by user_id and order by created_at"),
        (2, "medium", "Machine Learning", "A model has high bias and high training error. Which change is most likely to help?", ["Use a more expressive model", "Add stronger regularization", "Remove useful features", "Reduce training data"], "Use a more expressive model"),
        (2, "medium", "Debugging", "An endpoint is slow only in production. What should be checked first?", ["Change variable names", "Trace latency across dependencies and inspect metrics", "Delete logs", "Disable monitoring"], "Trace latency across dependencies and inspect metrics"),
        (2, "hard", "DSA", "What is the typical complexity of finding strongly connected components with Kosaraju's algorithm?", ["O(V + E)", "O(V²E)", "O(log V)", "O(E²)"], "O(V + E)"),
        (2, "hard", "Systems", "Which trade-off is introduced by eventually consistent replicas?", ["Lower availability always", "Reads may temporarily observe stale data", "Writes become impossible", "Indexes cannot exist"], "Reads may temporarily observe stale data"),
        (2, "hard", "Systems", "For a highly contended counter across regions, which approach avoids a single write bottleneck while preserving correctness?", ["Uncoordinated overwrites", "Sharded counters with a mergeable design", "One client-owned value", "Random sleeps only"], "Sharded counters with a mergeable design"),
        (2, "hard", "Machine Learning", "Why can a transformer inference service become memory-bound as context length grows?", ["Attention caches scale with tokens and layers", "Labels become integers", "HTTP headers disappear", "The tokenizer stops running"], "Attention caches scale with tokens and layers"),
        (2, "hard", "Architecture", "Which design best protects a core service from a failing downstream dependency?", ["Unbounded synchronous retries", "Timeouts, bounded retries, circuit breaking, and graceful degradation", "One shared global lock", "Ignoring error rates"], "Timeouts, bounded retries, circuit breaking, and graceful degradation"),
    ]
    for round_number, difficulty, category, prompt, options, answer in questions:
        if db.execute("SELECT 1 FROM question_bank WHERE prompt=?", (prompt,)).fetchone():
            continue
        db.execute(
            """INSERT INTO question_bank(round_number,prompt,answer,options_json,correct_answer,
               explanation,category,difficulty,role_name,source_type)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (round_number, prompt, answer, json.dumps(options), answer,
             f"{answer} is the placement-standard answer because it addresses the underlying concept.",
             category, difficulty, "", "placement_standard"),
        )


def _seed_aptitude_questions(db):
    """Seed final-year campus aptitude and analytical-reasoning MCQs."""
    questions = [
        ("easy", "Time, Speed & Distance", "A 180 m train crosses a 270 m platform in 18 seconds. What is the train's speed?", ["70 km/h", "80 km/h", "90 km/h", "100 km/h"], "90 km/h", "Total distance = 180 + 270 = 450 m. Speed = 450/18 = 25 m/s = 90 km/h."),
        ("hard", "Time, Speed & Distance", "Two trains of lengths 150 m and 250 m travel in opposite directions at 54 km/h and 72 km/h. How long do they take to cross each other?", ["10 s", "11.43 s", "14 s", "16 s"], "11.43 s", "Relative speed = 54 + 72 = 126 km/h = 35 m/s. Total length = 400 m, so time = 400/35 = 11.43 s."),
        ("hard", "Time, Speed & Distance", "A boat travels 30 km downstream in 2 hours and the same distance upstream in 3 hours. What is the speed of the stream?", ["2 km/h", "2.5 km/h", "3 km/h", "3.5 km/h"], "2.5 km/h", "Downstream speed = 15 km/h and upstream speed = 10 km/h. Stream speed = (15 - 10)/2 = 2.5 km/h."),
        ("easy", "Time, Speed & Distance", "A runner completes one lap of a circular track in 80 seconds and another in 120 seconds. If they start together, after how long will they meet at the start again?", ["120 s", "180 s", "240 s", "360 s"], "240 s", "They meet at the start after LCM(80, 120) = 240 seconds."),
        ("medium", "Time & Work", "A can complete a task in 12 days and B in 18 days. They work on alternate days starting with A. In how many days is the task completed?", ["14 days", "14 1/3 days", "15 days", "15 1/3 days"], "14 1/3 days", "Two-day work = 1/12 + 1/18 = 5/36. After 12 days, 30/36 is done; A adds 3/36 on day 13 and B adds the final 3/36 in one-third of day 14."),
        ("easy", "Time & Work", "Pipe A fills a tank in 20 minutes, B in 30 minutes, and a drain empties it in 60 minutes. If all are opened together, how long will filling take?", ["10 min", "12 min", "15 min", "18 min"], "15 min", "Net rate = 1/20 + 1/30 - 1/60 = 1/15 tank per minute."),
        ("hard", "Time & Work", "A is twice as efficient as B, and B is three times as efficient as C. Together they finish a task in 12 days. How long would C alone take?", ["72 days", "96 days", "120 days", "144 days"], "120 days", "Efficiency ratio A:B:C = 6:3:1, total 10 units. C contributes one-tenth of the joint rate, so C alone takes 12 x 10 = 120 days."),
        ("easy", "Probability", "Two fair dice are rolled. What is the probability that the sum is at least 10?", ["1/9", "1/6", "1/4", "1/3"], "1/6", "Favourable outcomes are (4,6), (5,5), (6,4), (5,6), (6,5), (6,6): 6/36 = 1/6."),
        ("hard", "Probability", "A card is drawn from a standard deck. Given that it is a face card, what is the probability it is a king?", ["1/3", "1/4", "1/6", "4/13"], "1/3", "There are 12 face cards and 4 kings. Conditional probability = 4/12 = 1/3."),
        ("hard", "Permutation & Combination", "How many arrangements of the letters in 'LEVEL' are possible?", ["20", "30", "40", "60"], "30", "There are 5 letters with L repeated twice and E repeated twice. Arrangements = 5!/(2!2!) = 30."),
        ("easy", "Permutation & Combination", "Six candidates sit in a row. If A and B must sit together, how many arrangements are possible?", ["120", "180", "240", "360"], "240", "Treat AB as one block: 5! arrangements, with 2 internal orders, giving 5! x 2 = 240."),
        ("medium", "Profit, Loss & Discount", "A marked price is discounted successively by 20% and 10%. What single discount gives the same selling price?", ["26%", "28%", "30%", "32%"], "28%", "Net price factor = 0.8 x 0.9 = 0.72, so equivalent discount = 1 - 0.72 = 28%."),
        ("hard", "Profit, Loss & Discount", "A shopkeeper uses a 900 g weight but charges for 1 kg at cost price. What is the effective profit percentage?", ["10%", "11 1/9%", "12.5%", "15%"], "11 1/9%", "Revenue equals the cost of 1 kg while cost is only 0.9 kg. Profit ratio = (1 - 0.9)/0.9 = 1/9 = 11 1/9%."),
        ("medium", "Mixtures & Alligation", "A 20 L solution contains milk and water in ratio 3:1. How much water must be added to make the ratio 3:2?", ["3 L", "4 L", "5 L", "6 L"], "5 L", "Milk = 15 L and water = 5 L. For ratio 3:2, water must be 10 L, so add 5 L."),
        ("medium", "Averages & Percentages", "The average of 8 numbers is 24. If one number 36 is replaced by 20, what is the new average?", ["20", "21", "22", "23"], "22", "Original total = 8 x 24 = 192. New total = 192 - 36 + 20 = 176; new average = 176/8 = 22."),
        ("hard", "Data Interpretation", "A team processes 120, 150 and 180 requests on Monday, Tuesday and Wednesday. By what percentage did Wednesday exceed Monday?", ["40%", "45%", "50%", "60%"], "50%", "Increase = 180 - 120 = 60. Percentage increase = 60/120 x 100 = 50%."),
        ("medium", "Data Interpretation", "Sales are 40, 50, 60 and 70 units across four quarters. What percentage of total sales occurred in Q3 and Q4?", ["50%", "55%", "59.09%", "62.5%"], "59.09%", "Q3 + Q4 = 130 and total = 220. Share = 130/220 x 100 = 59.09%."),
        ("medium", "Seating Arrangement", "P, Q, R, S and T sit in a row facing north. Q is immediately right of P, R is at the left end, and T is not next to R. Who sits in the middle?", ["P", "Q", "S", "T"], "Q", "R must be position 1. P-Q occupy positions 3-4 or 4-5; T not next to R forces P-Q to positions 3-4, leaving S at 2 and T at 5. Q is in the middle."),
        ("hard", "Seating Arrangement", "Six people sit around a circle facing the centre. A is between B and C, D is opposite A, and E is immediately clockwise from D. Who is immediately anticlockwise from A?", ["B", "C", "D", "F"], "B", "Fix A at the top. B and C occupy its adjacent seats; with D opposite A and E clockwise from D, the remaining constraints place B anticlockwise from A."),
        ("medium", "Syllogism", "Statements: Some analysts are coders. All coders are testers. Which conclusion definitely follows?", ["Some analysts are testers", "All analysts are testers", "No testers are analysts", "Some testers are not coders"], "Some analysts are testers", "The analysts who are coders must be testers because all coders are testers. Therefore some analysts are testers."),
        ("hard", "Critical Reasoning", "A company should add automated tests because its release defects increased. Which assumption is required?", ["Tests guarantee zero defects", "Some release defects are preventable by earlier validation", "Developers dislike testing", "Manual testing is illegal"], "Some release defects are preventable by earlier validation", "The recommendation connects automated tests to defect reduction only if at least some defects can be caught by earlier validation."),
        ("medium", "Blood Relations", "Pointing to a woman, Arun says: 'Her mother's only son is my father's only son.' How is the woman related to Arun?", ["Mother", "Sister", "Daughter", "Aunt"], "Sister", "Her mother's only son is Arun himself, and the woman is therefore Arun's sister."),
        ("hard", "Direction Sense", "A person walks 8 km north, 6 km east, 8 km south, then 2 km west. How far and in which direction are they from the start?", ["4 km east", "4 km west", "6 km east", "6 km west"], "4 km east", "North and south cancel. East displacement = 6 - 2 = 4 km, so the result is 4 km east."),
        ("medium", "Direction Sense", "Facing north, a person turns 135 degrees clockwise and then 90 degrees anticlockwise. Which direction do they face?", ["North-east", "North-west", "South-east", "South-west"], "North-east", "135 degrees clockwise points south-east; turning 90 degrees anticlockwise gives north-east."),
        ("hard", "Data Sufficiency", "Is x positive? (1) x² = 9. (2) x > -2.", ["Statement 1 alone", "Statement 2 alone", "Both together", "Neither together"], "Both together", "Statement 1 gives x = 3 or -3, not sufficient. Statement 2 allows many values. Together, x² = 9 and x > -2 force x = 3."),
        ("medium", "Data Sufficiency", "What is the value of n? (1) n is an even prime. (2) n is greater than 1.", ["Statement 1 alone", "Statement 2 alone", "Both together", "Neither together"], "Statement 1 alone", "The only even prime is 2, so statement 1 alone determines n."),
        ("hard", "Critical Reasoning", "A city proposes restricting private cars downtown to reduce congestion. Which is the strongest course of action?", ["Ban all vehicles immediately", "Increase public transit capacity before phased restrictions", "Ignore congestion data", "Close all businesses"], "Increase public transit capacity before phased restrictions", "A workable policy pairs demand reduction with a viable alternative, reducing disruption while addressing the stated cause."),
        ("medium", "Data Interpretation", "A bar chart shows candidates shortlisted from teams A-D as 24, 30, 36 and 45. What is the ratio of D to the total?", ["1:2", "1:3", "3:9", "5:15"], "1:3", "Total = 24 + 30 + 36 + 45 = 135. D's ratio = 45:135 = 1:3."),
        ("hard", "Mixtures & Alligation", "Rice costing 40/kg is mixed with rice costing 64/kg to obtain a mixture costing 49.60/kg. What is the ratio of cheaper to dearer rice?", ["2:1", "3:2", "4:1", "5:3"], "3:2", "By alligation, cheaper:dearer = (64 - 49.6):(49.6 - 40) = 14.4:9.6 = 3:2."),
        ("hard", "Logical Reasoning", "If every architect is a planner, no planner is careless, and some designers are architects, which conclusion follows?", ["Some designers are not careless", "All designers are planners", "Some careless people are architects", "No designers are architects"], "Some designers are not careless", "Some designers are architects, architects are planners, and planners are not careless. Therefore some designers are not careless."),
    ]
    for difficulty, category, prompt, options, answer, explanation in questions:
        existing = db.execute("SELECT id FROM question_bank WHERE prompt=?", (prompt,)).fetchone()
        if existing:
            db.execute(
                """UPDATE question_bank
                   SET round_number=1, answer=?, options_json=?, correct_answer=?,
                       explanation=?, category=?, difficulty=?, source_type='aptitude_standard'
                   WHERE id=?""",
                (answer, json.dumps(options), answer, explanation, category, difficulty, existing[0]),
            )
            continue
        db.execute(
            """INSERT INTO question_bank(round_number,prompt,answer,options_json,correct_answer,
               explanation,category,difficulty,role_name,source_type)
               VALUES (1,?,?,?,?,?,?,?,?,?)""",
            (prompt, answer, json.dumps(options), answer, explanation, category, difficulty, "", "aptitude_standard"),
        )

    additional = [
        ("easy", "Verbal Ability", "Choose the closest meaning of 'mitigate' in a production incident report.", ["Ignore", "Reduce", "Predict", "Repeat"], "Reduce", "Mitigate means to reduce the severity or impact of something."),
        ("medium", "Verbal Ability", "Complete the sentence: The patch was deployed after the issue had been ____ in staging.", ["identify", "identified", "identifying", "identifies"], "identified", "The past perfect passive construction requires 'had been identified'."),
        ("medium", "Verbal Ability", "Choose the best analogy: Cache : Latency :: Index : ____.", ["Storage", "Lookup time", "Compilation", "Encryption"], "Lookup time", "A cache reduces latency; an index reduces lookup time."),
        ("hard", "Verbal Ability", "In the sentence 'The observability gap obscured the regression', obscured most nearly means:", ["Exposed", "Concealed", "Measured", "Accelerated"], "Concealed", "To obscure something is to hide or make it difficult to see."),
        ("easy", "Verbal Ability", "Complete the sentence: A robust service should fail ____ when a dependency is unavailable.", ["gracefully", "graceful", "grace", "gracing"], "gracefully", "The adverb 'gracefully' correctly modifies the verb 'fail'."),
        ("medium", "Data Interpretation", "A team closes 48, 60 and 72 tickets in three sprints. What is the average closure rate?", ["54", "60", "64", "72"], "60", "Average = (48 + 60 + 72)/3 = 180/3 = 60."),
        ("hard", "Time, Speed & Distance", "A cyclist covers half a route at 20 km/h and the other half at 30 km/h. What is the average speed?", ["24 km/h", "25 km/h", "26 km/h", "27 km/h"], "24 km/h", "For equal distances, average speed is 2ab/(a+b) = 2x20x30/50 = 24 km/h."),
        ("medium", "Logical Deduction", "If all APIs are services and no service is stateless, which statement must be true?", ["Some APIs are stateless", "No APIs are stateless", "All stateless systems are APIs", "No services are APIs"], "No APIs are stateless", "APIs are a subset of services, and services are explicitly not stateless."),
        ("hard", "Seating Arrangement", "Four engineers P, Q, R and S sit in a row. P is left of Q, R is right of Q, and S is left of P. Who is second from the left?", ["P", "Q", "R", "S"], "P", "The only order satisfying S < P < Q < R places P second."),
        ("medium", "Data Sufficiency", "Is the integer n divisible by 6? (1) n is divisible by 2. (2) n is divisible by 3.", ["Statement 1 alone", "Statement 2 alone", "Both together", "Neither"], "Both together", "Divisibility by both 2 and 3 is required for divisibility by 6."),
    ]
    for difficulty, category, prompt, options, answer, explanation in additional:
        existing = db.execute("SELECT id FROM question_bank WHERE prompt=?", (prompt,)).fetchone()
        if existing:
            db.execute(
                """UPDATE question_bank SET round_number=1, answer=?, options_json=?,
                   correct_answer=?, explanation=?, category=?, difficulty=?, source_type='aptitude_standard'
                   WHERE id=?""",
                (answer, json.dumps(options), answer, explanation, category, difficulty, existing[0]),
            )
        else:
            db.execute(
                """INSERT INTO question_bank(round_number,prompt,answer,options_json,correct_answer,
                   explanation,category,difficulty,role_name,source_type)
                   VALUES (1,?,?,?,?,?,?,?,?,?)""",
                (prompt, answer, json.dumps(options), answer, explanation, category, difficulty, "", "aptitude_standard"),
            )


def _seed_expanded_technical_questions(db):
    """Ensure Round 2 has enough distinct campus technical questions for retests."""
    questions = [
        ("easy", "DSA", "Which traversal of a binary search tree returns keys in sorted order?", ["Preorder", "Inorder", "Postorder", "Level order"], "Inorder"),
        ("easy", "OOP", "Which feature allows one interface to have multiple implementations?", ["Polymorphism", "Inlining", "Hashing", "Paging"], "Polymorphism"),
        ("easy", "SQL/DBMS", "Which normal form removes partial dependency on part of a composite key?", ["1NF", "2NF", "3NF", "BCNF"], "2NF"),
        ("easy", "OS", "Which scheduling algorithm can cause starvation without aging?", ["Round robin", "FCFS", "Priority scheduling", "FIFO queue"], "Priority scheduling"),
        ("easy", "Networking", "Which protocol resolves a domain name to an IP address?", ["FTP", "DNS", "SSH", "SMTP"], "DNS"),
        ("medium", "DSA", "What is the best structure for implementing an LRU cache with O(1) get and put?", ["Array only", "Hash map plus doubly linked list", "Stack only", "Binary tree only"], "Hash map plus doubly linked list"),
        ("medium", "DSA", "Which condition is necessary for binary search to work correctly?", ["Random duplicates", "A sorted search space", "A linked list only", "A graph cycle"], "A sorted search space"),
        ("medium", "OOP", "Which principle says a class should have one reason to change?", ["Open/closed principle", "Single responsibility principle", "Liskov substitution", "Dependency inversion"], "Single responsibility principle"),
        ("medium", "SQL/DBMS", "What does a database transaction isolation level primarily control?", ["Query spelling", "Visibility of concurrent changes", "Disk partition size", "Column names"], "Visibility of concurrent changes"),
        ("medium", "OS", "A deadlock requires mutual exclusion, hold-and-wait, no preemption, and what fourth condition?", ["Recursion", "Circular wait", "Compilation", "Paging"], "Circular wait"),
        ("medium", "Networking", "Why is TCP preferred over UDP for reliable file transfer?", ["TCP has no headers", "TCP provides ordered delivery and retransmission", "UDP encrypts files", "UDP prevents congestion"], "TCP provides ordered delivery and retransmission"),
        ("medium", "AI Engineer", "Why should a feature pipeline be versioned with a trained model?", ["To increase randomness", "To reproduce training and prevent feature skew", "To remove labels", "To avoid monitoring"], "To reproduce training and prevent feature skew"),
        ("hard", "DSA", "What is the standard complexity of building a suffix array with an efficient doubling approach?", ["O(n)", "O(n log n)", "O(n log² n)", "O(n² log n)"], "O(n log² n)"),
        ("hard", "DSA", "Which technique is appropriate for detecting a negative cycle reachable from a source?", ["Dijkstra only", "Bellman-Ford relaxation", "Binary search", "Union-find only"], "Bellman-Ford relaxation"),
        ("hard", "OOP", "Which design most directly reduces high-level policy dependence on concrete infrastructure?", ["Dependency inversion", "Public fields", "Global variables", "Deep inheritance"], "Dependency inversion"),
        ("hard", "SQL/DBMS", "What is a likely trade-off of adding a covering index to a write-heavy table?", ["Writes may become more expensive", "Reads become impossible", "Transactions disappear", "Rows cannot be deleted"], "Writes may become more expensive"),
        ("hard", "OS", "What is the main purpose of copy-on-write after a process fork?", ["Share pages until mutation", "Disable virtual memory", "Force immediate copying", "Remove page tables"], "Share pages until mutation"),
        ("hard", "Networking", "What does a load balancer health check protect against?", ["Healthy traffic", "Routing traffic to unavailable instances", "Database normalization", "Source compilation"], "Routing traffic to unavailable instances"),
        ("hard", "Backend", "Which strategy best prevents a retry storm when a dependency is degraded?", ["Immediate infinite retries", "Exponential backoff with jitter and a retry budget", "Removing timeouts", "Synchronous global locking"], "Exponential backoff with jitter and a retry budget"),
        ("hard", "ML Engineer", "Why can a model with excellent offline accuracy fail after deployment?", ["The CPU has a name", "Production data distribution may differ from training data", "Labels are always perfect", "Indexes prevent inference"], "Production data distribution may differ from training data"),
        ("easy", "C++/Java", "What is the purpose of a destructor or finalizer concept in object-oriented languages?", ["Release resources during object cleanup", "Sort objects", "Create network routes", "Normalize tables"], "Release resources during object cleanup"),
        ("medium", "SQL/DBMS", "Which SQL operation combines rows from two tables using a related condition?", ["JOIN", "TRUNCATE", "ALTER", "COMMIT"], "JOIN"),
        ("medium", "OS", "What does virtual memory allow an operating system to do?", ["Use disk as an extension of addressable memory", "Remove process isolation", "Disable page faults", "Replace CPU scheduling"], "Use disk as an extension of addressable memory"),
        ("hard", "Distributed Systems", "What does idempotency allow a client to safely do after an uncertain network timeout?", ["Repeat the same request without changing the result again", "Skip authentication", "Guarantee zero latency", "Disable persistence"], "Repeat the same request without changing the result again"),
        ("hard", "Security", "Which control most directly reduces SQL injection risk in application queries?", ["Parameterized statements", "String concatenation", "Longer passwords only", "Client-side hiding"], "Parameterized statements"),
    ]
    for difficulty, category, prompt, options, answer in questions:
        if db.execute("SELECT 1 FROM question_bank WHERE prompt=?", (prompt,)).fetchone():
            continue
        db.execute(
            """INSERT INTO question_bank(round_number,prompt,answer,options_json,correct_answer,
               explanation,category,difficulty,role_name,source_type)
               VALUES (2,?,?,?,?,?,?,?,?,?)""",
            (prompt, answer, json.dumps(options), answer,
             f"{answer} is correct because it matches the underlying {category} principle.",
             category, difficulty, "", "placement_standard"),
        )


def json_error(message, status=400):
    return jsonify({"error": message}), status


def request_data():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")
    return data


def question_payload(row, number):
    options = json.loads(row["options_json"] or "[]")
    random.shuffle(options)
    return {
        "id": number,
        "question": row["prompt"],
        "category": row["category"],
        "options": options,
        "answer": row["correct_answer"],
        "explanation": row["explanation"],
    }


def csrf_ok():
    token = session.get("csrf_token")
    return bool(token and secrets.compare_digest(token, request.headers.get("X-CSRF-Token", "")))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return json_error("Login required.", 401) if request.path.startswith("/api/") else redirect(url_for("web_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def require_authentication():
    """Make the login page the only public application surface."""
    public_paths = {"/", "/login", "/register", "/logout", "/api/auth/google"}
    if request.path in public_paths or request.path.startswith("/static/"):
        return None
    if not session.get("user_id"):
        if request.path.startswith("/api/"):
            return json_error("Login required.", 401)
        return redirect(url_for("web_login", next=request.full_path.rstrip("?")))
    return None


def mutation_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not csrf_ok():
            return json_error("Invalid CSRF token.", 403)
        return view(*args, **kwargs)
    return wrapped


def call_claude(prompt, max_tokens=900, temperature=0.9, top_p=0.95):
    if client is None:
        raise RuntimeError("AI is not configured. Set ANTHROPIC_API_KEY to enable coaching.")
    response = client.messages.create(model=MODEL, max_tokens=max_tokens,
                                      temperature=temperature, top_p=top_p,
                                      messages=[{"role": "user", "content": prompt}])
    return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")


def parse_json(raw):
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(cleaned)


def ai_json(prompt, shape):
    result = parse_json(call_claude(prompt))
    if not shape(result):
        raise ValueError("AI returned an invalid response.")
    return result


def fallback_questions(role, company, count=5, difficulty="medium"):
    """Keep practice usable with placement-standard questions without an AI key."""
    context = f"{role} at {company}"
    banks = {
        "easy": [
            f"Explain the core data structure or concept you would use most often in a {role} role.",
            "What is the time complexity of looking up a key in a hash table on average?",
            "Explain encapsulation and why it helps maintainable software.",
            "What is overfitting, and how does regularization help?",
            "Describe the difference between a unit test and an integration test.",
        ],
        "medium": [
            f"How would you debug a production issue in a {context} service?",
            "When would you choose a two-pointer algorithm over nested loops?",
            "How would you design an API that handles retries without duplicate side effects?",
            "How would you diagnose a model whose training score is high but validation score is low?",
            "Describe a trade-off between caching and data freshness.",
        ],
        "hard": [
            f"How would you scale a critical {role} service across regions while controlling consistency trade-offs?",
            "Compare Dijkstra, BFS, and A* for shortest-path workloads and state their constraints.",
            "How would you reduce tail latency when a downstream dependency is intermittently slow?",
            "Explain how data distribution and feature drift can degrade an ML system after deployment.",
            "Design a fault-tolerant queue-based architecture and explain backpressure handling.",
        ],
    }
    bank = banks.get(difficulty, banks["medium"])
    if count > len(bank):
        bank.extend(
            f"Give a placement-level example of applying your {role} skill at {company}."
            for _ in range(count - len(bank))
        )
    return bank[:count]


def fallback_feedback(answer):
    word_count = len(answer.split())
    score = 5 if word_count < 35 else 7 if word_count < 90 else 8
    return {
        "score": score,
        "strengths": "You gave a clear attempt and addressed the question directly.",
        "improvements": "Add one specific example, explain your actions, and finish with a measurable result.",
        "model_answer": "Use a short STAR structure: explain the situation, your task, the actions you took, and the result.",
    }


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("web_login"))


@app.route("/static/<path:filename>", endpoint="static")
def static_files(filename):
    return send_from_directory(BASE_DIR, filename)


@app.route("/login", methods=["GET", "POST"])
def web_login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], request.form.get("password", "")):
            flash("Email or password is incorrect.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(request.args.get("next") or url_for("dashboard"))
    return render_template("login.html", google_client_id=GOOGLE_CLIENT_ID)


@app.route("/register", methods=["GET", "POST"])
def web_register():
    if request.method == "POST":
        data = {key: request.form.get(key, "") for key in (
            "email", "password", "confirm_password", "name", "phone", "college", "graduation_year", "preferred_role"
        )}
        try:
            email, password, name = data["email"].strip().lower(), data["password"], data["name"].strip()[:80]
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or not name:
                raise ValueError("Enter a valid email address and your full name.")
            phone = re.sub(r"[^\d+()\-\s]", "", data["phone"]).strip()[:30]
            college = data["college"].strip()[:160]
            preferred_role = data["preferred_role"].strip()[:120]
            graduation_year = int(data["graduation_year"]) if data["graduation_year"] else None
            if graduation_year is not None and not 1950 <= graduation_year <= 2100:
                raise ValueError("Graduation year must be between 1950 and 2100.")
            if len(password) < 8 or len(password) > 128 or not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password) or not re.search(r"\d", password) or not re.search(r"[^A-Za-z0-9]", password):
                raise ValueError("Password must be 8–128 characters with uppercase, lowercase, number, and symbol.")
            if password != data["confirm_password"]:
                raise ValueError("Passwords do not match.")
            if email.split("@")[0] in password.lower() or (len(name) >= 3 and name.lower() in password.lower()):
                raise ValueError("Password must not contain your email name or full name.")
            db = get_db()
            uid = db.execute("INSERT INTO users(email,password_hash) VALUES (?,?)", (email, generate_password_hash(password))).lastrowid
            db.execute(
                "INSERT INTO profiles(user_id,display_name,phone,college,graduation_year,preferred_role) VALUES (?,?,?,?,?,?)",
                (uid, name, phone, college, graduation_year, preferred_role),
            )
            db.execute("INSERT INTO streaks(user_id) VALUES (?)", (uid,))
            db.commit()
            flash("Account created successfully. Please log in.", "success")
            return redirect(url_for("web_login"))
        except sqlite3.IntegrityError:
            flash("An account with that email already exists.", "error")
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("register.html")


@app.get("/logout")
def web_logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("web_login"))


@app.post("/api/auth/google")
def google_login():
    if not GOOGLE_CLIENT_ID:
        return json_error("Google sign-in is not configured on this server.", 503)
    data = request_data()
    credential = data.get("credential")
    if not isinstance(credential, str) or not credential:
        return json_error("Google sign-in token is missing.", 400)
    try:
        claims = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError:
        return json_error("Google sign-in token is invalid or expired.", 401)
    google_id = claims.get("sub")
    email = str(claims.get("email", "")).strip().lower()
    name = str(claims.get("name", "")).strip()[:80]
    if not google_id or not email or claims.get("email_verified") is not True:
        return json_error("Google account email could not be verified.", 401)
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if user is None:
        uid = db.execute(
            "INSERT INTO users(email,password_hash) VALUES (?,?)",
            (email, generate_password_hash(secrets.token_urlsafe(32))),
        ).lastrowid
        db.execute(
            "INSERT INTO profiles(user_id,display_name) VALUES (?,?)",
            (uid, name or email.split("@")[0]),
        )
        db.execute("INSERT INTO streaks(user_id) VALUES (?)", (uid,))
    else:
        uid = user["id"]
        if name:
            db.execute("UPDATE profiles SET display_name=? WHERE user_id=?", (name, uid))
    db.commit()
    session.clear()
    session["user_id"] = uid
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify({"ok": True, "redirect": url_for("dashboard")})


@app.get("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]
    profile = db.execute("SELECT * FROM profiles WHERE user_id=?", (uid,)).fetchone()
    selected = db.execute(
        "SELECT r.name, c.name company FROM roles r JOIN companies c ON c.id=r.company_id WHERE r.id=?",
        (session.get("role_id"),),
    ).fetchone()
    attempts = db.execute(
        "SELECT round_number, mode, score, status, created_at FROM assessment_attempts WHERE user_id=? ORDER BY id DESC LIMIT 6",
        (uid,),
    ).fetchall()
    xp = db.execute("SELECT COALESCE(SUM(amount),0) total FROM xp_events WHERE user_id=?", (uid,)).fetchone()["total"]
    badges = db.execute("SELECT name, description FROM badges WHERE user_id=? ORDER BY id DESC LIMIT 6", (uid,)).fetchall()
    passed = {
        row["round_number"]: row["score"]
        for row in db.execute(
            """SELECT round_number, score FROM assessment_attempts
               WHERE user_id=? AND mode='official' AND status='passed'
               AND id IN (SELECT MAX(id) FROM assessment_attempts WHERE user_id=? AND mode='official' GROUP BY round_number)""",
            (uid, uid),
        ).fetchall()
    }
    readiness = round(sum(float(row["score"] or 0) * 10 for row in attempts) / len(attempts), 1) if attempts else 0
    levels = [(0, "Placement Beginner"), (100, "Interview Explorer"), (250, "Interview Fighter"),
              (500, "Placement Pro"), (800, "Interview Master"), (1200, "Placement Champion")]
    level = max((label for threshold, label in levels if xp >= threshold), default=levels[0][1])
    next_threshold = next((threshold for threshold, _ in levels if threshold > xp), 1200)
    previous_threshold = max((threshold for threshold, _ in levels if threshold <= xp), default=0)
    xp_progress = min(100, round((xp - previous_threshold) / max(1, next_threshold - previous_threshold) * 100))
    return render_template(
        "dashboard.html", profile=profile, selected=selected, attempts=attempts,
        xp=xp, level=level, xp_progress=xp_progress, readiness=readiness,
        passed=passed, badges=badges,
    )


@app.route("/select-role", methods=["GET", "POST"])
@login_required
def select_role():
    db = get_db()
    if request.method == "POST":
        role_name = request.form.get("role_name", "").strip()[:120]
        company_name = request.form.get("company_name", "").strip()[:120]
        company = db.execute("SELECT id, name FROM companies WHERE name=?", (company_name,)).fetchone()
        if role_name and company:
            role = db.execute("SELECT r.*, c.name company FROM roles r JOIN companies c ON c.id=r.company_id WHERE r.company_id=? AND r.name=?",
                              (company["id"], role_name)).fetchone()
            if not role:
                db.execute("INSERT INTO roles(company_id,name,level) VALUES (?,?,?)", (company["id"], role_name, "Entry-level"))
                role_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                role = db.execute("SELECT r.*, c.name company FROM roles r JOIN companies c ON c.id=r.company_id WHERE r.id=?", (role_id,)).fetchone()
        else:
            role = None
        if role:
            session["role_id"] = role["id"]
            db.execute("UPDATE profiles SET preferred_role=?, preferred_company=? WHERE user_id=?", (role["name"], role["company"], session["user_id"]))
            db.commit()
            flash(f"Selected {role['name']} at {role['company']}.", "success")
            return redirect(url_for("placement"))
        flash("Choose a valid role and company.", "error")
    role_names = db.execute("SELECT DISTINCT name FROM roles ORDER BY name").fetchall()
    companies = db.execute("SELECT id, name, description FROM companies ORDER BY name").fetchall()
    return render_template("select_role.html", role_names=role_names, companies=companies)


@app.get("/profile")
@login_required
def profile():
    row = get_db().execute("SELECT * FROM profiles WHERE user_id=?", (session["user_id"],)).fetchone()
    return render_template("profile.html", profile=row)


@app.get("/placement")
@login_required
def placement():
    selected = get_db().execute("SELECT r.name, c.name company FROM roles r JOIN companies c ON c.id=r.company_id WHERE r.id=?", (session.get("role_id"),)).fetchone()
    unlocked = 1
    for number in range(2, 5):
        if not get_db().execute("SELECT 1 FROM assessment_attempts WHERE user_id=? AND round_number=? AND status='passed'", (session["user_id"], number - 1)).fetchone():
            break
        unlocked = number
    return render_template("placement.html", selected=selected, unlocked=unlocked, rounds=range(1, 5))


def _official_questions(db, user_id, role_id, round_number, fresh=False):
    key = f"official_round_{round_number}_questions"
    if fresh:
        session.pop(key, None)
        session.pop(f"official_round_{round_number}_options", None)
    saved_ids = session.get(key)
    if saved_ids:
        marks = ",".join("?" for _ in saved_ids)
        rows = db.execute(f"SELECT * FROM question_bank WHERE id IN ({marks})", saved_ids).fetchall()
        by_id = {row["id"]: row for row in rows}
        saved_rows = [by_id[item] for item in saved_ids if item in by_id]
        expected_source = "aptitude_standard" if round_number == 1 else "placement_standard"
        expected_count = ROUND_QUESTION_COUNTS.get(round_number)
        if (len(saved_rows) == len(saved_ids) == expected_count
                and all(row["source_type"] == expected_source for row in saved_rows)):
            return saved_rows
        session.pop(key, None)
    count = ROUND_QUESTION_COUNTS.get(round_number)
    history_key = f"official_round_{round_number}_served"
    served = set(session.get(history_key, []))
    if round_number == 1:
        source = "aptitude_standard"
        category_filter = ""
    else:
        source = "placement_standard"
        role = db.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()
        role_name = role["name"] if role else ""
        category_filter = " AND (role_name=? OR role_name='') "
    params = [round_number, source]
    query = """SELECT * FROM question_bank
               WHERE round_number=? AND options_json <> '[]' AND source_type=?"""
    if category_filter:
        query += category_filter
        params.append(role_name)
    if served:
        marks = ",".join("?" for _ in served)
        query += f" AND id NOT IN ({marks})"
        params.extend(served)
    query += " ORDER BY RANDOM() LIMIT ?"
    params.append(count)
    rows = db.execute(query, params).fetchall()
    if len(rows) < count:
        # Do not repeat silently; a clear message tells the user the unique bank is exhausted.
        session.pop(key, None)
        session.modified = True
        return []
    ids = [row["id"] for row in rows]
    session[key] = ids
    session[history_key] = list(served.union(ids))
    return rows


def selected_role_name(db, role_id):
    row = db.execute("SELECT name FROM roles WHERE id=?", (role_id,)).fetchone()
    return row["name"] if row else "candidate"


def selected_company_name(db, role_id):
    row = db.execute(
        "SELECT c.name FROM companies c JOIN roles r ON r.company_id=c.id WHERE r.id=?",
        (role_id,),
    ).fetchone()
    return row["name"] if row else "the target company"


@app.route("/round/<int:round_number>", methods=["GET", "POST"])
@login_required
def round_page(round_number):
    if round_number not in range(1, 5):
        return redirect(url_for("placement"))
    db = get_db()
    if not session.get("role_id"):
        flash("Select a role before starting an official round.", "error")
        return redirect(url_for("select_role"))
    if round_number > 1 and not db.execute("SELECT 1 FROM assessment_attempts WHERE user_id=? AND round_number=? AND status='passed'", (session["user_id"], round_number - 1)).fetchone():
        flash(f"Round {round_number} is locked. Pass Round {round_number - 1} first.", "error")
        return redirect(url_for("placement"))
    if round_number >= 3:
        if round_number == 3:
            hr_questions = [
                "Tell me about yourself and your goals.",
                "Why should we hire you?",
                "What is your greatest strength?",
                "Describe a difficult situation and how you handled it.",
                "Why do you want to join this company?",
            ]
            if request.method == "POST":
                answers = [request.form.get(f"answer_{index}", "").strip() for index in range(len(hr_questions))]
                score = round(sum(bool(answer) for answer in answers) / len(hr_questions) * 10, 2)
                status = "passed" if score >= 6 else "failed"
                db.execute(
                    "INSERT INTO assessment_attempts(user_id,role_id,mode,round_number,status,score,answers_json) VALUES (?,?, 'official', ?,?,?,?)",
                    (session["user_id"], session["role_id"], round_number, status, score, json.dumps(answers)),
                )
                db.commit()
                flash(f"Round 3 {'passed' if status == 'passed' else 'submitted'} with a score of {score}/10.", "success" if status == "passed" else "error")
                return redirect(url_for("placement"))
        else:
            hr_questions = []
            if not client:
                hr_questions = []
            else:
                try:
                    generated = ai_json(
                        f"Generate exactly 5 technical interview questions for {selected_role_name(db, session['role_id'])} at {selected_company_name(db, session['role_id'])}. Return only a JSON array of strings.",
                        lambda value: isinstance(value, list) and len(value) == 5 and all(isinstance(item, str) and item.strip() for item in value),
                    )
                    hr_questions = generated
                except (RuntimeError, ValueError, TypeError, json.JSONDecodeError):
                    hr_questions = []
        return render_template(
            "interview_round.html",
            round_number=round_number,
            selected=db.execute(
                "SELECT r.name, c.name company FROM roles r JOIN companies c ON c.id=r.company_id WHERE r.id=?",
                (session["role_id"],),
            ).fetchone(),
            questions=hr_questions,
            ai_available=bool(client),
        )
    questions = _official_questions(
        db, session["user_id"], session["role_id"], round_number,
        fresh=request.args.get("retest") == "1",
    )
    expected_count = ROUND_QUESTION_COUNTS[round_number]
    if len(questions) < expected_count:
        flash("Fresh questions are exhausted for this round. Add more bank questions before retesting.", "error")
        return redirect(url_for("placement"))
    if request.method == "POST":
        answers = [request.form.get(f"answer_{q['id']}", "").strip() for q in questions]
        correct = sum(answer == q["correct_answer"] for answer, q in zip(answers, questions))
        answered = sum(bool(answer) for answer in answers)
        score = round(correct / len(questions) * 10, 2)
        status = "passed" if score >= 6 else "failed"
        db.execute("INSERT INTO assessment_attempts(user_id,role_id,mode,round_number,status,score,answers_json) VALUES (?,?, 'official', ?,?,?,?)",
                   (session["user_id"], session["role_id"], round_number, status, score, json.dumps(answers)))
        db.commit()
        session.pop(f"official_round_{round_number}_questions", None)
        session.pop(f"official_round_{round_number}_options", None)
        flash(f"Round {round_number} {'passed' if status == 'passed' else 'submitted'} with a score of {score}/10.", "success" if status == "passed" else "error")
        return redirect(url_for("placement"))
    display_questions = []
    option_key = f"official_round_{round_number}_options"
    saved_options = session.get(option_key, {})
    for question in questions:
        options = saved_options.get(str(question["id"]))
        if not options:
            options = json.loads(question["options_json"])
            random.shuffle(options)
            saved_options[str(question["id"])] = options
        item = dict(question)
        item["options_json"] = json.dumps(options)
        display_questions.append(item)
    session[option_key] = saved_options
    return render_template(
        "round.html",
        round_number=round_number,
        questions=display_questions,
        selected=db.execute(
            "SELECT r.name, c.name company FROM roles r JOIN companies c ON c.id=r.company_id WHERE r.id=?",
            (session["role_id"],),
        ).fetchone(),
        duration=20,
        total=expected_count,
    )


@app.get("/practice")
@login_required
def practice():
    return render_template("practice.html")


@app.get("/resume")
@login_required
def resume_page():
    return render_template("resume.html")


@app.route("/mocks", methods=["GET"])
@login_required
def mocks():
    return render_template("mocks.html")


@app.route("/mocks/start", methods=["GET", "POST"])
@login_required
def mock_start():
    requested_kind = request.args.get("kind", "mixed")
    if request.method == "POST":
        session["mock_setup"] = {
            "role": request.form.get("role", "").strip()[:120],
            "company": request.form.get("company", "").strip()[:120],
            "kind": request.form.get("kind", "mixed"),
            "difficulty": request.form.get("difficulty", "medium"),
            "count": request.form.get("count", "5"),
            "duration": request.form.get("duration", "10"),
        }
        if not session["mock_setup"]["role"] or not session["mock_setup"]["company"]:
            flash("Choose both a role and company.", "error")
        else:
            return redirect(url_for("mock_interview", kind=session["mock_setup"]["kind"]))
    db = get_db()
    return render_template(
        "mock_start.html",
        role_names=db.execute("SELECT DISTINCT name FROM roles ORDER BY name").fetchall(),
        companies=db.execute("SELECT name FROM companies ORDER BY name").fetchall(),
        setup={**session.get("mock_setup", {}), "kind": requested_kind},
    )


@app.route("/mocks/<kind>", methods=["GET", "POST"])
@login_required
def mock_interview(kind):
    setup = session.get("mock_setup")
    if not setup:
        return redirect(url_for("mock_start"))
    count = max(5, min(int(setup.get("count", 5)), 15))
    key = f"mock_questions_{kind}"
    questions = session.get(key)
    if not questions:
        questions = fallback_questions(
            setup["role"], setup["company"], count, setup.get("difficulty", "medium")
        )
        session[key] = questions
    if request.method == "POST":
        answers = [request.form.get(f"answer_{index}", "").strip() for index in range(len(questions))]
        answered = sum(bool(answer) for answer in answers)
        score = round(answered / len(questions) * 10, 2)
        db = get_db()
        db.execute(
            "INSERT INTO interview_sessions(user_id,mode,transcript_json,score) VALUES (?,?,?,?)",
            (session["user_id"], f"mock:{kind}", json.dumps(list(zip(questions, answers))), score),
        )
        db.execute("INSERT INTO xp_events(user_id,amount,reason) VALUES (?,?,?)", (session["user_id"], 25, f"Completed {kind} mock"))
        db.commit()
        session.pop(key, None)
        flash(f"Mock complete. You answered {answered} of {len(questions)} questions ({score}/10).", "success")
        return redirect(url_for("progress"))
    return render_template("mock_interview.html", kind=kind, setup=setup, questions=questions)


@app.get("/roadmap")
@login_required
def roadmap():
    return render_template("roadmap.html")


@app.get("/progress")
@login_required
def progress():
    rows = get_db().execute(
        "SELECT round_number, score, status, created_at FROM assessment_attempts WHERE user_id=? ORDER BY created_at DESC",
        (session["user_id"],),
    ).fetchall()
    return render_template("progress.html", attempts=rows)


@app.get("/leaderboard")
@login_required
def leaderboard():
    rows = get_db().execute(
        """SELECT p.display_name, COALESCE(SUM(x.amount),0) xp
           FROM profiles p LEFT JOIN xp_events x ON x.user_id=p.user_id
           GROUP BY p.user_id ORDER BY xp DESC LIMIT 25"""
    ).fetchall()
    return render_template("leaderboard.html", rows=rows)


@app.get("/rewards")
@login_required
def rewards():
    rows = get_db().execute("SELECT name, description FROM badges WHERE user_id=? ORDER BY id DESC", (session["user_id"],)).fetchall()
    return render_template("rewards.html", badges=rows)


@app.get("/badges")
@login_required
def badges():
    return redirect(url_for("rewards"))


@app.get("/about")
def about():
    return render_template("about.html")

@app.post("/api/register")
def register():
    try:
        data = request_data()
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        confirm_password = str(data.get("confirm_password", ""))
        name = str(data.get("name", "")).strip()[:80]
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return json_error("Enter a valid email address.")
        if not name:
            return json_error("Enter your full name.")
        if len(password) < 8 or len(password) > 128:
            return json_error("Password must be 8–128 characters.")
        if not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password):
            return json_error("Password must include uppercase and lowercase letters.")
        if not re.search(r"\d", password) or not re.search(r"[^A-Za-z0-9]", password):
            return json_error("Password must include a number and a special character.")
        if password != confirm_password:
            return json_error("Passwords do not match.")
        email_name = email.split("@", 1)[0].lower()
        if email_name and email_name in password.lower():
            return json_error("Password must not contain your email name.")
        if len(name) >= 3 and name.lower() in password.lower():
            return json_error("Password must not contain your name.")
        db = get_db()
        cur = db.execute("INSERT INTO users(email,password_hash) VALUES (?,?)",
                         (email, generate_password_hash(password)))
        uid = cur.lastrowid
        db.execute("INSERT INTO profiles(user_id,display_name) VALUES (?,?)", (uid, name))
        db.execute("INSERT INTO streaks(user_id) VALUES (?)", (uid,))
        db.commit()
        session.clear()
        session["user_id"] = uid
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify({"ok": True, "csrf_token": session["csrf_token"]}), 201
    except sqlite3.IntegrityError:
        return json_error("An account with that email already exists.", 409)


@app.post("/api/login")
def login():
    try:
        data = request_data()
        email, password = str(data.get("email", "")).strip().lower(), str(data.get("password", ""))
    except ValueError as exc:
        return json_error(str(exc))
    user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return json_error("Email or password is incorrect.", 401)
    session.clear()
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify({"ok": True, "csrf_token": session["csrf_token"]})


@app.post("/api/logout")
@login_required
@mutation_required
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
@login_required
def me():
    db = get_db(); uid = session["user_id"]
    user = db.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
    profile = db.execute("SELECT * FROM profiles WHERE user_id=?", (uid,)).fetchone()
    xp = db.execute("SELECT COALESCE(SUM(amount),0) AS total FROM xp_events WHERE user_id=?", (uid,)).fetchone()["total"]
    streak = db.execute("SELECT * FROM streaks WHERE user_id=?", (uid,)).fetchone()
    return jsonify({"user": dict(user), "profile": dict(profile), "xp": xp, "streak": dict(streak),
                    "csrf_token": session["csrf_token"]})


@app.put("/api/profile")
@login_required
@mutation_required
def update_profile():
    try:
        data = request_data()
        phone = re.sub(r"[^\d+()\-\s]", "", str(data.get("phone", ""))).strip()[:30]
        college = str(data.get("college", "")).strip()[:160]
        preferred_role = str(data.get("preferred_role", "")).strip()[:120]
        preferred_company = str(data.get("preferred_company", "")).strip()[:120]
        graduation_year = data.get("graduation_year")
        graduation_year = int(graduation_year) if graduation_year not in (None, "") else None
        if graduation_year is not None and not 1950 <= graduation_year <= 2100:
            raise ValueError("Graduation year must be between 1950 and 2100.")
    except (TypeError, ValueError) as exc:
        return json_error(str(exc))
    db = get_db()
    db.execute("""UPDATE profiles SET phone=?,college=?,graduation_year=?,preferred_role=?,preferred_company=?
                  WHERE user_id=?""",
           (phone, college, graduation_year, preferred_role, preferred_company, session["user_id"]))
    db.commit()
    return jsonify({"ok": True})


@app.get("/api/catalog")
@login_required
def catalog():
    rows = get_db().execute("""SELECT r.id, r.name, r.level, c.id company_id, c.name company
                               FROM roles r JOIN companies c ON c.id=r.company_id ORDER BY c.name,r.name""").fetchall()
    role_names = sorted({row["name"] for row in rows})
    companies = [dict(row) for row in get_db().execute(
        "SELECT id, name, description FROM companies ORDER BY name"
    ).fetchall()]
    return jsonify({"roles": [dict(r) for r in rows], "role_names": role_names, "companies": companies})


@app.post("/api/resume")
@login_required
@mutation_required
def resume():
    uploaded = request.files.get("resume")
    if not uploaded or not uploaded.filename or not uploaded.filename.lower().endswith(".pdf"):
        return json_error("Please upload a PDF resume.")
    try:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(uploaded.stream).pages).strip()
    except Exception:
        return json_error("We could not read that PDF.", 422)
    if not text:
        return json_error("No readable text was found in that PDF.", 422)
    text = text[:12000]
    db = get_db(); db.execute("UPDATE profiles SET resume_text=?,resume_filename=? WHERE user_id=?",
                              (text, secure_filename(uploaded.filename)[:120], session["user_id"])); db.commit()
    analysis = None
    if client:
        try:
            analysis = ai_json(f"Analyze this resume in JSON with keys strengths, gaps, suggestions (arrays of strings):\n{text}",
                               lambda x: isinstance(x, dict) and all(isinstance(x.get(k), list) for k in ("strengths","gaps","suggestions")))
        except Exception:
            analysis = None
    return jsonify({"text": text, "analysis": analysis})


@app.post("/api/assessment")
@login_required
@mutation_required
def assessment():
    try:
        data = request_data()
        role_id_value = data.get("role_id")
        role_id = int(role_id_value) if role_id_value not in (None, "") else None
        role_name = str(data.get("role_name", "")).strip()
        company_name = str(data.get("company_name", "")).strip()
        mode = str(data.get("mode", "practice"))
        round_number = int(data.get("round_number", 1))
    except (ValueError, TypeError):
        return json_error("A valid role is required.")
    if mode not in {"official", "practice"}:
        return json_error("Invalid assessment mode.")
    if not 1 <= round_number <= 4:
        return json_error("Round must be between 1 and 4.")
    if role_id is not None:
        role = get_db().execute(
            "SELECT r.*, c.name company FROM roles r JOIN companies c ON c.id=r.company_id WHERE r.id=?",
            (role_id,),
        ).fetchone()
    else:
        role = get_db().execute(
            """SELECT r.*, c.name company FROM roles r JOIN companies c ON c.id=r.company_id
               WHERE r.name=? AND c.name=? ORDER BY r.id LIMIT 1""",
            (role_name, company_name),
        ).fetchone()
    if not role and mode == "practice" and role_name and company_name:
        role = {"id": None, "name": role_name, "company": company_name, "level": "Entry-level"}
    if not role:
        return json_error("Choose a valid role and company.", 404)
    db = get_db()
    if mode == "official" and round_number > 1:
        previous = db.execute("""SELECT 1 FROM assessment_attempts
                                 WHERE user_id=? AND role_id=? AND mode='official'
                                 AND round_number=? AND status='passed'""",
                              (session["user_id"], role_id, round_number - 1)).fetchone()
        if not previous:
            return json_error("Pass the previous official round to unlock this one.", 423)
    difficulty = str(data.get("difficulty", "medium")).lower()
    if difficulty not in {"easy", "medium", "hard"}:
        return json_error("Difficulty must be easy, medium, or hard.")
    if mode == "official" and round_number in ROUND_QUESTION_COUNTS:
        questions = _official_questions(
            db, session["user_id"], role_id, round_number, fresh=bool(data.get("retest"))
        )
        expected_count = ROUND_QUESTION_COUNTS[round_number]
        if len(questions) != expected_count:
            return json_error("No fresh question set remains for this round. Please add more bank questions.", 409)
        cur = db.execute(
            "INSERT INTO assessment_attempts(user_id,role_id,mode,round_number) VALUES (?,?,?,?)",
            (session["user_id"], role_id, mode, round_number),
        )
        db.commit()
        return jsonify({
            "attempt_id": cur.lastrowid,
            "round_number": round_number,
            "questions": [question_payload(row, index) for index, row in enumerate(questions, 1)],
            "difficulty": "mixed",
            "role": dict(role),
        })
    structured_aptitude = mode == "official" and round_number == 1
    if structured_aptitude:
        prompt = f"""Generate 5 {difficulty} college final-year engineering campus-placement aptitude questions
for {role['company']} screening. Use only placement-standard Quantitative Aptitude and Logical/Analytical
Reasoning: trains, relative speed, boats and streams, alternate-day work, pipes, probability,
permutations, combinations, successive discounts, faulty weights, mixtures, averages, percentages,
data interpretation, seating arrangements, coded blood relations, direction vectors, syllogism
possibility cases, critical reasoning, statement-assumption, course of action, or data sufficiency.
Do not use school-level arithmetic, one-step trivia, or repetitive templates. Each item must have
exactly four distinct options labelled A, B, C, D, one correct option, and a crisp step-by-step
explanation. Return only a JSON array of objects with keys question, options, answer, explanation."""
    else:
        prompt = f"""Generate 5 college final-year engineering campus-placement interview questions
for a {role['level']} {role['name']} role at {role['company']}.
Difficulty: {difficulty}.
Easy means core DSA, OOP, and AI/ML concepts; medium means multi-step reasoning,
debugging, system-design basics, and applied frameworks; hard means optimization,
advanced DSA, scalability trade-offs, and deep-learning/system bottlenecks.
Do not generate school-level arithmetic or generic filler. Return only a JSON array of strings."""
    questions = None
    if client:
        try:
            if round_number == 1:
                questions = ai_json(
                    prompt,
                    lambda x: isinstance(x, list) and len(x) >= 3 and all(
                        isinstance(q, dict)
                        and isinstance(q.get("question"), str)
                        and isinstance(q.get("options"), list)
                        and len(q["options"]) == 4
                        and len({str(option) for option in q["options"]}) == 4
                        and q.get("answer") in q["options"]
                        and isinstance(q.get("explanation"), str)
                        and q["explanation"].strip()
                        for q in x
                    ),
                )
            else:
                questions = ai_json(prompt, lambda x: isinstance(x, list) and len(x) >= 3 and all(isinstance(q, str) and q.strip() for q in x))
        except Exception:
            questions = None
    if not questions:
        if structured_aptitude:
            rows = db.execute(
                """SELECT prompt, options_json, correct_answer, explanation
                   FROM question_bank
                   WHERE round_number=1 AND source_type='aptitude_standard' AND difficulty=?
                   ORDER BY RANDOM() LIMIT 5""",
                (difficulty,),
            ).fetchall()
            questions = [
                {
                    "question": row["prompt"],
                    "options": json.loads(row["options_json"]),
                    "answer": row["correct_answer"],
                    "explanation": row["explanation"],
                }
                for row in rows
            ]
        else:
            questions = fallback_questions(role["name"], role["company"], difficulty=difficulty)
    cur = db.execute("INSERT INTO assessment_attempts(user_id,role_id,mode,round_number) VALUES (?,?,?,?)",
                     (session["user_id"], role_id, mode, round_number)); db.commit()
    return jsonify({"attempt_id": cur.lastrowid, "round_number": round_number,
                    "questions": questions[:5], "difficulty": difficulty, "role": dict(role)})


@app.post("/api/assessment/<int:attempt_id>/complete")
@login_required
@mutation_required
def complete_assessment(attempt_id):
    try:
        data = request_data(); answers = data.get("answers", [])
        if not isinstance(answers, list) or len(answers) > 20: raise ValueError("Answers must be a list.")
        scores = [float(a["score"]) for a in answers if isinstance(a, dict) and 0 <= float(a.get("score", 0)) <= 10]
    except (ValueError, TypeError, KeyError):
        return json_error("Invalid answers.")
    db = get_db(); row = db.execute("SELECT * FROM assessment_attempts WHERE id=? AND user_id=?", (attempt_id, session["user_id"])).fetchone()
    if not row: return json_error("Attempt not found.", 404)
    score = round(sum(scores) / len(scores), 2) if scores else 0
    passed = score >= 7
    db.execute("UPDATE assessment_attempts SET status=?,score=?,answers_json=? WHERE id=?",
               ("passed" if passed else "failed", score, json.dumps(answers)[:50000], attempt_id))
    db.execute("INSERT INTO xp_events(user_id,amount,reason) VALUES (?,?,?)", (session["user_id"], 50, "Completed assessment"))
    db.commit()
    return jsonify({"score": score, "passed": passed, "xp_earned": 50})


@app.post("/api/interview")
@login_required
@mutation_required
def interview():
    try:
        data = request_data(); role = str(data.get("role", "")).strip(); question = str(data.get("question", "")).strip()
        answer = str(data.get("answer", "")).strip()
        if not role or not question or not answer or len(answer) > 10000: raise ValueError("Role, question and answer are required (10,000 character limit).")
        feedback = None
        if client:
            try:
                feedback = ai_json(f"Evaluate this answer for {role}. Question: {question}\nAnswer: {answer}\nReturn JSON keys score (integer 1-10), strengths, improvements, model_answer (all strings).",
                                   lambda x: isinstance(x, dict) and isinstance(x.get("score"), int) and 1 <= x["score"] <= 10 and all(isinstance(x.get(k), str) for k in ("strengths","improvements","model_answer")))
            except Exception:
                feedback = None
        if not feedback:
            feedback = fallback_feedback(answer)
        db = get_db()
        cur = db.execute("""INSERT INTO interview_sessions(user_id, transcript_json, score)
                            VALUES (?, ?, ?)""",
                         (session["user_id"], json.dumps([{"question": question, "answer": answer,
                                                           "feedback": feedback}]), feedback["score"]))
        db.commit()
        feedback["session_id"] = cur.lastrowid
        return jsonify(feedback)
    except ValueError as exc:
        return json_error(str(exc))
    except Exception:
        return json_error("AI interview feedback is unavailable.", 503)


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="That page does not exist."), 404


@app.errorhandler(500)
def server_error(_error):
    app.logger.exception("Unhandled server error")
    return render_template("error.html", code=500, message="Something went wrong on the server."), 500


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="You do not have permission to view this page."), 403


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(
        host=HOST,
        port=PORT,
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
