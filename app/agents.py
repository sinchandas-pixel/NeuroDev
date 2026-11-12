from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq
from groq import Groq

from app.config import settings
from app.memory import memory_service
from app.reasoning import reasoning_service
from app.state import NeuroDevState


# ============================================================
# LLM
# ============================================================

llm = ChatGroq(
    api_key=settings.GROQ_API_KEY,
    model=settings.GROQ_MODEL,
    temperature=settings.GROQ_TEMPERATURE,
)

response_llm = ChatGroq(
    api_key=settings.GROQ_API_KEY,
    model=settings.GROQ_RESPONSE_MODEL,
    temperature=settings.GROQ_TEMPERATURE,
)


# ============================================================
# Planner Agent
# ============================================================

def planner_agent(state: NeuroDevState) -> dict[str, Any]:
    """
    Break the developer's request into logical tasks.
    """

    question = state["question"]

    prompt = f"""
You are the Planner Agent of NeuroDev.

Analyze the developer's request and break it into a small number
of logical tasks required to answer it.

Developer request:
{question}

Return ONLY valid JSON in this format:

{{
    "tasks": [
        "task 1",
        "task 2"
    ]
}}
"""

    response = llm.invoke([
        HumanMessage(content=prompt)
    ])

    try:
        result = json.loads(response.content)
        tasks = result.get("tasks", [])
    except (json.JSONDecodeError, TypeError):
        tasks = [question]

    return {
        "tasks": tasks,
        "current_task": tasks[0] if tasks else question,
        "current_agent": "planner",
    }


# ============================================================
# Retrieval Agent
# ============================================================

def retrieval_agent(state: NeuroDevState) -> dict[str, Any]:
    """
    Retrieve relevant information from all three memory layers:
    Redis, PostgreSQL and pgvector.
    """

    question = state["question"]
    project_id = state["project_id"]
    session_id = state["session_id"]

    memories = memory_service.retrieve_relevant_memory(
        project_id=project_id,
        session_id=session_id,
        query=question,
        limit=settings.MAX_RETRIEVAL_RESULTS,
    )

    decisions = memories.get("decisions", [])
    semantic = memories.get("semantic", [])
    short_term = memories.get("short_term", [])

    context_parts = []

    if short_term:
        context_parts.append(
            "RECENT CONVERSATION:\n"
            + "\n".join(
                f'{item["role"]}: {item["content"]}'
                for item in short_term
            )
        )

    if decisions:
        context_parts.append(
            "HISTORICAL DECISIONS:\n"
            + "\n".join(
                (
                    f'Decision: {item["decision"]}\n'
                    f'Reason: {item["reason"]}\n'
                    f'Source: {item["source"]}\n'
                    f'Confidence: {item["confidence"]}'
                )
                for item in decisions
            )
        )

    if semantic:
        context_parts.append(
            "SEMANTICALLY RELEVANT MEMORY:\n"
            + "\n".join(
                (
                    f'{item["content"]}\n'
                    f'Similarity: {item["similarity"]}'
                )
                for item in semantic
            )
        )

    context = "\n\n".join(context_parts)

    return {
        "retrieved_memories": semantic,
        "relevant_decisions": decisions,
        "context": context,
        "current_agent": "retrieval",
    }


# ============================================================
# Decision Extraction Agent
# ============================================================

def decision_extraction_agent(
    state: NeuroDevState,
) -> dict[str, Any]:
    """
    Determine whether the developer's input contains a
    technical/project decision.
    """

    question = state["question"]

    prompt = f"""
You are NeuroDev's Decision Extraction Agent.

Determine whether the following developer message contains
a technical decision or proposed decision.

Message:
{question}

Return ONLY valid JSON:

{{
    "is_decision": true,
    "decision": "short decision statement",
    "reason": "reason behind the decision"
}}

If it is not a decision:

{{
    "is_decision": false,
    "decision": "",
    "reason": ""
}}
"""

    response = llm.invoke([
        HumanMessage(content=prompt)
    ])

    try:
        result = json.loads(response.content)
    except (json.JSONDecodeError, TypeError):
        result = {
            "is_decision": False,
            "decision": "",
            "reason": "",
        }

    if not result.get("is_decision"):
        return {
            "current_agent": "decision_extraction",
        }

    return {
        "new_decision": result.get("decision", ""),
        "new_decision_reason": result.get("reason", ""),
        "current_agent": "decision_extraction",
    }


