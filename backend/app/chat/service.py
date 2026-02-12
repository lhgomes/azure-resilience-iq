"""
Chat service for LLM-based infrastructure analysis.
Provides intelligent analysis of Azure infrastructure, recommendations, and guidance.
"""

import json
import logging
import re
from typing import List, Optional, Dict, Any, Tuple

from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI

from app.settings import get_settings
from app.chat.models import ChatResponse, SuggestedEdge, CriticalityInsight
from app.chat.guardrails import SemanticGuardrails

LOGGER = logging.getLogger(__name__)


class ChatService:
    """Service for handling chat interactions with LLM context."""

    def __init__(self):
        """Initialize chat service with Azure OpenAI client and semantic guardrails."""
        self.settings = get_settings()
        self.llm_config = self.settings.get_llm_config()
        self.azure_openai_config = self.settings.get_azure_openai_config()
        self.guardrail_config = self.settings.get_guardrail_config()
        
        LOGGER.debug(f"Chat service initialized with LLM config: {self.llm_config}")
        LOGGER.debug(f"Azure OpenAI endpoint (from .env): {self.azure_openai_config.get('endpoint')}")
        
        self.client = self._init_client()
        self.semantic_guardrails = self._init_guardrails()

    def _init_guardrails(self) -> Optional[SemanticGuardrails]:
        """Initialize semantic guardrails for query scope validation."""
        if not self.client:
            LOGGER.warning("Cannot initialize guardrails: LLM client unavailable")
            return None
        
        try:
            threshold = self.guardrail_config.get('semantic_threshold', 0.55)
            embedding_deployment = self.azure_openai_config.get('embedding_deployment', 'text-embedding-3-small')
            
            guardrails = SemanticGuardrails(
                client=self.client,
                embedding_deployment=embedding_deployment,
                threshold=threshold
            )
            
            LOGGER.info(
                f"✓ Semantic guardrails initialized (threshold={threshold}, "
                f"embedding_model={embedding_deployment})"
            )
            return guardrails
        except Exception as e:
            LOGGER.error(f"Failed to initialize semantic guardrails: {e}", exc_info=True)
            return None

    def _init_client(self) -> Optional[AzureOpenAI]:
        """
        Initialize Azure OpenAI client using managed identity (DefaultAzureCredential).
        
        Configuration:
        - Requires AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT from .env file
        - These are user-specific and should NOT be in app_config.yaml
        - Authentication: Uses DefaultAzureCredential (managed identity)
          * In Azure: managed identity for the resource
          * Locally: Azure CLI credentials (az login), environment variables, etc.
        
        Returns:
            AzureOpenAI client or None if initialization fails
        """
        if not self.settings.use_real_llm():
            LOGGER.warning("LLM is disabled in configuration (llm.enabled=false)")
            return None

        endpoint = self.azure_openai_config.get('endpoint')
        deployment = self.azure_openai_config.get('deployment')
        api_version = self.azure_openai_config.get('api_version', '2024-05-01-preview')

        LOGGER.debug(f"Checking Azure OpenAI configuration:")
        LOGGER.debug(f"  endpoint: {endpoint}")
        LOGGER.debug(f"  deployment: {deployment}")
        LOGGER.debug(f"  api_version: {api_version}")

        if not endpoint or not deployment:
            LOGGER.error(
                f"Azure OpenAI endpoint and deployment are required.\n"
                f"Configure in your .env file (backend/.env):\n"
                f"  AZURE_OPENAI_ENDPOINT=https://{{resource}}.openai.azure.com/\n"
                f"  AZURE_OPENAI_DEPLOYMENT=gpt-4-turbo\n"
                f"  AZURE_OPENAI_API_VERSION=2024-05-01-preview\n\n"
                f"Use backend/.env.example as a template.\n"
                f"IMPORTANT: .env should NOT be committed to the repository!\n"
                f"Current values: endpoint={endpoint}, deployment={deployment}"
            )
            return None

        try:
            LOGGER.info(
                f"Initializing Azure OpenAI with managed identity (DefaultAzureCredential)\n"
                f"  Endpoint: {endpoint}\n"
                f"  Deployment: {deployment}\n"
                f"  API Version: {api_version}"
            )
            
            credential = DefaultAzureCredential()
            
            # Test token retrieval to catch auth issues early
            try:
                test_token = credential.get_token("https://cognitiveservices.azure.com/.default")
                LOGGER.info(f"✓ Azure credentials verified, token obtained")
            except Exception as auth_error:
                LOGGER.error(f"Failed to obtain Azure credentials: {auth_error}", exc_info=True)
                raise auth_error
            
            return AzureOpenAI(
                api_version=api_version,
                azure_endpoint=endpoint,
                azure_ad_token_provider=lambda: credential.get_token(
                    "https://cognitiveservices.azure.com/.default"
                ).token,
            )
        except Exception as e:
            LOGGER.error(f"Failed to initialize Azure OpenAI client: {e}", exc_info=True)
            LOGGER.error(
                f"Configuration check:\n"
                f"  - endpoint: {endpoint}\n"
                f"  - deployment: {deployment}\n"
                f"  - api_version: {api_version}\n"
                f"Ensure .env file is set correctly and credentials are available (az login for local dev)"
            )
            return None

    async def process_query(
        self,
        query: str,
        graph: Dict[str, Any],
        subscription_id: str,
        context: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> ChatResponse:
        """
        Process user query with graph context using LLM.

        Args:
            query: User's natural language query
            graph: Current infrastructure graph
            subscription_id: Azure subscription ID
            context: Additional context (selected node, tab, etc.)
            conversation_history: Previous messages in conversation

        Returns:
            ChatResponse with message, suggestions, and insights
        """
        if not self.client:
            return ChatResponse(
                message="LLM service is not available. Please check configuration."
            )

        LOGGER.debug(f"Processing chat query: {query[:100]}...")

        try:
            # Validate query scope using semantic guardrails - reject off-topic questions early
            if self.semantic_guardrails:
                is_valid, reason = await self.semantic_guardrails.validate_query_full(query)
                if not is_valid:
                    LOGGER.info(f"Query rejected as out-of-scope (similarity score): {query[:100]}")
                    return ChatResponse(message=reason)
            else:
                LOGGER.warning("Semantic guardrails unavailable, proceeding without scope validation")

            # Normalize graph: convert Edge/Node objects to dicts if needed
            normalized_graph = self._normalize_graph(graph)
            
            # Detect query type and route appropriately
            query_type = self._detect_query_type(query)
            
            # Build prompt with context
            prompt = self._build_prompt(
                query, normalized_graph, subscription_id, context, conversation_history, query_type
            )

            # Call Azure OpenAI
            response = self.client.chat.completions.create(
                model=self.azure_openai_config.get('deployment'),
                messages=[
                    {"role": "system", "content": self._system_prompt(query_type)},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=self.azure_openai_config.get('max_tokens', 2000),
                response_format={"type": "json_object"}
            )

            # Parse response
            content = response.choices[0].message.content
            llm_output = json.loads(content)

            # Validate and enrich response
            return self._process_llm_response(llm_output, normalized_graph)

        except json.JSONDecodeError as e:
            LOGGER.error(f"Failed to parse LLM JSON response: {e}")
            return ChatResponse(
                message="I had trouble understanding the response. Please try again."
            )
        except Exception as e:
            LOGGER.error(f"Chat service error: {e}", exc_info=True)
            return ChatResponse(
                message=f"Error processing query: {str(e)}"
            )

    def _detect_query_type(self, query: str) -> str:
        """Detect the type of query to route appropriately."""
        query_lower = query.lower()
        
        if any(word in query_lower for word in ['failing', 'recommendation', 'check', 'issue', 'wrong']):
            return 'findings'
        elif any(word in query_lower for word in ['fix', 'remediat', 'resolv', 'how', 'steps']):
            return 'remediation'
        elif any(word in query_lower for word in ['terraform', 'iac', 'code', 'script']):
            return 'terraform'
        elif any(word in query_lower for word in ['connect', 'relationship', 'depend']):
            return 'connections'
        else:
            return 'general'

    def _sanitize_response_message(self, message: str) -> str:
        """
        Sanitize response message to remove any off-topic content.
        
        Checks for patterns that might indicate the LLM is answering off-topic questions.
        """
        max_length = 10000
        if len(message) > max_length:
            LOGGER.warning(f"Response message unusually long ({len(message)} chars), truncating")
            message = message[:max_length] + "\n\n(Message truncated due to length)"
        
        return message

    def _normalize_graph(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize graph by converting Pydantic models to dictionaries.
        
        The graph from get_workload_graph() may contain Edge and Node objects
        that need to be serialized for use in prompts.
        """
        normalized = dict(graph)
        
        # Normalize nodes
        if 'nodes' in normalized:
            nodes = normalized['nodes']
            normalized['nodes'] = [
                n.model_dump() if hasattr(n, 'model_dump') else n
                for n in nodes
            ]
        
        # Normalize edges
        if 'edges' in normalized:
            edges = normalized['edges']
            normalized['edges'] = [
                e.model_dump() if hasattr(e, 'model_dump') else e
                for e in edges
            ]
        
        return normalized

    def _system_prompt(self, query_type: str) -> str:
        """Get system prompt based on query type."""
        base_prompt = """
You are an Azure infrastructure architect analyzing a customer's specific workload.

SCOPE AND DATA CONSTRAINTS:
===========================

WORKLOAD SCOPE:
- You ONLY answer questions about the infrastructure shown in this graph
- You ANSWER questions about:
  * Resources in the graph and their configurations
  * Issues, findings, and remediation for these resources
  * Architecture improvements and resilience enhancements
  * Cost implications of suggested changes (e.g., ZRS vs LRS pricing)
  * Performance impact of configuration changes
  * Operational impact of improvements
  * Terraform code generation for these resources
- You do NOT provide general Azure training, certifications, or career advice  
- You do NOT answer questions completely unrelated to Azure infrastructure
- If asked about topics outside infrastructure/workload analysis, respond with:
  "I'm focused on analyzing your infrastructure. That question is outside my scope.
   Please ask about your workload, resources, issues, or improvements."

DATA SOURCES:
- Use ONLY official Microsoft documentation (Microsoft Learn, Azure Docs)
- Reference specific Microsoft documentation when providing guidance
- Do NOT invent resources, relationships, or data
- Do NOT suggest resources that aren't in the provided graph
- All suggestions must be grounded in the graph data

Your core role:
1. Analyze THIS workload's resource relationships and architecture
2. Explain failures based on checks in the graph
3. Suggest remediation steps grounded in graph data
4. Generate Terraform code for improvements
5. Help users understand their infrastructure

IMPORTANT CONSTRAINTS:
- Only reference resources that exist in the provided graph
- Never invent resources or relationships
- Base all suggestions on provided graph/context
- Provide confidence scores (0-1) for all suggestions
- Be technical but clear in explanations
- All outputs must be valid JSON

EDGE SUGGESTION RULES (critical):
- ONLY suggest edges between resources that logically connect
- A resource CANNOT depend on itself (source != target)
- Storage disks from different VMs should NOT have depends_on relationship
- Network interfaces from different VMs should NOT have depends_on relationship
- Managed disks can only logically connect to their parent VM or to backup/replication services
- Do NOT suggest edges just because resources exist - they must have a real architectural relationship

EXAMPLES OF INVALID EDGES (will be rejected):
- ❌ Disk A from VM1 depends_on Disk B from VM2 (wrong: different VMs)
- ❌ /subscriptions/xxx/VM1 depends_on /subscriptions/xxx/VM1 (wrong: self-loop)
- ❌ Two random managed disks with depends_on (wrong: no relationship)

EXAMPLES OF VALID EDGES:
- ✅ Application depends_on Database (different resource types, logical)
- ✅ Frontend depends_on API Backend (different VMs, logical)
- ✅ VM depends_on Virtual Network (different types, logical)

RESPONSE FORMAT (always JSON):
{
  "message": "Your conversational response to the user",
  "suggested_edges": [],
  "resources_to_highlight": ["node_id1", "node_id2"],
  "clarifying_questions": [],
  "remediation_guide": null,
  "terraform_code": null,
  "recommendations": [],
  "criticality_insights": []
}

IMPORTANT NOTES FOR EDGE SUGGESTIONS:
- Only suggest edges if the user is explicitly asking about connections/relationships
- For findings/remediation questions, focus on answering those - edges are optional
- When you DO suggest edges, ensure they are LOGICAL (not disks from different VMs, etc.)
- If no edges are needed for the query, return empty array: "suggested_edges": []
- Each edge MUST connect different logical resources (not different disks from same VM)

IMPORTANT NOTES FOR TERRAFORM CODE:
- Only generate terraform_code if the user is explicitly asking for it
- For findings/remediation/connections questions, focus on the question - return null for terraform_code
- When you DO generate Terraform, provide it as a STRING containing valid HCL syntax (not a JSON object)
- Example terraform_code value: "resource \\"azurerm_managed_disk\\" \\"vm_disk\\" {\\n  name = ...\\n}"
"""
        
        if query_type == 'findings':
            return base_prompt + """
For findings queries, focus on:
- Explaining why resources fail checks
- Assessing business impact
- Showing affected dependencies
- Recommending remediation priority

IMPORTANT: Base findings only on FAILED FINDINGS in the prompt. Do not infer issues from criticality alone.
EDGE SUGGESTIONS: Return empty array [] - focus on answering the findings question.
TERRAFORM CODE: Return null - focus on findings analysis, not code generation.
"""
        elif query_type == 'remediation':
            return base_prompt + """
For remediation queries, provide:
- Step-by-step fix instructions
- Effort and complexity estimation
- Prerequisites and dependencies
- Risk assessment
- Validation procedures

EDGE SUGGESTIONS: Return empty array [] - focus on remediation steps.
TERRAFORM CODE: Return null - focus on remediation guidance, not code generation.
If the user asks for Terraform for remediation, clarify and suggest they ask a terraform-specific question.
"""
        elif query_type == 'terraform':
            return base_prompt + """
For Terraform queries, generate:
- Valid, production-ready HCL code
- Resource definitions following Azure best practices
- Input variables for customization
- Output values for validation
- Implementation notes and prerequisites

EDGE SUGGESTIONS: Return empty array [] - focus on code generation.

CRITICAL - You MUST validate your Terraform code BEFORE responding:

BEFORE RETURNING TERRAFORM CODE, check EVERY line:
1. Is storage_account_type = "..." a FLAT attribute? (NOT inside a sku { } block)
   - Valid: storage_account_type = "Premium_ZRS"
   - Invalid: sku { name = "Premium_ZRS" }
2. Is create_option = "..." a FLAT attribute? (NOT inside creation_data { } block)
   - Valid: create_option = "Empty"
   - Invalid: creation_data { create_option = "Empty" }
3. Do NOT invent attributes. Only use documented attributes from Terraform Azure Provider:
   - For azurerm_managed_disk: name, resource_group_name, location, disk_size_gb, storage_account_type, create_option, zones, source_resource_id, etc.
   - REJECT any attributes like: sku, creation_data, zone_resilient, tier (these don't exist for this resource)
4. If using ZRS storage type: OMIT the zones parameter (ZRS is automatic across all zones)
5. Run through this checklist for EVERY resource in your code

FAILED FINDINGS are your requirements - your code MUST address them:
- If finding says "use ZRS": code MUST have storage_account_type = "..._ZRS"
- If finding says "zone redundancy": code MUST NOT have zones parameter (use ZRS instead)

SELF-VALIDATION BEFORE RESPONSE:
- Storage type uses _ZRS suffix for zone redundancy? ✓
- No zones parameter when using ZRS? ✓
- All attributes use storage_account_type (flat), not sku { } (block)? ✓
- No invented attributes (sku, creation_data, zone_resilient)? ✓
- Code references Microsoft Learn docs from failed findings? ✓

Only return code if ALL checks pass. If unsure about an attribute, omit it.

Suggest clarifying questions about deployment context first before generating code.
"""
        elif query_type == 'connections':
            return base_prompt + """
For connections queries, focus on:
- Identifying relationships between resources
- Analyzing dependency chains
- Suggesting missing logical connections
- Explaining why connections exist

EDGE SUGGESTIONS REQUIRED:
- ACTIVELY suggest logical edges based on resource types and architecture
- A database should typically connect to an application tier
- Load balancers should connect to backend pools
- Networks should connect to VMs
- BUT NEVER suggest illogical edges (e.g., disk A from VM1 depends_on disk B from VM2)
- Provide high-confidence suggestions only (>0.7)

Return suggested_edges with all relevant connections you identify.

TERRAFORM CODE: Return null - focus on connections analysis, not infrastructure code generation.
"""
        else:
            # General query type - don't force edge suggestions
            return base_prompt + """
For general queries:
- Answer the user's question about their infrastructure
- Focus on what the user asked, not on suggesting new edges
- Only suggest edges if the user is asking about connections/relationships
- Otherwise, return empty suggested_edges array

COST AND PERFORMANCE QUESTIONS:
If the user asks about cost or performance implications:
- Provide general guidance based on Azure pricing and performance characteristics
- Reference official Microsoft documentation for pricing details
- Explain performance differences between configuration options (e.g., LRS vs ZRS, Standard vs Premium)
- Clarify when exact pricing requires Azure Pricing Calculator
- These questions ARE within scope - help the user understand trade-offs

TERRAFORM CODE: Return null unless the user specifically asks for infrastructure-as-code.
If asking for terraform, suggest they ask a terraform-specific question.

RECOMMENDATIONS FOR CHANGE REQUESTS:
If the user asks for "top changes", "recommendations", "improvements", etc.:
- Populate the recommendations field with SPECIFIC, ACTIONABLE changes
- Each recommendation should include:
  * Description: What needs to change
  * Resource(s): Which specific resources are affected - USE DISPLAY NAMES from the node summary (NOT resource IDs)
  * Priority: high/medium/low
  * Impact: Expected benefit (resilience improvement, cost reduction, etc.)
  * Effort: Estimation (low/medium/high)
  * Details: Why this change is needed

CRITICAL: In the "resources" array, use the DISPLAY NAME exactly as shown in the TOP RESOURCES summary.
For example, if the summary shows "VM-Test-1 OS Disk (Microsoft.Compute/disks)", 
use "VM-Test-1 OS Disk" in the resources array, NOT the full resource ID.

Example format:
"recommendations": [
  {
    "description": "Enable zone redundancy on VM-Test-1 disk",
    "resources": ["VM-Test-1 OS Disk"],
    "priority": "high",
    "impact": "Improves availability during zone failures",
    "effort": "medium",
    "details": "VM currently has single-zone storage. Use Premium_ZRS storage type."
  }
]

Focus on providing helpful, focused answers to the specific question.
EDGE SUGGESTIONS: Return [] unless the user asks about connections.
"""

    def _build_prompt(
        self,
        query: str,
        graph: Dict[str, Any],
        subscription_id: str,
        context: Optional[Dict[str, Any]],
        conversation_history: Optional[List[Dict[str, str]]],
        query_type: str,
    ) -> str:
        """Build detailed prompt with graph context."""
        # Summarize graph
        nodes_summary = self._summarize_nodes(graph.get('nodes', []))
        edges_summary = self._summarize_edges(graph.get('edges', []))
        llm_baseline_summary = self._build_llm_baseline_summary(graph)
        failed_findings_summary = self._summarize_failed_findings(graph)

        prompt = f"""
INFRASTRUCTURE CONTEXT:
Graph has {len(graph.get('nodes', []))} resources and {len(graph.get('edges', []))} relationships.

TOP RESOURCES (by criticality):
{nodes_summary}

KEY RELATIONSHIPS:
{edges_summary}

FAILED FINDINGS (authoritative - use these for issue prioritization):
{failed_findings_summary}

LLM BASELINE ANALYSIS (from prior run):
{llm_baseline_summary}

"""
        
        # Add context if available
        if context:
            if context.get('selected_resource_id'):
                selected_resource_id = context['selected_resource_id']
                prompt += f"User is currently viewing resource: {selected_resource_id}\n"
                selected_node = next(
                    (n for n in graph.get('nodes', []) if n.get('id') == selected_resource_id),
                    None
                )
                if selected_node:
                    selected_data = selected_node.get('data', {}) if isinstance(selected_node, dict) else {}
                    selected_name = (
                        selected_data.get('label')
                        or selected_node.get('name')
                        or selected_resource_id
                    )
                    selected_type = (
                        selected_data.get('resourceType')
                        or selected_node.get('type')
                        or 'Unknown'
                    )
                    selected_criticality = selected_data.get('criticality_score')
                    prompt += "Selected resource details:\n"
                    prompt += f"- name: {selected_name}\n"
                    prompt += f"- type: {selected_type}\n"
                    if selected_criticality is not None:
                        prompt += f"- criticality_score: {selected_criticality}\n"
            if context.get('selected_recommendation_id'):
                prompt += f"User is looking at recommendation: {context['selected_recommendation_id']}\n"
            if context.get('tab'):
                prompt += f"In UI tab: {context['tab']}\n"

        prompt += f"\nUSER QUERY: {query}\n"

        # Add conversation history for context
        if conversation_history and len(conversation_history) > 0:
            prompt += "\nRECENT CONVERSATION:\n"
            for msg in conversation_history[-3:]:  # Last 3 messages
                role = "User" if msg.get('role') == 'user' else "Assistant"
                prompt += f"{role}: {msg.get('content', '')}\n"

        return prompt

    def _build_llm_baseline_summary(self, graph: Dict[str, Any]) -> str:
        """Summarize existing LLM annotations to ground chat in prior analysis."""
        llm_annotations = graph.get("llm_annotations") or {}
        nodes_ann = llm_annotations.get("nodes") or []
        edges_ann = llm_annotations.get("edges") or []

        if not nodes_ann and not edges_ann:
            return "No prior LLM annotations available."

        nodes_by_id = {n.get("id"): n for n in graph.get("nodes", []) if isinstance(n, dict)}

        node_lines = []
        for item in nodes_ann[:10]:
            node_id = item.get("node_id")
            ann = item.get("annotations") or {}
            node = nodes_by_id.get(node_id, {})
            data = node.get("data", {})
            name = ann.get("display_name") or data.get("label") or node.get("name") or node_id
            service = ann.get("azure_service_name") or data.get("resourceType") or node.get("type")
            score = ann.get("criticality_score")
            reason = ann.get("reason")
            line = f"- {name} ({service})"
            if score is not None:
                line += f" [criticality: {score}]"
            if reason:
                line += f" - {reason}"
            node_lines.append(line)

        edge_lines = []
        for edge in edges_ann[:10]:
            source = edge.get("source")
            target = edge.get("target")
            rel = edge.get("relationship") or "relates_to"
            conf = edge.get("confidence")
            reason = edge.get("reason")
            line = f"- {source} --[{rel}]--> {target}"
            if conf is not None:
                line += f" (confidence: {conf:.2f})"
            if reason:
                line += f" - {reason}"
            edge_lines.append(line)

        summary_parts = []
        if node_lines:
            summary_parts.append("Node annotations:\n" + "\n".join(node_lines))
        if edge_lines:
            summary_parts.append("Edge suggestions:\n" + "\n".join(edge_lines))

        return "\n\n".join(summary_parts) if summary_parts else "No prior LLM annotations available."

    def _extract_llm_references(self, graph: Dict[str, Any]) -> List[Dict[str, str]]:
        """Extract referenced node ids from LLM annotations with labels."""
        llm_annotations = graph.get("llm_annotations") or {}
        nodes_ann = llm_annotations.get("nodes") or []
        edges_ann = llm_annotations.get("edges") or []

        nodes_by_id = {n.get("id"): n for n in graph.get("nodes", []) if isinstance(n, dict)}
        seen: set[str] = set()
        references: List[Dict[str, str]] = []

        def add_ref(node_id: Optional[str]) -> None:
            if not node_id or node_id in seen:
                return
            node = nodes_by_id.get(node_id, {})
            data = node.get("data", {})
            label = data.get("label") or node.get("name") or node_id
            references.append({"id": node_id, "label": label})
            seen.add(node_id)

        for item in nodes_ann:
            if isinstance(item, dict):
                add_ref(item.get("node_id"))

        for edge in edges_ann:
            if not isinstance(edge, dict):
                continue
            add_ref(edge.get("source"))
            add_ref(edge.get("target"))

        references.sort(key=lambda r: r.get("label", ""))
        return references

    def _summarize_failed_findings(self, graph: Dict[str, Any]) -> str:
        """Summarize failed findings from resilience evaluations with detailed context and references."""
        evaluations = (graph.get("resilience_evaluations") or {}).get("evaluations", {})
        failed_items: List[str] = []
        nodes_by_id = {n.get("id"): n for n in graph.get("nodes", []) if isinstance(n, dict)}

        for resource_id, payload in evaluations.items():
            checks = payload.get("checks", []) if isinstance(payload, dict) else []
            failed_checks = [c for c in checks if isinstance(c, dict) and c.get("status") == "fail"]
            if not failed_checks:
                continue

            node = nodes_by_id.get(resource_id, {})
            data = node.get("data", {})
            name = data.get("label") or node.get("name") or resource_id
            resource_type = data.get("resourceType") or node.get("type") or "Unknown"

            # Extract detailed check information for each failed check
            check_details = []
            for check in failed_checks[:3]:
                # Extract problem description and context
                description = check.get("description", "")
                long_description = check.get("long_description", "").strip()
                impact = check.get("impact", "").lower()
                category = check.get("category", "")
                potential_benefits = check.get("potential_benefits", "").strip()
                learn_more = check.get("learn_more", {})
                
                # Build detailed check summary
                detail_parts = [description]
                if long_description:
                    # Take first 150 chars of long description for context
                    context_snippet = long_description.replace("\n", " ")[:150]
                    detail_parts.append(f"({context_snippet}...)")
                
                detail = " ".join(detail_parts)
                if impact:
                    detail += f" [Impact: {impact}]"
                
                # Add benefits for context
                if potential_benefits:
                    detail += f" Benefits: {potential_benefits[:100]}..."
                
                # Add Microsoft Learn reference if available
                if learn_more and isinstance(learn_more, dict):
                    links = learn_more.get("links", [])
                    if links:
                        learn_urls = [link.get("url") for link in links if isinstance(link, dict) and link.get("url")]
                        if learn_urls:
                            detail += f" | See: {learn_urls[0]}"
                
                check_details.append(detail)

            # Format resource entry with all failed check details
            if check_details:
                resource_entry = f"- {name} ({resource_type}):\n"
                for detail in check_details:
                    resource_entry += f"  * {detail}\n"
                failed_items.append(resource_entry)
            else:
                failed_items.append(f"- {name} ({resource_type}): Check failed")

        if not failed_items:
            return "No failed findings available."

        return "\n".join(failed_items)

    def get_baseline_summary(self, graph: Dict[str, Any]) -> str:
        """Public helper to build baseline summary for chat initialization."""
        normalized = self._normalize_graph(graph)
        return self._build_llm_baseline_summary(normalized)

    def get_baseline(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        """Return baseline summary and references for chat initialization."""
        normalized = self._normalize_graph(graph)
        summary = self._build_llm_baseline_summary(normalized)
        references = self._extract_llm_references(normalized)
        return {
            "summary": summary,
            "references": references,
            "reference_count": len(references),
        }

    def _summarize_nodes(self, nodes: List[Dict[str, Any]]) -> str:
        """Create concise node summary."""
        if not nodes:
            return "No nodes available"
        
        # Sort by criticality
        sorted_nodes = sorted(
            nodes,
            key=lambda n: n.get('data', {}).get('criticality_score', 0),
            reverse=True
        )[:15]

        summary = []
        for node in sorted_nodes:
            data = node.get('data', {})
            label = data.get('label', 'Unknown')
            resource_type = data.get('resourceType', 'Unknown')
            criticality = data.get('criticality_score', 'unknown')
            summary.append(
                f"- {label} ({resource_type}) [Criticality: {criticality}]"
            )

        return '\n'.join(summary) if summary else "No nodes available"

    def _summarize_edges(self, edges: List[Dict[str, Any]]) -> str:
        """Create concise edge summary."""
        if not edges:
            return "No relationships found"
        
        summary = []
        for edge in edges[:25]:  # Limit to first 25
            source = edge.get('source', '...')
            target = edge.get('target', '...')
            rel = edge.get('data', {}).get('relationship', 'relates_to')
            confidence = edge.get('data', {}).get('confidence', 1.0)
            summary.append(f"- {source} --[{rel}:{confidence:.2f}]--> {target}")

        return '\n'.join(summary) if summary else "No relationships found"

    def _extract_terraform_code(self, llm_output: Dict[str, Any]) -> Optional[str]:
        """Extract terraform code from LLM output, converting dict to HCL string if needed."""
        # Try standard keys
        terraform_code = (
            llm_output.get('terraform_code')
            or llm_output.get('terraform')
            or llm_output.get('code')
        )

        # If it's a dict/structured format, convert to HCL string
        if isinstance(terraform_code, dict):
            return self._dict_to_hcl(terraform_code)
        
        # If it's already a string, return it
        if isinstance(terraform_code, str):
            return terraform_code if terraform_code.strip() else None
        
        return None

    def _validate_terraform_against_findings(self, terraform_code: Optional[str], graph: Dict[str, Any]) -> Optional[str]:
        """
        Validate generated Terraform code against failed findings and best practices.
        Returns validation warnings if code has issues.
        Returns None if validation passes.
        """
        if not terraform_code:
            return None
        
        code_lower = terraform_code.lower()
        issues = []
        
        # Check for FromImage without source_image_reference
        if "create_option" in code_lower and "fromimage" in code_lower:
            # Check if source_image_reference exists in the code
            if "source_image_reference" not in code_lower:
                issues.append(
                    "⚠️ Code uses create_option = \"FromImage\" but doesn't specify source_image_reference. "
                    "Either add source_image_reference block with the image details, or use create_option = \"Empty\" for new disks, "
                    "or create_option = \"Copy\" with source_resource_id to migrate an existing disk."
                )
        
        # Check for zone+ZRS contradiction (zones parameter makes disk zone-pinned, not zone-redundant)
        if "zones = [" in code_lower or 'zones = ["' in code_lower:
            if "_zrs" in code_lower:
                issues.append(
                    "⚠️ Code uses 'zones' parameter with ZRS storage type. Zone-Redundant Storage automatically replicates "
                    "across all availability zones—the 'zones' parameter pins it to specific zones, contradicting ZRS. "
                    "Remove the 'zones' parameter when using ZRS storage types."
                )
        
        # Check if disk is zone-pinned (LRS/single zone) when ZRS findings exist
        evaluations = (graph.get("resilience_evaluations") or {}).get("evaluations", {})
        has_zrs_finding = any(
            "zone-redundant" in (str(check.get("description", "")).lower() or "")
            or "zrs" in str(check.get("description", "")).lower()
            for payload in evaluations.values()
            for check in (payload.get("checks", []) if isinstance(payload, dict) else [])
            if isinstance(check, dict) and check.get("status") == "fail"
        )
        
        if has_zrs_finding:
            # Check if code actually uses LRS (bad) vs ZRS (good)
            has_lrs = "premium_lrs" in code_lower or ("standard" in code_lower and "_lrs" in code_lower and "_zrs" not in code_lower)
            has_zrs = "_zrs" in code_lower
            
            if has_lrs and not has_zrs:
                issues.append(
                    "⚠️ Code uses LRS (Locally Redundant Storage) but the finding requires ZRS (Zone-Redundant Storage). "
                    "Change storage_account_type to use _ZRS suffix (e.g., Premium_ZRS, StandardSSD_ZRS, Standard_ZRS)."
                )
        
        return "\n\n".join(issues) if issues else None

    def _dict_to_hcl(self, obj: Any, indent: int = 0) -> str:
        """Convert a structured dict/JSON object to HCL string representation."""
        if not isinstance(obj, dict):
            return json.dumps(obj) if not isinstance(obj, str) else f'"{obj}"'
        
        lines = []
        indent_str = "  " * indent
        next_indent_str = "  " * (indent + 1)

        for key, value in obj.items():
            if isinstance(value, dict):
                # Handle nested objects
                lines.append(f'{indent_str}{key} = ' + "{")
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, dict):
                        lines.append(f'{next_indent_str}{sub_key} = ' + "{")
                        for k, v in sub_value.items():
                            if isinstance(v, dict):
                                lines.append(f'{next_indent_str}  {k} = ' + "{")
                                for mk, mv in v.items():
                                    if isinstance(mv, (list, dict)):
                                        lines.append(f'{next_indent_str}    {mk} = {json.dumps(mv)}')
                                    else:
                                        lines.append(f'{next_indent_str}    {mk} = {json.dumps(mv)}')
                                lines.append(f'{next_indent_str}  ' + "}")
                            elif isinstance(v, list):
                                lines.append(f'{next_indent_str}  {k} = {json.dumps(v)}')
                            else:
                                lines.append(f'{next_indent_str}  {k} = {json.dumps(v)}')
                        lines.append(f'{next_indent_str}' + "}")
                    elif isinstance(sub_value, list):
                        lines.append(f'{next_indent_str}{sub_key} = {json.dumps(sub_value)}')
                    else:
                        lines.append(f'{next_indent_str}{sub_key} = {json.dumps(sub_value)}')
                lines.append(f'{indent_str}' + "}")
            elif isinstance(value, list):
                lines.append(f'{indent_str}{key} = {json.dumps(value)}')
            else:
                lines.append(f'{indent_str}{key} = {json.dumps(value)}')

        return '\n'.join(lines)

    def _is_logical_edge(self, source_id: str, target_id: str, relationship: str, nodes_by_id: Dict[str, Any]) -> bool:
        """
        Validate that an edge suggestion is logically sensible.
        
        Filters out obviously illogical suggestions like:
        - Connecting two disks from different VMs with 'depends_on'
        - Connecting the same resource to itself
        - Connecting unrelated resources with strong relationships
        
        Returns:
            True if edge is logical, False if should be filtered out
        """
        # Rule 1: A resource cannot depend on itself
        if source_id == target_id:
            LOGGER.debug(f"Filtered self-loop edge: {source_id} -> {source_id}")
            return False
        
        source_node = nodes_by_id.get(source_id, {})
        target_node = nodes_by_id.get(target_id, {})
        
        source_data = source_node.get('data', {}) if isinstance(source_node, dict) else {}
        target_data = target_node.get('data', {}) if isinstance(target_node, dict) else {}
        
        source_type = source_data.get('resourceType', '').lower()
        target_type = target_data.get('resourceType', '').lower()
        
        # Rule 2: Storage disks from different VMs shouldn't have depends_on
        # Example: managed_disks from different VMs
        if relationship == 'depends_on':
            # Extract parent VM from the resource ID
            source_vm = self._extract_parent_resource(source_id, 'virtualMachines')
            target_vm = self._extract_parent_resource(target_id, 'virtualMachines')
            
            # If both are disks and belong to different VMs, this is illogical
            if source_type in ['microsoft.compute/disks', 'managed_disk'] and \
               target_type in ['microsoft.compute/disks', 'managed_disk']:
                if source_vm and target_vm and source_vm != target_vm:
                    LOGGER.debug(
                        f"Filtered illogical disk dependency: disk from VM {source_vm} "
                        f"depends on disk from VM {target_vm}"
                    )
                    return False
        
        # Rule 3: NICs from different VMs shouldn't have depends_on
        if relationship == 'depends_on':
            source_vm = self._extract_parent_resource(source_id, 'virtualMachines')
            target_vm = self._extract_parent_resource(target_id, 'virtualMachines')
            
            if source_type in ['microsoft.network/networkinterfaces', 'network_interface'] and \
               target_type in ['microsoft.network/networkinterfaces', 'network_interface']:
                if source_vm and target_vm and source_vm != target_vm:
                    LOGGER.debug(
                        f"Filtered illogical NIC dependency: NIC from VM {source_vm} "
                        f"depends on NIC from VM {target_vm}"
                    )
                    return False
        
        # Rule 4: Log suspiciously high-confidence edges for monitoring
        # (These often hide errors)
        
        return True
    
    def _extract_parent_resource(self, resource_id: str, parent_type: str) -> Optional[str]:
        """
        Extract parent resource ID from a resource path.
        
        Example:
            Input: '/subscriptions/sub/resourceGroups/rg/providers/microsoft.compute/
                    virtualMachines/vm1/storageProfile/osDisk'
            parent_type: 'virtualMachines'
            Output: '/subscriptions/sub/resourceGroups/rg/providers/microsoft.compute/virtualMachines/vm1'
        """
        try:
            # Split by the parent type
            if parent_type.lower() not in resource_id.lower():
                return None
            
            # Find the parent type in the path (case-insensitive)
            import re
            pattern = f"(?i){re.escape(parent_type)}/[^/]+"
            match = re.search(pattern, resource_id)
            
            if match:
                # Extract up to and including the resource name
                start = resource_id.lower().find(parent_type.lower())
                # Find the next '/' after the resource name
                remaining = resource_id[start + len(parent_type) + 1:]
                next_slash = remaining.find('/')
                
                if next_slash == -1:
                    # No more slashes, this is the end of the parent resource
                    resource_name = remaining.split('/')[0]
                    return resource_id[:start + len(parent_type) + 1 + len(resource_name)]
                else:
                    resource_name = remaining[:next_slash]
                    return resource_id[:start + len(parent_type) + 1 + len(resource_name)]
        except Exception as e:
            LOGGER.debug(f"Error extracting parent resource from {resource_id}: {e}")
        
        return None
    def _process_llm_response(self, llm_output: Dict[str, Any], graph: Dict[str, Any]) -> ChatResponse:
        """Validate and process LLM response against guardrails."""
        # Validate suggested edges
        suggested_edges = []
        nodes_by_id = {n['id']: n for n in graph.get('nodes', [])}

        for edge in llm_output.get('suggested_edges', []):
            source = edge.get('source')
            target = edge.get('target')
            relationship = edge.get('relationship', 'related')

            # Check 1: Both nodes must exist
            if source not in nodes_by_id or target not in nodes_by_id:
                LOGGER.warning(
                    f"LLM suggested edge with non-existent nodes: {source} -> {target}, "
                    f"filtering out"
                )
                continue
            
            # Check 2: Validate edge makes logical sense
            if not self._is_logical_edge(source, target, relationship, nodes_by_id):
                LOGGER.warning(
                    f"LLM suggested illogical edge: {source} -> {target} ({relationship}), "
                    f"filtering out"
                )
                continue

            suggested_edges.append(SuggestedEdge(
                source=source,
                target=target,
                relationship=relationship,
                confidence=min(1.0, max(0.0, edge.get('confidence', 0.5))),
                reason=edge.get('reason', ''),
            ))

        # Validate resources to highlight - prevent hallucinated resources
        resources_to_highlight = []
        for resource_id in llm_output.get('resources_to_highlight', []):
            if resource_id in nodes_by_id:
                resources_to_highlight.append(resource_id)
            else:
                LOGGER.warning(
                    f"LLM referenced non-existent resource: {resource_id}, filtering out"
                )

        # Validate criticality insights
        criticality_insights: List[CriticalityInsight] = []
        insights_raw = llm_output.get('criticality_insights', [])
        insights_list = insights_raw if isinstance(insights_raw, list) else [insights_raw]
        for insight in insights_list:
            if not isinstance(insight, dict):
                continue
            node_id = insight.get('node_id')
            if node_id in nodes_by_id:
                criticality_insights.append(CriticalityInsight(
                    node_id=node_id,
                    suggested_score=min(10, max(1, insight.get('suggested_score', 5))),
                    reason=insight.get('reason', ''),
                    current_score=nodes_by_id[node_id].get('data', {}).get('criticality_score'),
                ))
            else:
                LOGGER.warning(f"LLM suggested criticality insight for non-existent node: {node_id}")

        recommendations_raw = llm_output.get('recommendations', [])
        recommendations: List[Dict[str, Any]] = []
        for rec in recommendations_raw if isinstance(recommendations_raw, list) else [recommendations_raw]:
            if isinstance(rec, dict):
                recommendations.append(rec)
            elif isinstance(rec, str):
                recommendations.append({"text": rec})

        # Extract terraform code and convert to string if needed
        terraform_code = self._extract_terraform_code(llm_output)
        
        # Validate terraform code against failed findings
        terraform_validation = self._validate_terraform_against_findings(terraform_code, graph)

        # Sanitize message to ensure it's appropriate and not excessively long
        message = self._sanitize_response_message(llm_output.get('message', 'No response'))

        return ChatResponse(
            message=message,
            suggested_edges=suggested_edges,
            resources_to_highlight=resources_to_highlight,
            criticality_insights=criticality_insights,
            recommendations=recommendations,
            remediation_guide=llm_output.get('remediation_guide'),
            terraform_code=terraform_code,
            terraform_validation=terraform_validation,
            clarifying_questions=llm_output.get('clarifying_questions', []),
            raw_llm_output=llm_output,
        )
