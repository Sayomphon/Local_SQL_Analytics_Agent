"""LLM backend abstraction.

The agent only ever sees :class:`LLMBackend`, so swapping the model (Qwen on a
Colab GPU, Ollama on a laptop, scripted replay in tests) changes one
constructor call and nothing else. No backend here talks to a paid API — the
project constraint is zero API cost.
"""

from __future__ import annotations

import json
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Sequence

DEFAULT_MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
FALLBACK_MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"


class LLMBackend(ABC):
    """Text-completion interface. Implementations must be deterministic when
    ``temperature == 0`` so evaluation runs are reproducible."""

    name: str = "abstract"

    @abstractmethod
    def complete(
        self, prompt: str, *, max_new_tokens: int = 512, temperature: float = 0.0
    ) -> str:
        """Return the raw model text for one prompt."""


class ScriptedBackendExhaustedError(RuntimeError):
    """Raised when a scripted backend runs out of canned responses."""


class ScriptedBackend(LLMBackend):
    """Deterministic replay backend for tests and golden demo scenarios.

    This simulates model output; it is never used for reported quality
    metrics. Responses are consumed strictly in order.
    """

    name = "scripted"

    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []  # prompts received, for assertions in tests

    def complete(
        self, prompt: str, *, max_new_tokens: int = 512, temperature: float = 0.0
    ) -> str:
        self.calls.append(prompt)
        if not self._responses:
            raise ScriptedBackendExhaustedError(
                "scripted backend has no responses left "
                f"(received {len(self.calls)} prompts)"
            )
        return self._responses.pop(0)


class TransformersBackend(LLMBackend):
    """Local HuggingFace inference (intended for Colab GPU, 4-bit NF4).

    Imports are deferred so the core package works on machines without the
    GPU stack installed (``pip install -e ".[llm]"`` provides it).
    """

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        *,
        load_in_4bit: bool = True,
        device_map: str = "auto",
    ) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - GPU stack not in CI
            raise RuntimeError(
                "TransformersBackend requires the [llm] extra: "
                'pip install -e ".[llm]"'
            ) from exc

        self.name = model_id
        self._tokenizer = AutoTokenizer.from_pretrained(model_id)

        model_kwargs: dict = {"device_map": device_map, "torch_dtype": "auto"}
        if load_in_4bit:
            from transformers import BitsAndBytesConfig

            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
            )
        self._model = AutoModelForCausalLM.from_pretrained(model_id, **model_kwargs)

    def complete(
        self, prompt: str, *, max_new_tokens: int = 512, temperature: float = 0.0
    ) -> str:  # pragma: no cover - requires GPU
        messages = [{"role": "user", "content": prompt}]
        inputs = self._tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            return_tensors="pt",
        ).to(self._model.device)

        generate_kwargs: dict = {"max_new_tokens": max_new_tokens}
        if temperature and temperature > 0:
            generate_kwargs.update({"do_sample": True, "temperature": temperature})
        else:
            generate_kwargs["do_sample"] = False

        output = self._model.generate(inputs, **generate_kwargs)
        completion = output[0][inputs.shape[-1]:]
        return self._tokenizer.decode(completion, skip_special_tokens=True)


class OllamaBackend(LLMBackend):
    """Local Ollama server (default http://localhost:11434) for CPU/Metal dev.

    Only localhost URLs are accepted: this backend exists for local inference,
    never for shipping data to remote services.
    """

    def __init__(
        self,
        model: str = "qwen3:4b-instruct",
        *,
        base_url: str = "http://localhost:11434",
        timeout_s: float = 120.0,
    ) -> None:
        if not base_url.startswith(("http://localhost", "http://127.0.0.1")):
            raise ValueError("OllamaBackend only accepts localhost URLs")
        self.name = f"ollama:{model}"
        self._model = model
        self._url = base_url.rstrip("/") + "/api/generate"
        self._timeout_s = timeout_s

    def complete(
        self, prompt: str, *, max_new_tokens: int = 512, temperature: float = 0.0
    ) -> str:  # pragma: no cover - requires a running Ollama server
        payload = json.dumps(
            {
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_new_tokens,
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - localhost only, enforced above
            self._url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=self._timeout_s) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
        return body.get("response", "")