# ============================================================
# Reasoning Agent
# ============================================================

def reasoning_agent(
    state: NeuroDevState,
) -> dict[str, Any]:
    """
    Compare the new decision against the most relevant
    historical decision.
    """

    new_decision = state.get("new_decision", "")

    if not new_decision:
        return {
            "conflict_detected": False,
            "requires_user_decision": False,
            "current_agent": "reasoning",
        }

    decisions = state.get(
        "relevant_decisions",
        [],
    )

    if not decisions:
        return {
            "conflict_detected": False,
            "requires_user_decision": False,
            "current_agent": "reasoning",
        }

    previous = decisions[0]

    previous_decision = previous.get(
        "decision",
        "",
    )

    previous_reason = previous.get(
        "reason",
        "",
    )

    comparison = reasoning_service.compare_decisions(
        new_decision=new_decision,
        new_reason=state.get(
            "new_decision_reason",
            "",
        ),
        previous_decision=previous_decision,
        previous_reason=previous_reason,
    )

    previous_confidence = float(
        previous.get(
            "confidence",
            0.0,
        )
    )

    decision_context = reasoning_service.build_decision_context(
        new_decision=new_decision,
        new_reason=state.get(
            "new_decision_reason",
            "",
        ),
        previous_decision=previous_decision,
        previous_reason=previous_reason,
        comparison=comparison,
        previous_confidence=previous_confidence,
    )

    return {
        "previous_decision": previous_decision,
        "previous_decision_reason": previous_reason,
        "comparison": json.dumps(
            decision_context,
            default=str,
        ),
        "conflict_detected": decision_context[
            "conflict_detected"
        ],
        "conflict_type": decision_context[
            "conflict_type"
        ],
        "confidence_score": previous_confidence,
        "requires_user_decision": decision_context[
            "requires_user_decision"
        ],
        "current_agent": "reasoning",
    }


# ============================================================
# Conflict / Explanation Agent
# ============================================================

def conflict_agent(
    state: NeuroDevState,
) -> dict[str, Any]:
    """
    Use the LLM to explain why two decisions conflict.

    This does not make the final decision. It prepares an
    explanation for the developer.
    """

    if not state.get("conflict_detected"):
        return {
            "current_agent": "conflict",
        }

    prompt = f"""
You are NeuroDev's Conflict Analysis Agent.

A developer has proposed a new technical decision that may
conflict with an earlier project decision.

Previous decision:
{state.get("previous_decision", "")}

Previous reason:
{state.get("previous_decision_reason", "")}

New decision:
{state.get("new_decision", "")}

New reason:
{state.get("new_decision_reason", "")}

Conflict type:
{state.get("conflict_type", "")}

Confidence in historical decision:
{state.get("confidence_score", 0)}

Explain:
1. What the previous decision was.
2. What the new decision proposes.
3. Why they conflict.
4. The practical implications of both choices.

Do NOT choose which decision is correct.
The developer must make that decision.
"""

    response = llm.invoke([
        HumanMessage(content=prompt)
    ])

    return {
        "context": (
            state.get("context", "")
            + "\n\nCONFLICT ANALYSIS:\n"
            + response.content
        ),
        "requires_user_decision": True,
        "current_agent": "conflict",
    }


# ============================================================
# Decision Agent
# ============================================================

