from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from pypdf import PdfWriter
from fastapi.testclient import TestClient

from app.main import app
from app.main import _extract_pdf_text, _transcribe_audio, require_account
from app.agents import response_agent
from app.auth import (
    AuthService,
    AuthenticationError,
    EmailConfigurationError,
    send_password_reset_email,
    send_verification_email,
)
from app.config import settings
from app.memory import MemoryService
from app.models import ImageAttachment, PdfAttachment
from app.reasoning import ReasoningService


client = TestClient(app)


def authenticated_post(path, **kwargs):
    with patch.dict(
        app.dependency_overrides,
        {require_account: lambda: {"id": "test-account", "name": "Test", "email": "test@example.com"}},
    ):
        return client.post(path, **kwargs)


# ============================================================
# API tests
# ============================================================


def test_root():
    response = client.get("/")

    assert response.status_code == 200

    data = response.json()

    assert data["service"] == "NeuroDev"
    assert "version" in data


def test_frontend_is_served():
    response = client.get("/ui/")

    assert response.status_code == 200
    assert "NeuroDev — your thinking partner" in response.text


def test_transcribe_audio():
    with patch("app.main._transcribe_audio", return_value="Hello NeuroDev") as transcribe:
        response = authenticated_post(
            "/transcribe",
            content=b"audio data",
            headers={"content-type": "audio/webm"},
        )

    assert response.status_code == 200
    assert response.json() == {"text": "Hello NeuroDev"}
    transcribe.assert_called_once_with(b"audio data", "audio/webm")


def test_transcription_upload_has_audio_filename_extension():
    client_mock = MagicMock()
    client_mock.audio.transcriptions.create.return_value.text = "Hello NeuroDev, how can I help?"
    with patch("app.main.Groq", return_value=client_mock):
        assert _transcribe_audio(b"audio data", "audio/webm;codecs=opus") == "Hello NeuroDev, how can I help?"

    client_mock.audio.transcriptions.create.assert_called_once_with(
        file=("voice-input.webm", b"audio data", "audio/webm"),
        model="whisper-large-v3",
        language="en",
        prompt=(
            "Transcribe the complete English recording verbatim. Preserve every "
            "word in the speaker's question or request; do not summarize or answer it."
        ),
        response_format="json",
        temperature=0,
    )


def test_transcription_uses_full_accuracy_model_for_short_answers_too():
    client_mock = MagicMock()
    client_mock.audio.transcriptions.create.return_value.text = "you"

    with patch("app.main.Groq", return_value=client_mock):
        text = _transcribe_audio(b"audio data", "audio/webm")

    assert text == "you"
    assert client_mock.audio.transcriptions.create.call_args.kwargs["model"] == "whisper-large-v3"


def test_transcription_rejects_unsupported_audio_format():
    with patch("app.main.Groq") as groq:
        try:
            _transcribe_audio(b"audio data", "audio/x-unknown")
        except ValueError as exc:
            assert str(exc) == "Unsupported audio format: audio/x-unknown"
        else:
            raise AssertionError("Unsupported audio format should be rejected.")

    groq.assert_not_called()


