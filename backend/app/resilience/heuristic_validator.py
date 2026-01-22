"""
Heuristic-based validator for recommendations without KQL.

When KQL is unavailable, this module analyzes recommendation metadata
(description, longDescription, potentialBenefits) to infer validation strategies
and apply intelligent fallbacks.

Supports:
- Pattern-based heuristics (regex/keyword matching)
- Optional LLM analysis for complex recommendations
- Extensible validator registry
"""

import json
import logging
import re
import uuid
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from pathlib import Path

from app.settings import load_settings

LOGGER = logging.getLogger(__name__)


def _resource_id_to_uuid(resource_id: str) -> str:
    """Generate a stable UUIDv5 from a resource ID to reduce token usage in LLM calls."""
    namespace = uuid.NAMESPACE_DNS
    return str(uuid.uuid5(namespace, resource_id))


def _extract_partial_json_array(text: str, array_key: str) -> Optional[List[Dict[str, Any]]]:
    """Best-effort extraction of complete JSON objects from a truncated array."""
    key_pos = text.find(f'"{array_key}"')
    if key_pos == -1:
        return None
    start_bracket = text.find('[', key_pos)
    if start_bracket == -1:
        return None

    objects: List[Dict[str, Any]] = []
    depth = 0
    in_string = False
    escape = False
    obj_start: Optional[int] = None

    for idx, ch in enumerate(text[start_bracket + 1:], start=start_bracket + 1):
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            if depth == 0:
                obj_start = idx
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and obj_start is not None:
                candidate = text[obj_start:idx + 1]
                try:
                    objects.append(json.loads(candidate))
                except json.JSONDecodeError:
                    pass
                obj_start = None
        elif ch == ']' and depth == 0:
            break

    return objects or None


@dataclass
class ValidationStrategy:
    """Strategy for validating a recommendation without KQL."""
    recommendation_id: str
    aprl_guid: str
    strategy_type: str  # "heuristic", "property_check", "llm", "manual"
    logic: str  # Human-readable description of logic
    property_checks: List[Dict[str, Any]]  # List of checks to apply
    confidence: float  # 0.0-1.0 confidence in this strategy
    notes: str


