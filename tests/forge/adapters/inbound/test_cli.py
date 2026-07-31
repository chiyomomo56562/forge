import sys

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
