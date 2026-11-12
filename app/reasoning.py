from __future__ import annotations

from typing import Any

from app.config import settings


class ReasoningService:
    """
    Core decision-reasoning engine for NeuroDev.

    Responsibilities:
    - Compare new decisions with historical decisions
    - Detect contradictions
    - Classify the conflict
    - Calculate confidence
    - Prepare a human-in-the-loop decision
    """

    # ======================================================
    # Decision Comparison
    # ======================================================

    def compare_decisions(
        self,
        new_decision: str,
        new_reason: str,
        previous_decision: str,
        previous_reason: str,
    ) -> dict[str, Any]:
        """
        Compare a new technical decision with a previous one.

        This method intentionally provides the deterministic
        reasoning layer. The LLM can later be used by the
        Reasoning Agent for deeper semantic analysis.
        """

        new_text = self._normalize(
            f"{new_decision} {new_reason}"
        )

        old_text = self._normalize(
            f"{previous_decision} {previous_reason}"
        )

        if not new_text or not old_text:
            return {
                "conflict_detected": False,
                "conflict_type": "insufficient_information",
                "similarity": 0.0,
            }

        similarity = self._text_similarity(
            new_text,
            old_text,
        )

        same_decision = (
            self._normalize(new_decision)
            == self._normalize(previous_decision)
        )

        if same_decision:
            conflict = False
            conflict_type = "consistent"
        elif self._contains_contradiction(
            new_decision,
            previous_decision,
        ):
            conflict = True
            conflict_type = "direct_contradiction"
        elif similarity >= 0.25:
            conflict = True
            conflict_type = "potential_conflict"
        else:
            conflict = False
            conflict_type = "unrelated"

        return {
            "conflict_detected": conflict,
            "conflict_type": conflict_type,
            "similarity": round(similarity, 3),
            "same_decision": same_decision,
        }

    # ======================================================
    # Contradiction Detection
    # ======================================================

    def _contains_contradiction(
        self,
        new_decision: str,
        previous_decision: str,
    ) -> bool:
        """
        Detect obvious opposing decisions.

        This is a lightweight first-pass detector.
        The LLM-based Reasoning Agent will perform deeper
        semantic contradiction analysis later.
        """

        new = self._normalize(new_decision)
        old = self._normalize(previous_decision)

        contradiction_pairs = [
            ("use", "avoid"),
            ("enable", "disable"),
            ("allow", "block"),
            ("add", "remove"),
            ("keep", "remove"),
            ("replace", "keep"),
            ("switch", "keep"),
            ("postgresql", "mongodb"),
            ("mongodb", "postgresql"),
            ("redis", "memcached"),
            ("rest", "graphql"),
            ("graphql", "rest"),
        ]

        for first, second in contradiction_pairs:
            if first in new and second in old:
                return True

            if second in new and first in old:
                return True

        return False

    # ======================================================
    # Confidence Scoring
    # ======================================================

    def calculate_confidence(
        self,
        source: str,
        similarity: float = 0.0,
        has_reason: bool = False,
    ) -> float:
        """
        Calculate confidence in a historical decision.

        NeuroDev does not treat every memory as equally reliable.
        Source reliability is combined with semantic relevance
        and the presence of reasoning/provenance.
        """

        source_weights = {
            "developer": 1.00,
            "project_decision": 0.95,
            "documentation": 0.85,
            "verified": 0.90,
            "agent": 0.75,
            "conversation": 0.65,
            "unknown": 0.50,
        }

        base_score = source_weights.get(
            source.lower(),
            source_weights["unknown"],
        )

        similarity_score = min(
            max(similarity, 0.0),
            1.0,
        )

        confidence = (
            base_score * 0.60
            + similarity_score * 0.25
            + (0.15 if has_reason else 0.0)
        )

        return round(
            min(confidence, 1.0),
            3,
        )

    # ======================================================
    # Conflict Evaluation
    # ======================================================

    def evaluate_conflict(
        self,
        comparison: dict[str, Any],
        previous_confidence: float,
    ) -> dict[str, Any]:
        """
        Decide whether a detected difference is important
        enough to require developer intervention.
        """

        conflict_detected = comparison.get(
            "conflict_detected",
            False,
        )

        conflict_type = comparison.get(
            "conflict_type",
            "unknown",
        )

        similarity = comparison.get(
            "similarity",
            0.0,
        )

        if not conflict_detected:
            return {
                "requires_user_decision": False,
                "severity": "none",
                "reason": "No conflicting historical decision found.",
            }

        if conflict_type == "direct_contradiction":
            severity = "high"
        elif conflict_type == "potential_conflict":
            severity = "medium"
        else:
            severity = "low"

        requires_user_decision = (
            severity in {"high", "medium"}
            and previous_confidence
            >= settings.CONFIDENCE_THRESHOLD
        )

        return {
            "requires_user_decision": requires_user_decision,
            "severity": severity,
            "reason": (
                "A historical decision with sufficient confidence "
                "conflicts with the new decision."
                if requires_user_decision
                else "Conflict exists but historical confidence "
                     "is below the intervention threshold."
            ),
            "similarity": similarity,
        }

    # ======================================================
    # Decision Context
    # ======================================================

    def build_decision_context(
        self,
        new_decision: str,
        new_reason: str,
        previous_decision: str,
        previous_reason: str,
        comparison: dict[str, Any],
        previous_confidence: float,
    ) -> dict[str, Any]:
        """
        Build the structured reasoning result consumed by
        the LangGraph Decision Agent.
        """

        evaluation = self.evaluate_conflict(
            comparison=comparison,
            previous_confidence=previous_confidence,
        )

        return {
            "new_decision": new_decision,
            "new_reason": new_reason,
            "previous_decision": previous_decision,
            "previous_reason": previous_reason,
            "previous_confidence": previous_confidence,
            "comparison": comparison,
            "conflict_detected": comparison[
                "conflict_detected"
            ],
            "conflict_type": comparison[
                "conflict_type"
            ],
            "requires_user_decision": evaluation[
                "requires_user_decision"
            ],
            "severity": evaluation["severity"],
            "evaluation_reason": evaluation["reason"],
        }

    # ======================================================
    # Developer Resolution
    # ======================================================

    def resolve_decision(
        self,
        selected_decision: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """
        Create the final decision after the developer
        resolves a conflict.
        """

        return {
            "final_decision": selected_decision,
            "reason": reason,
            "resolved_by": "developer",
            "resolved": True,
        }

    # ======================================================
    # Utility Functions
    # ======================================================

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(
            text.lower().strip().split()
        )

    @staticmethod
    def _text_similarity(
        first: str,
        second: str,
    ) -> float:
        """
        Lightweight token-overlap similarity.

        Semantic similarity will ultimately be handled by
        BGE-M3 + pgvector retrieval. This method is only
        used for the reasoning layer's initial comparison.
        """

        first_tokens = set(first.split())
        second_tokens = set(second.split())

        if not first_tokens or not second_tokens:
            return 0.0

        intersection = first_tokens.intersection(
            second_tokens
        )

        union = first_tokens.union(
            second_tokens
        )

        return len(intersection) / len(union)


# ============================================================
# Singleton
# ============================================================

reasoning_service = ReasoningService()