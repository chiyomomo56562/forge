"""workspace 경계 안에서만 동작하는 읽기 전용 내장 도구 레지스트리.

최종 수정일: 2026-07-31
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from forge.domain.inner_loop import (
    ToolDefinition,
    ToolInvocation,
    ToolResult,
    ToolRiskTier,
    ToolStatus,
)


class ToolInvocationError(ValueError):
    """입력·경로·등록 계약을 위반해 도구를 실행할 수 없을 때 사용한다.

    최종 수정일: 2026-07-31
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _RegisteredTool:
    """registry 내부에서만 validator와 handler를 도구 정의에 결합한다.

    최종 수정일: 2026-07-31
    """

    definition: ToolDefinition
    validator: Callable[[object], dict[str, object]]
    handler: Callable[[ToolInvocation], ToolResult] | None


class StaticToolAuthorizationPolicy:
    """M2의 정적 권한 정책으로 변경 도구를 기본 거부한다.

    Args:
        allow_verification: 고정된 프로젝트 검증 template의 실행 허용 여부.

    최종 수정일: 2026-07-31
    """

    def __init__(self, *, allow_verification: bool = True) -> None:
        self._allow_verification = allow_verification

    def authorize(self, invocation: ToolInvocation, definition: ToolDefinition) -> bool:
        """위험도에 따라 단일 호출의 실행 허용 여부를 반환한다.

        Args:
            invocation: 이미 schema 검증을 통과한 도구 호출.
            definition: 호출 대상 도구의 정책 메타데이터.

        Returns:
            read-only 및 허용된 verification 도구의 실행 가능 여부.

        최종 수정일: 2026-07-31
        """
        del invocation
        if definition.risk_tier is ToolRiskTier.READ_ONLY:
            return True
        return definition.risk_tier is ToolRiskTier.VERIFICATION and self._allow_verification


