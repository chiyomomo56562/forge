"""Unit tests for the LLM client layer (Phase 2.1 — pulled forward)."""

from __future__ import annotations

import pytest


# ===========================================================================
# LLMConfig
# ===========================================================================

class TestLLMConfig:
    def test_defaults(self):
        from agent.llm.client import LLMConfig

        cfg = LLMConfig()
        assert cfg.backend == "ollama"
        assert cfg.ollama_model == "glm-5.2"
        assert cfg.openai_model == "gpt-4o"
        assert cfg.embed_backend == "local"
        assert cfg.embed_dimension == 384

    def test_from_yaml(self, tmp_path):
        import yaml
        from agent.llm.client import LLMConfig

        config = {
            "version": 1,
            "llm": {
                "backend": "openai",
                "ollama": {"base_url": "http://remote:11434", "model": "glm-5.2", "temperature": 0.5},
                "openai": {"model": "gpt-4o-mini", "temperature": 0.7},
            },
            "embedding": {
                "backend": "ollama",
                "model": "nomic-embed-text",
                "dimension": 768,
                "ollama": {"base_url": "http://remote:11434", "model": "nomic-embed-text", "dimension": 768},
            },
        }
        path = tmp_path / "agent.yml"
        with path.open("w") as f:
            yaml.dump(config, f)

        cfg = LLMConfig.from_yaml(str(path))
        assert cfg.backend == "openai"
        assert cfg.ollama_base_url == "http://remote:11434"
        assert cfg.ollama_temperature == 0.5
        assert cfg.openai_model == "gpt-4o-mini"
        assert cfg.openai_temperature == 0.7
        assert cfg.embed_backend == "ollama"
        assert cfg.embed_dimension == 768
        assert cfg.embed_ollama_dimension == 768

    def test_from_yaml_missing_file(self):
        from agent.llm.client import LLMConfig

        cfg = LLMConfig.from_yaml("nonexistent.yml")
        assert cfg.backend == "ollama"  # Defaults


# ===========================================================================
# LLMClient — Embedding
# ===========================================================================

class TestLLMClientEmbed:
    def test_embed_empty_text(self):
        from agent.llm.client import LLMClient, LLMConfig

        cfg = LLMConfig(embed_backend="local", embed_dimension=64, embed_cache_dir=None)
        client = LLMClient(config=cfg)
        vec = client.embed("")
        assert vec == [0.0] * 64

    def test_embed_whitespace_text(self):
        from agent.llm.client import LLMClient, LLMConfig

        cfg = LLMConfig(embed_backend="local", embed_dimension=64, embed_cache_dir=None)
        client = LLMClient(config=cfg)
        vec = client.embed("   ")
        assert vec == [0.0] * 64

    def test_embed_hash_fallback(self):
        from agent.llm.client import LLMClient, LLMConfig

        cfg = LLMConfig(embed_backend="local", embed_model="nonexistent", embed_dimension=128, embed_cache_dir=None)
        client = LLMClient(config=cfg)
        vec = client.embed("test text")
        assert len(vec) == 128
        # Should be normalized
        norm = sum(v * v for v in vec) ** 0.5
        assert 0.99 <= norm <= 1.01

    def test_embed_deterministic(self):
        from agent.llm.client import LLMClient, LLMConfig

        cfg = LLMConfig(embed_backend="local", embed_dimension=64, embed_cache_dir=None)
        client = LLMClient(config=cfg)
        v1 = client.embed("same text")
        v2 = client.embed("same text")
        assert v1 == v2

    def test_embed_cache(self, tmp_path):
        from agent.llm.client import LLMClient, LLMConfig

        cfg = LLMConfig(embed_backend="local", embed_dimension=64, embed_cache_dir=str(tmp_path / "cache"))
        client = LLMClient(config=cfg)
        v1 = client.embed("cached text")
        v2 = client.embed("cached text")
        assert v1 == v2

    def test_embed_unknown_backend_raises(self):
        from agent.llm.client import LLMClient, LLMConfig

        cfg = LLMConfig(embed_backend="invalid", embed_dimension=64, embed_cache_dir=None)
        client = LLMClient(config=cfg)
        with pytest.raises(ValueError, match="Unknown embedding backend"):
            client.embed("test")

    def test_embed_ollama_dimension(self):
        from agent.llm.client import LLMClient, LLMConfig

        cfg = LLMConfig(embed_backend="ollama", embed_ollama_dimension=768, embed_cache_dir=None)
        client = LLMClient(config=cfg)
        # Empty text should return zeros of ollama dimension
        vec = client.embed("")
        assert len(vec) == 768


# ===========================================================================
# LLMClient — Chat
# ===========================================================================

