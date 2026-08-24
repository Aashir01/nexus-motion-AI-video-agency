"""Agent runtime.

An "agent" here is a narrow, schema-bound specialist: one system prompt, one
Pydantic output type, one job.  That is a deliberate departure from a generic
crew framework — for a 130-shot episode you need every model turn to be
*validated* and *resumable*, not conversational.

Three things the runtime guarantees:

* **Typed output.**  The Pydantic model is converted to a JSON Schema and passed
  to the router, which uses native structured output where the provider supports
  it and falls back to instruction + repair where it doesn't.
* **Repair, not failure.**  A malformed or schema-invalid response is fed back to
  the model once with the validation errors attached before the call is failed.
* **Cost attribution.**  Every call lands in the job's ledger under its role.
"""
from __future__ import annotations

import json
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from nexus.routing.router import ModelRouter, Requirements
from nexus.routing.types import LLMRequest, ModelResponse
from nexus.util.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class Agent(Generic[T]):
    role: str = "agent"
    output_model: type[BaseModel]
    system_prompt: str = ""
    max_tokens: int = 16000
    effort: str | None = None
    temperature: float | None = None

    def __init__(self, router: ModelRouter):
        self.router = router

    # ── prompt construction ──────────────────────────────────────────────

    def build_system(self, **_: object) -> str:
        return self.system_prompt

    def schema(self) -> dict:
        return _simplify_schema(self.output_model.model_json_schema())

    # ── execution ────────────────────────────────────────────────────────

    async def run(
        self,
        prompt: str,
        *,
        system: str | None = None,
        label: str | None = None,
        image_urls: list[str] | None = None,
        requirements: Requirements | None = None,
        max_tokens: int | None = None,
    ) -> T:
        schema = self.schema()
        req = LLMRequest(
            system=system or self.build_system(),
            prompt=prompt,
            json_schema=schema,
            max_tokens=max_tokens or self.max_tokens,
            temperature=self.temperature,
            effort=self.effort,  # type: ignore[arg-type]
            image_urls=image_urls or [],
            label=label or self.role,
        )
        response = await self.router.text(self.role, req, requirements=requirements)
        try:
            return self._validate(response)
        except ValidationError as exc:
            log.info("agent_output_invalid_retrying",
                     extra={"role": self.role, "errors": exc.error_count()})
            repaired = await self._repair(req, response, exc)
            return self._validate(repaired)

    def _validate(self, response: ModelResponse) -> T:
        payload = response.parsed
        if payload is None:
            from nexus.providers._json import extract_json

            payload = extract_json(response.text or "")
        return self.output_model.model_validate(payload)  # type: ignore[return-value]

    async def _repair(
        self, req: LLMRequest, previous: ModelResponse, error: ValidationError
    ) -> ModelResponse:
        problems = json.dumps(
            [
                {"path": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
                for e in error.errors()[:25]
            ],
            indent=1,
        )
        repair_req = LLMRequest(
            system=req.system,
            prompt=(
                f"{req.prompt}\n\n"
                "─── REPAIR PASS ───\n"
                "Your previous response did not satisfy the schema. Here is what you returned:\n"
                f"{(previous.text or '')[:6000]}\n\n"
                f"These are the validation failures:\n{problems}\n\n"
                "Return the corrected JSON document. Fix only what is broken; keep every "
                "field that was already valid identical."
            ),
            json_schema=req.json_schema,
            max_tokens=req.max_tokens,
            temperature=0.0,
            effort=req.effort,
            label=f"{req.label}-repair",
        )
        return await self.router.text(self.role, repair_req)


def _simplify_schema(schema: dict) -> dict:
    """Inline `$defs` and drop keywords that trip strict provider validators.

    Providers vary wildly in JSON-Schema support; a flattened, conservative
    schema is the reliable common denominator across ten vendors.
    """
    defs = schema.pop("$defs", {}) or schema.pop("definitions", {}) or {}

    def resolve(node, depth: int = 0):
        if depth > 12:
            return {"type": "object"}
        if isinstance(node, list):
            return [resolve(n, depth + 1) for n in node]
        if not isinstance(node, dict):
            return node

        if "$ref" in node:
            ref = node["$ref"].rsplit("/", 1)[-1]
            target = defs.get(ref)
            if target is None:
                return {"type": "object"}
            merged = {k: v for k, v in node.items() if k != "$ref"}
            return {**resolve(target, depth + 1), **resolve(merged, depth + 1)}

        out = {}
        for key, value in node.items():
            if key in ("additionalProperties", "discriminator", "$schema", "$id"):
                continue
            if key == "anyOf":
                branches = [b for b in value if b.get("type") != "null"]
                if len(branches) == 1:
                    resolved = resolve(branches[0], depth + 1)
                    out.update(resolved if isinstance(resolved, dict) else {})
                    continue
                out[key] = resolve(branches or value, depth + 1)
                continue
            out[key] = resolve(value, depth + 1)
        return out

    return resolve(schema)
