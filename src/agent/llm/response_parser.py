"""Response parser for LLM outputs.

Extracts structured data (JSON, YAML) from LLM responses that may contain
markdown code blocks, explanatory text, or partial formatting.
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml


# ===========================================================================
# JSON extraction
# ===========================================================================

def extract_json(text: str) -> dict[str, Any] | list[Any] | None:
    """Extract a JSON object or array from an LLM response.

    Handles:
        - Raw JSON
        - JSON in markdown code blocks (```json ... ```)
        - JSON embedded in explanatory text

    Returns:
        Parsed JSON (dict or list) or None if no valid JSON found.
    """
    if not text:
        return None

    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    code_block_pattern = r"```(?:json)?\s*\n(.*?)\n\s*```"
    matches = re.findall(code_block_pattern, text, re.DOTALL)
    for match in matches:
        try:
            return json.loads(match.strip())
        except json.JSONDecodeError:
            continue

    # Try finding first { ... } or [ ... ] block
    for pattern in [r"\{.*\}", r"\[.*\]"]:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue

    return None


# ===========================================================================
# YAML extraction
# ===========================================================================

def extract_yaml(text: str) -> Any:
    """Extract YAML from an LLM response.

    Handles:
        - Raw YAML
        - YAML in markdown code blocks (```yaml ... ```)

    Returns:
        Parsed YAML data or None.
    """
    if not text:
        return None

    text = text.strip()

    # Try extracting from markdown code block
    code_block_pattern = r"```(?:yaml|yml)?\s*\n(.*?)\n\s*```"
    matches = re.findall(code_block_pattern, text, re.DOTALL)
    for match in matches:
        try:
            return yaml.safe_load(match.strip())
        except yaml.YAMLError:
            continue

    # Try direct parse
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        pass

    return None


# ===========================================================================
# Reflection parsing
# ===========================================================================

def parse_reflection(text: str) -> dict[str, str]:
    """Parse a reflection response into the 4 core fields.

    Expected fields: what_worked, what_failed, next_hint, causal_condition.

    Falls back to empty strings if fields are missing.
    """
    data = extract_json(text)
    if data and isinstance(data, dict):
        return {
            "what_worked": data.get("what_worked", ""),
            "what_failed": data.get("what_failed", ""),
            "next_hint": data.get("next_hint", ""),
            "causal_condition": data.get("causal_condition", ""),
        }

    # Fallback: try to parse from plain text
    return {
        "what_worked": _extract_field(text, "what_worked"),
        "what_failed": _extract_field(text, "what_failed"),
        "next_hint": _extract_field(text, "next_hint"),
        "causal_condition": _extract_field(text, "causal_condition"),
    }


# ===========================================================================
# Evaluation parsing
# ===========================================================================

def parse_cib_evaluation(text: str) -> dict[str, Any]:
    """Parse a CIB evaluation response.

    Expected fields: scores (list), min_score (float), passed (bool).
    """
    data = extract_json(text)
    if data and isinstance(data, dict):
        return {
            "scores": data.get("scores", []),
            "min_score": data.get("min_score", 0.0),
            "passed": data.get("passed", False),
        }
    return {"scores": [], "min_score": 0.0, "passed": False}


def parse_phoenix_evaluation(text: str) -> dict[str, float]:
    """Parse a Phoenix Auditor evaluation response.

    Expected fields: domain_score, reflection_score, phoenix_score.
    """
    data = extract_json(text)
    if data and isinstance(data, dict):
        domain = float(data.get("domain_score", 0.0))
        reflection = float(data.get("reflection_score", 0.0))
        phoenix = float(data.get("phoenix_score", 0.6 * domain + 0.4 * reflection))
        return {
            "domain_score": domain,
            "reflection_score": reflection,
            "phoenix_score": phoenix,
        }
    return {"domain_score": 0.0, "reflection_score": 0.0, "phoenix_score": 0.0}


# ===========================================================================
# Plan parsing
# ===========================================================================

def parse_plan(text: str) -> dict[str, Any]:
    """Parse a planning response.

    Expected fields: steps (list), estimated_success (float), task_category (str).
    """
    data = extract_json(text)
    if data and isinstance(data, dict):
        return {
            "steps": data.get("steps", []),
            "estimated_success": float(data.get("estimated_success", 0.5)),
            "task_category": data.get("task_category", "general"),
        }
    return {"steps": [], "estimated_success": 0.5, "task_category": "general"}


# ===========================================================================
# Helpers
# ===========================================================================

def _extract_field(text: str, field_name: str) -> str:
    """Try to extract a field value from plain text.

    Looks for patterns like:
        - "what_worked: value"
        - "what_worked: value"
        - "**what_worked**: value"
    """
    pattern = rf"(?:\*\*)?{field_name}(?:\*\*)?\s*[:：]\s*(.+?)(?:\n|$)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""