class TestLLMClientChat:
    def test_chat_fallback_no_sdk(self):
        from agent.llm.client import LLMClient, LLMConfig

        cfg = LLMConfig(backend="ollama", ollama_base_url="http://nonexistent:99999")
        client = LLMClient(config=cfg)
        # Should return fallback response, not crash
        response = client.chat("Hello")
        assert response.content is not None
        assert "LLM not available" in response.content or len(response.content) > 0

    def test_chat_unknown_backend_raises(self):
        from agent.llm.client import LLMClient, LLMConfig

        cfg = LLMConfig(backend="invalid")
        client = LLMClient(config=cfg)
        with pytest.raises(ValueError, match="Unknown LLM backend"):
            client.chat("test")

    def test_chat_with_system_prompt(self):
        from agent.llm.client import LLMClient, LLMConfig

        cfg = LLMConfig(backend="ollama", ollama_base_url="http://nonexistent:99999")
        client = LLMClient(config=cfg)
        response = client.chat("Hello", system="You are a test bot.")
        assert response.content is not None


# ===========================================================================
# Prompts
# ===========================================================================

class TestPrompts:
    def test_get_template(self):
        from agent.llm.prompts import get_template

        t = get_template("planning")
        assert t.name == "planning"
        assert "user_input" in t.template

    def test_get_template_unknown(self):
        from agent.llm.prompts import get_template

        with pytest.raises(KeyError, match="Unknown prompt template"):
            get_template("nonexistent")

    def test_template_render(self):
        from agent.llm.prompts import get_template

        t = get_template("reflection")
        rendered = t.render(
            task="test task",
            execution_summary="test summary",
            evaluation="Success",
            pain_index="0.1",
        )
        assert "test task" in rendered
        assert "test summary" in rendered

    def test_template_render_missing_var(self):
        from agent.llm.prompts import get_template

        t = get_template("reflection")
        with pytest.raises(ValueError, match="Missing variable"):
            t.render(task="only task")

    def test_all_templates_registered(self):
        from agent.llm.prompts import TEMPLATES

        expected = {"planning", "execution", "cib_evaluation", "phoenix_auditor", "reflection", "scenario_draft"}
        assert set(TEMPLATES.keys()) == expected


# ===========================================================================
# Response Parser
# ===========================================================================

class TestResponseParser:
    def test_extract_json_raw(self):
        from agent.llm.response_parser import extract_json

        result = extract_json('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_extract_json_markdown_block(self):
        from agent.llm.response_parser import extract_json

        text = 'Here is the result:\n```json\n{"a": 1}\n```\nDone.'
        result = extract_json(text)
        assert result == {"a": 1}

    def test_extract_json_embedded(self):
        from agent.llm.response_parser import extract_json

        text = 'The answer is {"x": "y"} and that is all.'
        result = extract_json(text)
        assert result == {"x": "y"}

    def test_extract_json_none(self):
        from agent.llm.response_parser import extract_json

        assert extract_json("no json here") is None
        assert extract_json("") is None

    def test_extract_yaml_raw(self):
        from agent.llm.response_parser import extract_yaml

        result = extract_yaml("key: value\nlist:\n  - a\n  - b")
        assert result["key"] == "value"
        assert result["list"] == ["a", "b"]

    def test_extract_yaml_markdown_block(self):
        from agent.llm.response_parser import extract_yaml

        text = "```yaml\nid: test\nrule: be honest\n```"
        result = extract_yaml(text)
        assert result["id"] == "test"

    def test_parse_reflection_json(self):
        from agent.llm.response_parser import parse_reflection

        text = '```json\n{"what_worked": "A", "what_failed": "B", "next_hint": "C", "causal_condition": "D"}\n```'
        result = parse_reflection(text)
        assert result["what_worked"] == "A"
        assert result["what_failed"] == "B"
        assert result["next_hint"] == "C"
        assert result["causal_condition"] == "D"

    def test_parse_reflection_fallback(self):
        from agent.llm.response_parser import parse_reflection

        text = "what_worked: good stuff\nwhat_failed: bad stuff\nnext_hint: try again\ncausal_condition: because"
        result = parse_reflection(text)
        assert result["what_worked"] == "good stuff"
        assert result["what_failed"] == "bad stuff"

    def test_parse_cib_evaluation(self):
        from agent.llm.response_parser import parse_cib_evaluation

        text = '```json\n{"scores": [{"scenario_id": "ks_01", "score": 0.98}], "min_score": 0.98, "passed": true}\n```'
        result = parse_cib_evaluation(text)
        assert result["passed"] is True
        assert result["min_score"] == 0.98
        assert len(result["scores"]) == 1

    def test_parse_phoenix_evaluation(self):
        from agent.llm.response_parser import parse_phoenix_evaluation

        text = '```json\n{"domain_score": 0.9, "reflection_score": 0.8, "phoenix_score": 0.86}\n```'
        result = parse_phoenix_evaluation(text)
        assert result["domain_score"] == 0.9
        assert result["reflection_score"] == 0.8
        assert result["phoenix_score"] == 0.86

    def test_parse_plan(self):
        from agent.llm.response_parser import parse_plan

        text = '```json\n{"steps": ["step1", "step2"], "estimated_success": 0.85, "task_category": "coding"}\n```'
        result = parse_plan(text)
        assert len(result["steps"]) == 2
        assert result["estimated_success"] == 0.85
        assert result["task_category"] == "coding"