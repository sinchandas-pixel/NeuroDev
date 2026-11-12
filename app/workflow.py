from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents import (
    conflict_agent,
    decision_agent,
    decision_extraction_agent,
    memory_agent,
    reasoning_agent,
    response_agent,
    retrieval_agent,
)
from app.state import NeuroDevState


# ============================================================
# Routing Functions
# ============================================================

def route_after_extraction(
    state: NeuroDevState,
) -> str:
    """
    Decide whether the request contains a technical decision.
    """

    if state.get("new_decision"):
        return "reasoning"

    return "response"


def route_after_reasoning(
    state: NeuroDevState,
) -> str:
    """
    Decide whether historical reasoning produced a conflict.
    """

    if state.get("conflict_detected"):
        return "conflict"

    return "memory"


def route_after_decision(
    state: NeuroDevState,
) -> str:
    """
    Decide whether the workflow needs the developer's choice.

    If a conflict exists, the workflow pauses at the
    developer-facing decision node.

    Otherwise, it continues to memory.
    """

    if state.get("requires_user_decision"):
        return "human"

    return "memory"


# ============================================================
# Human-in-the-Loop Node
# ============================================================

def human_decision_node(state: NeuroDevState) -> dict:
    decision = interrupt(
        {
            "type": "decision_conflict",
            "message": "A conflict with a previous project decision was detected.",
            "previous_decision": state.get("previous_decision", ""),
            "previous_reason": state.get("previous_decision_reason", ""),
            "new_decision": state.get("new_decision", ""),
            "new_reason": state.get("new_decision_reason", ""),
            "conflict_type": state.get("conflict_type", ""),
            "confidence_score": state.get("confidence_score", 0.0),
        }
    )

    return {
        "user_decision": decision.get("decision", ""),
        "final_decision": decision.get("decision", ""),
        "new_decision_reason": decision.get(
            "reason",
            state.get("new_decision_reason", ""),
        ),
        "requires_user_decision": False,
        "current_agent": "human",
    }


# ============================================================
# Build Graph
# ============================================================

def build_workflow():
    """
    Construct the NeuroDev LangGraph workflow.
    """

    graph = StateGraph(NeuroDevState)

    # --------------------------------------------------------
    # Register Agents
    # --------------------------------------------------------

    graph.add_node(
        "retrieval",
        retrieval_agent,
    )

    graph.add_node(
        "decision_extraction",
        decision_extraction_agent,
    )

    graph.add_node(
        "reasoning",
        reasoning_agent,
    )

    graph.add_node(
        "conflict",
        conflict_agent,
    )

    graph.add_node(
        "decision",
        decision_agent,
    )

    graph.add_node(
        "memory",
        memory_agent,
    )

    graph.add_node(
        "response",
        response_agent,
    )

    graph.add_node(
        "human",
        human_decision_node,
    )

    # --------------------------------------------------------
    # Initial Flow
    # --------------------------------------------------------

    graph.add_edge(START, "retrieval")

    graph.add_edge(
        "retrieval",
        "decision_extraction",
    )

    # --------------------------------------------------------
    # Decision Detection
    # --------------------------------------------------------

    graph.add_conditional_edges(
        "decision_extraction",
        route_after_extraction,
        {
            "reasoning": "reasoning",
            "response": "response",
        },
    )

    # --------------------------------------------------------
    # Reasoning / Conflict Detection
    # --------------------------------------------------------

    graph.add_conditional_edges(
        "reasoning",
        route_after_reasoning,
        {
            "conflict": "conflict",
            "memory": "memory",
        },
    )

    # --------------------------------------------------------
    # Conflict Analysis
    # --------------------------------------------------------

    graph.add_edge(
        "conflict",
        "decision",
    )

    # --------------------------------------------------------
    # Human Decision Routing
    # --------------------------------------------------------

    graph.add_conditional_edges(
        "decision",
        route_after_decision,
        {
            "human": "human",
            "memory": "memory",
        },
    )

    # --------------------------------------------------------
    # Final Response
    # --------------------------------------------------------
    graph.add_edge("human", "memory")
    
    graph.add_edge(
        "memory",
        "response",
    )

    graph.add_edge(
        "response",
        END,
    )

    # Human node intentionally terminates the current run.
    # The API can resume the workflow after receiving the
    # developer's decision.
    
    graph.add_edge(
        "human",
        END,
    )

    return graph.compile()


# ============================================================
# Compiled Workflow
# ============================================================

neurodev_workflow = build_workflow()