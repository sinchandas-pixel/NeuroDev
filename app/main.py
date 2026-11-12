
from __future__ import annotations

import base64
import logging
import uuid

from io import BytesIO
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from groq import Groq
from pypdf import PdfReader
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.auth import (
    AuthenticationError,
    EmailConfigurationError,
    UnverifiedAccountError,
    auth_service,
    send_password_reset_email,
    send_verification_email,
)
from app.memory import memory_service
from app.models import (
    ChatRequest,
    ChatResponse,
    DecisionRequest,
    DecisionResponse,
    HealthResponse,
    LoginRequest,
    MemoryRequest,
    MemoryResponse,
    PasswordResetCompleteRequest,
    PasswordResetRequest,
    ProjectCreateRequest,
    ProjectResponse,
    ResendVerificationRequest,
    SignUpRequest,
    VerifyEmailRequest,
)
from app.workflow import neurodev_workflow


# ================================================================
# Logging
# ================================================================

logger = logging.getLogger(__name__)


# ================================================================
# PDF Processing
# ================================================================

def _extract_pdf_text(pdf_data: bytes, filename: str) -> str:
    try:
        reader = PdfReader(BytesIO(pdf_data), strict=True)

        if reader.is_encrypted:
            raise ValueError(
                f"{filename} is password-protected."
            )

        if len(reader.pages) > 100:
            raise ValueError(
                f"{filename} exceeds the 100-page limit."
            )

        text = "\n\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )

    except ValueError:
        raise

    except Exception as exc:
        raise ValueError(
            f"{filename} could not be read as a PDF."
        ) from exc

    text = text.strip()

    if not text:
        raise ValueError(
            f"{filename} contains no extractable text."
        )

    return text[:100_000]


# ================================================================
# FastAPI Application
# ================================================================

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "NeuroDev - AI-powered second brain for developers."
    ),
)


# ================================================================
# Frontend Static Files
# ================================================================

FRONTEND_DIR = (
    Path(__file__).parent / "frontend" / "public"
)

if not FRONTEND_DIR.is_dir():
    raise RuntimeError(
        f"Frontend directory not found: {FRONTEND_DIR}"
    )

app.mount(
    "/ui",
    StaticFiles(
        directory=str(FRONTEND_DIR),
        html=True,
    ),
    name="frontend",
)


# ================================================================
# Authentication Dependencies
# ================================================================

def require_account(
    request: Request,
) -> dict[str, str]:

    account = auth_service.get_session_account(
        request.headers.get("x-neurodev-tab")
    )

    if account is None:
        raise HTTPException(
            status_code=401,
            detail="Please sign in to continue.",
        )

    return account


def require_admin(
    account: dict[str, str] = Depends(require_account),
) -> dict[str, str]:

    admin_emails = {
        email.strip().casefold()
        for email in settings.ADMIN_EMAILS.split(",")
        if email.strip()
    }

    if account["email"].casefold() not in admin_emails:
        raise HTTPException(
            status_code=403,
            detail="Administrator access is required.",
        )

    return account


# ================================================================
# Admin
# ================================================================

@app.get("/admin/status")
def admin_status(
    account: dict[str, str] = Depends(require_account),
) -> dict[str, bool]:

    admin_emails = {
        email.strip().casefold()
        for email in settings.ADMIN_EMAILS.split(",")
        if email.strip()
    }

    return {
        "is_admin": (
            account["email"].casefold() in admin_emails
        )
    }


@app.get("/admin/accounts")
def admin_accounts(
    _: dict[str, str] = Depends(require_admin),
) -> dict[str, list[dict[str, str | None]]]:

    return {
        "accounts": auth_service.list_accounts()
    }


# ================================================================
# Authentication Routes
# ================================================================

@app.post("/auth/signup", status_code=202)
def sign_up(
    request: SignUpRequest,
) -> dict[str, str]:

    try:
        account, code = auth_service.create_account(
            request.name,
            request.email,
            request.password,
        )

    except AuthenticationError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    try:
        send_verification_email(
            account["email"],
            account["name"],
            code,
        )

    except EmailConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Could not send an account verification email."
        )

        raise HTTPException(
            status_code=503,
            detail=(
                "Your account is saved, but the verification "
                "email could not be delivered. The app owner "
                "should check the SMTP configuration."
            ),
        ) from exc

    return {
        "message": (
            "Check your inbox for the six-digit verification "
            "code, then enter it here. "
            f"The code expires in "
            f"{settings.AUTH_VERIFICATION_MINUTES} minutes."
        )
    }


