"""Unified gateway for direct Azure AI Foundry SDK access."""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, Optional, Tuple

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

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
        agent_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
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
        agent_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> str:
        """Generate plain text response from the provider."""

    def get_embedding_client(self) -> Optional[Any]:
        """Return embedding-capable client when available."""
        return None

    def get_last_metrics(self) -> Optional[Dict[str, Any]]:
        """Return metadata from the last generation call (tokens/timing/model)."""
        return None

    def create_conversation(self) -> Optional[str]:
        """Create and return a provider conversation id when supported."""
        return None


class FoundryAgentGateway(LLMGateway):
    """Azure AI Foundry Agent gateway implementation (direct SDK)."""

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.config = settings.get_ai_agent_config()
        generation_config = settings.get_llm_generation_config()
        self._enabled = bool(self.settings.use_real_llm())
        self._last_metrics: Optional[Dict[str, Any]] = None
        self._agent_client: Optional[AIProjectClient] = None
        self._openai_client: Optional[Any] = None
        self._rate_limit_max_attempts = max(
            1,
            int(
                os.getenv("AI_FOUNDRY_RATE_LIMIT_MAX_ATTEMPTS")
                or generation_config.get("max_attempts")
                or 3
            ),
        )
        self._rate_limit_base_seconds = max(
            1,
            int(os.getenv("AI_FOUNDRY_RATE_LIMIT_BASE_SECONDS") or 2),
        )
        self._rate_limit_max_seconds = max(
            self._rate_limit_base_seconds,
            int(os.getenv("AI_FOUNDRY_RATE_LIMIT_MAX_SECONDS") or 30),
        )

        self._foundry_project_endpoint = str(self.config.get("foundry_project_endpoint") or "").strip().rstrip("/")
        self._openai_api_version = str(self.config.get("openai_api_version") or "").strip()

        self._agent_reference = self.config.get("agent_reference")
        self._chat_agent_reference = self.config.get("chat_agent_reference")
        self._resilience_agent_reference = self.config.get("resilience_agent_reference")
        self._annotations_agent_reference = self.config.get("annotations_agent_reference")
        self._terraform_agent_reference = self.config.get("terraform_agent_reference")

        self._init_client()

    def _init_client(self) -> None:
        if not self._enabled:
            return

        if not self._foundry_project_endpoint:
            LOGGER.error(
                "Foundry project endpoint missing. Set AI_FOUNDRY_PROJECT_ENDPOINT (or ai_agent.foundry_project_endpoint)."
            )
            return

        try:
            credential = DefaultAzureCredential()
            self._agent_client = AIProjectClient(
                endpoint=self._foundry_project_endpoint,
                credential=credential,
            )
            if self._openai_api_version:
                os.environ["OPENAI_API_VERSION"] = self._openai_api_version
            self._openai_client = self._agent_client.get_openai_client()
            LOGGER.info("Initialized Azure AI Foundry direct SDK gateway")
        except Exception as exc:
            LOGGER.error("Failed to initialize Azure AI Foundry SDK client: %s", exc)
            self._agent_client = None
            self._openai_client = None

    @staticmethod
    def _extract_json_object(text: str) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(0))

    @staticmethod
    def _to_dict(obj: Any) -> Dict[str, Any]:
        if isinstance(obj, dict):
            return obj
        if obj is None:
            return {}
        if hasattr(obj, "model_dump"):
            try:
                data = obj.model_dump(mode="python", warnings=False)  # pydantic v2
                if isinstance(data, dict):
                    return data
            except TypeError:
                try:
                    data = obj.model_dump()  # pydantic v2 (older signature)
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
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
        output_items = payload.get("output")

        if not isinstance(output_items, list) and hasattr(response, "output"):
            output_items = getattr(response, "output")

        if not isinstance(output_items, Iterable):
            return ""

        for item in output_items:
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

    def _resolve_agent_id(self, override_agent_id: Optional[str] = None) -> Optional[str]:
        if override_agent_id is None:
            return None
        selected_value = str(override_agent_id).strip()
        if not selected_value:
            return None

        alias_map = {
            "terraform-agent": "terraform-compiler-agent",
        }
        if selected_value in alias_map:
            LOGGER.warning(
                "Agent reference '%s' is deprecated; using '%s'.",
                selected_value,
                alias_map[selected_value],
            )
            return alias_map[selected_value]

        if selected_value.startswith("asst_") and self._agent_client is not None:
            try:
                agents_ops = self._agent_client.agents
                list_fn = getattr(agents_ops, "list", None) or getattr(agents_ops, "list_agents", None)
                if list_fn is None:
                    raise RuntimeError("Agent listing API not available on current SDK")
                for agent in list_fn(limit=200):
                    agent_id = str(self._to_dict(agent).get("id") or "").strip()
                    if agent_id != selected_value:
                        continue
                    agent_name = str(self._to_dict(agent).get("name") or "").strip()
                    if agent_name:
                        LOGGER.warning(
                            "Agent id reference detected (%s); resolved to name '%s'.",
                            selected_value,
                            agent_name,
                        )
                        return agent_name
            except Exception:
                LOGGER.warning("Failed to resolve agent id '%s' to name; using as-is.", selected_value)

        return selected_value

    def _extract_usage(self, response: Any) -> Dict[str, Any]:
        usage = {}
        if hasattr(response, "usage"):
            usage = self._to_dict(getattr(response, "usage"))
        if not usage:
            usage = self._to_dict(self._to_dict(response).get("usage"))
        return usage

    def _extract_response_id(self, response: Any) -> Optional[str]:
        rid = self._to_dict(response).get("id")
        if not rid and hasattr(response, "id"):
            rid = getattr(response, "id")
        if rid is None:
            return None
        rid_s = str(rid).strip()
        return rid_s or None

    @classmethod
    def _extract_conversation_id(
        cls,
        response: Any,
        fallback_conversation_id: Optional[str] = None,
    ) -> Optional[str]:
        payload = cls._to_dict(response)

        direct_id = payload.get("conversation_id")
        if isinstance(direct_id, str) and direct_id.strip():
            return direct_id.strip()

        conversation_payload = payload.get("conversation")
        if isinstance(conversation_payload, str) and conversation_payload.strip():
            return conversation_payload.strip()

        conversation_payload_dict = cls._to_dict(conversation_payload)
        conversation_id = conversation_payload_dict.get("id")
        if isinstance(conversation_id, str) and conversation_id.strip():
            return conversation_id.strip()

        if hasattr(response, "conversation"):
            conversation_attr = getattr(response, "conversation")
            if isinstance(conversation_attr, str) and conversation_attr.strip():
                return conversation_attr.strip()
            conversation_attr_dict = cls._to_dict(conversation_attr)
            conversation_attr_id = conversation_attr_dict.get("id")
            if isinstance(conversation_attr_id, str) and conversation_attr_id.strip():
                return conversation_attr_id.strip()

        if fallback_conversation_id and str(fallback_conversation_id).strip():
            return str(fallback_conversation_id).strip()
        return None

    def _run_agent(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        agent_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> str:
        selected_agent_reference = self._resolve_agent_id(agent_id)
        if not self._openai_client or not selected_agent_reference:
            raise RuntimeError("Foundry SDK gateway is not available")

        combined_prompt = user_prompt
        if system_prompt and str(system_prompt).strip():
            combined_prompt = (
                "Runtime hint:\n"
                f"{system_prompt}\n\n"
                "Input:\n"
                f"{user_prompt}"
            )

        extra_body: Dict[str, Any] = {
            "agent_reference": {
                "type": "agent_reference",
                "name": selected_agent_reference,
            }
        }
        create_args: Dict[str, Any] = {
            "input": combined_prompt,
            "extra_body": extra_body,
        }
        if conversation_id and str(conversation_id).strip():
            create_args["conversation"] = str(conversation_id).strip()

        response, retry_meta = self._create_response_with_retries(create_args)

        usage = self._extract_usage(response)
        response_payload = self._to_dict(response)
        resolved_conversation_id = self._extract_conversation_id(
            response,
            fallback_conversation_id=conversation_id,
        )

        self._last_metrics = {
            "provider": "azure_ai_foundry_agent",
            "model": response_payload.get("model"),
            "status": response_payload.get("status"),
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "conversation_id": resolved_conversation_id,
            "response_id": self._extract_response_id(response),
            "agent_reference": selected_agent_reference,
            "prompt_chars": len(combined_prompt),
            "rate_limit_retries": retry_meta.get("retries", 0),
            "rate_limit_wait_seconds": round(float(retry_meta.get("wait_seconds", 0.0)), 3),
            "rate_limit_encounters": retry_meta.get("encounters", 0),
        }

        LOGGER.info(
            "LLM call metrics agent=%s model=%s status=%s prompt_chars=%s input_tokens=%s output_tokens=%s total_tokens=%s retries=%s wait_seconds=%s response_id=%s",
            self._last_metrics.get("agent_reference"),
            self._last_metrics.get("model"),
            self._last_metrics.get("status"),
            self._last_metrics.get("prompt_chars"),
            self._last_metrics.get("prompt_tokens"),
            self._last_metrics.get("completion_tokens"),
            self._last_metrics.get("total_tokens"),
            self._last_metrics.get("rate_limit_retries"),
            self._last_metrics.get("rate_limit_wait_seconds"),
            self._last_metrics.get("response_id"),
        )

        text = self._extract_text_from_response(response)
        if text:
            return text

        raise RuntimeError("Foundry agent returned no assistant text output")

    @staticmethod
    def _is_rate_limit_error(error: Exception) -> bool:
        text = str(error).lower()
        if "rate limit" in text or "too many requests" in text or "too_many_requests" in text:
            return True

        status_code = getattr(error, "status_code", None)
        if status_code == 429:
            return True

        response = getattr(error, "response", None)
        response_status = getattr(response, "status_code", None)
        if response_status == 429:
            return True

        return " 429" in text or "(429)" in text

    @staticmethod
    def _extract_retry_after_seconds(error: Exception) -> int:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        if headers:
            for key in ("retry-after", "Retry-After", "x-ms-retry-after-ms"):
                value = headers.get(key) if hasattr(headers, "get") else None
                if value is None:
                    continue
                try:
                    numeric = float(str(value).strip())
                    if key.lower().endswith("-ms"):
                        numeric = numeric / 1000.0
                    return max(1, int(round(numeric)))
                except (TypeError, ValueError):
                    continue

        text = str(error)
        patterns = [
            r"retry\s+after\s+(\d+)\s+seconds",
            r"retry-after\D*(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            try:
                return max(1, int(match.group(1)))
            except ValueError:
                continue
        return 0

    def _create_response_with_retries(self, create_args: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        last_error: Optional[Exception] = None
        retry_count = 0
        total_wait_seconds = 0.0
        rate_limit_encounters = 0

        for attempt in range(1, self._rate_limit_max_attempts + 1):
            try:
                return self._openai_client.responses.create(**create_args), {
                    "retries": retry_count,
                    "wait_seconds": total_wait_seconds,
                    "encounters": rate_limit_encounters,
                }
            except Exception as error:
                if not self._is_rate_limit_error(error):
                    raise

                last_error = error
                rate_limit_encounters += 1
                if attempt >= self._rate_limit_max_attempts:
                    break

                retry_after_seconds = self._extract_retry_after_seconds(error)
                exponential_seconds = self._rate_limit_base_seconds * (2 ** (attempt - 1))
                wait_seconds = retry_after_seconds if retry_after_seconds > 0 else exponential_seconds
                wait_seconds = min(max(1, wait_seconds), self._rate_limit_max_seconds)
                jitter_seconds = random.uniform(0.0, 0.5)

                LOGGER.warning(
                    "Foundry agent throttled (429). Retrying in %.2fs (attempt %d/%d)",
                    wait_seconds + jitter_seconds,
                    attempt + 1,
                    self._rate_limit_max_attempts,
                )
                sleep_seconds = wait_seconds + jitter_seconds
                total_wait_seconds += sleep_seconds
                retry_count += 1
                time.sleep(sleep_seconds)

        if last_error:
            raise RuntimeError(
                f"Foundry agent rate limited after {self._rate_limit_max_attempts} attempts"
            ) from last_error

        raise RuntimeError("Foundry agent call failed without a captured error")

    def is_available(self) -> bool:
        any_configured_agent = any(
            bool(str(value).strip())
            for value in [
                self._chat_agent_reference,
                self._resilience_agent_reference,
                self._annotations_agent_reference,
                self._terraform_agent_reference,
                self._agent_reference,
            ]
            if value is not None
        )
        return self._enabled and self._openai_client is not None and any_configured_agent

    def create_conversation(self) -> Optional[str]:
        if not self._openai_client:
            raise RuntimeError("Foundry SDK gateway is not available")

        conversation = self._openai_client.conversations.create()
        conversation_payload = self._to_dict(conversation)
        conversation_id = conversation_payload.get("id")
        if not conversation_id and hasattr(conversation, "id"):
            conversation_id = getattr(conversation, "id")

        if conversation_id is None:
            raise RuntimeError("Conversation creation succeeded but no conversation id was returned")

        conversation_id_str = str(conversation_id).strip()
        if not conversation_id_str:
            raise RuntimeError("Conversation creation returned an empty conversation id")

        return conversation_id_str

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        response_text = self._run_agent(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )
        return self._extract_json_object(response_text)

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> str:
        return self._run_agent(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )

    def get_last_metrics(self) -> Optional[Dict[str, Any]]:
        return self._last_metrics


def create_llm_gateway(settings: AppSettings) -> LLMGateway:
    """Create the single supported LLM gateway (direct Foundry SDK mode)."""
    LOGGER.info("Using Foundry direct SDK gateway provider")
    return FoundryAgentGateway(settings)
