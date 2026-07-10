"""Runtime — session management, working memory, and loop execution context.

Manages the lifecycle of an inner-loop session:
    - Creates session-scoped working directories
    - Stages intermediate results (plan, execution, evaluation, reflection)
    - Provides :class:`ToolContext` for tool execution

Working directory layout::

    data/memory/working/sessions/{session_id}/
        plan.json
        execution.json
        evaluation.json
        reflection.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utils.ids import generate_session_id, generate_episode_id
from .utils.logging import get_logger
from .utils.serialization import write_json
from .tools.base import ToolContext

logger = get_logger("agent.runtime")


# ===========================================================================
# Working memory — intermediate results for a single loop cycle
# ===========================================================================

@dataclass
class WorkingMemory:
    """Intermediate results staged during one inner-loop cycle.

    Each field is populated as the loop progresses:
        1. plan — after planning stage
        2. execution — after execution stage
        3. evaluation — after evaluation stage
        4. reflection — after reflection stage
    """
    session_id: str = ""
    episode_id: str = ""
    user_input: str = ""
    task_category: str = "general"
    plan: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    evaluation: dict[str, Any] = field(default_factory=dict)
    reflection: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for JSON staging."""
        return {
            "session_id": self.session_id,
            "episode_id": self.episode_id,
            "user_input": self.user_input,
            "task_category": self.task_category,
            "plan": self.plan,
            "execution": self.execution,
            "evaluation": self.evaluation,
            "reflection": self.reflection,
            "retry_count": self.retry_count,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ===========================================================================
# Session
# ===========================================================================

@dataclass
class Session:
    """A single inner-loop session.

    Attributes:
        session_id: Unique session identifier.
        working_dir: Session-scoped working directory.
        working_memory: Intermediate results for the current cycle.
        created_at: ISO 8601 timestamp.
    """
    session_id: str
    working_dir: Path
    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    created_at: str = ""

    def stage(self, name: str, data: dict[str, Any]) -> Path:
        """Stage an intermediate result as ``{name}.json`` in the working dir.

        Args:
            name: File name without extension (e.g. ``"plan"``).
            data: JSON-serialisable dict.

        Returns:
            Path to the written file.
        """
        path = self.working_dir / f"{name}.json"
        write_json(path, data)
        logger.debug(f"Staged {name}.json for session {self.session_id}")
        return path

    def load_staged(self, name: str) -> dict[str, Any] | None:
        """Load a previously staged result.

        Args:
            name: File name without extension.

        Returns:
            Parsed dict, or ``None`` if the file does not exist.
        """
        path = self.working_dir / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def cleanup(self) -> None:
        """Remove the session working directory.

        Called after the loop completes and results are persisted to L1.
        """
        import shutil
        if self.working_dir.exists():
            shutil.rmtree(self.working_dir)
            logger.info(f"Cleaned up session {self.session_id}")


# ===========================================================================
# Runtime
# ===========================================================================

class Runtime:
    """Manages sessions and provides execution context.

    Args:
        base_working_dir: Root directory for session working dirs.
            Default: ``data/memory/working/sessions``.
    """

    def __init__(
        self,
        base_working_dir: str = "data/memory/working/sessions",
    ):
        self.base_working_dir = Path(base_working_dir)
        self.base_working_dir.mkdir(parents=True, exist_ok=True)
        self._active_sessions: dict[str, Session] = {}

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(self, user_input: str = "") -> Session:
        """Create a new session with a working directory.

        Args:
            user_input: The user's request that initiated this session.

        Returns:
            A new :class:`Session`.
        """
        session_id = generate_session_id()
        episode_id = generate_episode_id()
        working_dir = self.base_working_dir / session_id
        working_dir.mkdir(parents=True, exist_ok=True)

        now = _utc_now_iso()
        wm = WorkingMemory(
            session_id=session_id,
            episode_id=episode_id,
            user_input=user_input,
            started_at=now,
        )

        session = Session(
            session_id=session_id,
            working_dir=working_dir,
            working_memory=wm,
            created_at=now,
        )
        self._active_sessions[session_id] = session
        logger.info(f"Created session {session_id} (episode {episode_id})")
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve an active session by ID."""
        return self._active_sessions.get(session_id)

    def end_session(self, session_id: str, cleanup: bool = True) -> None:
        """End a session and optionally clean up its working directory.

        Args:
            session_id: The session to end.
            cleanup: If ``True``, remove the working directory.
        """
        session = self._active_sessions.pop(session_id, None)
        if session is None:
            logger.warning(f"Session not found: {session_id}")
            return

        session.working_memory.finished_at = _utc_now_iso()
        if cleanup:
            session.cleanup()

    # ------------------------------------------------------------------
    # Tool context
    # ------------------------------------------------------------------

    def make_tool_context(
        self,
        session: Session,
        sandbox: bool = True,
        user_confirm: Any = None,
    ) -> ToolContext:
        """Build a :class:`ToolContext` for a session.

        Args:
            session: The active session.
            sandbox: Whether to enforce sandbox restrictions.
            user_confirm: Optional HITL confirmation callback.

        Returns:
            A :class:`ToolContext` instance.
        """
        return ToolContext(
            session_id=session.session_id,
            working_dir=str(session.working_dir),
            sandbox=sandbox,
            user_confirm=user_confirm,
        )

    # ------------------------------------------------------------------
    # Staging helpers
    # ------------------------------------------------------------------

    def stage_plan(self, session: Session, plan: dict[str, Any]) -> Path:
        """Stage the plan result."""
        session.working_memory.plan = plan
        return session.stage("plan", plan)

    def stage_execution(self, session: Session, execution: dict[str, Any]) -> Path:
        """Stage the execution result."""
        session.working_memory.execution = execution
        return session.stage("execution", execution)

    def stage_evaluation(self, session: Session, evaluation: dict[str, Any]) -> Path:
        """Stage the evaluation result."""
        session.working_memory.evaluation = evaluation
        return session.stage("evaluation", evaluation)

    def stage_reflection(self, session: Session, reflection: dict[str, Any]) -> Path:
        """Stage the reflection result."""
        session.working_memory.reflection = reflection
        return session.stage("reflection", reflection)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def active_session_count(self) -> int:
        """Number of currently active sessions."""
        return len(self._active_sessions)

    def list_active_sessions(self) -> list[str]:
        """Return IDs of all active sessions."""
        return list(self._active_sessions.keys())


# ===========================================================================
# Helpers
# ===========================================================================

def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()