@app.post("/auth/verify")
def verify_email(
    request: VerifyEmailRequest,
) -> dict[str, str]:

    try:
        account = auth_service.verify_email(
            request.email,
            request.code,
        )

    except AuthenticationError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return {
        "message": "Email verified. You can now sign in.",
        "email": account["email"],
    }


@app.post(
    "/auth/resend-verification",
    status_code=202,
)
def resend_verification(
    request: ResendVerificationRequest,
) -> dict[str, str]:

    code = auth_service.issue_verification(
        request.email
    )

    if code:
        try:
            account = auth_service.get_unverified_account(
                request.email
            )

            if account is None:
                raise RuntimeError(
                    "The pending account was not found."
                )

            send_verification_email(
                account["email"],
                account["name"],
                code,
            )

        except EmailConfigurationError as exc:
            raise HTTPException(
                status_code=503,
                detail=str(exc),
            ) from exc

        except Exception as exc:
            logger.exception(
                "Could not resend an account verification email."
            )

            raise HTTPException(
                status_code=503,
                detail=(
                    "The verification email could not be "
                    "delivered. Check the server SMTP settings."
                ),
            ) from exc

    return {
        "message": (
            "If that address has an unverified account, "
            "a new verification code has been emailed."
        )
    }


@app.post(
    "/auth/password-reset/request",
    status_code=202,
)
def request_password_reset(
    request: PasswordResetRequest,
) -> dict[str, str]:

    reset = auth_service.issue_password_reset(
        request.email
    )

    if reset:
        account, code = reset

        try:
            send_password_reset_email(
                account["email"],
                account["name"],
                code,
            )

        except EmailConfigurationError as exc:
            raise HTTPException(
                status_code=503,
                detail=str(exc),
            ) from exc

        except Exception as exc:
            logger.exception(
                "Could not send a password reset email."
            )

            raise HTTPException(
                status_code=503,
                detail=(
                    "The password reset email could not be "
                    "delivered. Check the server SMTP settings."
                ),
            ) from exc

    return {
        "message": (
            "If a verified account exists for that address, "
            "a password reset code has been emailed."
        )
    }


@app.post("/auth/password-reset/complete")
def complete_password_reset(
    request: PasswordResetCompleteRequest,
) -> dict[str, str]:

    try:
        auth_service.reset_password(
            request.email,
            request.code,
            request.new_password,
        )

    except AuthenticationError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return {
        "message": (
            "Your password has been reset. "
            "Sign in with your new password."
        )
    }


@app.post("/auth/login")
def log_in(
    request: LoginRequest,
) -> dict[str, dict[str, str] | str]:

    try:
        account = auth_service.authenticate(
            request.email,
            request.password,
        )

    except UnverifiedAccountError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc

    except AuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
        ) from exc

    tab_session, _ = auth_service.create_session(
        account["id"]
    )

    return {
        "account": account,
        "tab_session": tab_session,
    }


@app.get("/auth/me")
def current_account(
    account: dict[str, str] = Depends(require_account),
) -> dict[str, dict[str, str]]:

    return {
        "account": account
    }


@app.post("/auth/logout")
def log_out(
    request: Request,
) -> dict[str, str]:

    auth_service.delete_session(
        request.headers.get("x-neurodev-tab")
    )

    return {
        "message": "Signed out."
    }


# ================================================================
# Health Check
# ================================================================

