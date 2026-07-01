from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from json import JSONDecodeError, loads
from pathlib import Path
from shutil import which

import yaml
from pydantic import BaseModel, Field

from forge.contracts import FileCandidate, FileRole, LocalizerResult, UserRequest


class LocalizerRuleConfig(BaseModel):
    id: str
    description: str
    weight: int = Field(default=1, ge=1)


class LocalizerLimits(BaseModel):
    max_file_candidates: int = Field(default=12, ge=1)
    max_test_candidates: int = Field(default=5, ge=0)
    max_docs: int = Field(default=3, ge=0)
    include_metadata_only: bool = True


class LocalizerConfig(BaseModel):
    candidate_rules: list[LocalizerRuleConfig] = Field(default_factory=list)
    limits: LocalizerLimits = Field(default_factory=LocalizerLimits)


@dataclass(slots=True)
class RankedFile:
    path: str
    role: FileRole
    score: float
    reasons: list[str]
    symbols: list[str]


class LocalizerService:
    """MVP localizer using rg file listing and heuristic keyword matching."""

    STOPWORDS = {
        "a",
        "an",
        "and",
        "are",
        "be",
        "by",
        "create",
        "for",
        "from",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "make",
        "of",
        "on",
        "or",
        "please",
        "that",
        "the",
        "this",
        "to",
        "use",
        "with",
        "해줘",
        "구현",
        "구현해줘",
        "그리고",
        "나는",
        "를",
        "을",
        "이",
        "가",
        "은",
        "는",
        "좀",
        "먼저",
        "우선",
    }

    def __init__(self, project_root: str | Path | None = None, config_path: str | Path | None = None) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.config_path = Path(config_path) if config_path else self.project_root / ".agents" / "localizer.yaml"
        self.config = self._load_config()

    def localize(self, request: UserRequest, codex_fallback: CodexLocalizerFallback | None = None) -> LocalizerResult:
        keywords = self._extract_keywords(request.user_text, request.normalized_goal)
        ranked = self._rank_files(keywords)
        selected = self._select_candidates(ranked)
        warnings: list[str] = []
        if not selected:
            warnings.append("No strong file candidates found from rg-based localization.")
        if codex_fallback is not None and codex_fallback.is_available():
            try:
                selected = codex_fallback.refine_candidates(request, selected)
            except Exception as exc:  # pragma: no cover - best-effort fallback path
                warnings.append(f"Codex fallback unavailable: {exc}")
        return LocalizerResult(
            request_id=request.request_id,
            normalized_query=request.normalized_goal,
            keywords=keywords,
            file_candidates=selected,
            warnings=warnings,
        )

    def build_codex_fallback_prompt(self, request: UserRequest, rg_candidates: list[FileCandidate]) -> str:
        candidate_lines = "\n".join(
            f"- {candidate.path} ({candidate.role}, score={candidate.score:.2f}): {candidate.reason}"
            for candidate in rg_candidates
        )
        return (
            "You are a codebase localizer.\n"
            "Given the user request and heuristic file candidates, suggest up to 12 repository files "
            "that are most relevant for implementation. Prefer real code files, include tests when helpful, "
            "and keep explanations brief.\n\n"
            f"User request:\n{request.user_text}\n\n"
            f"Normalized goal:\n{request.normalized_goal}\n\n"
            f"Heuristic candidates:\n{candidate_lines or '- none'}\n\n"
            "Return JSON with this shape:\n"
            "{"
            "\"file_candidates\": ["
            "{\"path\": \"...\", \"role\": \"source|test|config|doc|policy|memory|other\", "
            "\"reason\": \"...\", \"score\": 0.0, \"symbols\": [\"...\"]}"
            "]}"
        )

    def _load_config(self) -> LocalizerConfig:
        if not self.config_path.exists():
            return LocalizerConfig()

        raw_text = self.config_path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return LocalizerConfig()

        loaded = yaml.safe_load(raw_text) or {}
        return LocalizerConfig.model_validate(loaded)

    def _extract_keywords(self, *parts: str) -> list[str]:
        tokens: list[str] = []
        for part in parts:
            for token in re.findall(r"[A-Za-z0-9_./-]+|[가-힣]{2,}", part.lower()):
                token = token.strip("._-/")
                if len(token) < 2 or token in self.STOPWORDS:
                    continue
                tokens.append(token)

        unique: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if token not in seen:
                seen.add(token)
                unique.append(token)
        return unique

    def _rank_files(self, keywords: list[str]) -> list[RankedFile]:
        files = self._list_files()
        ranked: list[RankedFile] = []
        for relative_path in files:
            role = self._infer_role(relative_path)
            score, reasons, symbols = self._score_path(relative_path, role, keywords)
            if score <= 0:
                continue
            ranked.append(
                RankedFile(
                    path=relative_path,
                    role=role,
                    score=min(score, 1.0),
                    reasons=reasons,
                    symbols=symbols,
                )
            )

        ranked.sort(key=lambda item: (-item.score, item.path))
        return ranked

    def _list_files(self) -> list[str]:
        rg_bin = which("rg")
        if rg_bin is None:
            return self._walk_files()

        result = subprocess.run(
            [rg_bin, "--files"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]

    def _walk_files(self) -> list[str]:
        files: list[str] = []
        ignored_dirs = {".git", ".venv", "__pycache__"}

        for path in self.project_root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in ignored_dirs for part in path.parts):
                continue
            files.append(path.relative_to(self.project_root).as_posix())

        files.sort()
        return files

    def _infer_role(self, relative_path: str) -> FileRole:
        lower = relative_path.lower()
        if lower.endswith((".yaml", ".yml", ".toml", ".json", ".ini")):
            return FileRole.CONFIG
        if (
            "/tests/" in lower
            or lower.startswith("tests/")
            or lower.endswith("_test.py")
            or lower.endswith(".test.ts")
            or lower.endswith(".spec.ts")
            or lower.endswith(".test.js")
            or lower.endswith(".spec.js")
        ):
            return FileRole.TEST
        if lower.endswith(".md"):
            return FileRole.DOC
        if lower.startswith("memory/"):
            return FileRole.MEMORY
        if lower.startswith(".agents/"):
            return FileRole.POLICY
        if lower.endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
            return FileRole.SOURCE
        return FileRole.OTHER

    def _score_path(self, relative_path: str, role: FileRole, keywords: list[str]) -> tuple[float, list[str], list[str]]:
        path_lower = relative_path.lower()
        parts = [part for part in re.split(r"[/.\\_-]+", path_lower) if part]
        matched_symbols: list[str] = []
        reasons: list[str] = []
        score = 0.0

        for keyword in keywords:
            if keyword == path_lower:
                score += 0.85
                matched_symbols.append(keyword)
                reasons.append(f"exact path match for '{keyword}'")
                continue
            if keyword in path_lower:
                score += 0.35
                matched_symbols.append(keyword)
                reasons.append(f"path contains '{keyword}'")
                continue
            if any(keyword == part for part in parts):
                score += 0.25
                matched_symbols.append(keyword)
                reasons.append(f"segment match for '{keyword}'")

        if role == FileRole.SOURCE:
            score += 0.12
            reasons.append("prefer executable/source files")
        elif role == FileRole.TEST:
            score += 0.08
            reasons.append("include related tests")
        elif role == FileRole.DOC:
            score -= 0.05
        elif role == FileRole.CONFIG:
            score -= 0.02

        if relative_path.startswith(".agents/"):
            score += 0.2
            reasons.append("agent policy/config may guide behavior")
        if relative_path.startswith("memory/"):
            score += 0.1
            reasons.append("memory architecture files can inform behavior")

        deduped_symbols = list(dict.fromkeys(matched_symbols))
        deduped_reasons = list(dict.fromkeys(reasons))
        return score, deduped_reasons, deduped_symbols

    def _select_candidates(self, ranked: list[RankedFile]) -> list[FileCandidate]:
        limits = self.config.limits
        selected: list[FileCandidate] = []
        counts: defaultdict[FileRole, int] = defaultdict(int)

        for item in ranked:
            if len(selected) >= limits.max_file_candidates:
                break
            if item.role == FileRole.TEST and counts[FileRole.TEST] >= limits.max_test_candidates:
                continue
            if item.role == FileRole.DOC and counts[FileRole.DOC] >= limits.max_docs:
                continue

            selected.append(
                FileCandidate(
                    path=item.path,
                    role=item.role,
                    reason="; ".join(item.reasons[:3]) if item.reasons else "heuristic keyword match",
                    score=round(item.score, 4),
                    symbols=item.symbols,
                )
            )
            counts[item.role] += 1

        return selected


