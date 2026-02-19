from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

import requests

from app.settings import AppSettings

LOGGER = logging.getLogger(__name__)


class APIMModelClient:
    """Direct APIM /model client for reasoning and embeddings."""

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.config = settings.get_ai_agent_config()
        self._enabled = bool(settings.use_real_llm())
        self._session: Optional[requests.Session] = None
        self._last_metrics: Optional[Dict[str, Any]] = None

        gateway_base_url = str(self.config.get("gateway_base_url") or "").strip().rstrip("/")

        self._base_url = gateway_base_url
        self._model_base_url = f"{gateway_base_url}/model" if gateway_base_url else ""
        self._subscription_key = str(self.config.get("subscription_key") or "").strip()
        self._subscription_header = str(self.config.get("subscription_header_name") or "api-key").strip()

        self._init_session()

    def _init_session(self) -> None:
        if not self._enabled:
            return
        if not self._model_base_url:
            LOGGER.error("APIM base URL missing. Set AI_GATEWAY_AGENT_BASE_URL (or ai_agent.gateway_base_url).")
            return
        if not self._subscription_key:
            LOGGER.error("APIM subscription key missing. Set AI_GATEWAY_SUBSCRIPTION_KEY.")
            return
        self._session = requests.Session()

    def is_available(self) -> bool:
        return self._enabled and self._session is not None

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            self._subscription_header: self._subscription_key,
        }

    def _http(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: int = 60) -> Dict[str, Any]:
        if not self._session:
            raise RuntimeError("APIM model session is not available")

        url = f"{self._model_base_url}{path}"
        response = self._session.request(
            method=method,
            url=url,
            headers=self._headers(),
            json=payload,
            timeout=timeout,
        )

        if response.status_code >= 400:
            raise RuntimeError(f"APIM model call failed ({response.status_code}) {method} {url}: {response.text[:1500]}")

        if not response.text:
            return {}

        try:
            return response.json()
        except Exception as exc:
            raise RuntimeError(f"Invalid JSON response from {method} {url}: {response.text[:500]}") from exc

    @staticmethod
    def _extract_text_from_response(payload: Dict[str, Any]) -> str:
        output = payload.get("output")
        if not isinstance(output, list):
            return ""

        for item in output:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).lower() != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for entry in content:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("type", "")).lower() != "output_text":
                    continue
                text = entry.get("text")
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
        payload = {
            "model": model,
            "input": f"System:\n{system_prompt}\n\nUser:\n{user_prompt}",
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "text": {"format": {"type": "text"}},
        }
        result = self._http("POST", "/responses", payload=payload)
        usage = result.get("usage") or {}
        self._last_metrics = {
            "provider": "azure_apim_model",
            "model": result.get("model") or model,
            "status": result.get("status"),
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "response_id": result.get("id"),
        }

        text = self._extract_text_from_response(result)
        if not text:
            raise RuntimeError("Model responses returned no assistant text output")
        return self._extract_json(text)

    def embed_texts(
        self,
        *,
        inputs: List[str],
        embedding_model: str,
    ) -> List[List[float]]:
        result = self._http(
            "POST",
            "/embeddings",
            payload={
                "model": embedding_model,
                "input": inputs,
            },
        )
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            raise RuntimeError("Invalid embeddings response format: missing data list")

        vectors = [item.get("embedding") for item in data if isinstance(item, dict) and isinstance(item.get("embedding"), list)]
        if len(vectors) != len(inputs):
            raise RuntimeError(
                "Embedding response size mismatch. Ensure ai_agent.embedding_model is a valid deployed embedding model name."
            )
        return vectors

    def get_last_metrics(self) -> Optional[Dict[str, Any]]:
        return self._last_metrics


def create_model_client(settings: AppSettings) -> APIMModelClient:
    return APIMModelClient(settings)
