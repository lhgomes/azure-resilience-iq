from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import sys
from types import ModuleType

azure_ai_projects = ModuleType("azure.ai.projects")
azure_ai_projects.AIProjectClient = object
azure_identity = ModuleType("azure.identity")
azure_identity.DefaultAzureCredential = object
app_settings = ModuleType("app.settings")
app_settings.AppSettings = object
sys.modules.setdefault("azure.ai.projects", azure_ai_projects)
sys.modules.setdefault("azure.identity", azure_identity)
sys.modules.setdefault("app.settings", app_settings)

from app.llm import gateway as gateway_module
from app.llm.gateway import FoundryAgentGateway


class _Response:
    def __init__(self, headers):
        self.headers = headers
        self.status_code = 429


class _RateLimitError(Exception):
    status_code = 429

    def __init__(self, headers):
        super().__init__("429 Too Many Requests")
        self.response = _Response(headers)


class _Responses:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise self.error
        return "ok"


class _OpenAIClient:
    def __init__(self, responses):
        self.responses = responses


def test_extract_retry_after_supports_seconds_milliseconds_and_http_date():
    assert FoundryAgentGateway._extract_retry_after_seconds(
        _RateLimitError({"Retry-After": "45"})
    ) == 45
    assert FoundryAgentGateway._extract_retry_after_seconds(
        _RateLimitError({"x-ms-retry-after-ms": "2500"})
    ) == 2.5

    retry_at = datetime.now(timezone.utc) + timedelta(seconds=60)
    parsed_delay = FoundryAgentGateway._extract_retry_after_seconds(
        _RateLimitError({"Retry-After": format_datetime(retry_at, usegmt=True)})
    )
    assert 58 <= parsed_delay <= 60


def test_retry_after_is_not_truncated_by_fallback_cap(monkeypatch):
    responses = _Responses(_RateLimitError({"Retry-After": "75"}))
    gateway = FoundryAgentGateway.__new__(FoundryAgentGateway)
    gateway._openai_client = _OpenAIClient(responses)
    gateway._rate_limit_max_attempts = 2
    gateway._rate_limit_base_seconds = 2
    gateway._rate_limit_max_seconds = 30

    sleeps = []
    monkeypatch.setattr(gateway_module.random, "uniform", lambda _start, _end: 0.0)
    monkeypatch.setattr(gateway_module.time, "sleep", sleeps.append)

    response, retry_meta = gateway._create_response_with_retries({"input": "test"})

    assert response == "ok"
    assert sleeps == [75]
    assert retry_meta == {"retries": 1, "wait_seconds": 75.0, "encounters": 1}