@app.get(
    "/health",
    response_model=HealthResponse,
)
def health_check() -> HealthResponse:

    dependencies = {}

    # Redis
    try:
        memory_service.redis.ping()
        dependencies["redis"] = "connected"

    except Exception:
        logger.exception("Redis health check failed.")
        dependencies["redis"] = "disconnected"

    # PostgreSQL
    try:
        with psycopg.connect(
            settings.POSTGRES_URL,
            connect_timeout=settings.POSTGRES_CONNECT_TIMEOUT,
        ) as conn:

            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")

        dependencies["postgresql"] = "connected"

    except Exception:
        logger.exception(
            "PostgreSQL health check failed."
        )

        dependencies["postgresql"] = "disconnected"

    return HealthResponse(
        status="ok",
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        dependencies=dependencies,
    )


# ================================================================
# Audio Transcription
# ================================================================

def _transcribe_audio(
    audio_data: bytes,
    content_type: str,
) -> str:

    audio_formats = {
        "audio/flac": "flac",
        "audio/mp3": "mp3",
        "audio/mp4": "mp4",
        "audio/mpeg": "mpeg",
        "audio/mpga": "mpga",
        "audio/m4a": "m4a",
        "audio/ogg": "ogg",
        "audio/opus": "opus",
        "audio/wav": "wav",
        "audio/webm": "webm",
    }

    media_type = (
        content_type
        .split(";", maxsplit=1)[0]
        .strip()
        .lower()
    )

    extension = audio_formats.get(media_type)

    if extension is None:
        raise ValueError(
            f"Unsupported audio format: {media_type}"
        )

    client = Groq(
        api_key=settings.GROQ_API_KEY
    )

    result = client.audio.transcriptions.create(
        file=(
            f"voice-input.{extension}",
            audio_data,
            media_type,
        ),
        model="whisper-large-v3",
        language="en",
        prompt=(
            "Transcribe the complete English recording verbatim. "
            "Preserve every word in the speaker's question or "
            "request; do not summarize or answer it."
        ),
        response_format="json",
        temperature=0,
    )

    return result.text.strip()


@app.post(
    "/transcribe",
    dependencies=[Depends(require_account)],
)
async def transcribe(
    request: Request,
) -> dict[str, str]:

    content_type = (
        request.headers
        .get("content-type", "")
        .split(";", maxsplit=1)[0]
        .strip()
        .lower()
    )

    if not content_type.startswith("audio/"):
        raise HTTPException(
            status_code=415,
            detail="Upload an audio recording to transcribe.",
        )

    content_length = request.headers.get(
        "content-length"
    )

    max_size = 25 * 1024 * 1024

    if (
        content_length
        and content_length.isdigit()
        and int(content_length) > max_size
    ):
        raise HTTPException(
            status_code=413,
            detail=(
                "Audio recordings must be 25 MB or smaller."
            ),
        )

    audio_data = await request.body()

    if not audio_data:
        raise HTTPException(
            status_code=400,
            detail="The audio recording is empty.",
        )

    if len(audio_data) > max_size:
        raise HTTPException(
            status_code=413,
            detail=(
                "Audio recordings must be 25 MB or smaller."
            ),
        )

    try:
        text = await run_in_threadpool(
            _transcribe_audio,
            audio_data,
            content_type,
        )

    except Exception as exc:
        logger.exception(
            "Speech transcription failed."
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Speech transcription failed. "
                "Check the Groq API configuration and try again."
            ),
        ) from exc

    return {
        "text": text
    }


# ================================================================
# NeuroDev Chat
# ================================================================

