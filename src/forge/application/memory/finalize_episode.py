"""하나의 L0 세션을 불변 L1 Episode로 축약한다.

최종 수정일: 2026-07-31
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from forge.domain.memory import (
    CibEvaluationStatus,
    Episode,
    EpisodeStatus,
    Evaluation,
    EvaluationReason,
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
    """근거 이벤트만 읽어 단일 L1 Episode를 만들고 저장 결과 이벤트를 기록한다.

    최종 수정일: 2026-07-31
    """

    _EVIDENCE_TYPES = {
        L0EventType.SESSION_STARTED,
        L0EventType.PLAN_COMPLETED,
        L0EventType.EXECUTION_STARTED,
        L0EventType.EXECUTION_COMPLETED,
        L0EventType.EXECUTION_FAILED,
        L0EventType.EXECUTION_SUMMARY_COMPLETED,
        L0EventType.EVALUATION_COMPLETED,
        L0EventType.REFLECTION_COMPLETED,
    }

    def __init__(self, store: L0EventStore, repository: EpisodeRepository) -> None:
        """L0 reader/writer와 L1 정본 repository를 주입한다.

        Args:
            store: 원본 L0 이벤트와 완료 상태를 관리하는 outbound port.
            repository: 완성된 Episode를 영속화할 L1 정본 port.

        최종 수정일: 2026-07-31
        """
        self._store = store
        self._repository = repository

    def handle(self, session_id: str) -> Episode:
        """세션의 L0 근거를 Finalize하고 결과 이벤트·종료 상태를 남긴다.

        Args:
            session_id: 단일 Episode로 확정할 Inner Loop 세션 ID.

        Returns:
            L1 repository가 저장 완료로 반환한 Episode.

        Raises:
            MemoryOperationError: L0 근거가 부족하거나 L1 저장이 실패할 때. 실패 내용은
                ``episode.failed`` 이벤트의 safe code/message으로만 별도 기록한다.

        최종 수정일: 2026-07-31
        """
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
            if saved.index_state.value == "indexed"
            else L0EventType.EPISODE_INDEX_DEFERRED
        )
        self._record_terminal(session_id, event_type, {"episode_id": saved.episode_id})
        self._record_terminal(
            session_id, L0EventType.SESSION_COMPLETED, {"episode_id": saved.episode_id}
        )
        self._store.mark_completed(session_id)
        return episode

    def _build_episode(
        self, events: tuple[L0Event, ...], episode_id: str, session_id: str
    ) -> Episode:
        """근거 이벤트 payload를 L1 도메인 값으로 매핑하고 execution을 집계한다.

        Args:
            events: Finalize 이전에 기록된 sequence 순서의 L0 이벤트.
            episode_id: 세션 시작 시 미리 배정한 L1 Episode ID.
            session_id: L1 Episode에 보존할 세션 ID.

        Returns:
            아직 repository에 저장되지 않은 검증된 Episode.

        최종 수정일: 2026-07-31
        """
        terminals = [
            event
            for event in events
            if event.event_type == L0EventType.EXECUTION_SUMMARY_COMPLETED
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
            if event.event_type in {
                L0EventType.EXECUTION_COMPLETED,
                L0EventType.EXECUTION_FAILED,
                L0EventType.EXECUTION_SUMMARY_COMPLETED,
            }:
                for name in self._required(event.payload, "tool_names"):
                    if not isinstance(name, str):
                        raise MemoryValidationError(
                            code="l0.invalid_tool_names", safe_message="Tool names are invalid."
                        )
                    if name not in tools:
                        tools.append(name)
        reasons = tuple(
            EvaluationReason(**item) for item in self._required(evaluation.payload, "reasons")
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
                result_quality=self._required(evaluation.payload, "result_quality"),
                requirement_coverage=self._required(evaluation.payload, "requirement_coverage"),
                verification_confidence=self._required(
                    evaluation.payload, "verification_confidence"
                ),
                success_score=self._required(evaluation.payload, "success_score"),
                pain_index=self._required(evaluation.payload, "pain_index"),
                retry_ratio=self._required(evaluation.payload, "retry_ratio"),
                tool_error_ratio=self._required(evaluation.payload, "tool_error_ratio"),
                budget_overrun_ratio=self._required(evaluation.payload, "budget_overrun_ratio"),
                rework_ratio=self._required(evaluation.payload, "rework_ratio"),
                human_intervention_ratio=self._required(
                    evaluation.payload, "human_intervention_ratio"
                ),
                cib_score=self._required(evaluation.payload, "cib_score"),
                cib_evaluation_status=self._enum(
                    CibEvaluationStatus, self._required(evaluation.payload, "cib_evaluation_status")
                ),
                status=self._enum(EpisodeStatus, self._required(evaluation.payload, "status")),
                promotion_eligibility=self._enum(
                    PromotionEligibility,
                    self._required(evaluation.payload, "promotion_eligibility"),
                ),
                retryable=self._required(evaluation.payload, "retryable"),
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
        """Finalize 결과를 나타내는 L1 이후 이벤트를 append한다.

        Args:
            session_id: 결과 이벤트를 기록할 세션 ID.
            event_type: persisted, index_deferred, failed, completed 중 기록할 유형.
            payload: 민감 원인을 제외한 결과 metadata.

        최종 수정일: 2026-07-31
        """
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
        """특정 유형의 완료 이벤트가 정확히 한 건인지 확인한다.

        Args:
            events: 검사할 세션 이벤트.
            event_type: 정확히 한 건이어야 하는 이벤트 유형.

        Returns:
            유일하게 발견한 이벤트.

        최종 수정일: 2026-07-31
        """
        matches = [event for event in events if event.event_type == event_type]
        if len(matches) != 1:
            raise MemoryValidationError(
                code="l0.missing_required_event",
                safe_message=f"Exactly one {event_type.value} event is required.",
            )
        return matches[0]

    @staticmethod
    def _required(payload: Mapping[str, Any], key: str) -> Any:
        """L1 복원에 필요한 payload key를 읽고 누락을 typed error로 바꾼다.

        Args:
            payload: 조회할 이벤트 payload.
            key: 반드시 존재해야 하는 field 이름.

        Returns:
            payload에 들어 있던 원본 값.

        최종 수정일: 2026-07-31
        """
        if key not in payload:
            raise MemoryValidationError(
                code="l0.missing_required_payload",
                safe_message="Required event payload is missing.",
            )
        return payload[key]

    @staticmethod
    def _enum(enum_type: type[Any], value: Any) -> Any:
        """문자열 payload를 대상 enum으로 안전하게 변환한다.

        Args:
            enum_type: 변환 대상 ``StrEnum`` 클래스.
            value: producer가 기록한 문자열 값.

        Returns:
            변환된 enum 구성원.

        최종 수정일: 2026-07-31
        """
        try:
            return enum_type(value)
        except (TypeError, ValueError) as exc:
            raise MemoryValidationError(
                code="l0.invalid_payload", safe_message="Event payload is invalid."
            ) from exc