def test_transcribe_rejects_non_audio():
    response = authenticated_post(
        "/transcribe",
        content=b"not audio",
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 415


def test_transcribe_reports_provider_failure():
    with patch("app.main._transcribe_audio", side_effect=RuntimeError("provider unavailable")):
        response = authenticated_post(
            "/transcribe",
            content=b"audio data",
            headers={"content-type": "audio/webm"},
        )

    assert response.status_code == 502


def test_image_attachment_accepts_jpeg_data_url():
    image = ImageAttachment(
        name="photo.jpg",
        data_url="data:image/jpeg;base64,aGVsbG8=",
    )

    assert image.name == "photo.jpg"


def test_image_attachment_rejects_invalid_data_url():
    try:
        ImageAttachment(name="photo.jpg", data_url="data:image/jpeg;base64,not base64")
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid image data should be rejected.")


def test_pdf_attachment_requires_valid_pdf_bytes():
    import base64

    import pytest

    with pytest.raises(ValueError, match="not a valid PDF"):
        PdfAttachment(name="notes.pdf", data_base64=base64.b64encode(b"not pdf").decode("ascii"))


def test_signup_verifies_email_before_issuing_protected_session(tmp_path):
    service = AuthService(tmp_path / "accounts.sqlite3")
    with (
        patch("app.main.auth_service", service),
        patch("app.main.send_verification_email") as send_email,
    ):
        signup = client.post(
            "/auth/signup",
            json={
                "name": "NeuroDev User",
                "email": "New.User@gmail.com",
                "password": "safe-password-123",
            },
        )
        assert signup.status_code == 202
        assert "inbox" in signup.json()["message"]
        send_email.assert_called_once()
        assert send_email.call_args.args[0] == "new.user@gmail.com"

        code = send_email.call_args.args[2]
        assert len(code) == 6 and code.isascii() and code.isdigit()
        unverified_login = client.post(
            "/auth/login",
            json={"email": "new.user@gmail.com", "password": "safe-password-123"},
        )
        assert unverified_login.status_code == 403

        verified = client.post(
            "/auth/verify",
            json={"email": "new.user@gmail.com", "code": code},
        )
        assert verified.status_code == 200
        assert verified.json()["email"] == "new.user@gmail.com"
        assert client.post(
            "/auth/verify",
            json={"email": "new.user@gmail.com", "code": "12ab56"},
        ).status_code == 422
        assert client.post(
            "/auth/verify",
            json={"email": "new.user@gmail.com", "code": code},
        ).status_code == 400

        login = client.post(
            "/auth/login",
            json={"email": "new.user@gmail.com", "password": "safe-password-123"},
        )
        assert login.status_code == 200
        assert login.json()["account"]["name"] == "NeuroDev User"
        tab_session = login.json()["tab_session"]
        assert isinstance(tab_session, str) and len(tab_session) >= 40
        client.cookies.set("neurodev_session", "legacy-cookie-only")
        assert client.get("/auth/me").status_code == 401
        client.headers["X-NeuroDev-Tab"] = tab_session
        assert client.get("/auth/me").json()["account"]["email"] == "new.user@gmail.com"

        other_tab = TestClient(app)
        assert other_tab.get("/auth/me").status_code == 401

        assert client.post("/auth/logout").status_code == 200
        assert client.get("/auth/me").status_code == 401
        client.headers.pop("X-NeuroDev-Tab", None)
        client.cookies.delete("neurodev_session")
        assert client.post(
            "/chat",
            json={
                "question": "hello",
                "session_id": "test",
                "project_id": "test",
            },
        ).status_code == 401


def test_signup_rejects_duplicate_account_and_weak_password(tmp_path):
    service = AuthService(tmp_path / "accounts.sqlite3")
    _, verification_code = service.create_account(
        "Existing User",
        "existing@example.com",
        "a-long-enough-password",
    )
    assert len(verification_code) == 6
    with (
        patch("app.main.auth_service", service),
        patch("app.main.send_verification_email"),
    ):
        duplicate = client.post(
            "/auth/signup",
            json={
                "name": "Another User",
                "email": "EXISTING@example.com",
                "password": "another-long-password",
            },
        )
        weak_password = client.post(
            "/auth/signup",
            json={
                "name": "New User",
                "email": "new@example.com",
                "password": "short",
            },
        )
        valid_eight_character_password = client.post(
            "/auth/signup",
            json={
                "name": "New User",
                "email": "new@example.com",
                "password": "abcdefgh",
            },
        )
    assert duplicate.status_code == 409
    assert weak_password.status_code == 422
    assert valid_eight_character_password.status_code == 202


def test_password_reset_uses_email_code_changes_password_and_revokes_sessions(tmp_path):
    service = AuthService(tmp_path / "accounts.sqlite3")
    _, verification_code = service.create_account(
        "Reset User",
        "reset@example.com",
        "old-password-123",
    )
    service.verify_email("reset@example.com", verification_code)
    account = service.authenticate("reset@example.com", "old-password-123")
    session, _ = service.create_session(account["id"])
    try:
        with (
            patch("app.main.auth_service", service),
            patch("app.main.send_password_reset_email") as send_email,
        ):
            unknown = client.post(
                "/auth/password-reset/request",
                json={"email": "unknown@example.com"},
            )
            requested = client.post(
                "/auth/password-reset/request",
                json={"email": "RESET@example.com"},
            )
            assert unknown.status_code == requested.status_code == 202
            assert unknown.json() == requested.json()
            send_email.assert_called_once()
            assert send_email.call_args.args[:2] == ("reset@example.com", "Reset User")
            reset_code = send_email.call_args.args[2]
            assert len(reset_code) == 6 and reset_code.isdigit()

            client.headers["X-NeuroDev-Tab"] = session
            invalid = client.post(
                "/auth/password-reset/complete",
                json={
                    "email": "reset@example.com",
                    "code": str((int(reset_code) + 1) % 1_000_000).zfill(6),
                    "new_password": "new-password-456",
                },
            )
            assert invalid.status_code == 400
            assert client.get("/auth/me").status_code == 200

            completed = client.post(
                "/auth/password-reset/complete",
                json={
                    "email": "reset@example.com",
                    "code": reset_code,
                    "new_password": "new-password-456",
                },
            )
            assert completed.status_code == 200
            assert client.get("/auth/me").status_code == 401
            assert client.post(
                "/auth/login",
                json={"email": "reset@example.com", "password": "old-password-123"},
            ).status_code == 401
            assert client.post(
                "/auth/login",
                json={"email": "reset@example.com", "password": "new-password-456"},
            ).status_code == 200
    finally:
        client.headers.pop("X-NeuroDev-Tab", None)


def test_signup_rejects_common_email_domain_typos(tmp_path):
    service = AuthService(tmp_path / "accounts.sqlite3")
    with (
        patch("app.main.auth_service", service),
        patch("app.main.send_verification_email") as send_email,
    ):
        response = client.post(
            "/auth/signup",
            json={
                "name": "Typo User",
                "email": "person@gmail.co",
                "password": "safe-password-123",
            },
        )

    assert response.status_code == 422
    assert "person@gmail.com" in str(response.json()["detail"])
    send_email.assert_not_called()


def test_missing_email_configuration_preserves_account_for_resend(tmp_path):
    service = AuthService(tmp_path / "accounts.sqlite3")
    with patch.multiple(
        "app.auth.settings",
        SMTP_HOST="",
        SMTP_USERNAME="",
        SMTP_PASSWORD="",
        SMTP_FROM_EMAIL="",
    ):
        with pytest.raises(EmailConfigurationError, match="SMTP_HOST"):
            send_verification_email("pending@example.com", "Pending User", "token")

    with (
        patch("app.main.auth_service", service),
        patch.multiple(
            "app.auth.settings",
            SMTP_HOST="",
            SMTP_USERNAME="",
            SMTP_PASSWORD="",
            SMTP_FROM_EMAIL="",
        ),
    ):
        signup = client.post(
            "/auth/signup",
            json={
                "name": "Pending User",
                "email": "pending@example.com",
                "password": "simple-password",
            },
        )

    assert signup.status_code == 503
    assert "SMTP_HOST" in signup.json()["detail"]
    with pytest.raises(AuthenticationError, match="Verify your email"):
        service.authenticate("pending@example.com", "simple-password")

    with (
        patch("app.main.auth_service", service),
        patch.multiple(
            "app.auth.settings",
            SMTP_HOST="",
            SMTP_USERNAME="",
            SMTP_PASSWORD="",
            SMTP_FROM_EMAIL="",
        ),
    ):
        resend_without_sender = client.post(
            "/auth/resend-verification",
            json={"email": "pending@example.com"},
        )
    assert resend_without_sender.status_code == 503
    assert "SMTP_HOST" in resend_without_sender.json()["detail"]

    with (
        patch("app.main.auth_service", service),
        patch("app.main.send_verification_email") as send_email,
    ):
        resent = client.post(
            "/auth/resend-verification",
            json={"email": "pending@example.com"},
        )

    assert resent.status_code == 202
    send_email.assert_called_once()
    assert send_email.call_args.args[0] == "pending@example.com"
    resent_code = send_email.call_args.args[2]
    with patch("app.main.auth_service", service):
        verified = client.post(
            "/auth/verify",
            json={"email": "pending@example.com", "code": resent_code},
        )
    assert verified.status_code == 200


def test_auth_service_only_accepts_correct_verified_password(tmp_path):
    service = AuthService(tmp_path / "accounts.sqlite3")
    _, code = service.create_account("User", "user@example.com", "long-password-123")
    with pytest.raises(AuthenticationError, match="Verify your email"):
        service.authenticate("user@example.com", "long-password-123")
    service.verify_email("user@example.com", code)
    with pytest.raises(AuthenticationError, match="incorrect"):
        service.authenticate("user@example.com", "wrong-password")
    assert service.authenticate("user@example.com", "long-password-123")["email"] == "user@example.com"


def test_verification_code_expires_and_locks_after_maximum_attempts(tmp_path):
    service = AuthService(tmp_path / "accounts.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with patch.object(service, "_now", return_value=now):
        _, expired_code = service.create_account(
            "Expired User",
            "expired@example.com",
            "long-password-123",
        )
    with patch.object(service, "_now", return_value=now + timedelta(minutes=16)):
        with pytest.raises(AuthenticationError, match="expired"):
            service.verify_email("expired@example.com", expired_code)

    _, correct_code = service.create_account(
        "Locked User",
        "locked@example.com",
        "long-password-123",
    )
    for _ in range(settings.AUTH_VERIFICATION_MAX_ATTEMPTS):
        with pytest.raises(AuthenticationError):
            service.verify_email("locked@example.com", "000000" if correct_code != "000000" else "000001")
    with pytest.raises(AuthenticationError, match="Too many incorrect attempts"):
        service.verify_email("locked@example.com", correct_code)

    replacement_code = service.issue_verification("locked@example.com")
    assert replacement_code
    assert service.verify_email("locked@example.com", replacement_code)["email"] == "locked@example.com"


def test_pending_legacy_account_can_be_resend_verified_with_code(tmp_path):
    import sqlite3

    database_path = tmp_path / "accounts.sqlite3"
    service = AuthService(database_path)
    service.create_account("Legacy User", "legacy@example.com", "long-password-123")
    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE accounts DROP COLUMN verification_salt")
        connection.execute("ALTER TABLE accounts DROP COLUMN verification_attempts")

    replacement_code = service.issue_verification("legacy@example.com")
    assert replacement_code
    assert service.verify_email("legacy@example.com", replacement_code)["email"] == "legacy@example.com"


def test_admin_roster_requires_allowlisted_account_and_excludes_passwords(tmp_path):
    service = AuthService(tmp_path / "accounts.sqlite3")
    _, admin_verification = service.create_account(
        "Faculty Admin",
        "faculty@example.com",
        "faculty-password",
    )
    service.verify_email("faculty@example.com", admin_verification)
    admin = service.authenticate("faculty@example.com", "faculty-password")
    admin_session, _ = service.create_session(admin["id"])
    _, user_verification = service.create_account(
        "Student User",
        "student@example.com",
        "student-password",
    )

    try:
        with (
            patch("app.main.auth_service", service),
            patch.object(settings, "ADMIN_EMAILS", "FACULTY@example.com"),
        ):
            client.headers["X-NeuroDev-Tab"] = admin_session
            assert client.get("/admin/status").json() == {"is_admin": True}
            roster = client.get("/admin/accounts")
            assert roster.status_code == 200
            accounts = roster.json()["accounts"]
            student = next(row for row in accounts if row["email"] == "student@example.com")
            assert student["name"] == "Student User"
            assert student["verified_at"] is None
            assert set(student) == {
                "name",
                "email",
                "verified_at",
                "created_at",
                "last_login_at",
            }
            assert service.verify_email("student@example.com", user_verification)["email"] == "student@example.com"

            with patch.object(settings, "ADMIN_EMAILS", ""):
                assert client.get("/admin/status").json() == {"is_admin": False}
                assert client.get("/admin/accounts").status_code == 403
    finally:
        client.headers.pop("X-NeuroDev-Tab", None)


def test_verification_email_uses_configured_smtp_sender():
    with (
        patch.multiple(
            "app.auth.settings",
            SMTP_HOST="smtp.gmail.com",
            SMTP_PORT=587,
            SMTP_USERNAME="sender@gmail.com",
            SMTP_PASSWORD="app pass word",
            SMTP_FROM_EMAIL="sender@gmail.com",
            SMTP_USE_SSL=False,
            SMTP_STARTTLS=True,
            AUTH_VERIFICATION_MINUTES=15,
            AUTH_VERIFICATION_MAX_ATTEMPTS=5,
        ),
        patch("smtplib.SMTP") as smtp_factory,
    ):
        send_verification_email("recipient@gmail.com", "NeuroDev User", "042681")

    server = smtp_factory.return_value.__enter__.return_value
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("sender@gmail.com", "apppassword")
    email = server.send_message.call_args.args[0]
    assert email["To"] == "recipient@gmail.com"
    body = email.get_content()
    assert "042681" in body
    assert "localhost" not in body
    assert "http://" not in body
    assert "15 minutes" in body


def test_password_reset_email_uses_configured_smtp_sender():
    with (
        patch.multiple(
            "app.auth.settings",
            SMTP_HOST="smtp.gmail.com",
            SMTP_PORT=587,
            SMTP_USERNAME="sender@gmail.com",
            SMTP_PASSWORD="app pass word",
            SMTP_FROM_EMAIL="sender@gmail.com",
            SMTP_USE_SSL=False,
            SMTP_STARTTLS=True,
            AUTH_VERIFICATION_MINUTES=15,
            AUTH_VERIFICATION_MAX_ATTEMPTS=5,
        ),
        patch("smtplib.SMTP") as smtp_factory,
    ):
        send_password_reset_email("recipient@gmail.com", "NeuroDev User", "042681")

    server = smtp_factory.return_value.__enter__.return_value
    server.login.assert_called_once_with("sender@gmail.com", "apppassword")
    email = server.send_message.call_args.args[0]
    assert email["To"] == "recipient@gmail.com"
    assert "password reset" in email["Subject"].lower()
    assert "042681" in email.get_content()
    assert "15 minutes" in email.get_content()


def test_protected_chat_rejects_anonymous_requests():
    response = client.post(
        "/chat",
        json={
            "question": "hello",
            "session_id": "anonymous-test",
            "project_id": "anonymous-test",
        },
    )
    assert response.status_code == 401


def test_extract_pdf_text_reads_attached_document():
    from io import BytesIO

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    pdf_data = BytesIO()
    writer.write(pdf_data)
    reader = MagicMock()
    reader.is_encrypted = False
    reader.pages = [MagicMock()]
    reader.pages[0].extract_text.return_value = "SQL means Structured Query Language."

    with patch("app.main.PdfReader", return_value=reader):
        assert _extract_pdf_text(pdf_data.getvalue(), "notes.pdf") == "SQL means Structured Query Language."


def test_chat_passes_extracted_pdf_text_to_workflow():
    import base64

    workflow = MagicMock()
    workflow.invoke.return_value = {"final_response": "SQL means Structured Query Language."}
    pdf_data = base64.b64encode(b"%PDF-1.4 test").decode("ascii")
    with (
        patch("app.main._extract_pdf_text", return_value="SQL means Structured Query Language."),
        patch("app.main.neurodev_workflow", workflow),
        patch("app.main.memory_service.save_session_message"),
    ):
        response = authenticated_post(
            "/chat",
            json={
                "question": "What does this document say?",
                "session_id": "pdf-test",
                "project_id": "project-test",
                "pdfs": [{"name": "notes.pdf", "data_base64": pdf_data}],
            },
        )

    assert response.status_code == 200
    initial_state = workflow.invoke.call_args.args[0]
    assert initial_state["documents"] == [
        {"name": "notes.pdf", "text": "SQL means Structured Query Language."}
    ]


def test_response_agent_sends_images_to_vision_model():
    client_mock = MagicMock()
    client_mock.chat.completions.create.return_value.choices[0].message.content = "A photo of a tree."
    image = "data:image/jpeg;base64,aGVsbG8="
    state = {
        "question": "What is in this picture?",
        "context": "",
        "images": [image],
    }

    with patch("app.agents.Groq", return_value=client_mock):
        response = response_agent(state)

    assert response["final_response"] == "A photo of a tree."
    payload = client_mock.chat.completions.create.call_args.kwargs
    assert payload["model"] == "qwen/qwen3.8-27b"
    assert payload["messages"][0]["content"][-1] == {
        "type": "image_url",
        "image_url": {"url": image},
    }


def test_response_agent_uses_fast_response_model():
    response_model = MagicMock()
    response_model.invoke.return_value.content = "Here is a concise answer."
    state = {
        "question": "How do I reverse a list in Python?",
        "context": "",
        "images": [],
    }

    with patch("app.agents.response_llm", response_model):
        response = response_agent(state)

    assert response["final_response"] == "Here is a concise answer."
    response_model.invoke.assert_called_once()


def test_response_agent_answers_general_questions_without_project_context():
    response_model = MagicMock()
    response_model.invoke.return_value.content = "Droupadi Murmu is the president of India."
    state = {
        "question": "Who is the president of India?",
        "context": "",
        "images": [],
    }

    with patch("app.agents.response_llm", response_model):
        response = response_agent(state)

    prompt = response_model.invoke.call_args.args[0][0].content
    assert response["final_response"] == "Droupadi Murmu is the president of India."
    assert "answer from your knowledge" in prompt
    assert "optional" in prompt


def test_health():
    with (
        patch("app.main.memory_service.redis.ping"),
        patch("app.main.psycopg.connect"),
    ):
        response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "ok"
    assert data["service"] == "NeuroDev"


def test_create_project():
    mock_connection = MagicMock()
    mock_cursor = MagicMock()

    mock_connection.__enter__.return_value = mock_connection
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

    with patch(
        "app.memory.psycopg.connect",
        return_value=mock_connection,
    ):
        response = authenticated_post(
            "/projects",
            json={
                "name": "Test Project",
                "description": "NeuroDev test project",
            },
        )

    assert response.status_code == 200

    data = response.json()

    assert "project_id" in data
    assert data["name"] == "Test Project"
    assert data["description"] == "NeuroDev test project"

    mock_cursor.execute.assert_called_once()


def test_local_storage_fallback(tmp_path):
    memory = MemoryService()
    memory.local_store.path = tmp_path / "neurodev.sqlite3"

    with patch(
        "app.memory.psycopg.connect",
        side_effect=psycopg.OperationalError("database unavailable"),
    ):
        memory.create_project("project-1", "Test workspace", None)

    memory.save_session_message("session-1", "user", "hello")
    memory.save_session_message("session-1", "assistant", "Hi there.")
    memory.save_memory("project-1", "Use FastAPI for the API", "decision")
    memory.save_decision("project-1", "Use FastAPI", "It fits the project.")

    assert memory.get_session_messages("session-1")[-1]["content"] == "Hi there."
    assert memory.search_semantic_memory("project-1", "FastAPI API")[0]["content"] == "Use FastAPI for the API"

    restarted_memory = MemoryService()
    restarted_memory.local_store.path = memory.local_store.path
    restarted_memory.use_local_store = True
    assert restarted_memory.get_session_messages("session-1")[0]["content"] == "hello"
    assert restarted_memory.get_project_decisions("project-1")[0]["decision"] == "Use FastAPI"


# ============================================================
# Reasoning tests
# ============================================================


def test_same_decision_is_consistent():
    reasoning = ReasoningService()

    result = reasoning.compare_decisions(
        new_decision="Use PostgreSQL",
        new_reason="Need relational storage",
        previous_decision="Use PostgreSQL",
        previous_reason="Project data is relational",
    )

    assert result["conflict_detected"] is False
    assert result["conflict_type"] == "consistent"


def test_direct_conflict_is_detected():
    reasoning = ReasoningService()

    result = reasoning.compare_decisions(
        new_decision="Use MongoDB",
        new_reason="Prefer document storage",
        previous_decision="Use PostgreSQL",
        previous_reason="Need relational storage",
    )

    assert result["conflict_detected"] is True
    assert result["conflict_type"] == "direct_contradiction"


def test_unrelated_decisions_do_not_conflict():
    reasoning = ReasoningService()

    result = reasoning.compare_decisions(
        new_decision="Use Redis for sessions",
        new_reason="Need fast temporary storage",
        previous_decision="Use PostgreSQL",
        previous_reason="Need relational project data",
    )

    assert result["conflict_detected"] is False


def test_confidence_for_developer_decision():
    reasoning = ReasoningService()

    confidence = reasoning.calculate_confidence(
        source="developer",
        similarity=1.0,
        has_reason=True,
    )

    assert confidence == 1.0


def test_high_confidence_conflict_requires_user():
    reasoning = ReasoningService()

    comparison = {
        "conflict_detected": True,
        "conflict_type": "direct_contradiction",
        "similarity": 0.8,
    }

    result = reasoning.evaluate_conflict(
        comparison=comparison,
        previous_confidence=0.95,
    )

    assert result["requires_user_decision"] is True
    assert result["severity"] == "high"


def test_low_confidence_conflict_does_not_require_user():
    reasoning = ReasoningService()

    comparison = {
        "conflict_detected": True,
        "conflict_type": "direct_contradiction",
        "similarity": 0.8,
    }

    result = reasoning.evaluate_conflict(
        comparison=comparison,
        previous_confidence=0.40,
    )

    assert result["requires_user_decision"] is False


# ============================================================
# Decision resolution tests
# ============================================================


def test_resolve_decision():
    reasoning = ReasoningService()

    result = reasoning.resolve_decision(
        selected_decision="Use PostgreSQL",
        reason="The project needs relational data.",
    )

    assert result["resolved"] is True
    assert result["resolved_by"] == "developer"
    assert result["final_decision"] == "Use PostgreSQL"