@app.post(
    "/chat",
    response_model=ChatResponse,
    dependencies=[Depends(require_account)],
)
def chat(
    request: ChatRequest,
) -> ChatResponse:
    """
    Start a new NeuroDev conversation.

    The session_id is also used as the LangGraph thread ID
    so that a paused decision conflict can later be resumed
    using /decision.
    """

    try:
        initial_state = {
            "question": request.question,
            "session_id": request.session_id,
            "project_id": request.project_id,
            "images": [
                image.data_url
                for image in request.images
            ],
            "documents": [],
        }

        for pdf in request.pdfs:
            initial_state["documents"].append(
                {
                    "name": pdf.name,
                    "text": _extract_pdf_text(
                        base64.b64decode(
                            pdf.data_base64,
                            validate=True,
                        ),
                        pdf.name,
                    ),
                }
            )

        # Save user message after PDF validation.
        memory_service.save_session_message(
            session_id=request.session_id,
            role="user",
            content=request.question,
        )

        config = {
            "configurable": {
                "thread_id": request.session_id,
            }
        }

        result = neurodev_workflow.invoke(
            initial_state,
            config=config,
        )

        response = result.get(
            "final_response",
            "I could not generate a response.",
        )

        # Save assistant response.
        memory_service.save_session_message(
            session_id=request.session_id,
            role="assistant",
            content=response,
        )

        return ChatResponse(
            response=response,
            session_id=request.session_id,
            project_id=request.project_id,
            conflict_detected=result.get(
                "conflict_detected",
                False,
            ),
            requires_user_decision=result.get(
                "requires_user_decision",
                False,
            ),
            confidence_score=result.get(
                "confidence_score"
            ),
            previous_decision=result.get(
                "previous_decision"
            ),
            new_decision=result.get(
                "new_decision"
            ),
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "NeuroDev chat processing failed."
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Chat processing failed. Please try again."
            ),
        ) from exc


# ================================================================
# Decision Resolution
# ================================================================

@app.post(
    "/decision",
    response_model=DecisionResponse,
    dependencies=[Depends(require_account)],
)
def resolve_decision(
    request: DecisionRequest,
) -> DecisionResponse:
    """
    Resume a paused LangGraph workflow after the developer
    resolves a decision conflict.
    """

    config = {
        "configurable": {
            "thread_id": request.session_id,
        }
    }

    try:
        # Resume the interrupted human decision node.
        result = neurodev_workflow.invoke(
            {
                "decision": request.decision,
                "reason": request.reason,
            },
            config={
                **config,
                "resume": {
                    "decision": request.decision,
                    "reason": request.reason,
                },
            },
        )

        final_decision = result.get(
            "final_decision",
            request.decision,
        )

        final_response = result.get(
            "final_response",
            f"Decision resolved: {final_decision}",
        )

        # Save final assistant response in short-term memory.
        memory_service.save_session_message(
            session_id=request.session_id,
            role="assistant",
            content=final_response,
        )

        return DecisionResponse(
            success=True,
            message=final_response,
            final_decision=final_decision,
            reason=request.reason,
            memory_saved=result.get(
                "memory_saved",
                False,
            ),
        )

    except Exception as exc:
        logger.exception(
            "Decision resolution failed."
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Decision resolution failed. Please try again."
            ),
        ) from exc


# ================================================================
# Memory Management
# ================================================================

@app.post(
    "/memory",
    response_model=MemoryResponse,
    dependencies=[Depends(require_account)],
)
def save_memory(
    request: MemoryRequest,
) -> MemoryResponse:
    """
    Manually save a piece of long-term project memory.
    """

    try:
        memory_id = memory_service.save_memory(
            project_id=request.project_id,
            content=request.content,
            memory_type=request.memory_type,
            metadata={
                **request.metadata,
                "session_id": request.session_id,
                "source": "developer",
            },
        )

        return MemoryResponse(
            success=True,
            message="Memory saved successfully.",
            memory_id=memory_id,
        )

    except Exception as exc:
        logger.exception(
            "Memory save failed."
        )

        raise HTTPException(
            status_code=500,
            detail="Memory save failed. Please try again.",
        ) from exc


# ================================================================
# Project Management
# ================================================================

@app.post(
    "/projects",
    response_model=ProjectResponse,
    dependencies=[Depends(require_account)],
)
def create_project(
    request: ProjectCreateRequest,
) -> ProjectResponse:

    project_id = str(uuid.uuid4())

    try:
        memory_service.create_project(
            project_id=project_id,
            name=request.name,
            description=request.description,
        )

        return ProjectResponse(
            project_id=project_id,
            name=request.name,
            description=request.description,
        )

    except Exception as exc:
        logger.exception(
            "Project creation failed."
        )

        raise HTTPException(
            status_code=500,
            detail="Project creation failed. Please try again.",
        ) from exc


# ================================================================
# Root Route
# ================================================================

@app.get("/", include_in_schema=False)
def root():
    """
    Redirect visitors to the NeuroDev frontend.
    """

    return RedirectResponse(
        url="/ui/",
        status_code=307,
    )
