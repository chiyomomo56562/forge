"""Reduce one L0 session into its single immutable L1 Episode."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from forge.domain.memory import (
    CibEvaluationStatus,
    Episode,
    Evaluation,
    EvaluationReason,
    EvaluationStatus,
    ExecutionOutcome,
    ExecutionResult,
    L0Event,
    L0EventType,
    MemoryOperationError,
    MemoryValidationError,
    PromotionEligibility,
    Reflection,
)
from forge.ports.outbound import EpisodeRepository, L0EventStore


class FinalizeEpisodeService:
    _EVIDENCE_TYPES = {
        L0EventType.SESSION_STARTED,
        L0EventType.PLAN_COMPLETED,
        L0EventType.EXECUTION_STARTED,
        L0EventType.EXECUTION_COMPLETED,
        L0EventType.EXECUTION_FAILED,
        L0EventType.EVALUATION_COMPLETED,
        L0EventType.REFLECTION_COMPLETED,
    }

    def __init__(self, store: L0EventStore, repository: EpisodeRepository) -> None:
        self._store = store
        self._repository = repository

    def handle(self, session_id: str) -> Episode:
        manifest = self._store.get_manifest(session_id)
        events = self._store.list_events(session_id)
        try:
            episode = self._build_episode(events, manifest.episode_id, session_id)
            saved = self._repository.save(episode)
        except MemoryOperationError as exc:
            self._record_terminal(
                session_id,
                L0EventType.EPISODE_FAILED,
                {"code": exc.code, "safe_message": exc.safe_message},
            )
            raise
        event_type = (
            L0EventType.EPISODE_PERSISTED
            if getattr(saved, "index_state", "indexed") == "indexed"
            else L0EventType.EPISODE_INDEX_DEFERRED
        )
        self._record_terminal(session_id, event_type, {"episode_id": saved.episode_id})
        self._record_terminal(
            session_id, L0EventType.SESSION_COMPLETED, {"episode_id": saved.episode_id}
        )
        self._store.mark_completed(session_id)
        return saved

    def _build_episode(
        self, events: tuple[L0Event, ...], episode_id: str, session_id: str
    ) -> Episode:
        terminals = [
            event
            for event in events
            if event.event_type == L0EventType.EXECUTION_COMPLETED
            and event.payload.get("is_terminal") is True
        ]
        if len(terminals) != 1:
            raise MemoryValidationError(
                code="l0.invalid_terminal_execution",
                safe_message="Exactly one terminal execution is required.",
            )
        started = self._one(events, L0EventType.SESSION_STARTED)
        evaluation = self._one(events, L0EventType.EVALUATION_COMPLETED)
        reflection = self._one(events, L0EventType.REFLECTION_COMPLETED)
        terminal = terminals[0]
        tools: list[str] = []
        for event in events:
            if event.event_type in {L0EventType.EXECUTION_COMPLETED, L0EventType.EXECUTION_FAILED}:
                for name in self._required(event.payload, "tool_names"):
                    if not isinstance(name, str):
                        raise MemoryValidationError(
                            code="l0.invalid_tool_names", safe_message="Tool names are invalid."
                        )
                    if name not in tools:
                        tools.append(name)
        reasons = tuple(
            EvaluationReason(metric=item["metric"], reason=item["reason"])
            for item in self._required(evaluation.payload, "reasons")
        )
        pattern_events = [
            event for event in events if event.event_type == L0EventType.PLAN_COMPLETED
        ]
        pattern_candidate_id = (
            pattern_events[-1].payload.get("pattern_candidate_id") if pattern_events else None
        )
        return Episode(
            episode_id=episode_id,
            session_id=session_id,
            task_request=self._required(started.payload, "task_request"),
            task_category=self._required(started.payload, "task_category"),
            execution=ExecutionResult(
                summary=self._required(terminal.payload, "summary"),
                outcome=self._enum(ExecutionOutcome, self._required(terminal.payload, "outcome")),
                tool_names=tuple(tools),
            ),
            evaluation=Evaluation(
                success_score=self._required(evaluation.payload, "success_score"),
                cib_score=self._required(evaluation.payload, "cib_score"),
                cib_evaluation_status=self._enum(
                    CibEvaluationStatus, self._required(evaluation.payload, "cib_evaluation_status")
                ),
                status=self._enum(EvaluationStatus, self._required(evaluation.payload, "status")),
                retryable=self._required(evaluation.payload, "retryable"),
                promotion_eligibility=self._enum(
                    PromotionEligibility,
                    self._required(evaluation.payload, "promotion_eligibility"),
                ),
                reasons=reasons,
            ),
            reflection=Reflection(
                what_worked=self._required(reflection.payload, "what_worked"),
                what_failed=self._required(reflection.payload, "what_failed"),
                next_hint=self._required(reflection.payload, "next_hint"),
                causal_condition=self._required(reflection.payload, "causal_condition"),
            ),
            raw_event_refs=tuple(
                event.event_id for event in events if event.event_type in self._EVIDENCE_TYPES
            ),
            evidence_complete=self._required(evaluation.payload, "evidence_complete"),
            created_at=datetime.now(UTC),
            pattern_candidate_id=pattern_candidate_id,
        )

    def _record_terminal(
        self, session_id: str, event_type: L0EventType, payload: Mapping[str, Any]
    ) -> None:
        manifest = self._store.get_manifest(session_id)
        self._store.append(
            L0Event(
                event_id=f"evt_{__import__('uuid').uuid4().hex}",
                session_id=session_id,
                episode_id=manifest.episode_id,
                sequence=manifest.next_sequence,
                occurred_at=datetime.now(UTC),
                event_type=event_type,
                payload=payload,
            )
        )

    @staticmethod
    def _one(events: tuple[L0Event, ...], event_type: L0EventType) -> L0Event:
        matches = [event for event in events if event.event_type == event_type]
        if len(matches) != 1:
            raise MemoryValidationError(
                code="l0.missing_required_event",
                safe_message=f"Exactly one {event_type.value} event is required.",
            )
        return matches[0]

    @staticmethod
    def _required(payload: Mapping[str, Any], key: str) -> Any:
        if key not in payload:
            raise MemoryValidationError(
                code="l0.missing_required_payload",
                safe_message="Required event payload is missing.",
            )
        return payload[key]

    @staticmethod
    def _enum(enum_type: type[Any], value: Any) -> Any:
        try:
            return enum_type(value)
        except (TypeError, ValueError) as exc:
            raise MemoryValidationError(
                code="l0.invalid_payload", safe_message="Event payload is invalid."
            ) from exc
