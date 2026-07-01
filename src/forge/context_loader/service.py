from __future__ import annotations

import ast
from pathlib import Path

from forge.contracts import CodeContext, CodeSnippet, StructuredPlan, TaskContext, TaskContextRef, UserRequest


class ContextLoaderService:
    """Simple context loader that selects partial file contents for the coder."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        max_files: int = 5,
        max_snippets: int = 12,
        max_lines_per_snippet: int = 80,
        max_total_chars: int = 20000,
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.max_files = max_files
        self.max_snippets = max_snippets
        self.max_lines_per_snippet = max_lines_per_snippet
        self.max_total_chars = max_total_chars

    def load(
        self,
        *,
        user_request: UserRequest,
        plan: StructuredPlan,
        task_context: TaskContext | None = None,
    ) -> CodeContext:
        snippets: list[CodeSnippet] = []
        total_chars = 0

        for relative_path in plan.target_files[: self.max_files]:
            path = self.project_root / relative_path
            for snippet in self._load_file_snippets(relative_path, path):
                snippet_chars = len(snippet.content)
                if len(snippets) >= self.max_snippets or total_chars + snippet_chars > self.max_total_chars:
                    break
                snippets.append(snippet)
                total_chars += snippet_chars
            if len(snippets) >= self.max_snippets or total_chars >= self.max_total_chars:
                break

        related_context = self._build_related_context(task_context)
        constraints = list(dict.fromkeys(user_request.constraints + plan.assumptions))
        open_questions = list(dict.fromkeys(plan.required_context))

        return CodeContext(
            request_id=user_request.request_id,
            plan_id=plan.plan_id,
            snippets=snippets,
            related_context=related_context,
            constraints=constraints,
            open_questions=open_questions,
        )

    def _load_file_snippets(self, relative_path: str, path: Path) -> list[CodeSnippet]:
        if not path.exists() or not path.is_file():
            return []

        content = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            snippets = self._load_python_snippets(relative_path, content)
            if snippets:
                return snippets
        return [self._load_head_snippet(relative_path, content, reason="file head for target file")]

    def _load_python_snippets(self, relative_path: str, content: str) -> list[CodeSnippet]:
        lines = content.splitlines()
        snippets: list[CodeSnippet] = []

        import_end = 0
        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                import_end = index
                continue
            if import_end and stripped:
                break

        if import_end:
            snippets.append(
                CodeSnippet(
                    path=relative_path,
                    start_line=1,
                    end_line=min(import_end, self.max_lines_per_snippet),
                    reason="imports and module setup",
                    content="\n".join(lines[: min(import_end, self.max_lines_per_snippet)]),
                )
            )

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return snippets

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = node.lineno
                end = getattr(node, "end_lineno", node.lineno)
                bounded_end = min(end, start + self.max_lines_per_snippet - 1)
                snippets.append(
                    CodeSnippet(
                        path=relative_path,
                        start_line=start,
                        end_line=bounded_end,
                        reason=f"top-level {'class' if isinstance(node, ast.ClassDef) else 'function'} {node.name}",
                        content="\n".join(lines[start - 1 : bounded_end]),
                    )
                )
            if len(snippets) >= 3:
                break

        if not snippets:
            snippets.append(self._load_head_snippet(relative_path, content, reason="python file head"))
        return snippets

    def _load_head_snippet(self, relative_path: str, content: str, *, reason: str) -> CodeSnippet:
        lines = content.splitlines()
        bounded = lines[: self.max_lines_per_snippet]
        end_line = max(1, len(bounded))
        return CodeSnippet(
            path=relative_path,
            start_line=1,
            end_line=end_line,
            reason=reason,
            content="\n".join(bounded),
        )

    def _build_related_context(self, task_context: TaskContext | None) -> list[TaskContextRef]:
        if task_context is None or not task_context.events:
            return []

        related: list[TaskContextRef] = []
        for event in task_context.events[-5:]:
            related.append(
                TaskContextRef(
                    kind=event.event_type,
                    ref_id=event.event_id,
                    summary=self._summarize_event(event.payload),
                )
            )
        return related

    def _summarize_event(self, payload: dict[str, object]) -> str:
        if not payload:
            return "No payload details."

        preview_parts: list[str] = []
        for key, value in list(payload.items())[:3]:
            rendered = str(value)
            if len(rendered) > 80:
                rendered = rendered[:77] + "..."
            preview_parts.append(f"{key}={rendered}")
        return ", ".join(preview_parts)
