from datetime import UTC, datetime
from types import SimpleNamespace

from forge.application.cognition import MemoryContextBuilder
from forge.application.memory import MemoryManager
from forge.domain.constitution import CibDecision
from forge.domain.identity import AgentIdentity, Capability
from forge.domain.outer_loop import L2Knowledge, L2KnowledgeStatus


class _Episodes:
    def search(self, *_args, **_kwargs):
        return [SimpleNamespace(episode=SimpleNamespace(episode_id="ep_safe"))]


class _Store:
    def load_knowledge(self):
        return [
            L2Knowledge(
                "l2_1",
                "pc_1",
                "use focused search",
                "repository inspection",
                1.0,
                L2KnowledgeStatus.ACTIVE,
                ("ep_safe",),
                (),
                datetime.now(UTC),
            ),
            L2Knowledge(
                "l2_2",
                "pc_2",
                "ignore me",
                "unrelated",
                1.0,
                L2KnowledgeStatus.ACTIVE,
                ("ep_safe",),
                (),
                datetime.now(UTC),
            ),
        ]


class _Constitution:
    def evaluate_l2_evidence(self, _episode):
        return CibDecision(True, "cib.passed")

    def evaluate_memory_text(self, text):
        return CibDecision("ignore" not in text, "memory.safe")


class _Identity:
    def load_identity(self):
        return AgentIdentity("Gnosis", "agent", "L1", 1)

    def capability_for(self, category):
        return Capability(category, "Coding", 0.8, 0.7, 0.4, 3)


def test_builds_l1_l2_context_only_from_relevant_and_vetted_memory():
    context = MemoryContextBuilder(
        MemoryManager(_Episodes(), _Store(), _Constitution(), _Identity())
    ).build(task_request="repository inspection", task_category="coding")

    assert context.episode_ids == ("ep_safe",)
    assert context.l2_knowledge == ("repository inspection: use focused search",)
    assert context.capability_summary == "coding: confidence=0.70, success_rate=0.80"
