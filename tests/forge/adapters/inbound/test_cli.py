import sys

from forge.adapters.inbound import cli
from forge.domain.conversation import ModelReply


class FakeInitialInputService:
    def handle(self, command):
        assert command.text == "hello"
        return ModelReply(text="chat response", model="gpt-5.6-terra")


def test_cli_single_query_uses_forge_bootstrap_without_agent_import(monkeypatch, capsys):
    before_agent_imports = {name for name in sys.modules if name == "agent" or name.startswith("agent.")}

    monkeypatch.setattr(
        cli,
        "build_initial_input_service",
        lambda *, config_path: FakeInitialInputService(),
    )

    assert cli.main(["--query", "hello", "--config", "nonexistent.yml"]) == 0

    captured = capsys.readouterr()
    after_agent_imports = {name for name in sys.modules if name == "agent" or name.startswith("agent.")}
    assert captured.out == "chat response\n"
    assert captured.err == ""
    assert after_agent_imports == before_agent_imports


def test_run_initial_input_prints_model_text(monkeypatch):
    monkeypatch.setattr(
        cli,
        "build_initial_input_service",
        lambda *, config_path: FakeInitialInputService(),
    )

    assert cli.run_initial_input("hello", config_path="nonexistent.yml") == "chat response"
