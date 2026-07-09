"""LLM package for the Forge agent framework.

Re-exports the unified LLM client, prompt templates, and response parsers.
"""

from .client import ChatMessage, ChatResponse, LLMClient, LLMConfig
from .prompts import PromptTemplate, TEMPLATES, get_template
from .response_parser import (
    extract_json,
    extract_yaml,
    parse_cib_evaluation,
    parse_phoenix_evaluation,
    parse_plan,
    parse_reflection,
)

__all__ = [
    # client
    "ChatMessage",
    "ChatResponse",
    "LLMClient",
    "LLMConfig",
    # prompts
    "PromptTemplate",
    "TEMPLATES",
    "get_template",
    # response parser
    "extract_json",
    "extract_yaml",
    "parse_cib_evaluation",
    "parse_phoenix_evaluation",
    "parse_plan",
    "parse_reflection",
]