class CodexLocalizerFallback:
    """Optional Codex SDK-based refinement step for localizer candidates."""

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        model: str = "gpt-5.4",
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.model = model

    def is_available(self) -> bool:
        try:
            from openai_codex import Codex  # noqa: F401
        except ImportError:
            return False
        return True

    def refine_candidates(self, request: UserRequest, candidates: list[FileCandidate]) -> list[FileCandidate]:
        from openai_codex import Codex, Sandbox

        prompt = LocalizerService(project_root=self.project_root).build_codex_fallback_prompt(request, candidates)
        with Codex() as codex:
            thread = codex.thread_start(
                model=self.model,
                cwd=str(self.project_root),
                sandbox=Sandbox.read_only,
            )
            result = thread.run(prompt)

        if not result.final_response:
            raise ValueError("Codex returned no final response.")

        try:
            payload = loads(result.final_response)
        except JSONDecodeError as exc:
            raise ValueError("Codex did not return valid JSON.") from exc

        raw_candidates = payload.get("file_candidates", [])
        if not isinstance(raw_candidates, list):
            raise ValueError("Codex response did not include a file_candidates list.")

        validated: list[FileCandidate] = []
        for item in raw_candidates:
            validated.append(FileCandidate.model_validate(item))
        return validated or candidates
