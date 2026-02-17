"""Unified gateway for APIM + Azure AI Foundry LLM access."""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import requests

from app.settings import AppSettings

LOGGER = logging.getLogger(__name__)


class LLMGateway(ABC):
    """Provider-agnostic LLM access boundary."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return whether the provider is ready to serve requests."""

    @abstractmethod
    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
        memory_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a JSON object response from the provider."""

    @abstractmethod
    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
        memory_key: Optional[str] = None,
    ) -> str:
        """Generate plain text response from the provider."""

    def get_embedding_client(self) -> Optional[Any]:
        """Return embedding-capable client when available."""
        return None

    def get_last_metrics(self) -> Optional[Dict[str, Any]]:
        """Return metadata from the last generation call (tokens/timing/model)."""
        return None


class FoundryAgentGateway(LLMGateway):
    """Azure AI Foundry Agent gateway implementation."""

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.config = settings.get_ai_agent_config()
        self._enabled = bool(self.settings.use_real_llm())
        self._session: Optional[requests.Session] = None
        self._thread_ids: Dict[str, str] = {}
        self._last_metrics: Optional[Dict[str, Any]] = None
        self._memory_scope = str(self.config.get("memory_scope", "subscription_or_workload")).strip().lower()
        self._agent_id = self.config.get("agent_id")
        self._gateway_base_url = str(self.config.get("gateway_base_url") or "").strip().rstrip("/")
        self._subscription_key = str(self.config.get("subscription_key") or "").strip()
        self._subscription_header = str(self.config.get("subscription_header_name") or "api-key").strip()
        self._api_version = str(self.config.get("api_version") or "2025-05-01").strip()
        self._run_timeout_seconds = int(self.config.get("run_timeout_seconds", 120))
        self._poll_interval_seconds = float(self.config.get("poll_interval_seconds", 1.5))
        self._init_client()

    def _init_client(self) -> None:
        if not self._enabled:
            return

        if not self._agent_id:
            LOGGER.error(
                "ai_agent.agent_id (or AZURE_AI_FOUNDRY_AGENT_ID) is required when llm.provider=azure_ai_foundry_agent"
            )
            return

        if not str(self._agent_id).startswith("asst"):
            LOGGER.error("Foundry agent id must be exact asst_* id when using APIM gateway")
            return

        if not self._gateway_base_url:
            LOGGER.error(
                "APIM base URL missing. Set AI_GATEWAY_AGENT_BASE_URL (or ai_agent.gateway_base_url)."
            )
            return

        if not self._subscription_key:
            LOGGER.error(
                "APIM subscription key missing. Set AI_GATEWAY_SUBSCRIPTION_KEY."
            )
            return

        self._session = requests.Session()
        LOGGER.info("Initialized Azure AI Foundry agent gateway via APIM")

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            self._subscription_header: self._subscription_key,
        }

    def _http(self, method: str, path_with_query: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self._session:
            raise RuntimeError("Foundry APIM session is not available")

        url = f"{self._gateway_base_url}{path_with_query}"
        response = self._session.request(
            method=method,
            url=url,
            headers=self._headers(),
            json=payload,
            timeout=60,
        )

        if response.status_code >= 400:
            body = response.text[:1500]
            raise RuntimeError(f"APIM Foundry call failed ({response.status_code}) {method} {url}: {body}")

        if not response.text:
            return {}

        try:
            return response.json()
        except Exception as exc:
            raise RuntimeError(f"Invalid JSON response from {method} {url}: {response.text[:500]}") from exc

    @staticmethod
    def _extract_json_object(text: str) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(0))

    def _memory_key(self, runtime_key: Optional[str] = None) -> str:
        if self._memory_scope in {"none", "stateless"}:
            return "stateless"
        if runtime_key and runtime_key.strip():
            return runtime_key.strip()
        return "shared"

    def _get_thread_id(self, runtime_key: Optional[str] = None) -> str:
        if not self._session:
            raise RuntimeError("Foundry APIM session is not available")

        memory_key = self._memory_key(runtime_key)
        if memory_key == "stateless":
            thread = self._http("POST", f"/threads?api-version={self._api_version}", payload={})
            thread_id = thread.get("id")
            if not thread_id:
                raise RuntimeError("Thread creation returned no id")
            LOGGER.debug("Memory scope stateless: created thread_id=%s", thread_id)
            return str(thread_id)

        cached = self._thread_ids.get(memory_key)
        if cached:
            LOGGER.debug("Memory key '%s' reusing thread_id=%s", memory_key, cached)
            return cached

        thread = self._http("POST", f"/threads?api-version={self._api_version}", payload={})
        thread_id = thread.get("id")
        if not thread_id:
            raise RuntimeError("Thread creation returned no id")
        thread_id_value = str(thread_id)
        self._thread_ids[memory_key] = thread_id_value
        LOGGER.debug("Memory key '%s' mapped to new thread_id=%s", memory_key, thread_id_value)
        return thread_id_value

    @staticmethod
    def _iter_messages(messages: Any) -> List[Any]:
        if messages is None:
            return []
        if isinstance(messages, dict):
            data = messages.get("data")
            if isinstance(data, list):
                return data
        if hasattr(messages, "data") and isinstance(messages.data, list):
            return messages.data
        if isinstance(messages, list):
            return messages
        try:
            return list(messages)
        except Exception:
            return []

    @staticmethod
    def _extract_message_text(message: Any) -> str:
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: List[str] = []
                for item in content:
                    if isinstance(item, dict):
                        text_obj = item.get("text")
                        if isinstance(text_obj, dict) and text_obj.get("value"):
                            parts.append(str(text_obj["value"]))
                        elif isinstance(text_obj, str) and text_obj.strip():
                            parts.append(text_obj.strip())
                        elif item.get("value"):
                            parts.append(str(item.get("value")))
                if parts:
                    return "\n".join(parts)

        text_messages = getattr(message, "text_messages", None)
        if text_messages:
            parts = []
            for entry in text_messages:
                if isinstance(entry, dict):
                    text_obj = entry.get("text")
                    if isinstance(text_obj, dict) and text_obj.get("value"):
                        parts.append(str(text_obj["value"]))
                        continue
                text_obj = getattr(entry, "text", None)
                value = getattr(text_obj, "value", None) if text_obj else None
                if value:
                    parts.append(str(value))
            if parts:
                return "\n".join(parts)

        content = getattr(message, "content", None)
        if isinstance(content, list):
            parts = []
            for item in content:
                text_obj = getattr(item, "text", None)
                if text_obj is not None:
                    value = getattr(text_obj, "value", None)
                    if value:
                        parts.append(str(value))
                        continue

                if isinstance(item, dict):
                    item_text = item.get("text")
                    if isinstance(item_text, dict) and item_text.get("value"):
                        parts.append(str(item_text["value"]))
            if parts:
                return "\n".join(parts)

        if isinstance(content, str):
            return content

        return ""

    def _run_agent(self, *, system_prompt: str, user_prompt: str, memory_key: Optional[str] = None) -> str:
        if not self._session or not self._agent_id:
            raise RuntimeError("Foundry agent gateway is not available")

        thread_id = self._get_thread_id(memory_key)
        combined_prompt = (
            "Follow these system instructions strictly:\n"
            f"{system_prompt}\n\n"
            "User request:\n"
            f"{user_prompt}"
        )

        self._http(
            "POST",
            f"/threads/{thread_id}/messages?api-version={self._api_version}",
            payload={"role": "user", "content": combined_prompt},
        )

        run = self._http(
            "POST",
            f"/threads/{thread_id}/runs?api-version={self._api_version}",
            payload={"assistant_id": self._agent_id},
        )
        run_id = str(run.get("id", "") or "")
        if not run_id:
            raise RuntimeError("Foundry run creation returned no id")

        run_dict = run
        deadline = time.time() + float(self._run_timeout_seconds)
        while True:
            status = str(run_dict.get("status", "")).lower()
            if status in {"completed", "failed", "cancelled", "expired"}:
                break

            if time.time() >= deadline:
                raise RuntimeError(f"Timed out waiting for Foundry run completion (run_id={run_id})")

            time.sleep(self._poll_interval_seconds)
            run_dict = self._http(
                "GET",
                f"/threads/{thread_id}/runs/{run_id}?api-version={self._api_version}",
            )

        created_at = run_dict.get("created_at")
        started_at = run_dict.get("started_at")
        completed_at = run_dict.get("completed_at")
        usage = run_dict.get("usage") or {}
        self._last_metrics = {
            "provider": "azure_ai_foundry_agent",
            "model": run_dict.get("model"),
            "status": str(run_dict.get("status", "")).lower() if run_dict.get("status") is not None else None,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "total_ms": int((completed_at - created_at) * 1000) if isinstance(created_at, int) and isinstance(completed_at, int) else None,
            "queue_ms": int((started_at - created_at) * 1000) if isinstance(created_at, int) and isinstance(started_at, int) else None,
            "processing_ms": int((completed_at - started_at) * 1000) if isinstance(started_at, int) and isinstance(completed_at, int) else None,
        }
        if str(run_dict.get("status", "")).lower() == "failed":
            raise RuntimeError(f"Foundry agent run failed: {run_dict.get('last_error', 'unknown error')}")

        messages = self._http("GET", f"/threads/{thread_id}/messages?api-version={self._api_version}")
        for message in self._iter_messages(messages):
            role_value = ""
            if isinstance(message, dict):
                role_value = str(message.get("role", "")).lower()
            else:
                role_value = str(getattr(message, "role", "")).lower()

            # Normalize enum-like values such as "MessageRole.AGENT" -> "agent"
            if "." in role_value:
                role_value = role_value.split(".")[-1]

            if role_value not in {"assistant", "agent"}:
                continue
            text = self._extract_message_text(message)
            if text:
                return text

        raise RuntimeError("Foundry agent returned no assistant text output")

    def is_available(self) -> bool:
        return self._enabled and self._session is not None and bool(self._agent_id)

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
        memory_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        response_text = self._run_agent(system_prompt=system_prompt, user_prompt=user_prompt, memory_key=memory_key)
        return self._extract_json_object(response_text)

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
        memory_key: Optional[str] = None,
    ) -> str:
        return self._run_agent(system_prompt=system_prompt, user_prompt=user_prompt, memory_key=memory_key)

    def get_last_metrics(self) -> Optional[Dict[str, Any]]:
        return self._last_metrics


def create_llm_gateway(settings: AppSettings) -> LLMGateway:
    """Create the single supported LLM gateway (APIM + Foundry agent)."""
    LOGGER.info("Using Foundry agent gateway provider (APIM + Foundry only mode)")
    return FoundryAgentGateway(settings)
