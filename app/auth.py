from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Iterator

from app.config import settings


PASSWORD_ITERATIONS = 600_000
VERIFICATION_ITERATIONS = 100_000


class AuthenticationError(ValueError):
    pass


class UnverifiedAccountError(AuthenticationError):
    pass


class EmailConfigurationError(RuntimeError):
    pass


class AuthService:
    def __init__(self, database_path: Path | None = None) -> None:
        data_dir = Path(
            os.getenv(
                "NEURODEV_DATA_DIR",
                str(Path.home() / ".neurodev"),
            )
        )
        self.database_path = database_path or data_dir / "accounts.sqlite3"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                name TEXT NOT NULL,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                verified_at TEXT,
                verification_token_hash TEXT,
                verification_salt BLOB,
                verification_attempts INTEGER NOT NULL DEFAULT 0,
                verification_expires_at TEXT,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS account_sessions (
                token_hash TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_reset_codes (
                account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                code_hash BLOB NOT NULL,
                salt BLOB NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_account_sessions_expiry
                ON account_sessions(expires_at);
            """
        )
        account_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(accounts)")
        }
        if "last_login_at" not in account_columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN last_login_at TEXT")
        if "verification_salt" not in account_columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN verification_salt BLOB")
        if "verification_attempts" not in account_columns:
            connection.execute(
                "ALTER TABLE accounts ADD COLUMN verification_attempts INTEGER NOT NULL DEFAULT 0"
            )
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _account(row: sqlite3.Row) -> dict[str, str]:
        return {"id": row["id"], "name": row["name"], "email": row["email"]}

    @staticmethod
    def _verification_hash(code: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256",
            code.encode("ascii"),
            salt,
            VERIFICATION_ITERATIONS,
        )

    @staticmethod
    def _password_hash(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            PASSWORD_ITERATIONS,
        )

    def create_account(
        self,
        name: str,
        email: str,
        password: str,
    ) -> tuple[dict[str, str], str]:
        salt = secrets.token_bytes(16)
        verification_salt = secrets.token_bytes(16)
        verification_code = f"{secrets.randbelow(1_000_000):06d}"
        now = self._now()
        account_id = secrets.token_urlsafe(18)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO accounts (
                        id, email, name, password_salt, password_hash,
                        verification_token_hash, verification_salt, verification_attempts,
                        verification_expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        account_id,
                        email,
                        name,
                        salt,
                        self._password_hash(password, salt),
                        self._verification_hash(verification_code, verification_salt),
                        verification_salt,
                        (now + timedelta(minutes=settings.AUTH_VERIFICATION_MINUTES)).isoformat(),
                        now.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise AuthenticationError("An account with this email already exists.") from exc
        return {"id": account_id, "name": name, "email": email}, verification_code

    def issue_verification(self, email: str) -> str | None:
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_bytes(16)
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts
                SET verification_token_hash = ?, verification_salt = ?,
                    verification_attempts = 0, verification_expires_at = ?
                WHERE email = ? AND verified_at IS NULL
                """,
                (
                    self._verification_hash(code, salt),
                    salt,
                    (now + timedelta(minutes=settings.AUTH_VERIFICATION_MINUTES)).isoformat(),
                    email,
                ),
            )
        return code if cursor.rowcount else None

    def issue_password_reset(self, email: str) -> tuple[dict[str, str], str] | None:
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_bytes(16)
        now = self._now()
        with self._connect() as connection:
            account = connection.execute(
                """
                SELECT id, name, email FROM accounts
                WHERE email = ? AND verified_at IS NOT NULL
                """,
                (email,),
            ).fetchone()
            if account is None:
                return None
            connection.execute(
                """
                INSERT INTO password_reset_codes (account_id, code_hash, salt, attempts, expires_at)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    code_hash = excluded.code_hash,
                    salt = excluded.salt,
                    attempts = 0,
                    expires_at = excluded.expires_at
                """,
                (
                    account["id"],
                    self._verification_hash(code, salt),
                    salt,
                    (now + timedelta(minutes=settings.AUTH_VERIFICATION_MINUTES)).isoformat(),
                ),
            )
        return self._account(account), code

    def reset_password(self, email: str, code: str, password: str) -> None:
        now = self._now()
        error_message = ""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT a.id, r.code_hash, r.salt, r.attempts, r.expires_at
                FROM accounts a
                JOIN password_reset_codes r ON r.account_id = a.id
                WHERE a.email = ? AND a.verified_at IS NOT NULL
                """,
                (email,),
            ).fetchone()
            if row is None:
                error_message = "The email or password reset code is incorrect."
            elif row["expires_at"] <= now.isoformat():
                connection.execute(
                    "DELETE FROM password_reset_codes WHERE account_id = ?",
                    (row["id"],),
                )
                error_message = "This code has expired. Request a new password reset code."
            elif row["attempts"] >= settings.AUTH_VERIFICATION_MAX_ATTEMPTS:
                error_message = "Too many incorrect attempts. Request a new password reset code."
            elif not hmac.compare_digest(
                self._verification_hash(code, row["salt"]),
                row["code_hash"],
            ):
                attempts = row["attempts"] + 1
                if attempts >= settings.AUTH_VERIFICATION_MAX_ATTEMPTS:
                    connection.execute(
                        "DELETE FROM password_reset_codes WHERE account_id = ?",
                        (row["id"],),
                    )
                    error_message = "Too many incorrect attempts. Request a new password reset code."
                else:
                    connection.execute(
                        "UPDATE password_reset_codes SET attempts = ? WHERE account_id = ?",
                        (attempts, row["id"]),
                    )
                    error_message = "The email or password reset code is incorrect."
            else:
                salt = secrets.token_bytes(16)
                connection.execute(
                    "UPDATE accounts SET password_salt = ?, password_hash = ? WHERE id = ?",
                    (salt, self._password_hash(password, salt), row["id"]),
                )
                connection.execute(
                    "DELETE FROM account_sessions WHERE account_id = ?",
                    (row["id"],),
                )
                connection.execute(
                    "DELETE FROM password_reset_codes WHERE account_id = ?",
                    (row["id"],),
                )

        if error_message:
            raise AuthenticationError(error_message)

    def get_unverified_account(self, email: str) -> dict[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, email FROM accounts
                WHERE email = ? AND verified_at IS NULL
                """,
                (email,),
            ).fetchone()
        return self._account(row) if row else None

    def verify_email(self, email: str, code: str) -> dict[str, str]:
        now = self._now()
        error_message = ""
        account = None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, email, verification_token_hash, verification_salt,
                       verification_attempts, verification_expires_at
                FROM accounts
                WHERE email = ? AND verified_at IS NULL
                """,
                (email,),
            ).fetchone()
            if row is None:
                error_message = "The email or verification code is incorrect."
            elif row["verification_attempts"] >= settings.AUTH_VERIFICATION_MAX_ATTEMPTS:
                error_message = "Too many incorrect attempts. Request a new code and try again."
            elif (
                not row["verification_token_hash"]
                or not row["verification_salt"]
                or not row["verification_expires_at"]
                or row["verification_expires_at"] <= now.isoformat()
            ):
                connection.execute(
                    """
                    UPDATE accounts SET verification_token_hash = NULL,
                        verification_salt = NULL, verification_expires_at = NULL
                    WHERE id = ?
                    """,
                    (row["id"],),
                )
                error_message = "This code has expired. Request a new code and try again."
            elif not hmac.compare_digest(
                self._verification_hash(code, row["verification_salt"]),
                row["verification_token_hash"],
            ):
                attempts = row["verification_attempts"] + 1
                connection.execute(
                    """
                    UPDATE accounts
                    SET verification_attempts = ?,
                        verification_token_hash = CASE WHEN ? >= ? THEN NULL ELSE verification_token_hash END,
                        verification_salt = CASE WHEN ? >= ? THEN NULL ELSE verification_salt END,
                        verification_expires_at = CASE WHEN ? >= ? THEN NULL ELSE verification_expires_at END
                    WHERE id = ?
                    """,
                    (
                        attempts,
                        attempts,
                        settings.AUTH_VERIFICATION_MAX_ATTEMPTS,
                        attempts,
                        settings.AUTH_VERIFICATION_MAX_ATTEMPTS,
                        attempts,
                        settings.AUTH_VERIFICATION_MAX_ATTEMPTS,
                        row["id"],
                    ),
                )
                error_message = (
                    "Too many incorrect attempts. Request a new code and try again."
                    if attempts >= settings.AUTH_VERIFICATION_MAX_ATTEMPTS
                    else "The email or verification code is incorrect."
                )
            else:
                connection.execute(
                    """
                    UPDATE accounts SET verified_at = ?, verification_token_hash = NULL,
                        verification_salt = NULL, verification_attempts = 0,
                        verification_expires_at = NULL WHERE id = ?
                    """,
                    (now.isoformat(), row["id"]),
                )
                account = self._account(row)

        if error_message:
            raise AuthenticationError(error_message)
        if account is None:
            raise AuthenticationError("Email verification could not be completed.")
        return account

    def authenticate(self, email: str, password: str) -> dict[str, str]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, email, name, password_salt, password_hash, verified_at
                FROM accounts WHERE email = ?
                """,
                (email,),
            ).fetchone()

        salt = row["password_salt"] if row else b"\0" * 16
        password_hash = self._password_hash(password, salt)
        expected_hash = row["password_hash"] if row else b"\1" * 32
        if not hmac.compare_digest(password_hash, expected_hash):
            raise AuthenticationError("Email or password is incorrect.")
        if row["verified_at"] is None:
            raise UnverifiedAccountError("Verify your email address before signing in.")
        with self._connect() as connection:
            connection.execute(
                "UPDATE accounts SET last_login_at = ? WHERE id = ?",
                (self._now().isoformat(), row["id"]),
            )
        return self._account(row)

    def list_accounts(self) -> list[dict[str, str | None]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT name, email, verified_at, created_at, last_login_at
                FROM accounts
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_session(self, account_id: str) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)
        expires_at = self._now() + timedelta(days=settings.AUTH_SESSION_DAYS)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM account_sessions WHERE expires_at <= ?",
                (self._now().isoformat(),),
            )
            connection.execute(
                "INSERT INTO account_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                (self._token_hash(token), account_id, expires_at.isoformat()),
            )
        return token, expires_at

    def get_session_account(self, token: str | None) -> dict[str, str] | None:
        if not token:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT a.id, a.name, a.email
                FROM account_sessions s
                JOIN accounts a ON a.id = s.account_id
                WHERE s.token_hash = ? AND s.expires_at > ? AND a.verified_at IS NOT NULL
                """,
                (self._token_hash(token), self._now().isoformat()),
            ).fetchone()
        return self._account(row) if row else None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM account_sessions WHERE token_hash = ?",
                (self._token_hash(token),),
            )


def send_verification_email(recipient: str, name: str, code: str) -> None:
    import smtplib

    if not all(
        (
            settings.SMTP_HOST,
            settings.SMTP_USERNAME,
            settings.SMTP_PASSWORD,
            settings.SMTP_FROM_EMAIL,
        )
    ):
        raise EmailConfigurationError(
            "Email sending is not configured on this NeuroDev server. "
            "The app owner must add SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, "
            "and SMTP_FROM_EMAIL to the backend .env file, then restart the server. "
            "After setup, select Resend verification code."
        )

    message = EmailMessage()
    message["Subject"] = "Your NeuroDev verification code"
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = recipient
    message.set_content(
        f"Hi {name},\n\n"
        "Thanks for signing up for NeuroDev. Enter this verification code in the app:\n\n"
        f"    {code}\n\n"
        f"This code expires in {settings.AUTH_VERIFICATION_MINUTES} minutes and can "
        f"be entered up to {settings.AUTH_VERIFICATION_MAX_ATTEMPTS} times. "
        "If you did not create an account, you can ignore this email."
    )

    smtp_password = settings.SMTP_PASSWORD
    if settings.SMTP_HOST.casefold() == "smtp.gmail.com":
        smtp_password = "".join(smtp_password.split())

    if settings.SMTP_USE_SSL:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            server.login(settings.SMTP_USERNAME, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            if settings.SMTP_STARTTLS:
                server.starttls()
            server.login(settings.SMTP_USERNAME, smtp_password)
            server.send_message(message)


def send_password_reset_email(recipient: str, name: str, code: str) -> None:
    import smtplib

    if not all(
        (
            settings.SMTP_HOST,
            settings.SMTP_USERNAME,
            settings.SMTP_PASSWORD,
            settings.SMTP_FROM_EMAIL,
        )
    ):
        raise EmailConfigurationError(
            "Email sending is not configured on this NeuroDev server. "
            "The app owner must configure SMTP in the backend .env file, then restart the server."
        )

    message = EmailMessage()
    message["Subject"] = "Your NeuroDev password reset code"
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = recipient
    message.set_content(
        f"Hi {name},\n\n"
        "Enter this code in NeuroDev to choose a new password:\n\n"
        f"    {code}\n\n"
        f"This code expires in {settings.AUTH_VERIFICATION_MINUTES} minutes and can "
        f"be entered up to {settings.AUTH_VERIFICATION_MAX_ATTEMPTS} times. "
        "If you did not request a password reset, you can ignore this email."
    )

    smtp_password = settings.SMTP_PASSWORD
    if settings.SMTP_HOST.casefold() == "smtp.gmail.com":
        smtp_password = "".join(smtp_password.split())

    if settings.SMTP_USE_SSL:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            server.login(settings.SMTP_USERNAME, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            if settings.SMTP_STARTTLS:
                server.starttls()
            server.login(settings.SMTP_USERNAME, smtp_password)
            server.send_message(message)


auth_service = AuthService()
