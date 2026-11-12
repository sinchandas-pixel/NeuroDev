import base64
import binascii
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator # type: ignore


# ============================================================
# Chat / Developer Query
# ============================================================

class ImageAttachment(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    data_url: str = Field(..., max_length=7_000_000)

    @field_validator("data_url")
    @classmethod
    def validate_data_url(cls, value: str) -> str:
        try:
            header, encoded = value.split(",", maxsplit=1)
            if header not in {
                "data:image/jpeg;base64",
                "data:image/png;base64",
                "data:image/webp;base64",
            }:
                raise ValueError("Images must be JPEG, PNG, or WebP.")
            if len(encoded) > 6_000_000:
                raise ValueError("Each image must be 4.5 MB or smaller.")
            base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("The uploaded image data is invalid.") from exc
        return value


class PdfAttachment(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    data_base64: str = Field(..., min_length=8, max_length=14_000_000)

    @field_validator("data_base64")
    @classmethod
    def validate_pdf_data(cls, value: str) -> str:
        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("The uploaded PDF data is invalid.") from exc
        if not data.startswith(b"%PDF-"):
            raise ValueError("The uploaded file is not a valid PDF.")
        return value


class ChatRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        description="Developer's question, idea, or technical decision."
    )
    session_id: str = Field(
        ...,
        min_length=1,
        description="Unique conversation/session identifier."
    )
    project_id: str = Field(
        ...,
        min_length=1,
        description="Project associated with the conversation."
    )
    images: list[ImageAttachment] = Field(default_factory=list, max_length=3)
    pdfs: list[PdfAttachment] = Field(default_factory=list, max_length=3)


class ChatResponse(BaseModel):
    response: str
    session_id: str
    project_id: str

    conflict_detected: bool = False
    requires_user_decision: bool = False

    confidence_score: float | None = None
    previous_decision: str | None = None
    new_decision: str | None = None


class SignUpRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter your name.")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = normalize_email_address(value)
        local_part, domain = value.rsplit("@", maxsplit=1)
        suggested_domain = EMAIL_DOMAIN_TYPO_SUGGESTIONS.get(domain)
        if suggested_domain:
            raise ValueError(f"Check the email domain. Did you mean {local_part}@{suggested_domain}?")
        return value


EMAIL_DOMAIN_TYPO_SUGGESTIONS = {
    "gmail.co": "gmail.com",
    "gmail.con": "gmail.com",
    "gmail.cmo": "gmail.com",
    "gmail.om": "gmail.com",
    "gamil.com": "gmail.com",
    "gmial.com": "gmail.com",
    "gmai.com": "gmail.com",
    "yahoo.co": "yahoo.com",
    "yahoo.con": "yahoo.com",
    "yaho.com": "yahoo.com",
    "hotmai.com": "hotmail.com",
    "hotmail.co": "hotmail.com",
    "hotmail.con": "hotmail.com",
    "outlook.co": "outlook.com",
    "outlook.con": "outlook.com",
}


def normalize_email_address(value: str) -> str:
    value = value.strip().casefold()
    if re.fullmatch(
        r"[^@\s.](?:[^@\s]*[^@\s.])?@"
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
        value,
    ) is None:
        raise ValueError("Enter a valid email address.")
    return value


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return normalize_email_address(value)


class VerifyEmailRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=6, max_length=6)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return normalize_email_address(value)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not value.isascii() or not value.isdigit():
            raise ValueError("Enter the six-digit code from your email.")
        return value


class ResendVerificationRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return normalize_email_address(value)


class PasswordResetRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return normalize_email_address(value)


class PasswordResetCompleteRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return normalize_email_address(value)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not value.isascii() or not value.isdigit():
            raise ValueError("Enter the six-digit code from your email.")
        return value


# ============================================================
# Developer Decision
# ============================================================

class DecisionRequest(BaseModel):
    session_id: str
    project_id: str

    decision: str = Field(
        ...,
        min_length=1,
        description="Developer's choice when resolving a decision conflict."
    )

    reason: str | None = Field(
        default=None,
        description="Optional explanation for the developer's choice."
    )


class DecisionResponse(BaseModel):
    success: bool
    message: str

    final_decision: str
    reason: str | None = None

    memory_saved: bool = False


# ============================================================
# Memory
# ============================================================

class MemoryRequest(BaseModel):
    session_id: str
    project_id: str

    content: str = Field(
        ...,
        min_length=1,
        description="Information or decision to remember."
    )

    memory_type: str = Field(
        default="general",
        description="Type of memory, e.g. decision, fact, insight, conversation."
    )

    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryResponse(BaseModel):
    success: bool
    message: str
    memory_id: str | None = None


# ============================================================
# Project
# ============================================================

class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    description: str | None = None


class ProjectResponse(BaseModel):
    project_id: str
    name: str
    description: str | None = None


# ============================================================
# Health Check
# ============================================================

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    dependencies: dict[str, str] = Field(default_factory=dict)