class BuiltinToolRegistry:
    """고정 schema와 handler만 노출하는 workspace 내장 도구 registry.

    Args:
        workspace_root: 모든 파일·Git·검증 명령의 기준 디렉터리.
        max_read_bytes: 단일 read 결과에 반환할 최대 byte 수.
        max_search_results: search/list 결과 최대 항목 수.
        max_output_bytes: command 결과의 inline 최대 byte 수.
        timeout_seconds: Git 및 verification command 기본 제한 시간.

    최종 수정일: 2026-07-31
    """

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        max_read_bytes: int = 32_768,
        max_search_results: int = 100,
        max_output_bytes: int = 32_768,
        timeout_seconds: int = 30,
    ) -> None:
        self._root = Path(workspace_root).resolve()
        if not self._root.is_dir():
            raise ValueError("workspace_root must be an existing directory")
        self._max_read_bytes = max_read_bytes
        self._max_search_results = max_search_results
        self._max_output_bytes = max_output_bytes
        self._timeout_seconds = timeout_seconds
        self._tools = self._build_tools()

    def definition_for(self, tool_name: str) -> ToolDefinition:
        """이름에 대응하는 공개 도구 정의를 반환한다.

        Args:
            tool_name: planner가 선택한 registry 이름.

        Raises:
            ToolInvocationError: 등록되지 않은 이름일 때.

        최종 수정일: 2026-07-31
        """
        try:
            return self._tools[tool_name].definition
        except KeyError as exc:
            raise ToolInvocationError("tool.unknown") from exc

    def validate_arguments(self, tool_name: str, arguments: object) -> dict[str, object]:
        """도구별 schema를 검사하고 canonicalized 인자를 반환한다.

        Args:
            tool_name: 검증할 등록 도구 이름.
            arguments: planner가 제출한 JSON 객체여야 하는 입력 값.

        Raises:
            ToolInvocationError: 이름 또는 입력 schema가 유효하지 않을 때.

        최종 수정일: 2026-07-31
        """
        try:
            return self._tools[tool_name].validator(arguments)
        except KeyError as exc:
            raise ToolInvocationError("tool.unknown") from exc

    def execute(self, invocation: ToolInvocation) -> ToolResult:
        """검증된 단일 도구 호출을 handler에 위임한다.

        Args:
            invocation: registry와 정책 검사를 통과한 도구 호출.

        Raises:
            ToolInvocationError: handler가 없는 등록 도구를 직접 실행하려 할 때.

        최종 수정일: 2026-07-31
        """
        registered = self._tools.get(invocation.tool_name)
        if registered is None:
            raise ToolInvocationError("tool.unknown")
        if registered.handler is None:
            raise ToolInvocationError("tool.not_implemented")
        return registered.handler(invocation)

    def _build_tools(self) -> dict[str, _RegisteredTool]:
        """M2에서 허용할 고정 도구 집합을 구성한다.

        Returns:
            도구 이름별 validator/handler 쌍.

        최종 수정일: 2026-07-31
        """
        return {
            "workspace.list_files": _RegisteredTool(
                ToolDefinition(
                    "workspace.list_files",
                    "1",
                    ToolRiskTier.READ_ONLY,
                    10,
                    self._max_output_bytes,
                ),
                self._validate_list_files,
                self._list_files,
            ),
            "workspace.read_file": _RegisteredTool(
                ToolDefinition(
                    "workspace.read_file", "1", ToolRiskTier.READ_ONLY, 10, self._max_read_bytes
                ),
                self._validate_read_file,
                self._read_file,
            ),
            "workspace.search_text": _RegisteredTool(
                ToolDefinition(
                    "workspace.search_text",
                    "1",
                    ToolRiskTier.READ_ONLY,
                    15,
                    self._max_output_bytes,
                ),
                self._validate_search_text,
                self._search_text,
            ),
            "git.status": _RegisteredTool(
                ToolDefinition(
                    "git.status",
                    "1",
                    ToolRiskTier.READ_ONLY,
                    self._timeout_seconds,
                    self._max_output_bytes,
                ),
                self._validate_empty,
                self._git_status,
            ),
            "git.diff": _RegisteredTool(
                ToolDefinition(
                    "git.diff",
                    "1",
                    ToolRiskTier.READ_ONLY,
                    self._timeout_seconds,
                    self._max_output_bytes,
                ),
                self._validate_empty,
                self._git_diff,
            ),
            "workspace.apply_patch": _RegisteredTool(
                ToolDefinition(
                    "workspace.apply_patch",
                    "1",
                    ToolRiskTier.WORKSPACE_MUTATION,
                    30,
                    self._max_output_bytes,
                ),
                self._validate_patch,
                self._apply_patch,
            ),
            "project.verify": _RegisteredTool(
                ToolDefinition(
                    "project.verify",
                    "1",
                    ToolRiskTier.VERIFICATION,
                    self._timeout_seconds,
                    self._max_output_bytes,
                ),
                self._validate_verify,
                self._verify,
            ),
        }

    @staticmethod
    def _require_mapping(arguments: object, *, allowed: set[str]) -> dict[str, object]:
        """입력이 허용 key만 가진 JSON object인지 검사한다.

        최종 수정일: 2026-07-31
        """
        if not isinstance(arguments, Mapping) or any(
            not isinstance(key, str) or key not in allowed for key in arguments
        ):
            raise ToolInvocationError("tool.invalid_arguments")
        return dict(arguments)

    def _validate_empty(self, arguments: object) -> dict[str, object]:
        """인자를 받지 않는 Git 도구의 입력을 검사한다.

        최종 수정일: 2026-07-31
        """
        return self._require_mapping(arguments, allowed=set())

    def _validate_list_files(self, arguments: object) -> dict[str, object]:
        """파일 탐색 입력의 경로와 선택적 glob를 검사한다.

        최종 수정일: 2026-07-31
        """
        values = self._require_mapping(arguments, allowed={"path", "glob"})
        path = values.get("path", ".")
        glob = values.get("glob", "*")
        if not isinstance(path, str) or not isinstance(glob, str) or not glob:
            raise ToolInvocationError("tool.invalid_arguments")
        self._resolve_path(path)
        return {"path": path, "glob": glob}

    def _validate_read_file(self, arguments: object) -> dict[str, object]:
        """텍스트 파일 읽기 입력의 경로·offset·length를 검사한다.

        최종 수정일: 2026-07-31
        """
        values = self._require_mapping(arguments, allowed={"path", "offset", "max_bytes"})
        path = values.get("path")
        offset = values.get("offset", 0)
        max_bytes = values.get("max_bytes", self._max_read_bytes)
        if (
            not isinstance(path, str)
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes < 1
        ):
            raise ToolInvocationError("tool.invalid_arguments")
        self._resolve_path(path)
        return {"path": path, "offset": offset, "max_bytes": min(max_bytes, self._max_read_bytes)}

    def _validate_search_text(self, arguments: object) -> dict[str, object]:
        """텍스트 검색어와 탐색 기준 경로를 검사한다.

        최종 수정일: 2026-07-31
        """
        values = self._require_mapping(arguments, allowed={"query", "path"})
        query = values.get("query")
        path = values.get("path", ".")
        if not isinstance(query, str) or not query or not isinstance(path, str):
            raise ToolInvocationError("tool.invalid_arguments")
        self._resolve_path(path)
        return {"query": query, "path": path}

    def _validate_patch(self, arguments: object) -> dict[str, object]:
        """patch 도구의 입력 형식을 검사한다.

        최종 수정일: 2026-08-05
        """
        values = self._require_mapping(arguments, allowed={"patch"})
        if not isinstance(values.get("patch"), str) or not values["patch"]:
            raise ToolInvocationError("tool.invalid_arguments")
        return {"patch": values["patch"]}

    def _validate_verify(self, arguments: object) -> dict[str, object]:
        """고정된 검증 template ID만 수용한다.

        최종 수정일: 2026-07-31
        """
        values = self._require_mapping(arguments, allowed={"template"})
        template = values.get("template")
        if template not in {"pytest", "ruff", "mypy"}:
            raise ToolInvocationError("tool.invalid_arguments")
        return {"template": template}

    def _resolve_path(self, raw_path: str) -> Path:
        """상대 경로가 workspace 내부의 symlink가 아닌 위치인지 확인한다.

        Args:
            raw_path: caller가 전달한 workspace 기준 상대 경로.

        Returns:
            resolve된 workspace 내부 경로.

        Raises:
            ToolInvocationError: 절대 경로, root 탈출 또는 symlink 접근일 때.

        최종 수정일: 2026-07-31
        """
        candidate = Path(raw_path)
        if candidate.is_absolute():
            raise ToolInvocationError("tool.path_outside_workspace")
        unresolved = self._root / candidate
        if unresolved.is_symlink():
            raise ToolInvocationError("tool.symlink_not_allowed")
        resolved = unresolved.resolve()
        if not resolved.is_relative_to(self._root):
            raise ToolInvocationError("tool.path_outside_workspace")
        return resolved

    def _list_files(self, invocation: ToolInvocation) -> ToolResult:
        """workspace 아래 일반 파일의 제한된 목록을 반환한다.

        최종 수정일: 2026-07-31
        """
        started = time.monotonic()
        path = self._resolve_path(str(invocation.arguments["path"]))
        glob = str(invocation.arguments["glob"])
        if not path.is_dir():
            return self._failed("tool.not_a_directory", started)
        files: list[str] = []
        truncated = False
        for item in path.rglob(glob):
            if item.is_symlink() or not item.is_file():
                continue
            try:
                relative = item.resolve().relative_to(self._root).as_posix()
            except ValueError:
                continue
            files.append(relative)
            if len(files) >= self._max_search_results:
                truncated = True
                break
        return ToolResult(
            ToolStatus.COMPLETED,
            f"Listed {len(files)} file(s).",
            output={"files": files},
            audit_details={"path": str(invocation.arguments["path"]), "count": len(files)},
            duration_ms=self._duration_ms(started),
            truncated=truncated,
        )

    def _read_file(self, invocation: ToolInvocation) -> ToolResult:
        """허용 범위의 UTF-8 텍스트 파일 일부를 읽는다.

        최종 수정일: 2026-07-31
        """
        started = time.monotonic()
        path = self._resolve_path(str(invocation.arguments["path"]))
        if not path.is_file() or path.is_symlink():
            return self._failed("tool.file_not_found", started)
        offset = int(invocation.arguments["offset"])
        limit = int(invocation.arguments["max_bytes"])
        try:
            with path.open("rb") as file_handle:
                file_handle.seek(offset)
                content = file_handle.read(limit + 1)
        except OSError:
            return self._failed("tool.read_failed", started)
        if b"\x00" in content:
            return self._failed("tool.binary_file", started)
        try:
            text = content[:limit].decode("utf-8")
        except UnicodeDecodeError:
            return self._failed("tool.non_utf8_file", started)
        truncated = len(content) > limit
        return ToolResult(
            ToolStatus.COMPLETED,
            f"Read {len(text.encode('utf-8'))} byte(s) from {path.name}.",
            output={"path": str(invocation.arguments["path"]), "content": text, "offset": offset},
            audit_details={
                "path": str(invocation.arguments["path"]),
                "bytes": len(text.encode("utf-8")),
            },
            duration_ms=self._duration_ms(started),
            truncated=truncated,
        )

    def _search_text(self, invocation: ToolInvocation) -> ToolResult:
        """workspace 텍스트 파일에서 literal 검색 결과를 제한적으로 반환한다.

        최종 수정일: 2026-07-31
        """
        started = time.monotonic()
        base = self._resolve_path(str(invocation.arguments["path"]))
        if not base.is_dir():
            return self._failed("tool.not_a_directory", started)
        query = str(invocation.arguments["query"])
        matches: list[dict[str, object]] = []
        truncated = False
        for directory, _, filenames in os.walk(base, followlinks=False):
            for filename in filenames:
                path = Path(directory, filename)
                if path.is_symlink() or path.stat().st_size > self._max_read_bytes:
                    continue
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeDecodeError):
                    continue
                for line_number, line in enumerate(lines, start=1):
                    if query not in line:
                        continue
                    matches.append(
                        {
                            "path": path.resolve().relative_to(self._root).as_posix(),
                            "line": line_number,
                            "snippet": line[:200],
                        }
                    )
                    if len(matches) >= self._max_search_results:
                        truncated = True
                        break
                if truncated:
                    break
            if truncated:
                break
        return ToolResult(
            ToolStatus.COMPLETED,
            f"Found {len(matches)} match(es).",
            output={"matches": matches},
            audit_details={"path": str(invocation.arguments["path"]), "match_count": len(matches)},
            duration_ms=self._duration_ms(started),
            truncated=truncated,
        )

    def _git_status(self, invocation: ToolInvocation) -> ToolResult:
        """고정된 읽기 전용 Git status를 실행한다.

        최종 수정일: 2026-07-31
        """
        del invocation
        return self._run_command(
            [
                "git",
                "-c",
                "core.pager=cat",
                "-c",
                "color.ui=false",
                "--no-optional-locks",
                "status",
                "--short",
                "--untracked-files=normal",
            ]
        )

    def _git_diff(self, invocation: ToolInvocation) -> ToolResult:
        """external diff와 pager 없이 고정된 Git diff를 실행한다.

        최종 수정일: 2026-07-31
        """
        del invocation
        return self._run_command(
            [
                "git",
                "-c",
                "core.pager=cat",
                "-c",
                "color.ui=false",
                "-c",
                "diff.external=",
                "--no-optional-locks",
                "diff",
                "--no-ext-diff",
                "--no-submodule",
            ]
        )

    def _verify(self, invocation: ToolInvocation) -> ToolResult:
        """등록된 검증 template 하나를 shell 없이 실행한다.

        최종 수정일: 2026-07-31
        """
        commands = {
            "pytest": ["python", "-m", "pytest"],
            "ruff": ["python", "-m", "ruff", "check", "src", "tests"],
            "mypy": ["python", "-m", "mypy", "src"],
        }
        return self._run_command(commands[str(invocation.arguments["template"])])

    _HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    def _apply_patch(self, invocation: ToolInvocation) -> ToolResult:
        """unified diff patch를 workspace 내 파일에 적용한다.

        각 hunk의 대상 파일 경로가 workspace root 내부인지 검증한 뒤,
        파일을 읽어 hunk를 순서대로 적용하고 다시 쓴다.

        Args:
            invocation: tool_name="workspace.apply_patch",
                arguments={"patch": "<unified diff text>"}.

        Returns:
            적용된 파일 수와 상태를 포함한 ToolResult.

        최종 수정일: 2026-08-05
        """
        started = time.monotonic()
        patch_text = str(invocation.arguments["patch"])
        try:
            file_patches = self._parse_unified_diff(patch_text)
        except ToolInvocationError:
            return self._failed("tool.invalid_patch_format", started)

        if not file_patches:
            return self._failed("tool.invalid_patch_format", started)

        applied: list[str] = []
        for relative_path, hunks in file_patches:
            try:
                target = self._resolve_path(relative_path)
            except ToolInvocationError:
                return self._failed("tool.path_outside_workspace", started)

            try:
                if target.exists():
                    if target.is_symlink():
                        return self._failed("tool.symlink_not_allowed", started)
                    original = target.read_text(encoding="utf-8")
                else:
                    original = ""
                patched = self._apply_hunks(original, hunks)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(patched, encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return self._failed("tool.patch_failed", started)
            except ToolInvocationError as exc:
                return self._failed(exc.code, started)
            applied.append(relative_path)

        return ToolResult(
            ToolStatus.COMPLETED,
            f"Patched {len(applied)} file(s).",
            output={"patched_files": applied},
            audit_details={"file_count": len(applied), "files": applied},
            duration_ms=self._duration_ms(started),
        )

    def _parse_unified_diff(self, patch_text: str) -> list[tuple[str, list[list[str]]]]:
        """unified diff 텍스트를 파일별 hunk 목록으로 파싱한다.

        Args:
            patch_text: unified diff 형식의 patch 문자열.

        Returns:
            (상대 경로, hunk 라인 목록)의 리스트.

        Raises:
            ToolInvocationError: patch 형식이 유효하지 않을 때.

        최종 수정일: 2026-08-05
        """
        lines = patch_text.splitlines()
        result: list[tuple[str, list[list[str]]]] = []
        current_file: str | None = None
        current_hunks: list[list[str]] = []
        current_hunk: list[str] | None = None

        for line in lines:
            if line.startswith("--- "):
                if current_hunk is not None:
                    current_hunks.append(current_hunk)
                    current_hunk = None
                if current_file is not None:
                    result.append((current_file, current_hunks))
                    current_hunks = []
                # --- a/path 또는 --- path 형식에서 경로 추출
                path = line[4:].strip()
                if path == "/dev/null":
                    # /dev/null은 새 파일 생성을 의미하므로 +++ 헤더에서 경로를 가져온다
                    current_file = None
                else:
                    current_file = self._strip_prefix(path)
            elif line.startswith("+++ "):
                if current_hunk is not None:
                    current_hunks.append(current_hunk)
                    current_hunk = None
                path = line[4:].strip()
                if current_file is None:
                    # --- /dev/null이었으므로 +++에서 경로를 가져온다
                    current_file = self._strip_prefix(path)
                # --- 헤더에서 추출한 경로가 있으면 그것을 유지, 없으면 +++ 사용
                if current_file is None or current_file == "/dev/null":
                    current_file = self._strip_prefix(path)
            elif line.startswith("@@"):
                if current_hunk is not None:
                    current_hunks.append(current_hunk)
                current_hunk = [line]
            elif current_hunk is not None:
                if line.startswith((" ", "-", "+", "\\")):
                    current_hunk.append(line)
                elif line.strip() == "":
                    current_hunk.append(" ")
                else:
                    # hunk 내부가 아닌 일반 텍스트는 무시
                    pass

        if current_hunk is not None:
            current_hunks.append(current_hunk)
        if current_file is not None:
            result.append((current_file, current_hunks))

        return result

    @staticmethod
    def _strip_prefix(path: str) -> str:
        """diff 경로에서 a/ b/ 접두어를 제거한다.

        Args:
            path: diff 헤더의 파일 경로.

        Returns:
            접두어가 제거된 상대 경로.

        최종 수정일: 2026-08-05
        """
        if path == "/dev/null":
            return path
        if path.startswith(("a/", "b/")):
            return path[2:]
        return path

    def _apply_hunks(self, original: str, hunks: list[list[str]]) -> str:
        """원본 텍스트에 hunk 목록을 순서대로 적용한다.

        Args:
            original: 패치 전 파일 내용.
            hunks: 각 hunk를 구성하는 라인 목록의 리스트.

        Returns:
            패치가 적용된 파일 내용.

        Raises:
            ToolInvocationError: hunk가 원본과 매칭되지 않을 때.

        최종 수정일: 2026-08-05
        """
        lines = original.splitlines(keepends=True)
        result_lines: list[str] = []
        consumed = 0

        for hunk_lines in hunks:
            header = hunk_lines[0]
            match = self._HUNK_HEADER_RE.match(header)
            if match is None:
                raise ToolInvocationError("tool.invalid_patch_format")

            old_start = int(match.group(1))
            old_count = int(match.group(2) or 1)

            # hunk 헤더 이후의 라인들을 분류
            context_and_removed: list[str] = []
            added: list[str] = []
            for hunk_line in hunk_lines[1:]:
                if hunk_line.startswith("\\"):
                    # \ No newline at end of file 등의 메타데이터 — 무시
                    continue
                if hunk_line.startswith("-"):
                    context_and_removed.append(hunk_line[1:])
                elif hunk_line.startswith("+"):
                    added.append(hunk_line[1:])
                elif hunk_line.startswith(" "):
                    context_and_removed.append(hunk_line[1:])
                # 빈 라인이나 기타는 무시

            # old_start는 1-based, 0은 빈 파일(새 파일 생성)을 의미
            if old_start == 0:
                # 새 파일: 원본에 추가
                result_lines.extend(line if line.endswith("\n") else line + "\n" for line in added)
                consumed = len(lines)
                continue

            # old_start 이전의 아직 처리하지 않은 라인들을 결과에 추가
            start_idx = old_start - 1
            if start_idx < consumed:
                # hunk가 겹치는 경우 — 매칭 검증
                start_idx = consumed
            if start_idx > len(lines):
                raise ToolInvocationError("tool.patch_context_mismatch")
            result_lines.extend(lines[consumed:start_idx])
            consumed = start_idx

            # context + removed 라인이 원본과 매칭되는지 검증
            for i, expected in enumerate(context_and_removed):
                if consumed + i >= len(lines):
                    raise ToolInvocationError("tool.patch_context_mismatch")
                actual = lines[consumed + i]
                # keepends=True이므로 줄바꿈 문자 포함 비교
                expected_stripped = expected.rstrip("\n")
                actual_stripped = actual.rstrip("\n")
                if expected_stripped != actual_stripped:
                    raise ToolInvocationError("tool.patch_context_mismatch")

            # removed + context 라인 수만큼 원본에서 소비
            consumed += old_count

            # added 라인을 결과에 추가
            for line in added:
                if line.endswith("\n"):
                    result_lines.append(line)
                else:
                    result_lines.append(line + "\n")

        # 남은 원본 라인 추가
        result_lines.extend(lines[consumed:])

        return "".join(result_lines)

    def _run_command(self, command: list[str]) -> ToolResult:
        """고정 argument vector를 timeout·출력 상한과 함께 실행한다.

        최종 수정일: 2026-07-31
        """
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=self._root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=self._timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                ToolStatus.FAILED,
                "Command timed out.",
                retryable=True,
                safe_error_code="tool.timeout",
                duration_ms=self._duration_ms(started),
            )
        except OSError:
            return self._failed("tool.command_unavailable", started)
        output = completed.stdout + completed.stderr
        limited = output[: self._max_output_bytes]
        text = limited.decode("utf-8", errors="replace")
        truncated = len(output) > self._max_output_bytes
        if completed.returncode != 0:
            return ToolResult(
                ToolStatus.FAILED,
                f"Command failed with exit code {completed.returncode}.",
                output={"output": text, "exit_code": completed.returncode},
                audit_details={"exit_code": completed.returncode, "output_bytes": len(output)},
                safe_error_code="tool.command_failed",
                duration_ms=self._duration_ms(started),
                truncated=truncated,
            )
        return ToolResult(
            ToolStatus.COMPLETED,
            "Command completed.",
            output={"output": text},
            audit_details={"output_bytes": len(output)},
            duration_ms=self._duration_ms(started),
            truncated=truncated,
        )

    def _failed(self, code: str, started: float) -> ToolResult:
        """안전 code만 포함하는 non-retryable failure 결과를 만든다.

        최종 수정일: 2026-07-31
        """
        return ToolResult(
            ToolStatus.FAILED,
            "Tool execution failed.",
            safe_error_code=code,
            duration_ms=self._duration_ms(started),
        )

    @staticmethod
    def _duration_ms(started: float) -> int:
        """monotonic 시작 시각부터 경과한 밀리초를 계산한다.

        최종 수정일: 2026-07-31
        """
        return int((time.monotonic() - started) * 1000)
