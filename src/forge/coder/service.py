from __future__ import annotations

from json import JSONDecodeError, loads
from pathlib import Path
from uuid import uuid4

from openai_codex import Codex, Sandbox

from forge.contracts import CoderInput, PatchResult


class CoderService:
    """Codex SDK-backed patch generator."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        model: str = "gpt-5.4-mini",
        sandbox: Sandbox = Sandbox.read_only,
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.model = model
        self.sandbox = sandbox

    def generate_patch(self, coder_input: CoderInput) -> PatchResult:
        prompt = self.build_prompt(coder_input)

        with Codex() as codex:
            thread = codex.thread_start(
                model=self.model,
                cwd=str(self.project_root),
                sandbox=self.sandbox,
            )
            result = thread.run(prompt)

        if not result.final_response:
            raise ValueError("Codex returned no final response for patch generation.")

        try:
            payload = loads(result.final_response)
        except JSONDecodeError as exc:
            raise ValueError("Codex did not return valid JSON for PatchResult.") from exc

        payload.setdefault("patch_id", f"patch-{uuid4()}")
        payload.setdefault("request_id", coder_input.user_request.request_id)
        payload.setdefault("plan_id", coder_input.plan.plan_id)
        return PatchResult.model_validate(payload)

    def build_prompt(self, coder_input: CoderInput) -> str:
        request = coder_input.user_request
        plan = coder_input.plan
        context = coder_input.code_context

        snippet_blocks = []
        for snippet in context.snippets:
            snippet_blocks.append(
                f"FILE: {snippet.path}:{snippet.start_line}-{snippet.end_line}\n"
                f"REASON: {snippet.reason}\n"
                "```python\n"
                f"{snippet.content}\n"
                "```"
            )

        related_context = "\n".join(
            f"- {item.kind} ({item.ref_id}): {item.summary}" for item in context.related_context
        ) or "- none"

        return (
            "You are the Coder stage in an implementation pipeline.\n"
            "Use the provided plan and code context to propose the smallest valid patch.\n"
            "Do not apply the patch. Do not explain outside JSON.\n\n"
            "Requirements:\n"
            "- Stay within the user's requested scope.\n"
            "- Prefer existing files listed in target_files.\n"
            "- Return only valid JSON matching the PatchResult contract.\n"
            "- Put code edits in changes and test edits in test_changes.\n"
            "- Each diff should be a unified diff fragment or structured patch text.\n\n"
            f"User request:\n{request.user_text}\n\n"
            f"Normalized goal:\n{request.normalized_goal}\n\n"
            f"Plan summary:\n{plan.summary}\n\n"
            f"Plan strategy:\n{plan.strategy}\n\n"
            f"Target files:\n" + "\n".join(f"- {path}" for path in plan.target_files) + "\n\n"
            f"Success checks:\n" + "\n".join(f"- {item}" for item in plan.success_checks) + "\n\n"
            f"Review focus:\n" + "\n".join(f"- {item}" for item in plan.review_focus) + "\n\n"
            f"Constraints:\n" + "\n".join(f"- {item}" for item in context.constraints) + "\n\n"
            f"Open questions:\n" + ("\n".join(f"- {item}" for item in context.open_questions) or "- none") + "\n\n"
            f"Related task context:\n{related_context}\n\n"
            "Code context snippets:\n"
            + ("\n\n".join(snippet_blocks) if snippet_blocks else "No snippets were provided.")
            + "\n\n"
            "Return JSON with this exact shape:\n"
            "{\n"
            '  "patch_id": "optional-string",\n'
            '  "request_id": "optional-string",\n'
            '  "plan_id": "optional-string",\n'
            '  "rationale": "why this patch satisfies the plan",\n'
            '  "changes": [\n'
            '    {\n'
            '      "path": "repo/relative/path.py",\n'
            '      "change_type": "add|modify|delete|rename",\n'
            '      "summary": "short summary",\n'
            '      "diff": "--- a/...\\n+++ b/...\\n@@ ..."\n'
            "    }\n"
            "  ],\n"
            '  "test_changes": [],\n'
            '  "warnings": []\n'
            "}"
        )
