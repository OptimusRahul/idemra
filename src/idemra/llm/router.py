"""Hybrid local/cloud LLM completion — Ollama first, Claude cloud as fallback.

Uses litellm.Router's native fallbacks= directly rather than hand-rolling
retry/routing logic — see docs/architecture.md for why this pairing was
chosen, and the Phase 3 plan for the search check confirming litellm
already ships this.
"""

from __future__ import annotations

import os

from litellm import Router

DEFAULT_MODEL_ALIAS = "coder"
DEFAULT_TIMEOUT_SECONDS = 60


def build_router() -> Router:
    local_model = os.environ.get("IDEMRA_LOCAL_MODEL", "ollama/qwen2.5-coder:7b")
    ollama_url = os.environ.get("IDEMRA_OLLAMA_URL", "http://localhost:11434")
    cloud_model = os.environ.get("IDEMRA_CLOUD_MODEL", "claude-sonnet-5")

    model_list = [
        {
            "model_name": DEFAULT_MODEL_ALIAS,
            "litellm_params": {"model": local_model, "api_base": ollama_url},
        },
        {
            "model_name": DEFAULT_MODEL_ALIAS,
            "litellm_params": {"model": cloud_model},
        },
    ]
    return Router(
        model_list=model_list,
        fallbacks=[{DEFAULT_MODEL_ALIAS: [DEFAULT_MODEL_ALIAS]}],
        num_retries=1,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )


def complete(prompt: str, router: Router | None = None) -> str:
    router = router or build_router()
    response = router.completion(
        model=DEFAULT_MODEL_ALIAS,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content
