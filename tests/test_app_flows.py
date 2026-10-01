import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app as app_module
from coding_bank import CODING_QUESTIONS


class ApplicationFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_database = app_module.DATABASE
        self.original_sandbox_url = app_module.CODE_SANDBOX_URL
        self.original_sandbox_key = app_module.CODE_SANDBOX_API_KEY
        self.original_secret = app_module.app.config["SECRET_KEY"]
        app_module.DATABASE = Path(self.tempdir.name) / "test.sqlite3"
        app_module.CODE_SANDBOX_URL = ""
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        app_module.init_db()
        self.client = app_module.app.test_client()
        self.sent_signup_codes = []
        self.signup_sender = patch.object(
            app_module,
            "_send_signup_otp",
            side_effect=lambda method, destination, otp: self.sent_signup_codes.append(
                (method, destination, otp)
            ),
        )
        self.signup_sender.start()

    def tearDown(self):
        app_module.DATABASE = self.original_database
        app_module.CODE_SANDBOX_URL = self.original_sandbox_url
        app_module.CODE_SANDBOX_API_KEY = self.original_sandbox_key
        app_module.app.config.update(SECRET_KEY=self.original_secret, TESTING=False)
        self.signup_sender.stop()
        self.tempdir.cleanup()

    @staticmethod
    def registration(email, name="Mira"):
        return {
            "email": email,
            "name": name,
            "password": "SecurePass9!x",
            "confirm_password": "SecurePass9!x",
            "phone": "",
            "college": "",
            "graduation_year": "",
            "preferred_role": "",
        }

    def create_verified_account(self, email, name="Mira", phone="", verification_method="email"):
        self.client.get("/register")
        with self.client.session_transaction() as session:
            csrf_token = session["signup_csrf_token"]
        payload = self.registration(email, name)
        payload["phone"] = phone
        payload["verification_method"] = verification_method
        payload["csrf_token"] = csrf_token
        response = self.client.post("/register", data=payload)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/verify-signup", response.headers["Location"])
        code = self.sent_signup_codes[-1][2]
        verified = self.client.post("/verify-signup", data={
            "csrf_token": csrf_token,
            "action": "verify",
            "otp": code,
        })
        self.assertEqual(verified.status_code, 302)
        self.assertIn("/login", verified.headers["Location"])
        return code

    def test_signup_login_dashboard_and_logout_for_multiple_users(self):
        self.client.get("/register")
        with self.client.session_transaction() as session:
            signup_csrf = session["signup_csrf_token"]
        signup_data = self.registration("mira@example.com")
        signup_data.update(
            csrf_token=signup_csrf,
            verification_method="email",
        )
        first = self.client.post("/register", data=signup_data)
        self.assertEqual(first.status_code, 302)
        self.assertIn("/verify-signup", first.headers["Location"])
        with app_module.app.app_context():
            account = app_module.get_db().execute(
                "SELECT id, email_verified FROM users WHERE email=?",
                ("mira@example.com",),
            ).fetchone()
            otp_row = app_module.get_db().execute(
                "SELECT otp_hash FROM signup_verification_otps WHERE user_id=?",
                (account["id"],),
            ).fetchone()
        self.assertEqual(account["email_verified"], 0)
        self.assertNotEqual(otp_row["otp_hash"], self.sent_signup_codes[-1][2])
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        unverified_login = self.client.post("/login", data={
            "email": "mira@example.com",
            "password": "SecurePass9!x",
        })
        self.assertEqual(unverified_login.status_code, 302)
        self.assertIn("/verify-signup", unverified_login.headers["Location"])
        self.assertEqual(
            self.client.post("/verify-signup", data={
                "csrf_token": signup_csrf,
                "action": "verify",
                "otp": self.sent_signup_codes[-1][2],
            }).status_code,
            302,
        )

        self.client.get("/register")
        with self.client.session_transaction() as session:
            signup_csrf = session["signup_csrf_token"]
        duplicate_data = self.registration("MIRA@example.com")
        duplicate_data.update(csrf_token=signup_csrf, verification_method="email")
        duplicate = self.client.post("/register", data=self.registration("MIRA@example.com"))
        self.assertEqual(duplicate.status_code, 403)
        duplicate = self.client.post("/register", data=duplicate_data)
        self.assertIn(b"already exists", duplicate.data)

        wrong_login = self.client.post(
            "/login", data={"email": "mira@example.com", "password": "not-the-password"}
        )
        self.assertIn(b"Email or password is incorrect", wrong_login.data)

        login = self.client.post(
            "/login?next=https://evil.example",
            data={"email": " MIRA@example.com ", "password": "SecurePass9!x"},
        )
        self.assertEqual(login.status_code, 302)
        self.assertEqual(login.headers["Location"], "/dashboard")
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

        rejected_logout = self.client.post("/logout")
        self.assertEqual(rejected_logout.status_code, 403)
        with self.client.session_transaction() as session:
            csrf_token = session["csrf_token"]
        logout = self.client.post("/logout", data={"csrf_token": csrf_token})
        self.assertEqual(logout.status_code, 302)
        self.assertIn("/login", logout.headers["Location"])
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

        self.create_verified_account("dev@example.com", "Dev")
        second = self.client.get("/login")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            self.client.post("/login", data={
                "email": "dev@example.com",
                "password": "SecurePass9!x",
            }).status_code,
            302,
        )
        self.assertEqual(self.client.get("/dashboard").status_code, 200)
        with app_module.app.app_context():
            count = app_module.get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(count, 2)

    def test_legacy_database_migration_preserves_users_and_adds_security_storage(self):
        legacy_database = Path(self.tempdir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_database)
        connection.execute(
            """CREATE TABLE users (
               id INTEGER PRIMARY KEY,
               email TEXT UNIQUE NOT NULL,
               password_hash TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        connection.execute(
            "INSERT INTO users(email, password_hash) VALUES (?, ?)",
            ("legacy@example.com", "existing-hash"),
        )
        connection.commit()
        connection.close()

        app_module.DATABASE = legacy_database
        app_module.init_db()
        connection = sqlite3.connect(legacy_database)
        connection.execute(
            "INSERT INTO profiles(user_id, display_name) VALUES (?, ?)",
            (99, "Retained orphan profile"),
        )
        connection.execute("INSERT INTO streaks(user_id) VALUES (?)", (99,))
        connection.commit()
        connection.close()
        self.create_verified_account("new-after-orphan@example.com", "New Account")
        connection = sqlite3.connect(legacy_database)
        user = connection.execute(
            "SELECT email, password_hash, auth_version FROM users WHERE email=?",
            ("legacy@example.com",),
        ).fetchone()
        new_user = connection.execute(
            "SELECT id FROM users WHERE email=?",
            ("new-after-orphan@example.com",),
        ).fetchone()
        retained_profile = connection.execute(
            "SELECT display_name FROM profiles WHERE user_id=99",
        ).fetchone()
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        connection.close()
        self.assertEqual(user, ("legacy@example.com", "existing-hash", 0))
        self.assertEqual(new_user, (100,))
        self.assertEqual(retained_profile, ("Retained orphan profile",))
        self.assertIn("password_reset_otps", tables)
        self.assertIn("security_limits", tables)
        app_module.DATABASE = Path(self.tempdir.name) / "test.sqlite3"

    def test_authenticated_application_pages_and_round_locks_render(self):
        self.create_verified_account("pages@example.com")
        self.assertEqual(self.client.post("/login", data={
            "email": "pages@example.com",
            "password": "SecurePass9!x",
        }).status_code, 302)
        with app_module.app.app_context():
            role_id = app_module.get_db().execute(
                "SELECT id FROM roles ORDER BY id LIMIT 1"
            ).fetchone()["id"]
        with self.client.session_transaction() as session:
            session["role_id"] = role_id

        for path in (
            "/dashboard", "/select-role", "/profile", "/placement",
            "/round/1", "/practice", "/coding", "/resume", "/mocks",
            "/roadmap", "/progress", "/leaderboard", "/rewards",
            "/about", "/api/me", "/api/catalog",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

        for round_number in (2, 3, 4):
            with self.subTest(round_number=round_number):
                response = self.client.get(f"/round/{round_number}")
                self.assertEqual(response.status_code, 302)
                self.assertIn("/placement", response.headers["Location"])
        self.assertEqual(self.client.get("/badges").status_code, 302)

    def test_json_auth_endpoints_are_public_and_share_the_same_session_lifecycle(self):
        payload = self.registration("api@example.com")
        self.client.get("/register")
        with self.client.session_transaction() as session:
            signup_csrf = session["signup_csrf_token"]
        created = self.client.post(
            "/api/register",
            json=payload,
            headers={"X-CSRF-Token": signup_csrf},
        )
        self.assertEqual(created.status_code, 202)
        self.assertTrue(created.json["verification_required"])
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
        verification_code = self.sent_signup_codes[-1][2]
        verified = self.client.post("/verify-signup", data={
            "csrf_token": signup_csrf,
            "action": "verify",
            "otp": verification_code,
        })
        self.assertEqual(verified.status_code, 302)

        self.client.get("/register")
        with self.client.session_transaction() as session:
            signup_csrf = session["signup_csrf_token"]
        duplicate = self.client.post(
            "/api/register",
            json=payload,
            headers={"X-CSRF-Token": signup_csrf},
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("already exists", duplicate.json["error"])

        self.client.post("/api/login", json={
            "email": "api@example.com",
            "password": payload["password"],
        })
        with self.client.session_transaction() as session:
            token = session["csrf_token"]
        logout = self.client.post("/api/logout", headers={"X-CSRF-Token": token})
        self.assertEqual(logout.status_code, 200)
        logged_in = self.client.post("/api/login", json={
            "email": "api@example.com",
            "password": payload["password"],
        })
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_login_attempts_enter_account_cooldown(self):
        self.create_verified_account("locked@example.com")
        for _ in range(app_module.LOGIN_ACCOUNT_FAILURE_LIMIT):
            response = self.client.post("/login", data={
                "email": "locked@example.com",
                "password": "WrongPassword9!",
            })
        self.assertIn(b"Too many unsuccessful attempts", response.data)
        blocked = self.client.post("/login", data={
            "email": "locked@example.com",
            "password": "SecurePass9!x",
        })
        self.assertIn(b"Please try again in about 15 minute(s)", blocked.data)
        with app_module.app.app_context():
            user_id = app_module.get_db().execute(
                "SELECT id FROM users WHERE email=?", ("locked@example.com",)
            ).fetchone()["id"]
            lock = app_module.get_db().execute(
                "SELECT blocked_until FROM security_limits WHERE scope_key=?",
                (app_module._secret_digest("login-account", str(user_id)),),
            ).fetchone()
        self.assertIsNotNone(lock)
        self.assertGreater(lock["blocked_until"], app_module.time.time())

    def test_signup_otp_wrong_attempts_expiry_resend_and_one_time_verification(self):
        self.client.get("/register")
        with self.client.session_transaction() as session:
            csrf_token = session["signup_csrf_token"]
        payload = self.registration("otp-flow@example.com")
        payload.update(csrf_token=csrf_token, verification_method="email")
        created = self.client.post("/register", data=payload)
        self.assertEqual(created.status_code, 302)
        code = self.sent_signup_codes[-1][2]
        with app_module.app.app_context():
            stored = app_module.get_db().execute(
                "SELECT otp_hash, expires_at, verify_attempts FROM signup_verification_otps"
            ).fetchone()
        self.assertNotEqual(stored["otp_hash"], code)
        self.assertEqual(stored["verify_attempts"], 0)

        invalid = None
        wrong_code = "000000" if code != "000000" else "000001"
        for attempt in range(app_module.SIGNUP_OTP_VERIFY_LIMIT):
            invalid = self.client.post("/verify-signup", data={
                "csrf_token": csrf_token,
                "action": "verify",
                "otp": wrong_code,
            }, follow_redirects=True)
            self.assertNotIn(code.encode(), invalid.data)
        self.assertIn(b"Too many incorrect codes", invalid.data)
        blocked = self.client.post("/verify-signup", data={
            "csrf_token": csrf_token,
            "action": "verify",
            "otp": code,
        }, follow_redirects=True)
        self.assertIn(b"Too many incorrect codes", blocked.data)
        cooldown = self.client.post("/verify-signup", data={
            "csrf_token": csrf_token,
            "action": "resend",
        }, follow_redirects=True)
        self.assertIn(b"wait 60 seconds", cooldown.data)

        with app_module.app.app_context():
            app_module.get_db().execute(
                "UPDATE signup_verification_otps SET expires_at=?",
                (app_module.time.time() - 1,),
            )
            app_module.get_db().commit()
        expired = self.client.post("/verify-signup", data={
            "csrf_token": csrf_token,
            "action": "verify",
            "otp": code,
        }, follow_redirects=True)
        self.assertIn(b"expired", expired.data.lower())

        with app_module.app.app_context():
            app_module.get_db().execute(
                "UPDATE signup_verification_otps SET sent_at=?",
                (app_module.time.time() - app_module.OTP_RESEND_COOLDOWN_SECONDS - 1,),
            )
            app_module.get_db().commit()
        resent = self.client.post("/verify-signup", data={
            "csrf_token": csrf_token,
            "action": "resend",
        }, follow_redirects=True)
        self.assertIn(b"A new code was sent by email", resent.data)
        replacement_code = self.sent_signup_codes[-1][2]
        self.assertNotEqual(code, replacement_code)
        verify = self.client.post("/verify-signup", data={
            "csrf_token": csrf_token,
            "action": "verify",
            "otp": replacement_code,
        }, follow_redirects=True)
        self.assertEqual(verify.status_code, 200)
        self.assertIn(b"Your account is verified", verify.data)
        with app_module.app.app_context():
            user = app_module.get_db().execute(
                "SELECT email_verified FROM users WHERE email=?",
                ("otp-flow@example.com",),
            ).fetchone()
            remaining = app_module.get_db().execute(
                "SELECT 1 FROM signup_verification_otps WHERE user_id=?",
                (1,),
            ).fetchone()
        self.assertEqual(user["email_verified"], 1)
        self.assertIsNone(remaining)

        with self.client.session_transaction() as session:
            session["signup_verification_user_id"] = 1
            session["signup_verification_method"] = "email"
        already_verified = self.client.get("/verify-signup", follow_redirects=True)
        self.assertIn(b"already verified", already_verified.data)
        self.assertIn(b"You can sign in", already_verified.data)

    def test_sms_signup_works_and_duplicate_phone_is_rejected(self):
        self.signup_sender.stop()
        sms_response = Mock(status_code=201)
        with patch.dict("os.environ", {
            "TWILIO_ACCOUNT_SID": "test-account",
            "TWILIO_AUTH_TOKEN": "test-auth",
            "TWILIO_FROM_NUMBER": "+15550001111",
        }), patch.object(app_module.requests, "post", return_value=sms_response) as send_sms, patch.object(
            app_module.secrets, "randbelow", return_value=123456,
        ):
            self.client.get("/register")
            with self.client.session_transaction() as session:
                csrf_token = session["signup_csrf_token"]
            payload = self.registration("sms-user@example.com")
            payload.update(
                phone="+1 (415) 555-0123",
                verification_method="sms",
                csrf_token=csrf_token,
            )
            signup = self.client.post("/register", data=payload)
            self.assertEqual(signup.status_code, 302)
            self.assertEqual(send_sms.call_count, 1)
            self.assertIn("123456", send_sms.call_args.kwargs["data"]["Body"])
            self.assertEqual(
                send_sms.call_args.kwargs["auth"],
                ("test-account", "test-auth"),
            )
            unverified_session = self.client.post("/login", data={
                "email": "sms-user@example.com",
                "password": "SecurePass9!x",
            }, follow_redirects=True)
            self.assertIn(b"mobile number", unverified_session.data)
            self.assertEqual(self.client.get("/dashboard").status_code, 302)
            with self.client.session_transaction() as session:
                self.assertNotIn("user_id", session)
            verify = self.client.post("/verify-signup", data={
                "csrf_token": csrf_token,
                "action": "verify",
                "otp": "123456",
            })
            self.assertEqual(verify.status_code, 302)

            self.client.get("/register")
            with self.client.session_transaction() as session:
                duplicate_csrf = session["signup_csrf_token"]
            duplicate_payload = self.registration("other-sms-user@example.com")
            duplicate_payload.update(
                phone="+14155550123",
                verification_method="email",
                csrf_token=duplicate_csrf,
            )
            duplicate = self.client.post("/register", data=duplicate_payload)
            self.assertIn(b"phone number is already linked", duplicate.data)
            with app_module.app.app_context():
                count = app_module.get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0]
            self.assertEqual(count, 1)

    def test_unverified_signup_delivery_failure_never_creates_a_session(self):
        self.client.get("/register")
        with self.client.session_transaction() as session:
            csrf_token = session["signup_csrf_token"]
        with patch.object(
            app_module,
            "_send_signup_otp",
            side_effect=RuntimeError("delivery failure containing private delivery data"),
        ):
            payload = self.registration("undelivered@example.com")
            payload.update(csrf_token=csrf_token, verification_method="email")
            response = self.client.post("/register", data=payload, follow_redirects=True)
        self.assertIn(b"not active yet", response.data)
        self.assertNotIn(b"delivery failure containing", response.data)
        with app_module.app.app_context():
            verified = app_module.get_db().execute(
                "SELECT email_verified FROM users WHERE email=?",
                ("undelivered@example.com",),
            ).fetchone()["email_verified"]
        self.assertEqual(verified, 0)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)
        protected = self.client.get("/dashboard")
        self.assertEqual(protected.status_code, 302)
        with self.client.session_transaction() as session:
            session["user_id"] = 1
            session["auth_version"] = 0
        blocked = self.client.get("/api/me")
        self.assertEqual(blocked.status_code, 403)

    def test_password_reset_otp_is_hashed_limited_and_consumed_once(self):
        self.create_verified_account("reset@example.com")
        second_session = app_module.app.test_client()
        self.assertEqual(second_session.post("/login", data={
            "email": "reset@example.com",
            "password": "SecurePass9!x",
        }).status_code, 302)
        page = self.client.get("/forgot-password")
        self.assertEqual(page.status_code, 200)
        with self.client.session_transaction() as session:
            csrf_token = session["password_reset_csrf"]

        sent_codes = []
        def capture_code(email, otp):
            self.assertEqual(email, "reset@example.com")
            sent_codes.append(otp)

        with patch.object(app_module, "_send_password_reset_otp", side_effect=capture_code):
            requested = self.client.post("/forgot-password", data={
                "csrf_token": csrf_token,
                "action": "request",
                "email": "reset@example.com",
            }, follow_redirects=True)
            self.assertEqual(requested.status_code, 200)
            self.assertIn(b"If an account matches", requested.data)
            self.assertEqual(len(sent_codes), 1)
            self.assertNotIn(sent_codes[0].encode(), requested.data)

            with app_module.app.app_context():
                stored = app_module.get_db().execute(
                    "SELECT * FROM password_reset_otps"
                ).fetchone()
            self.assertNotEqual(stored["otp_hash"], sent_codes[0])
            self.assertEqual(stored["verify_attempts"], 0)

            resend = self.client.post("/forgot-password", data={
                "csrf_token": csrf_token,
                "action": "resend",
            }, follow_redirects=True)
            self.assertIn(b"code was sent recently", resend.data)
            self.assertEqual(len(sent_codes), 1)

            verified = self.client.post("/forgot-password", data={
                "csrf_token": csrf_token,
                "action": "verify",
                "otp": sent_codes[0],
            }, follow_redirects=True)
            self.assertIn(b"Choose a new password", verified.data)
            self.assertNotIn(sent_codes[0].encode(), verified.data)
            replay_session = app_module.app.test_client()
            reset_cookie = self.client.get_cookie("session")
            self.assertIsNotNone(reset_cookie)
            replay_session.set_cookie("session", reset_cookie.value)
            reset = self.client.post("/forgot-password", data={
                "csrf_token": csrf_token,
                "action": "reset",
                "password": "NewSecurePass9!",
                "confirm_password": "NewSecurePass9!",
            })
            self.assertEqual(reset.status_code, 302)
            self.assertIn("/login", reset.headers["Location"])
            replay = replay_session.post("/forgot-password", data={
                "csrf_token": csrf_token,
                "action": "reset",
                "password": "ReplaySecure9!",
                "confirm_password": "ReplaySecure9!",
            }, follow_redirects=True)
            self.assertIn(b"verification has expired", replay.data)
            with app_module.app.app_context():
                self.assertIsNone(app_module.get_db().execute(
                    "SELECT 1 FROM password_reset_otps"
                ).fetchone())

        self.assertEqual(self.client.post("/login", data={
            "email": "reset@example.com",
            "password": "SecurePass9!x",
        }).status_code, 200)
        self.assertEqual(self.client.post("/login", data={
            "email": "reset@example.com",
            "password": "NewSecurePass9!",
        }).status_code, 302)
        self.assertEqual(second_session.get("/dashboard").status_code, 302)

    def test_password_reset_code_expires_and_verification_is_rate_limited(self):
        self.create_verified_account("expire@example.com")
        self.client.get("/forgot-password")
        with self.client.session_transaction() as session:
            csrf_token = session["password_reset_csrf"]
        sent_codes = []
        def capture_code(email, otp):
            self.assertEqual(email, "expire@example.com")
            sent_codes.append(otp)

        with patch.object(app_module, "_send_password_reset_otp", side_effect=capture_code):
            self.client.post("/forgot-password", data={
                "csrf_token": csrf_token,
                "action": "request",
                "email": "expire@example.com",
            })
            with app_module.app.app_context():
                expiry = app_module.get_db().execute(
                    "SELECT expires_at FROM password_reset_otps"
                ).fetchone()["expires_at"]

            for _ in range(app_module.OTP_VERIFY_LIMIT):
                response = self.client.post("/forgot-password", data={
                    "csrf_token": csrf_token,
                    "action": "verify",
                    "otp": "000000" if sent_codes[0] != "000000" else "000001",
                }, follow_redirects=True)
            self.assertIn(b"Too many incorrect codes", response.data)
            rejected = self.client.post("/forgot-password", data={
                "csrf_token": csrf_token,
                "action": "verify",
                "otp": sent_codes[0],
            }, follow_redirects=True)
            self.assertIn(b"Too many incorrect codes", rejected.data)

            with patch.object(app_module.time, "time", return_value=expiry + 1):
                expired_page = self.client.get("/forgot-password", follow_redirects=True)
            self.assertIn(b"expired", expired_page.data.lower())

    def test_practice_filters_company_and_difficulty_and_c_support_is_sandboxed(self):
        self.create_verified_account("company@example.com")
        self.client.post("/login", data={
            "email": "company@example.com",
            "password": "SecurePass9!x",
        })
        self.client.get("/coding")
        with self.client.session_transaction() as session:
            csrf_token = session["csrf_token"]
        start = self.client.post("/coding/start", data={
            "csrf_token": csrf_token,
            "company": "Amazon",
            "difficulty": "easy",
            "count": "3",
        })
        self.assertEqual(start.status_code, 302)
        with self.client.session_transaction() as session:
            coding_session = session["coding_session"]
        self.assertEqual(coding_session["company"], "Amazon")
        for question_id in coding_session["question_ids"]:
            question = next(item for item in CODING_QUESTIONS if item["id"] == question_id)
            self.assertEqual(question["difficulty"], "easy")
            self.assertIn("Amazon", question["companies"])

        self.assertEqual(
            set(app_module.LANGUAGES),
            {"c", "cpp", "java", "javascript", "python"},
        )
        app_module.CODE_SANDBOX_URL = "https://sandbox.example"
        app_module.CODE_SANDBOX_API_KEY = "sandbox-test-token"
        question_id = coding_session["question_ids"][0]
        sandbox_response = Mock()
        sandbox_response.status_code = 200
        sandbox_response.json.return_value = {
            "run": {"stdout": "1\n", "stderr": "", "code": 0}
        }
        with patch.object(app_module.requests, "post", return_value=sandbox_response) as execute:
            response = self.client.post("/api/coding/execute", json={
                "question_id": question_id,
                "language": "c",
                "code": "int main(void) { return 0; }",
                "action": "run",
            }, headers={"X-CSRF-Token": csrf_token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(execute.call_args.kwargs["json"]["files"][0]["name"], "main.c")
        self.assertEqual(execute.call_args.kwargs["json"]["language"], "c")
        self.assertEqual(
            execute.call_args.kwargs["headers"]["Authorization"],
            "Bearer sandbox-test-token",
        )
        for language, filename, piston_language in (
            ("python", "main.py", "python"),
            ("cpp", "main.cpp", "c++"),
            ("java", "Main.java", "java"),
            ("javascript", "index.js", "javascript"),
        ):
            with patch.object(app_module.requests, "post", return_value=sandbox_response) as execute:
                result = self.client.post("/api/coding/execute", json={
                    "question_id": question_id,
                    "language": language,
                    "code": "submission",
                    "action": "run",
                }, headers={"X-CSRF-Token": csrf_token})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(execute.call_args.kwargs["json"]["files"][0]["name"], filename)
            self.assertEqual(execute.call_args.kwargs["json"]["language"], piston_language)

    def test_round_two_includes_sandbox_coding_and_scores_it_before_unlocking_round_three(self):
        self.create_verified_account("roundtwo@example.com")
        self.client.post("/login", data={
            "email": "roundtwo@example.com",
            "password": "SecurePass9!x",
        })
        with app_module.app.app_context():
            db = app_module.get_db()
            role_id = db.execute("SELECT id FROM roles ORDER BY id LIMIT 1").fetchone()[0]
            user_id = db.execute(
                "SELECT id FROM users WHERE email=?", ("roundtwo@example.com",)
            ).fetchone()[0]
            db.execute(
                """INSERT INTO assessment_attempts
                   (user_id, role_id, mode, round_number, status, score)
                   VALUES (?, ?, 'official', 1, 'passed', 8)""",
                (user_id, role_id),
            )
            db.commit()
        with self.client.session_transaction() as session:
            session["role_id"] = role_id
            csrf_token = session["csrf_token"]

        page = self.client.get("/round/2")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Round 2 coding task", page.data)
        self.assertIn(b"data-round2-code", page.data)
        with self.client.session_transaction() as session:
            first_ids = session["official_round_2_questions"]
        with app_module.app.app_context():
            marks = ",".join("?" for _ in first_ids)
            first_questions = app_module.get_db().execute(
                f"SELECT id, correct_answer FROM question_bank WHERE id IN ({marks})",
                first_ids,
            ).fetchall()
        no_code_answers = {
            f"answer_{question['id']}": question["correct_answer"]
            for question in first_questions
        }
        self.client.post("/round/2", data=no_code_answers)
        self.assertEqual(self.client.get("/round/3").status_code, 302)
        with app_module.app.app_context():
            failed_attempt = app_module.get_db().execute(
                """SELECT status, score FROM assessment_attempts
                   WHERE user_id=? AND round_number=2 ORDER BY id DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        self.assertEqual(failed_attempt["status"], "failed")

        page = self.client.get("/round/2")
        self.assertEqual(page.status_code, 200)
        with self.client.session_transaction() as session:
            question_id = session["official_round2_coding_question_id"]
        question = next(item for item in CODING_QUESTIONS if item["id"] == question_id)
        app_module.CODE_SANDBOX_URL = "https://sandbox.example"

        def sandbox_response(url, **kwargs):
            self.assertEqual(url, "https://sandbox.example/api/v2/execute")
            self.assertEqual(kwargs["json"]["language"], "c")
            self.assertEqual(kwargs["json"]["files"][0]["name"], "main.c")
            case = next(test for test in question["tests"] if test["input"] == kwargs["json"]["stdin"])
            response = Mock()
            response.status_code = 200
            response.json.return_value = {
                "run": {"stdout": f"{case['output']}\n", "stderr": "", "code": 0}
            }
            return response

        with patch.object(app_module.requests, "post", side_effect=sandbox_response):
            submission = self.client.post("/api/coding/execute", json={
                "mode": "official_round2",
                "question_id": question_id,
                "language": "c",
                "code": "int main(void) { return 0; }",
                "action": "submit",
            }, headers={"X-CSRF-Token": csrf_token})
        self.assertEqual(submission.status_code, 200)
        self.assertTrue(submission.json["coding_completed"])
        with self.client.session_transaction() as session:
            self.assertTrue(session["official_round2_coding_passed"])

        with self.client.session_transaction() as session:
            question_ids = session["official_round_2_questions"]
        with app_module.app.app_context():
            marks = ",".join("?" for _ in question_ids)
            questions = app_module.get_db().execute(
                f"SELECT id, correct_answer FROM question_bank WHERE id IN ({marks})",
                question_ids,
            ).fetchall()
        answers = {f"answer_{question['id']}": question["correct_answer"] for question in questions}
        completed = self.client.post("/round/2", data=answers)
        self.assertEqual(completed.status_code, 302)
        with app_module.app.app_context():
            attempt = app_module.get_db().execute(
                """SELECT status, score, answers_json FROM assessment_attempts
                   WHERE user_id=? AND round_number=2 ORDER BY id DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        self.assertEqual(attempt["status"], "passed")
        self.assertEqual(attempt["score"], 10)
        self.assertTrue(json.loads(attempt["answers_json"])["coding_passed"])
        self.assertEqual(self.client.get("/round/3").status_code, 200)

    def test_coding_sessions_are_unique_and_execution_never_falls_back_to_server(self):
        self.create_verified_account("coder@example.com")
        self.client.post("/login", data={
            "email": "coder@example.com",
            "password": "SecurePass9!x",
        })
        self.assertEqual(self.client.get("/coding").status_code, 200)
        with self.client.session_transaction() as session:
            first = session["coding_session"]["question_ids"]
            csrf_token = session["csrf_token"]
        self.assertEqual(len(first), len(set(first)))

        restarted = self.client.post("/coding/start", data={
            "csrf_token": csrf_token,
            "difficulty": "mixed",
            "count": "5",
        })
        self.assertEqual(restarted.status_code, 302)
        with self.client.session_transaction() as session:
            second = session["coding_session"]["question_ids"]
        self.assertEqual(len(second), len(set(second)))
        self.assertNotEqual(set(first), set(second))

        result = self.client.post("/api/coding/execute", json={
            "question_id": second[0],
            "language": "python",
            "code": "print('this must not run on the app server')",
            "action": "run",
        }, headers={"X-CSRF-Token": csrf_token})
        self.assertEqual(result.status_code, 503)
        self.assertIn(b"never run on the InterviewForge server", result.data)

        self.assertEqual(len(CODING_QUESTIONS), 45)
        self.assertEqual(
            {question["topic"] for question in CODING_QUESTIONS},
            {
                "Arrays", "Strings", "Hashing", "Searching", "Sorting",
                "Linked Lists", "Stacks", "Queues", "Recursion", "Trees",
                "Basic Algorithms", "Loops", "Functions", "Pointers",
                "Object-Oriented Programming",
            },
        )
        for level in ("easy", "medium", "hard"):
            self.assertEqual(sum(question["difficulty"] == level for question in CODING_QUESTIONS), 15)

    def test_configured_sandbox_runs_samples_and_marks_only_a_full_submission(self):
        self.create_verified_account("runner@example.com")
        self.client.post("/login", data={
            "email": "runner@example.com",
            "password": "SecurePass9!x",
        })
        self.client.get("/coding")
        with self.client.session_transaction() as session:
            question_id = session["coding_session"]["question_ids"][0]
            csrf_token = session["csrf_token"]
        question = next(item for item in CODING_QUESTIONS if item["id"] == question_id)
        app_module.CODE_SANDBOX_URL = "https://sandbox.example"
        original_sandbox_key = app_module.CODE_SANDBOX_API_KEY
        app_module.CODE_SANDBOX_API_KEY = "test-sandbox-token"

        def sandbox_response(url, **kwargs):
            self.assertEqual(url, "https://sandbox.example/api/v2/execute")
            self.assertEqual(kwargs["json"]["files"][0]["name"], "main.py")
            case = next(test for test in question["tests"] if test["input"] == kwargs["json"]["stdin"])
            response = Mock()
            response.status_code = 200
            response.json.return_value = {
                "run": {"stdout": f"{case['output']}\n", "stderr": "", "code": 0}
            }
            return response

        with patch.object(app_module.requests, "post", side_effect=sandbox_response) as execute:
            sample = self.client.post("/api/coding/execute", json={
                "question_id": question_id,
                "language": "python",
                "code": "print('sample')",
                "action": "run",
            }, headers={"X-CSRF-Token": csrf_token})
            self.assertEqual(sample.status_code, 200)
            self.assertTrue(sample.json["passed"])
            self.assertEqual(len(sample.json["results"]), 1)

            submission = self.client.post("/api/coding/execute", json={
                "question_id": question_id,
                "language": "python",
                "code": "print('submitted')",
                "action": "submit",
            }, headers={"X-CSRF-Token": csrf_token})
            self.assertEqual(submission.status_code, 200)
            self.assertTrue(submission.json["passed"])
            self.assertIn(question_id, submission.json["completed"])
            self.assertEqual(submission.json["score"], 2.0)
            self.assertEqual(len(submission.json["results"]), len(question["tests"]))
            self.assertEqual(execute.call_count, 3)
            self.assertEqual(
                execute.call_args.kwargs["headers"]["Authorization"],
                "Bearer test-sandbox-token",
            )
        app_module.CODE_SANDBOX_API_KEY = original_sandbox_key

    def test_official_interview_renders_voice_tools_with_a_typed_answer_fallback(self):
        self.create_verified_account("voice@example.com")
        self.client.post("/login", data={
            "email": "voice@example.com",
            "password": "SecurePass9!x",
        })
        with app_module.app.app_context():
            db = app_module.get_db()
            role_id = db.execute("SELECT id FROM roles ORDER BY id LIMIT 1").fetchone()[0]
            account_id = db.execute("SELECT id FROM users WHERE email=?", ("voice@example.com",)).fetchone()[0]
            for round_number in (1, 2):
                db.execute(
                    """INSERT INTO assessment_attempts
                       (user_id, role_id, mode, round_number, status, score)
                       VALUES (?, ?, 'official', ?, 'passed', 8)""",
                    (account_id, role_id, round_number),
                )
            db.commit()
        with self.client.session_transaction() as session:
            session["role_id"] = role_id
        response = self.client.get("/round/3")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Read question", response.data)
        self.assertIn(b"Start recording", response.data)
        self.assertIn(b"Stop", response.data)
        self.assertIn(b"Your answer", response.data)
        self.assertIn(b"voice.js", response.data)


if __name__ == "__main__":
    unittest.main()