def decision_agent(
    state: NeuroDevState,
) -> dict[str, Any]:
    """
    Prepare the developer-facing response.

    If there is a conflict, NeuroDev explicitly asks the
    developer to resolve it instead of automatically
    overwriting historical knowledge.
    """

    if state.get("requires_user_decision"):
        response = (
            "⚠️ Decision conflict detected.\n\n"
            f"Previous decision:\n"
            f"{state.get('previous_decision', 'N/A')}\n\n"
            f"Reason:\n"
            f"{state.get('previous_decision_reason', 'N/A')}\n\n"
            f"New decision:\n"
            f"{state.get('new_decision', 'N/A')}\n\n"
            f"New reason:\n"
            f"{state.get('new_decision_reason', 'N/A')}\n\n"
            f"Analysis:\n"
            f"{state.get('context', '')}\n\n"
            "Which decision should NeuroDev follow?"
        )

        return {
            "final_response": response,
            "current_agent": "decision",
        }

    return {
        "current_agent": "decision",
    }


# ============================================================
# Memory Agent
# ============================================================

def memory_agent(
    state: NeuroDevState,
) -> dict[str, Any]:
    """
    Store finalized project knowledge.

    Conflicting decisions are NOT automatically written as
    replacements. The developer must resolve them first.
    """

    if state.get("requires_user_decision"):
        return {
            "memory_saved": False,
            "current_agent": "memory",
        }

    project_id = state["project_id"]

    content = state.get("final_decision") or state.get("new_decision") or state["question"]
    reason = state.get("new_decision_reason", "")


    memory_id = memory_service.save_memory(
        project_id=project_id,
        content=content,
        memory_type="decision",
        metadata={
            "reason": reason,
            "source": "developer",
            "provenance": {
                "session_id": state["session_id"],
                "original_question": state["question"],
            },
        },
    )

    if state.get("new_decision"):
        memory_service.save_decision(
            project_id=project_id,
            decision=content,
            reason=reason,
            source="developer",
            confidence=1.0,
            metadata={
                "memory_id": memory_id,
                "session_id": state["session_id"],
                "resolved_by": "developer" if state.get("user_decision") else "original_decision",
            },
        )

    return {
        "memory_to_store": {
            "memory_id": memory_id,
            "content": content,
            "reason": reason,
        },
        "memory_saved": True,
        "current_agent": "memory",
    }


# ============================================================
# Response Agent
# ============================================================

def response_agent(
    state: NeuroDevState,
) -> dict[str, Any]:
    """
    Generate the final developer-facing response for
    non-conflicting/general requests.
    """

    if state.get("final_response"):
        return {
            "current_agent": "response",
        }

    prompt = f"""
You are NeuroDev, a helpful general-purpose AI assistant and an expert
development partner. Answer the developer's actual question directly, whether
it is about programming, general knowledge, writing, science, or another topic.

Developer:
{state["question"]}

Project context:
{state.get("context") or "No relevant project context was found."}

Attached PDF documents:
{chr(10).join(f'--- {document["name"]} ---{chr(10)}{document["text"]}' for document in state.get("documents", [])) or "No PDFs attached."}

Important:
- Use project context only when it is relevant to the question; it is optional
  background, not the limit of what you know.
- For general-knowledge questions, answer from your knowledge even when the
  project context is empty or unrelated.
- If historical project decisions are relevant, respect their reasoning. Do
  not invent project-specific history or claim that a general fact came from
  project context.
- Answer naturally, clearly, and helpfully. Explain enough to be useful and
  adapt the detail to the question.
- Be honest about uncertainty. For facts that may have changed recently and
  cannot be verified, state that limitation instead of guessing or refusing
  solely because the answer is absent from project context.
"""

    images = state.get("images", [])
    if images:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": image},
            }
            for image in images
        )
        response = Groq(api_key=settings.GROQ_API_KEY).chat.completions.create(
            model=settings.GROQ_VISION_MODEL,
            messages=[{"role": "user", "content": content}],
            temperature=settings.GROQ_TEMPERATURE,
            max_completion_tokens=1024,
        ).choices[0].message.content
        if not response:
            raise RuntimeError("The vision model returned an empty response.")
    else:
        response = response_llm.invoke([
            HumanMessage(content=prompt)
        ]).content

    return {
        "final_response": response,
        "current_agent": "response",
    }