class HeuristicValidator:
    """
    Analyzes recommendations and builds validation strategies.
    
    Uses NLP-like pattern matching and optional LLM to determine
    how to validate recommendations without KQL files.
    """

    def __init__(self, aoai_client=None):
        """
        Initialize validator.
        
        Args:
            aoai_client: Optional Azure OpenAI client for LLM analysis
        """
        settings = load_settings()
        self.aoai_client = aoai_client
        self.strategies_cache: Dict[str, ValidationStrategy] = {}
        self.learn_more_defaults = settings.get_learn_more_defaults()
        self.aoai_config = settings.get_azure_openai_config()

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """Extract lowercase keywords from recommendation text."""
        if not text:
            return []
        # Extract words, remove common stop words
        words = re.findall(r'\b[a-z]+\b', text.lower())
        stop_words = {
            "the", "a", "an", "and", "or", "is", "are", "be", "to", "of", "in", "for",
            "with", "from", "by", "at", "as", "on", "it", "that", "should", "must",
            "can", "could", "will", "would", "may", "might", "consider", "ensure"
        }
        return [w for w in words if w not in stop_words]

    def analyze_recommendation(
        self,
        recommendation_id: str,
        aprl_guid: str,
        resource_type: str,
        description: str,
        long_description: str,
        potential_benefits: str,
        use_llm: bool = False,
    ) -> Optional[ValidationStrategy]:
        """
        Analyze a recommendation and build a validation strategy.
        
        Args:
            recommendation_id: External recommendation ID
            aprl_guid: APRL GUID
            resource_type: Azure resource type
            description: Short description
            long_description: Detailed description
            potential_benefits: Benefits text
            use_llm: If True and LLM available, ask LLM for analysis
            
        Returns:
            ValidationStrategy or None if no strategy can be inferred
        """
        combined_text = f"{description} {long_description} {potential_benefits}".lower()
        keywords = self._extract_keywords(combined_text)
        
        # Try heuristic-based strategies first
        strategy = self._heuristic_strategy(
            recommendation_id, aprl_guid, resource_type, combined_text, keywords
        )
        
        # If heuristics found a low-confidence strategy and LLM is available, try LLM instead
        if strategy and strategy.confidence < 0.5 and use_llm and self.aoai_client:
            LOGGER.debug(
                f"Heuristic confidence too low ({strategy.confidence:.1%}) for {aprl_guid}, trying LLM analysis"
            )
            llm_strategy = self._llm_strategy(
                recommendation_id, aprl_guid, resource_type, description, long_description
            )
            if llm_strategy:
                return llm_strategy
            # If LLM fails, fall back to the low-confidence heuristic strategy
            LOGGER.debug(f"LLM analysis failed, using heuristic strategy for {aprl_guid}")
            return strategy
        
        if strategy:
            return strategy
        
        # If heuristics fail completely and LLM available, try LLM analysis
        if use_llm and self.aoai_client:
            strategy = self._llm_strategy(
                recommendation_id, aprl_guid, resource_type, description, long_description
            )
            if strategy:
                return strategy
        
        # No strategy found
        LOGGER.debug(
            f"No validation strategy inferred for {aprl_guid} ({description[:50]}...)"
        )
        return None

    def _heuristic_strategy(
        self,
        recommendation_id: str,
        aprl_guid: str,
        resource_type: str,
        combined_text: str,
        keywords: List[str],
    ) -> Optional[ValidationStrategy]:
        """Build strategy using pattern matching and keyword analysis."""
        
        checks = []
        logic_parts = []
        confidence = 0.0
        
        # Multi-region pattern
        if any(k in keywords for k in ["multi", "region", "regional", "failover", "disaster", "global"]):
            checks.append({
                "type": "region_count",
                "check": "Count unique regions for resource replicas/deployments",
                "expect": "Multiple regions",
                "property_path": "properties.replicationSettings.regions or properties.location",
            })
            logic_parts.append("Check for multi-region configuration")
            confidence = max(confidence, 0.7)
        
        # Zone/availability pattern
        if any(k in keywords for k in ["zone", "availability", "redundant", "distributed", "spread"]):
            checks.append({
                "type": "zone_check",
                "check": "Verify resource spans availability zones",
                "expect": "zones array length > 1 or zone-redundant SKU",
                "property_path": "properties.zones",
            })
            logic_parts.append("Check availability zone coverage")
            confidence = max(confidence, 0.75)
        
        # AOAI/Cognitive Services deployment patterns
        if any(k in keywords for k in ["deployment", "global", "provisioned", "standard", "batch", "payg", "throughput"]):
            if "cognitiveservices" in resource_type.lower():
                checks.append({
                    "type": "deployment_configuration",
                    "check": "Check AOAI deployment types (Global Standard, Provisioned, Batch, Data Zone)",
                    "expect": "Deployment type configured in sub-resources",
                    "property_path": "properties.deployments",
                })
                logic_parts.append("Check AOAI deployment configuration")
                confidence = 0.35  # Low confidence without /deployments sub-resource
                if any(k in keywords for k in ["provisioned", "throughput"]):
                    confidence = max(confidence, 0.40)
        
        # SKU/performance pattern
        if any(k in keywords for k in ["sku", "performance", "premium", "boost", "tier", "standard", "throughput"]):
            checks.append({
                "type": "sku_configuration",
                "check": "Check resource SKU or performance tier",
                "expect": "Correct SKU for workload type",
                "property_path": "sku.name or properties.sku",
            })
            logic_parts.append("Check performance/SKU configuration")
            confidence = max(confidence, 0.65)
        
        # Monitoring/alerting pattern
        if any(k in keywords for k in ["monitor", "alert", "insight", "tracking", "event"]):
            checks.append({
                "type": "monitoring_enabled",
                "check": "Check if monitoring extensions/agents installed",
                "expect": "extensions or monitoring config present",
                "property_path": "properties.extensions or tags",
            })
            logic_parts.append("Check for monitoring agents/extensions")
            confidence = max(confidence, 0.6)
        
        # Replication/backup pattern
        if any(k in keywords for k in ["replicate", "replica", "backup", "recovery", "failover", "restore", "versioning", "point-in-time"]):
            checks.append({
                "type": "replication_configured",
                "check": "Verify replication or backup is enabled",
                "expect": "Replication/backup settings present",
                "property_path": "properties.replication or properties.backup or properties.isVersioningEnabled",
            })
            logic_parts.append("Check replication/backup configuration")
            confidence = max(confidence, 0.65)
        
        # Encryption/security pattern
        if any(k in keywords for k in ["encrypt", "security", "private", "protected", "tls", "ssl", "zone", "residency"]):
            checks.append({
                "type": "encryption_enabled",
                "check": "Verify encryption at rest and in transit",
                "expect": "encryption settings enabled",
                "property_path": "properties.encryption or properties.enableHttpsTrafficOnly",
            })
            logic_parts.append("Check encryption configuration")
            confidence = max(confidence, 0.7)
        
        # Agent/maintenance pattern
        if any(k in keywords for k in ["agent", "linux", "update", "scheduled", "event", "maintenance", "sensitive"]):
            checks.append({
                "type": "agent_config",
                "check": "Verify VM agent or maintenance configuration",
                "expect": "Agent version or scheduled events enabled",
                "property_path": "properties.osProfile or properties.scheduledEventsPolicy",
            })
            logic_parts.append("Check VM agent/maintenance configuration")
            confidence = max(confidence, 0.55)
        
        # Storage specific patterns
        if "storage" in resource_type.lower():
            if any(k in keywords for k in ["block", "blob", "premium", "performance"]):
                checks.append({
                    "type": "storage_tier",
                    "check": "Check storage account access tier and replication",
                    "expect": "Premium or appropriate tier configured",
                    "property_path": "sku.name or properties.accessTier",
                })
                logic_parts.append("Check storage performance tier")
                confidence = max(confidence, 0.65)
        
        if not checks:
            return None
        
        return ValidationStrategy(
            recommendation_id=recommendation_id,
            aprl_guid=aprl_guid,
            strategy_type="heuristic",
            logic=" | ".join(logic_parts),
            property_checks=checks,
            confidence=confidence,
            notes=f"Inferred from keywords: {', '.join(keywords[:10])}",
        )

    def _llm_strategy(
        self,
        recommendation_id: str,
        aprl_guid: str,
        resource_type: str,
        description: str,
        long_description: str,
    ) -> Optional[ValidationStrategy]:
        """
        Ask LLM to analyze recommendation and suggest validation approach.
        
        Sends recommendation details to Azure OpenAI and asks for property-based
        validation strategies when heuristics fail.
        """
        if not self.aoai_client:
            return None
        
        deployment = self.aoai_config.get("deployment")
        if not deployment:
            LOGGER.warning("azure_openai.deployment not set, cannot use LLM")
            return None
        
        prompt = f"""You are an Azure resilience expert. Given this recommendation, suggest how to validate if an Azure resource complies.

IMPORTANT: Do NOT suggest KQL queries (we don't have KQL). Instead, suggest property-based checks using Azure Resource Manager properties that can be accessed from resource metadata.

Recommendation Title: {description}

Resource Type: {resource_type}

Recommendation Details:
{long_description}

Please provide validation checks in this JSON format:
{{
  "checks": [
    {{"property_path": "properties.xyz or sku.name", "condition": "should have", "expected_value": "specific value or pattern"}},
    ...
  ],
  "confidence": 0.65,
  "logic": "Brief explanation of the validation logic",
  "notes": "Any special considerations"
}}

Examples of valid property paths:
- properties.zones (for availability zones)
- properties.replicationSettings.regions (for multi-region)
- sku.name (for SKU/performance tier)
- properties.backup.enabled (for backup config)
- tags (for tag-based validation)

RETURN ONLY VALID JSON, no markdown, no explanation text outside the JSON."""
        
        try:
            LOGGER.debug(f"Calling LLM for recommendation {aprl_guid}: {description[:50]}...")
            
            response = self.aoai_client.chat.completions.create(
                model=deployment,  # Use deployment from environment variable
                messages=[
                    {
                        "role": "system",
                        "content": "You are an Azure cloud architect specializing in resilience. Provide only valid JSON responses.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                temperature=0.3,
                max_tokens=500,
            )
            
            response_text = response.choices[0].message.content.strip()
            LOGGER.debug("LLM raw response (truncated 2000 chars): %s", response_text[:2000])
            LOGGER.debug("LLM raw response (truncated): %s", response_text[:2000])
            
            # Parse JSON response
            try:
                parsed = json.loads(response_text)
            except json.JSONDecodeError:
                # Try to extract JSON if there's extra text
                import re
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group())
                else:
                    LOGGER.warning(f"LLM response not valid JSON for {aprl_guid}: {response_text[:100]}")
                    return None
            
            # Validate response structure
            if not isinstance(parsed, dict) or "checks" not in parsed:
                LOGGER.warning(f"LLM response missing 'checks' for {aprl_guid}")
                return None
            
            confidence = parsed.get("confidence", 0.0)
            logic = parsed.get("logic", "LLM-analyzed validation")
            notes = parsed.get("notes", "")
            
            LOGGER.debug(
                f"LLM strategy created for {aprl_guid}: "
                f"confidence={confidence:.0%}, logic={logic}"
            )
            
            # Build ValidationStrategy from LLM response
            return ValidationStrategy(
                recommendation_id=recommendation_id,
                aprl_guid=aprl_guid,
                strategy_type="llm",
                logic=logic,
                property_checks=parsed.get("checks", []),
                confidence=confidence,
                notes=f"LLM-generated strategy. {notes}",
            )
            
        except Exception as e:
            LOGGER.warning(f"LLM analysis failed for {aprl_guid}: {e}")
            return None

    def _llm_full_resource_analysis(
        self,
        recommendation_id: str,
        aprl_guid: str,
        resource_type: str,
        description: str,
        long_description: str,
        impact: str,
        resource: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        """
        Perform deep LLM analysis by sending full resource configuration.
        
        Used for:
        - High impact recommendations (critical)
        - When property checks fail (need detailed reasoning)
        - Very low confidence scenarios (<40%)
        
        Returns:
            (is_failing, reasoning): True if resource fails recommendation, with explanation
        """
        if not self.aoai_client:
            return False, None
        
        deployment = self.aoai_config.get("deployment")
        if not deployment:
            return False, None
        
        # Create a clean resource representation (remove large/irrelevant fields)
        clean_resource = {
            "id": resource.get("id"),
            "name": resource.get("name"),
            "type": resource.get("type"),
            "location": resource.get("location"),
            "sku": resource.get("sku"),
            "properties": resource.get("properties", {}),
            "tags": resource.get("tags"),
            "zones": resource.get("zones"),
        }
        
        prompt = f"""You are an Azure resilience expert analyzing a resource against a specific recommendation.

Recommendation: {description}
Impact: {impact}
Details: {long_description}

Resource Configuration:
{json.dumps(clean_resource, indent=2)}

Analyze whether this resource FAILS to meet the recommendation. Respond in JSON format:
{{
  "fails_recommendation": true/false,
  "reasoning": "Specific explanation of why the resource fails or passes",
  "missing_properties": ["list of missing or incorrect properties"],
  "confidence": 0.0-1.0
}}

Be specific about which properties are missing or incorrectly configured.
RETURN ONLY VALID JSON."""
        
        try:
            LOGGER.debug(
                f"Deep LLM analysis for {aprl_guid} (impact: {impact}) on resource {resource.get('name')}"
            )
            
            response = self.aoai_client.chat.completions.create(
                model=deployment,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an Azure cloud architect specializing in resilience. Provide only valid JSON responses.",
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=800,
            )
            
            response_text = response.choices[0].message.content.strip()
            
            # Parse JSON response
            try:
                parsed = json.loads(response_text)
            except json.JSONDecodeError:
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group())
                else:
                    LOGGER.warning(f"LLM full analysis response not valid JSON: {response_text[:100]}")
                    return False, None
            
            fails = parsed.get("fails_recommendation", False)
            reasoning = parsed.get("reasoning", "")
            confidence = parsed.get("confidence", 0.0)
            
            LOGGER.debug(
                f"LLM full analysis result: fails={fails}, confidence={confidence:.0%}, "
                f"reasoning={reasoning[:80]}..."
            )
            
            return fails, reasoning if fails else None
            
        except Exception as e:
            LOGGER.warning(f"LLM full resource analysis failed for {aprl_guid}: {e}")
            return False, None

    def generate_user_guidance(
        self,
        description: str,
        long_description: str,
        potential_benefits: str,
        impact: str,
    ) -> str:
        """
        Generate specific user guidance for manual validation when automated checks cannot be performed.
        """
        if not self.aoai_client:
            return "Manual review required. Please consult the Azure Well-Architected Framework documentation for guidance."
        
        deployment = self.aoai_config.get("deployment")
        if not deployment:
            return "Manual review required. Please consult the Azure Well-Architected Framework documentation for guidance."
        
        prompt = f"""You are an Azure resilience expert. Create clear, specific user guidance for manually validating this recommendation.

Recommendation: {description}
Impact Level: {impact}

Details:
{long_description}

Benefits:
{potential_benefits}

Generate concise, actionable steps for a user to MANUALLY validate whether their Azure resource complies with this recommendation.

Include:
1. Specific things to check in Azure Portal or Azure CLI
2. What properties or configurations to look for
3. Why this matters for resilience
4. Where to find the setting in Azure Portal

Format as a practical guide (plain text, no JSON). Keep it under 150 words."""
        
        try:
            response = self.aoai_client.chat.completions.create(
                model=deployment,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful Azure guide. Provide clear, actionable guidance for manual validation steps.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                temperature=0.3,
                max_tokens=200,
            )
            
            guidance = response.choices[0].message.content.strip()
            LOGGER.info(f"Generated user guidance for manual validation")
            return guidance
            
        except Exception as e:
            LOGGER.warning(f"Failed to generate user guidance via LLM: {e}")
            return "Manual review required. Please consult the Azure Well-Architected Framework documentation for guidance."

    def generate_batch_user_guidance(
        self,
        pending_items: List[Dict[str, str]],
    ) -> Dict[str, Dict[str, str]]:
        """Generate user guidance for multiple pending recommendations in one LLM call."""
        if not pending_items:
            return {}

        if not self.aoai_client:
            return {
                item['id']: {
                    'quick_header': 'Manual review required',
                    'practical_guide': 'Please consult the Azure Well-Architected Framework documentation.'
                }
                for item in pending_items
            }
        
        deployment = self.aoai_config.get("deployment")
        if not deployment:
            return {
                item['id']: {
                    'quick_header': 'Manual review required',
                    'practical_guide': 'Please consult the Azure Well-Architected Framework documentation.'
                }
                for item in pending_items
            }

        # Map full IDs to short UUIDs to reduce token usage
        uuid_to_full_id: Dict[str, str] = {}
        recommendations_text = ""
        for item in pending_items:
            item_uuid = _resource_id_to_uuid(item['id'])
            uuid_to_full_id[item_uuid] = item['id']
            recommendations_text += f"""
ID: {item_uuid}
Title: {item['description']}
Impact: {item['impact']}
Details: {item['long_description']}
Benefits: {item['potential_benefits']}
---"""

        prompt = f"""You are an Azure resilience expert. For each recommendation below, generate TWO things:
1. A QUICK HEADER (max 10 words) that summarizes what to check
2. A PRACTICAL GUIDE (max 150 words) with specific validation steps

Respond in JSON format ONLY, with this structure:
{{
  "recommendations": [
    {{
      "id": "the-recommendation-uuid",
      "quick_header": "Check if backup is enabled and retention is set",
      "practical_guide": "Go to Azure Portal > [Resource Type] > Backup. Verify backup is enabled and retention policy meets your requirements. Can also use: az backup vault list --resource-group <rg-name>. See: https://learn.microsoft.com/azure/backup/backup-overview"
    }},
    ...
  ]
}}

RECOMMENDATIONS TO PROCESS:{recommendations_text}

Requirements for practical guides:
- Include specific Azure Portal navigation path
- Include Azure CLI command example if applicable (escape quotes properly for JSON)
- Include official Microsoft documentation link
- Focus on what to verify, not how to implement
- Be actionable in 5 minutes
- Keep under 150 words per guide

IMPORTANT: When including Azure CLI queries, escape all quotes properly:
- Use single quotes for the outer query string
- Use escaped double quotes (\\\" ) inside JMESPath queries
- Example: 'az storage account list --query \"[?sku.name==\\\"Standard_GRS\\\"]\"'

RETURN ONLY VALID JSON, no markdown, no explanations."""

        try:
            LOGGER.info(f"Generating batch user guidance for {len(pending_items)} pending items")

            response = self.aoai_client.chat.completions.create(
                model=deployment,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert Azure compliance guide. Generate only valid JSON responses.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                temperature=0.2,
                max_tokens=4000,
            )

            response_text = response.choices[0].message.content.strip()

            try:
                parsed = json.loads(response_text)
            except json.JSONDecodeError as decode_err:
                # Persist the full raw response for debugging malformed JSON from the model
                try:
                    import datetime
                    dump_path = Path("/tmp") / f"llm_terraform_response_{datetime.datetime.utcnow().isoformat()}.txt"
                    dump_path.write_text(response_text)
                    LOGGER.error("Saved malformed LLM response to %s", dump_path)
                except Exception as dump_err:
                    LOGGER.warning("Failed to persist malformed LLM response: %s", dump_err)

                partial = _extract_partial_json_array(response_text, "recommendations")
                if partial:
                    parsed = {"recommendations": partial}
                    LOGGER.warning("Recovered %d partial recommendation items from malformed JSON", len(partial))
                else:
                    import re
                    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                    if not json_match:
                        raise decode_err
                    parsed = json.loads(json_match.group())
            LOGGER.debug("LLM parsed type: %s keys: %s", type(parsed), list(parsed.keys()) if isinstance(parsed, dict) else None)

            result: Dict[str, Dict[str, str]] = {}
            for rec in parsed.get('recommendations', []):
                rec_uuid = rec.get('id')
                if rec_uuid and rec_uuid in uuid_to_full_id:
                    full_id = uuid_to_full_id[rec_uuid]
                    result[full_id] = {
                        'quick_header': rec.get('quick_header', 'Manual review required'),
                        'practical_guide': rec.get('practical_guide', '')
                    }
                elif rec_uuid:
                    # UUID not in mapping; try to use it directly (fallback)
                    result[rec_uuid] = {
                        'quick_header': rec.get('quick_header', 'Manual review required'),
                        'practical_guide': rec.get('practical_guide', '')
                    }

            LOGGER.info(f"✓ Generated guidance for {len(result)} recommendations")
            return result

        except Exception as e:
            LOGGER.warning(f"Failed to generate batch user guidance via LLM: {e}")
            return {
                item['id']: {
                    'quick_header': 'Manual review required',
                    'practical_guide': 'Unable to generate automated guidance. Please consult Azure documentation.'
                }
                for item in pending_items
            }
    
    @staticmethod
    def _sanitize_cli_quotes(response_text: str) -> str:
        """
        Sanitize Azure CLI commands in LLM response by replacing problematic quote patterns.
        
        Prevents JSON parsing errors from unescaped quotes in JMESPath queries like:
        --query "[?sku.name=='Standard_GRS']"
        
        Args:
            response_text: Raw LLM response text
            
        Returns:
            Sanitized response with fixed quotes
        """
        import re
        
        # Pattern 1: Fix --query with double-quoted array expressions
        # Replace: --query "[...]" with --query '[...]'
        response_text = re.sub(
            r'--query\s+"(\[[^\]]+\])"',
            r"--query '\1'",
            response_text
        )
        
        # Pattern 2: Fix --query with other double-quoted expressions
        # Replace: --query "..." with --query '...'
        response_text = re.sub(
            r'--query\s+"([^"]+)"',
            r"--query '\1'",
            response_text
        )
        
        return response_text

    def _check_obvious_failures(
        self,
        resource_type: str,
        description: str,
        resource: Dict[str, Any],
    ) -> Optional[Tuple[str, str]]:
        """
        Check for obvious FAIL conditions before LLM evaluation.
        
        Returns (status, reasoning) if a definitive result can be determined, else None.
        This catches explicit non-compliant configurations to avoid unnecessary LLM calls.
        
        Args:
            resource_type: Azure resource type
            description: Recommendation description
            resource: Resource configuration
            
        Returns:
            (status, reasoning) tuple if obvious failure detected, None otherwise
        """
        # SQL logical server geo-replication / failover group checks
        if "sql/servers" in resource_type.lower():
            desc_lower = description.lower()
            if "geo replication" in desc_lower or "failover" in desc_lower or "secondary" in desc_lower:
                props = resource.get("properties", {}) if isinstance(resource, dict) else {}
                has_geo = any(
                    props.get(key)
                    for key in [
                        "failover_groups",
                        "auto_failover_groups",
                        "replication_links",
                        "replication_role",
                        "partner_servers",
                        "secondary_endpoints",
                    ]
                )
                if not has_geo:
                    return (
                        "fail",
                        "No geo-replication or failover groups configured for this SQL server",
                    )

        # SQL database geo-replication checks
        if "sql/servers/databases" in resource_type.lower():
            if "geo-replication" in description.lower() or "failover" in description.lower():
                # Check for explicit zone_redundant = false (obvious fail)
                if resource.get("zone_redundant") is False:
                    return ("fail", "zone_redundant is explicitly set to false - no geo-replication configured")
                
                # Check for absence of geo-replication properties (obvious fail)
                has_geo_replication = (
                    resource.get("active_geo_replication_enabled") or
                    resource.get("failover_group") or
                    resource.get("secondary_replicas") or
                    resource.get("enable_failover_group")
                )
                if not has_geo_replication:
                    return ("fail", "No active geo-replication, failover groups, or secondary replicas configured")
        
        # AKS cluster zone redundancy checks
        if "kubernetes/managedclusters" in resource_type.lower():
            if "zone" in description.lower() or "availability" in description.lower():
                # Check if explicitly single-zone
                default_node_pool = resource.get("default_node_pool", {})
                if isinstance(default_node_pool, dict):
                    zones = default_node_pool.get("availability_zones") or default_node_pool.get("zones")
                    if zones and len(zones) == 1:
                        return ("fail", f"Only single availability zone configured: {zones}")
                    elif not zones:
                        return ("fail", "No availability zones or zone redundancy configured")
        
        # Storage account redundancy checks
        if "storage/storageaccounts" in resource_type.lower():
            if "geo" in description.lower() or "redundancy" in description.lower() or "replication" in description.lower():
                replication_type = resource.get("replication_type") or resource.get("account_replication_type")
                if replication_type and "LRS" in str(replication_type).upper():
                    return ("fail", f"Replication type is {replication_type} - not geo-redundant")
                elif replication_type and "GRS" not in str(replication_type).upper() and "GZRS" not in str(replication_type).upper():
                    return ("fail", f"Replication type {replication_type} is not geo-redundant")
        
        # Cosmos DB multi-region checks
        if "documentdb/databaseaccounts" in resource_type.lower():
            if "multi-region" in description.lower() or "failover" in description.lower():
                locations = resource.get("locations") or resource.get("regions") or resource.get("geo_locations")
                if not locations or len(locations) < 2:
                    return ("fail", "No multi-region configuration detected - only single region")
        
        return None
    
    def evaluate_resources_without_kql(
        self,
        pending_items: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """
        Evaluate resources against APRL recommendations when KQL is unavailable.
        
        This handles both:
        1. Virtual/Terraform resources (don't exist in Azure, analyze config directly)
        2. Collector resources with missing KQL rules (analyze actual Azure properties)
        
        Since these resources cannot be queried via KQL, we use LLM to analyze
        the resource configuration and recommendation to determine compliance.
        
        Args:
            pending_items: List of items with recommendation and resource info
        
        Returns:
            Dict mapping item_id to evaluation result with status, guidance, and URL
        """
        if not pending_items:
            return {}
        
        if not self.aoai_client:
            # Without LLM, mark all as requiring review
            return {
                item['id']: {
                    'status': 'pending',
                    'reason': 'LLM unavailable',
                    'quick_header': 'Manual review required',
                    'practical_guide': 'LLM evaluation unavailable. Please manually review the resource against the recommendation.'
                }
                for item in pending_items
            }
        
        deployment = self.aoai_config.get("deployment")
        if not deployment:
            return {
                item['id']: {
                    'status': 'pending',
                    'reason': 'LLM not configured',
                    'quick_header': 'Manual review required',
                    'practical_guide': 'LLM not configured. Please consult Azure documentation.'
                }
                for item in pending_items
            }
        
        # Build evaluation prompt with resources and recommendations, using short UUIDs
        uuid_to_full_id: Dict[str, str] = {}
        uuid_to_resource_type: Dict[str, str] = {}
        evaluations_text = ""
        heuristic_results: Dict[str, Dict[str, Any]] = {}  # Track items with definitive heuristic results
        items_for_llm = []  # Items that need LLM evaluation
        
        for item in pending_items:
            item_uuid = _resource_id_to_uuid(item['id'])
            uuid_to_full_id[item_uuid] = item['id']
            resource_type = item.get('resource', {}).get('type', '')
            uuid_to_resource_type[item_uuid] = resource_type.lower() if resource_type else ""
            description = item.get('description', '')
            resource = item.get('resource', {})
            
            # First: Try heuristic pre-check for obvious failures
            heuristic_result = self._check_obvious_failures(resource_type, description, resource)
            if heuristic_result:
                status, reasoning = heuristic_result
                heuristic_results[item['id']] = {
                    'status': status,
                    'reasoning': reasoning,
                    'quick_header': 'Heuristic check' if status == 'fail' else 'Configured',
                    'practical_guide': f"Configuration issue detected: {reasoning}",
                    'llm_evaluated': False,  # Mark as heuristic, not LLM-based
                }
                LOGGER.debug(f"✓ Heuristic pre-check FAIL for {item['id']}: {reasoning}")
                continue
            
            # If no obvious failure, add to LLM evaluation list
            items_for_llm.append(item)
            
            # Include full resource properties up to 2000 chars to ensure critical properties like availability_zones, zones, etc. are visible
            # Note: resources.json now has sanitized properties from terraform generator, no need to clean here
            resource_json = json.dumps(resource, indent=2)[:2000]
            evaluations_text += f"""
ID: {item_uuid}
Recommendation: {description}
Impact: {item['impact']}
Details: {item['long_description']}
Benefits: {item['potential_benefits']}
Resource Configuration:
{resource_json}
---"""
        
        # If all items were handled by heuristics, return early
        if not items_for_llm:
            LOGGER.info(f"✓ All {len(heuristic_results)} items evaluated via heuristics, skipping LLM")
            return heuristic_results
        
        def _default_learn_url(resource_type: str) -> str:
            """Return a deterministic Microsoft Learn URL for the given resource type."""
            normalized = (resource_type or "").lower()
            for key, url in self.learn_more_defaults.items():
                if normalized.startswith(key):
                    return url
            return "https://learn.microsoft.com/en-us/azure/reliability/"

        def _normalize_learn_url(candidate_url: Any, resource_type: str) -> str:
            """Ensure the learn URL is a Microsoft Learn link; otherwise fallback to a service quick start."""
            if candidate_url:
                candidate = str(candidate_url).strip()
                if candidate.startswith("https://learn.microsoft.com/"):
                    return candidate
            return _default_learn_url(resource_type)

        prompt = f"""You are an Azure resilience expert evaluating resources against Azure best practices.

These resources may be:
- Terraform-defined resources (analyze configuration directly)
- Azure resources from Azure Portal (analyze actual properties)

For each resource and recommendation pair below, determine COMPLIANCE:

STATUS RULES (STRICT):
- "pass" = All required configuration is present and correctly set
- "fail" = Required configuration is MISSING or EXPLICITLY DISABLED (this is the default assumption)
- "pending" = ONLY if the property is truly ambiguous or cannot be determined from the data
           (Use "pending" very rarely - only for properties that are genuinely unclear)

For each evaluation, provide:
1. id: The item UUID
2. status: "pass" or "fail" (almost never "pending")
3. reasoning: Why it passes or fails (1-2 sentences, be specific)
4. quick_header: Short status label (e.g., "Pass - Zone Redundant" or "Fail - GRS Not Enabled")
5. practical_guide: Specific steps to fix the issue:
   - For Terraform: Specific properties to add/modify (e.g., "Set 'zone_redundant = true' in the configuration")
   - For Azure resources: Steps using Azure Portal (e.g., "Go to Portal > [Resource] > Settings and enable GRS replication")
6. learn_more_url: Official Microsoft Learn documentation URL most relevant to this recommendation (e.g., https://learn.microsoft.com/en-us/azure/reliability/...); if uncertain, leave empty and the system will apply an official default URL.

Response format - RETURN ONLY VALID JSON:
{{
  "evaluations": [
    {{
      "id": "uuid-here",
      "status": "pass" or "fail",
      "reasoning": "Specific reason based on observed properties...",
      "quick_header": "Status header",
      "practical_guide": "Specific steps to address (Terraform properties or Portal steps)...",
      "learn_more_url": "https://learn.microsoft.com/en-us/azure/..."
    }},
    ...
  ]
}}

RESOURCES AND RECOMMENDATIONS:{evaluations_text}

PROPERTY DETECTION RULES:
- If a property is NOT in the resource JSON, assume it's not configured → "fail"
- If a property is explicitly false/disabled (e.g., zone_redundant=false) → "fail"
- If a property is empty/null and required → "fail"
- If all required properties are present and enabled → "pass"
- Only use "pending" if a property's meaning is genuinely ambiguous (extremely rare)

SPECIFIC CHECKS:
- SQL databases: Check for zone_redundant=true, active_geo_replication, failover groups
- AKS clusters: Check 'availability_zones', 'zones' in default_node_pool
- Multi-region: Check 'locations' or 'regions' arrays for multiple entries
- Zone redundancy: Look for 'availability_zones', 'zones', 'zone_redundant', 'enable_zone_redundancy'
- Replication: Check for failover groups, secondary replicas, geo-replication config

MICROSOFT LEARN URL GUIDELINES:
- Use base URLs from https://learn.microsoft.com/en-us/azure/ (not docs.microsoft.com or other domains)
- Include specific resource type in path (e.g., azure/storage, azure/reliability, azure/sql-database)
- Prefer "/reliability/" or "/architecture/" sections for resilience topics
- If you are not certain of the exact page, leave the URL blank (the system will supply a correct official link)

CRITICAL: Absence of evidence IS evidence of absence. If a resilience property is missing from the configuration, the resource FAILS that requirement."""
        
        try:
            LOGGER.info(f"Evaluating {len(pending_items)} resources without KQL with LLM")
            
            response = self.aoai_client.chat.completions.create(
                model=deployment,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert Azure resilience evaluator. Analyze Terraform resources for compliance. Generate only valid JSON responses.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                temperature=0.2,
                max_tokens=4500,
            )
            
            response_text = response.choices[0].message.content.strip()
            LOGGER.debug("LLM raw response (truncated 2000 chars): %s", response_text[:2000])
            
            # Sanitize CLI commands: replace double quotes with single quotes in Azure CLI examples
            # to prevent JSON parsing errors from unescaped quotes in JMESPath queries
            response_text = self._sanitize_cli_quotes(response_text)

            try:
                parsed = json.loads(response_text)
            except json.JSONDecodeError as decode_err:
                # Persist the full raw response for debugging malformed JSON from the model
                try:
                    import datetime
                    dump_path = Path("/tmp") / f"llm_terraform_response_{datetime.datetime.utcnow().isoformat()}.txt"
                    dump_path.write_text(response_text)
                    LOGGER.error("Saved malformed LLM response to %s", dump_path)
                except Exception as dump_err:
                    LOGGER.warning("Failed to persist malformed LLM response: %s", dump_err)

                partial = _extract_partial_json_array(response_text, "evaluations")
                if partial:
                    parsed = {"evaluations": partial}
                    LOGGER.warning("Recovered %d partial evaluation items from malformed JSON", len(partial))
                else:
                    import re
                    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                    if not json_match:
                        raise decode_err
                    parsed = json.loads(json_match.group())
            LOGGER.debug("LLM parsed type: %s keys: %s", type(parsed), list(parsed.keys()) if isinstance(parsed, dict) else None)
            
            # Log raw parsed structure for debugging
            import pprint
            LOGGER.debug("Full parsed structure:\n%s", pprint.pformat(parsed))

            # Start with heuristic results already determined
            result: Dict[str, Dict[str, Any]] = dict(heuristic_results)
            
            # Add LLM results for items that needed evaluation
            if isinstance(parsed, dict):
                eval_block = parsed.get('evaluations')
                if eval_block is None:
                    eval_block = parsed.get('recommendations')
                    if eval_block is not None:
                        LOGGER.debug("Using 'recommendations' key as evaluations block")
                if eval_block is None:
                    LOGGER.warning("LLM response missing 'evaluations' key; got keys=%s", list(parsed.keys()))
                    return result
            else:
                eval_block = []

            LOGGER.debug("Evaluations block type: %s len: %s", type(eval_block), len(eval_block) if hasattr(eval_block, '__len__') else None)
            try:
                if isinstance(eval_block, list) and eval_block:
                    LOGGER.debug("Evaluations sample[0]: %s", eval_block[0])
                elif isinstance(eval_block, dict) and eval_block:
                    first_key = next(iter(eval_block.keys()))
                    LOGGER.debug("Evaluations sample key=%r value=%s", first_key, eval_block[first_key])
            except Exception as log_err:
                LOGGER.debug("Unable to log evaluation sample: %s", log_err)

            def _add_eval(item_id_raw: Any, eval_item: Dict[str, Any]):
                item_uuid = str(item_id_raw)
                # Map UUID back to full resource ID
                full_id = uuid_to_full_id.get(item_uuid, item_uuid)
                result[full_id] = {
                    'status': eval_item.get('status', 'pending'),
                    'reasoning': eval_item.get('reasoning', ''),
                    'quick_header': eval_item.get('quick_header', 'Review required'),
                    'practical_guide': eval_item.get('practical_guide', 'See APRL documentation'),
                    'learn_more_url': _normalize_learn_url(eval_item.get('learn_more_url', None), uuid_to_resource_type.get(item_uuid, "")),
                    'llm_evaluated': True,
                }

            # Support both list and dict payloads from the LLM
            if isinstance(eval_block, dict):
                LOGGER.debug("Evaluations block is dict with %d entries", len(eval_block))
                for item_id_raw, eval_item in eval_block.items():
                    if not isinstance(eval_item, dict):
                        LOGGER.debug("Skipping non-dict evaluation item from LLM response (dict form): %s", eval_item)
                        continue
                    try:
                        try:
                            hash(item_id_raw)
                        except TypeError:
                            LOGGER.warning("Skipping eval item with unhashable id key (dict form): %r", item_id_raw)
                            continue
                        _add_eval(item_id_raw, eval_item)
                    except Exception as err:
                        LOGGER.warning("Failed to add eval item (dict form): id=%r err=%s item=%s", item_id_raw, err, eval_item)
            else:
                if not isinstance(eval_block, list):
                    LOGGER.debug("Evaluations block unexpected type: %s", type(eval_block))
                for eval_item in eval_block:
                    if not isinstance(eval_item, dict):
                        LOGGER.debug("Skipping non-dict evaluation item from LLM response: %s", eval_item)
                        continue

                    item_id_raw = eval_item.get('id')
                    if not item_id_raw:
                        LOGGER.debug("Skipping evaluation with missing id: %s", eval_item)
                        continue

                    try:
                        try:
                            hash(item_id_raw)
                        except TypeError:
                            LOGGER.warning("Skipping eval item with unhashable id (list form): %r", item_id_raw)
                            continue
                        _add_eval(item_id_raw, eval_item)
                    except Exception as err:
                        LOGGER.warning("Failed to add eval item (list form): id=%r err=%s item=%s", item_id_raw, err, eval_item)

            return result
        
        except Exception as e:
            LOGGER.exception("Failed to evaluate Terraform resources via LLM: %s", e)
            # Start with heuristic results that were already successful
            fallback: Dict[str, Dict[str, Any]] = dict(heuristic_results)
            # Add pending status for items that failed LLM evaluation
            for item in items_for_llm:
                item_id = item.get('id') or f"pending-{len(fallback)}"
                if item_id not in fallback:  # Don't override heuristic results
                    fallback[item_id] = {
                        'status': 'pending',
                        'reason': 'LLM evaluation failed',
                        'quick_header': 'Manual review required',
                        'practical_guide': 'LLM evaluation encountered an error. Please manually review.'
                    }
            return fallback
    def apply_strategy(
        self,
        strategy: ValidationStrategy,
        resource: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Apply a validation strategy to a resource.
        
        Args:
            strategy: ValidationStrategy to apply
            resource: Resource dict to validate
            
        Returns:
            (is_failing, reason): True if resource fails the check, reason explaining why
        
        Note: Sets strategy.escalate_to_llm flag when checks fail (for downstream escalation)
        """
        props = resource.get("properties", {})
        failed_checks = []
        
        for check in strategy.property_checks:
            check_type = check.get("type")
            
            # For LLM-generated checks, use generic property validation
            if strategy.strategy_type == "llm" and not check_type:
                # LLM checks have property_path, condition, expected_value
                prop_path = check.get("property_path", "")
                condition = check.get("condition", "")
                expected = check.get("expected_value", "")
                
                # Try to get the property value
                value = None
                for path in prop_path.split(" or "):
                    path = path.strip()
                    if path.startswith("properties."):
                        value = self._get_nested_prop(props, path.replace("properties.", ""))
                    elif path == "sku.name":
                        value = resource.get("sku", {}).get("name")
                    elif path == "tags":
                        value = resource.get("tags", {})
                    else:
                        # Try direct property access
                        value = self._get_nested_prop(props, path)
                    
                    if value is not None:
                        break
                
                # Check if property exists and matches expected value
                # For now, we conservatively assume resource fails if property is missing
                # (LLM checks are exploratory - low confidence on missing properties)
                if value is None:
                    # Property doesn't exist - check fails
                    failed_checks.append(f"{prop_path}: property not found")
                else:
                    # Property exists but we can't validate complex conditions
                    # Just log that we found it
                    LOGGER.debug(f"LLM check: found {prop_path} = {value}")
            
            # Heuristic check types (hardcoded)
            elif check_type == "region_count":
                regions = self._get_nested_prop(props, "replicationSettings.regions")
                if not regions or len(regions) < 2:
                    failed_checks.append(check.get("check", "Region check"))
            
            elif check_type == "zone_check":
                zones = resource.get("zones", [])
                if not zones or len(zones) < 2:
                    failed_checks.append("Not spread across multiple zones")
            
            elif check_type == "deployment_type":
                # Can't determine without sub-resource enumeration
                LOGGER.debug(f"Skipping deployment_type check (requires sub-resources)")
                pass
            
            elif check_type == "monitoring_enabled":
                extensions = props.get("extensions", [])
                tags = resource.get("tags", {})
                monitoring_tags = [t for t in tags if "monitor" in t.lower()]
                if not extensions and not monitoring_tags:
                    failed_checks.append("No monitoring detected")
            
            elif check_type == "replication_configured":
                replication = self._get_nested_prop(props, "replication")
                if not replication:
                    failed_checks.append("Replication not configured")
            
            elif check_type == "encryption_enabled":
                encryption = props.get("encryption", {})
                https_only = props.get("enableHttpsTrafficOnly", False)
                if not encryption and not https_only:
                    failed_checks.append("Encryption not configured")
        
        if failed_checks:
            # Mark for potential LLM escalation (for critical recommendations)
            if not hasattr(strategy, 'failed_property_checks'):
                strategy.failed_property_checks = failed_checks
            return True, " | ".join(failed_checks)
        
        strategy_desc = "LLM checks" if strategy.strategy_type == "llm" else "heuristic checks"
        return False, f"Passed {strategy_desc}"

    @staticmethod
    def _get_nested_prop(obj: Dict[str, Any], path: str) -> Any:
        """Safely get nested property using dot notation."""
        parts = path.split(".")
        current = obj
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current


def build_fallback_findings(
    resource_type: str,
    resources: List[Dict[str, Any]],
    recommendation_id: str,
    aprl_guid: str,
    description: str,
    long_description: str,
    potential_benefits: str,
) -> Tuple[List[str], Optional[ValidationStrategy]]:
    """
    Attempt to find failing resources using heuristics.
    
    Args:
        resource_type: Azure resource type
        resources: List of resources to evaluate
        recommendation_id: Recommendation ID
        aprl_guid: APRL GUID
        description: Recommendation description
        long_description: Detailed description
        potential_benefits: Benefits text
        
    Returns:
        (failing_resource_ids, strategy_used)
    """
    validator = HeuristicValidator()
    
    strategy = validator.analyze_recommendation(
        recommendation_id=recommendation_id,
        aprl_guid=aprl_guid,
        resource_type=resource_type,
        description=description,
        long_description=long_description,
        potential_benefits=potential_benefits,
        use_llm=False,
    )
    
    if not strategy:
        return [], None
    
    # Only apply if confidence is high enough
    if strategy.confidence < 0.5:
        LOGGER.debug(
            f"Strategy confidence too low ({strategy.confidence}) for {aprl_guid}"
        )
        return [], strategy
    
    failing_ids = []
    for resource in resources:
        is_failing, reason = validator.apply_strategy(strategy, resource)
        if is_failing:
            failing_ids.append(resource.get("id"))
            LOGGER.debug(
                f"Heuristic flagged {resource.get('id')}: {reason}"
            )
    
    return failing_ids, strategy
