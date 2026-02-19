import logging
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

# Enable full HTTP logging
logging.basicConfig(level=logging.DEBUG)

endpoint = "https://resilienceiq.services.ai.azure.com/api/projects/resilience-iq-project"

client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
openai_client = client.get_openai_client()

response = openai_client.responses.create(
    input=[{"role": "user", "content": "test"}],
    extra_body={"agent": {"name": "resilience-iq-agent", "type": "agent_reference"}},
)
