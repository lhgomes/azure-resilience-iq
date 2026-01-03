from azure.identity import DefaultAzureCredential
from azure.mgmt.resourcegraph import ResourceGraphClient


def get_arg_client() -> ResourceGraphClient:
    """
    Uses DefaultAzureCredential.
    For local dev, this will use `az login`.
    """
    credential = DefaultAzureCredential(
        exclude_interactive_browser_credential=False
    )
    return ResourceGraphClient(credential)
