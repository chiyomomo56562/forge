import sys
from types import SimpleNamespace

from forge.adapters.inbound import cli
from forge.domain.conversation import AssistantReply


class FakeReceiveMessageService:
    def __init__(self) -> None:
        self.commands = []

    def handle(self, command):
        self.commands.append(command)
        return AssistantReply(text="chat response", model="fake-model")


def test_cli_single_query_uses_forge_bootstrap_without_agent_import(monkeypatch, capsys):
    before_agent_imports = {
        name for name in sys.modules if name == "agent" or name.startswith("agent.")
    }
    service = FakeReceiveMessageService()
    monkeypatch.setattr(cli, "build_receive_message_service", lambda *, config_path: service)

    assert (
        cli.main(
            [
                "--query",
                "hello",
                "--conversation-id",
                "thread-1",
                "--system",
                "brief",
                "--config",
                "none.yml",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    after_agent_imports = {
        name for name in sys.modules if name == "agent" or name.startswith("agent.")
    }
    assert captured.out == "chat response\n"
    assert captured.err == ""
    assert after_agent_imports == before_agent_imports
    assert service.commands[0].conversation_id == "thread-1"
    assert service.commands[0].system_instruction == "brief"


def test_run_message_returns_assistant_text(monkeypatch):
    service = FakeReceiveMessageService()
    monkeypatch.setattr(cli, "build_receive_message_service", lambda *, config_path: service)

    assert (
        cli.run_message("hello", conversation_id="thread-1", config_path="none.yml")
        == "chat response"
    )
    assert service.commands[0].conversation_id == "thread-1"


def test_cli_lists_and_approves_l3_drafts(monkeypatch, capsys):
    class ProceduralService:
        def __init__(self):
            self.approval = None

        def list_step_drafts(self, skill_id):
            assert skill_id == "skill_1"
            return (
                SimpleNamespace(
                    draft_id="draft_1",
                    source_episode_id="ep_1",
                    hint="inspect",
                    tool_name="git.status",
                ),
            )

        def approve_step_draft(self, skill_id, *, draft_id, step_id, tool_arguments):
            self.approval = (skill_id, draft_id, step_id, tool_arguments)
            return SimpleNamespace(skill_id=skill_id)

    service = ProceduralService()
    monkeypatch.setattr(cli, "build_procedural_memory_service", lambda _path: service)

    assert cli.main(["--list-l3-drafts", "--l3-skill-id", "skill_1"]) == 0
    assert '"draft_id": "draft_1"' in capsys.readouterr().out
    assert (
        cli.main(
            [
                "--approve-l3-draft",
                "draft_1",
                "--l3-skill-id",
                "skill_1",
                "--step-id",
                "status",
                "--tool-arguments",
                "{}",
            ]
        )
        == 0
    )
    assert service.approval == ("skill_1", "draft_1", "status", {})
