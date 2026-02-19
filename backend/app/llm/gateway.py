"""Unified gateway for APIM + Azure AI Foundry LLM access."""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

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


class FoundryAgentGateway(LLMGateway):
	"""Azure AI Foundry Agent gateway implementation."""

	def __init__(self, settings: AppSettings):
		self.settings = settings
		self.config = settings.get_ai_agent_config()
		self._enabled = bool(self.settings.use_real_llm())
		self._session: Optional[requests.Session] = None
		self._last_metrics: Optional[Dict[str, Any]] = None
		self._agent_reference = self.config.get("agent_reference")
		self._chat_agent_reference = self.config.get("chat_agent_reference")
		self._resilience_agent_reference = self.config.get("resilience_agent_reference")
		self._annotations_agent_reference = self.config.get("annotations_agent_reference")
		gateway_base_url = str(self.config.get("gateway_base_url") or "").strip().rstrip("/")
		self._gateway_base_url = gateway_base_url
		self._agent_base_url = f"{gateway_base_url}/agent" if gateway_base_url else ""
		self._subscription_key = str(self.config.get("subscription_key") or "").strip()
		self._subscription_header = str(self.config.get("subscription_header_name") or "api-key").strip()
		self._init_client()

	def _init_client(self) -> None:
		if not self._enabled:
			return

		if not self._agent_base_url:
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
		LOGGER.info("Initialized Azure AI Foundry agent gateway via APIM (conversations/responses)")

	def _headers(self) -> Dict[str, str]:
		return {
			"Content-Type": "application/json",
			self._subscription_header: self._subscription_key,
		}

	def _http(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
		if not self._session:
			raise RuntimeError("Foundry APIM session is not available")

		url = f"{self._agent_base_url}{path}"
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

	def _create_conversation(self) -> str:
		response = self._http("POST", "/conversations", payload={})
		conversation_id = response.get("id")
		if not conversation_id:
			raise RuntimeError("Conversation creation returned no id")
		return str(conversation_id)

	def _get_conversation_id(
		self,
		*,
		conversation_id: Optional[str] = None,
	) -> str:
		if conversation_id and str(conversation_id).strip():
			return str(conversation_id).strip()

		created = self._create_conversation()
		LOGGER.debug("Created conversation_id=%s", created)
		return created

	@staticmethod
	def _extract_message_text_from_output(output: Any) -> str:
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

	def _resolve_agent_id(self, override_agent_id: Optional[str] = None) -> Optional[str]:
		"""Resolve agent reference for a request."""
		if override_agent_id is None:
			return None
		selected_value = str(override_agent_id).strip()
		return selected_value or None

	def _run_agent(
		self,
		*,
		system_prompt: str,
		user_prompt: str,
		agent_id: Optional[str] = None,
		conversation_id: Optional[str] = None,
	) -> str:
		selected_agent_reference = self._resolve_agent_id(agent_id)
		if not self._session or not selected_agent_reference:
			raise RuntimeError("Foundry agent gateway is not available")

		resolved_conversation_id = self._get_conversation_id(
			conversation_id=conversation_id,
		)

		combined_prompt = user_prompt
		if system_prompt and str(system_prompt).strip():
			combined_prompt = (
				"Runtime hint:\n"
				f"{system_prompt}\n\n"
				"Input:\n"
				f"{user_prompt}"
			)

		response_payload = {
			"agent": {
				"type": "agent_reference",
				"name": selected_agent_reference,
			},
			"conversation": resolved_conversation_id,
			"input": combined_prompt,
		}

		result = self._http("POST", "/responses", payload=response_payload)
		status = str(result.get("status", "")).lower()
		error = result.get("error")
		if status and status != "completed":
			raise RuntimeError(f"Foundry agent response status='{status}' error={error}")

		if error:
			raise RuntimeError(f"Foundry agent response failed: {error}")

		usage = result.get("usage") or {}
		self._last_metrics = {
			"provider": "azure_ai_foundry_agent",
			"model": result.get("model"),
			"status": status or None,
			"prompt_tokens": usage.get("input_tokens"),
			"completion_tokens": usage.get("output_tokens"),
			"total_tokens": usage.get("total_tokens"),
			"conversation_id": (result.get("conversation") or {}).get("id") or resolved_conversation_id,
			"response_id": result.get("id"),
		}

		text = self._extract_message_text_from_output(result.get("output"))
		if text:
			return text

		raise RuntimeError("Foundry agent returned no assistant text output")

	def is_available(self) -> bool:
		any_configured_agent = any(
			bool(str(value).strip())
			for value in [
				self._chat_agent_reference,
				self._resilience_agent_reference,
				self._annotations_agent_reference,
				self._agent_reference,
			]
			if value is not None
		)
		return self._enabled and self._session is not None and any_configured_agent

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
	"""Create the single supported LLM gateway (APIM + Foundry agent)."""
	LOGGER.info("Using Foundry agent gateway provider (APIM + Foundry only mode)")
	return FoundryAgentGateway(settings)
