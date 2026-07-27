from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

from azure.ai.projects import AIProjectClient
from azure.identity import get_bearer_token_provider
from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI

from app.settings import AppSettings

LOGGER = logging.getLogger(__name__)


class FoundryModelClient:
    """Direct Foundry model client for reasoning and embeddings."""

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.config = settings.get_ai_agent_config()
        self._enabled = bool(settings.use_real_llm())
        self._project_client: Optional[AIProjectClient] = None
        self._openai_client: Optional[Any] = None
        self._embeddings_fallback_client: Optional[AzureOpenAI] = None
        self._project_embeddings_supported = True
        self._last_metrics: Optional[Dict[str, Any]] = None

        self._foundry_project_endpoint = str(self.config.get("foundry_project_endpoint") or "").strip().rstrip("/")
        self._openai_api_version = str(self.config.get("openai_api_version") or "2025-03-01-preview").strip()

        self._init_session()

    def _init_session(self) -> None:
        if not self._enabled:
            return
        if not self._foundry_project_endpoint:
            LOGGER.error(
                "Foundry project endpoint missing. Set AI_FOUNDRY_PROJECT_ENDPOINT (or ai_agent.foundry_project_endpoint)."
            )
            return
        try:
            credential = DefaultAzureCredential()
            self._project_client = AIProjectClient(
                endpoint=self._foundry_project_endpoint,
                credential=credential,
            )
            try:
                self._openai_client = self._project_client.get_openai_client(api_version=self._openai_api_version)
            except TypeError:
                self._openai_client = self._project_client.get_openai_client()
        except Exception as exc:
            LOGGER.error("Failed to initialize Foundry model client: %s", exc)
            self._project_client = None
            self._openai_client = None

    def _build_embeddings_fallback_client(self) -> Optional[AzureOpenAI]:
        if self._embeddings_fallback_client is not None:
            return self._embeddings_fallback_client

        parsed = urlparse(self._foundry_project_endpoint)
        hostname = (parsed.hostname or "").strip().lower()
        suffix = ".services.ai.azure.com"
        if not hostname.endswith(suffix):
            return None

        subdomain = hostname[: -len(suffix)]
        if not subdomain:
            return None

        azure_endpoint = f"https://{subdomain}.openai.azure.com"
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )

        self._embeddings_fallback_client = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_version=self._openai_api_version,
            azure_ad_token_provider=token_provider,
        )
        return self._embeddings_fallback_client

    def is_available(self) -> bool:
        return self._enabled and self._openai_client is not None

    @staticmethod
    def _to_dict(obj: Any) -> Dict[str, Any]:
        if isinstance(obj, dict):
            return obj
        if obj is None:
            return {}
        if hasattr(obj, "model_dump"):
            try:
                data = obj.model_dump()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        if hasattr(obj, "as_dict"):
            try:
                data = obj.as_dict()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        if hasattr(obj, "__dict__"):
            try:
                return dict(obj.__dict__)
            except Exception:
                pass
        return {}

    def _extract_text_from_response(self, response: Any) -> str:
        payload = self._to_dict(response)
        output = payload.get("output")
        if output is None and hasattr(response, "output"):
            output = getattr(response, "output")
        if not isinstance(output, list):
            return ""

        for item in output:
            item_payload = self._to_dict(item)
            if str(item_payload.get("type", "")).lower() != "message":
                continue
            content = item_payload.get("content")
            if not isinstance(content, list):
                continue
            for entry in content:
                entry_payload = self._to_dict(entry)
                if str(entry_payload.get("type", "")).lower() != "output_text":
                    continue
                text = entry_payload.get("text")
                if isinstance(text, str) and text.strip():
                    return text
        return ""

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(0))

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> Dict[str, Any]:
        if not self._openai_client:
            raise RuntimeError("Foundry model client is not available")

        response = self._openai_client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_output_tokens=max_tokens,
            temperature=temperature,
        )

        usage = self._to_dict(self._to_dict(response).get("usage"))
        self._last_metrics = {
            "provider": "azure_foundry_model",
            "model": self._to_dict(response).get("model") or model,
            "status": self._to_dict(response).get("status"),
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "response_id": self._to_dict(response).get("id"),
        }

        text = self._extract_text_from_response(response)
        if not text:
            raise RuntimeError("Model responses returned no assistant text output")
        return self._extract_json(text)

    def embed_texts(
        self,
        *,
        inputs: List[str],
        embedding_model: str,
    ) -> List[List[float]]:
        if not self._openai_client:
            raise RuntimeError("Foundry model client is not available")

        response = None
        if self._project_embeddings_supported:
            try:
                response = self._openai_client.embeddings.create(
                    model=embedding_model,
                    input=inputs,
                )
            except Exception as exc:
                error_text = str(exc).lower()
                if "404" in error_text or "not found" in error_text:
                    self._project_embeddings_supported = False
                fallback_client = self._build_embeddings_fallback_client()
                if fallback_client is None:
                    raise RuntimeError(
                        f"Embedding request failed with project endpoint and no fallback endpoint could be derived: {exc}"
                    ) from exc
                response = fallback_client.embeddings.create(
                    model=embedding_model,
                    input=inputs,
                )

        if response is None:
            fallback_client = self._build_embeddings_fallback_client()
            if fallback_client is None:
                raise RuntimeError(
                    "Embedding request failed: project endpoint embeddings disabled and no fallback endpoint could be derived"
                )
            response = fallback_client.embeddings.create(
                model=embedding_model,
                input=inputs,
            )

        data = getattr(response, "data", None)
        if data is None and isinstance(response, dict):
            data = response.get("data")
        if not isinstance(data, list):
            raise RuntimeError("Invalid embeddings response format: missing data list")

        vectors: List[List[float]] = []
        for item in data:
            emb = getattr(item, "embedding", None)
            if emb is None and isinstance(item, dict):
                emb = item.get("embedding")
            if isinstance(emb, list):
                vectors.append(emb)

        if len(vectors) != len(inputs):
            raise RuntimeError(
                "Embedding response size mismatch. Ensure AI_FOUNDRY_EMBEDDING_MODEL is a valid deployed embedding model name."
            )
        return vectors

    def get_last_metrics(self) -> Optional[Dict[str, Any]]:
        return self._last_metrics


def create_model_client(settings: AppSettings) -> FoundryModelClient:
    return FoundryModelClient(settings)
