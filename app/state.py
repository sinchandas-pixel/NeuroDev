from typing import Any, TypedDict


class NeuroDevState(TypedDict, total=False):
    # --------------------------------------------------
    # User Input
    # --------------------------------------------------
    question: str
    session_id: str
    project_id: str
    images: list[str]
    documents: list[dict[str, str]]

    # --------------------------------------------------
    # Planning
    # --------------------------------------------------
    tasks: list[str]
    current_task: str

    # --------------------------------------------------
    # Retrieved Memory / Context
    # --------------------------------------------------
    retrieved_memories: list[dict[str, Any]]
    relevant_decisions: list[dict[str, Any]]
    context: str

    # --------------------------------------------------
    # Current Decision
    # --------------------------------------------------
    new_decision: str
    new_decision_reason: str

    # --------------------------------------------------
    # Historical Decision
    # --------------------------------------------------
    previous_decision: str
    previous_decision_reason: str

    # --------------------------------------------------
    # Reasoning
    # --------------------------------------------------
    comparison: str
    conflict_detected: bool
    conflict_type: str
    confidence_score: float

    # --------------------------------------------------
    # Human-in-the-Loop
    # --------------------------------------------------
    requires_user_decision: bool
    user_decision: str

    # --------------------------------------------------
    # Final Result
    # --------------------------------------------------
    final_decision: str
    final_response: str

    # --------------------------------------------------
    # Memory
    # --------------------------------------------------
    memory_to_store: dict[str, Any]
    memory_saved: bool

    # --------------------------------------------------
    # Agent Execution
    # --------------------------------------------------
    current_agent: str
    errors: list[str]