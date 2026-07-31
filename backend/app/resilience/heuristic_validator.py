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

from app.llm.gateway import create_llm_gateway
from app.settings import load_settings
from app.storage.conversation_store import (
    get_subscription_conversation_id,
    set_subscription_conversation_id,
)

LOGGER = logging.getLogger(__name__)

RESILIENCE_UTILITY_JSON_HINT = (
    "Embedded agent instructions are authoritative for safety. "
    "Runtime mode: resilience_utility_json. Return valid JSON only."
)

RESILIENCE_UTILITY_TEXT_HINT = (
    "Embedded agent instructions are authoritative for safety. "
    "Runtime mode: resilience_utility_text. Return concise plain text only."
)


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

    def __init__(
        self,
        llm_gateway=None,
        subscription_id: Optional[str] = None,
        resource_type: Optional[str] = None,
    ):
        """
        Initialize validator.
        
        Args:
            llm_gateway: Optional provider-agnostic LLM gateway
            subscription_id: Optional subscription identifier for conversation reuse
            resource_type: Optional resource type for finer memory partitioning
        """
        settings = load_settings()
        self.llm_gateway = llm_gateway
        self.subscription_id = subscription_id
        self.conversation_id = get_subscription_conversation_id(subscription_id, "resilience") if subscription_id else None
        LOGGER.debug("Resilience conversation_id=%s", self.conversation_id)

        self.strategies_cache: Dict[str, ValidationStrategy] = {}
        self.learn_more_defaults = settings.get_learn_more_defaults()
        self.llm_generation_config = settings.get_llm_generation_config()
        self.resilience_agent_id = settings.get_agent_id_for_flow("resilience")

    def _get_llm_gateway(self):
        if self.llm_gateway is None:
            settings = load_settings()
            self.llm_gateway = create_llm_gateway(settings)
        return self.llm_gateway

    def _llm_available(self) -> bool:
        gateway = self._get_llm_gateway()
        return bool(gateway and gateway.is_available())

    def _generate_text_response(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        deployment = self.llm_generation_config.get("model")
        gateway = self._get_llm_gateway()
        if gateway is None:
            raise RuntimeError("LLM gateway is unavailable")
        if not self.resilience_agent_id:
            raise RuntimeError(
                "Resilience agent reference not configured. "
                "Set AI_FOUNDRY_RESILIENCE_AGENT_REFERENCE."
            )

        response = gateway.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            model=deployment,
            conversation_id=self.conversation_id,
            agent_id=self.resilience_agent_id,
        )
        metrics = gateway.get_last_metrics()
        generated_conversation_id = metrics.get("conversation_id") if isinstance(metrics, dict) else None
        if generated_conversation_id and str(generated_conversation_id).strip():
            self.conversation_id = str(generated_conversation_id).strip()
            if self.subscription_id:
                set_subscription_conversation_id(self.subscription_id, "resilience", self.conversation_id)
        return response

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
        if strategy and strategy.confidence < 0.5 and use_llm and self._llm_available():
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
        if use_llm and self._llm_available():
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
        if not self._llm_available():
            return None
        
        deployment = self.llm_generation_config.get("model")
        if not deployment:
            LOGGER.warning("azure_openai.deployment not set, cannot use LLM")
            return None
        
        prompt = f"""Build property-based validation checks (no KQL) for this recommendation.

Recommendation: {description}
Resource Type: {resource_type}
Details: {long_description}

Return valid JSON only:
{{
    "checks": [
        {{"property_path": "properties.xyz or sku.name", "condition": "should have", "expected_value": "value/pattern"}}
    ],
    "confidence": 0.0,
    "logic": "brief rationale",
    "notes": "special considerations"
}}

Use ARM property paths only (for example: properties.zones, properties.replicationSettings.regions, sku.name, properties.backup.enabled, tags)."""
        
        try:
            LOGGER.debug(f"Calling LLM for recommendation {aprl_guid}: {description[:50]}...")
            
            response_text = self._generate_text_response(
                system_prompt=RESILIENCE_UTILITY_JSON_HINT,
                user_prompt=prompt,
                temperature=0.3,
                max_tokens=500,
            )
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
        if not self._llm_available():
            return False, None
        
        deployment = self.llm_generation_config.get("model")
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
        
        prompt = f"""Determine if this resource fails the recommendation.

Recommendation: {description}
Impact: {impact}
Details: {long_description}
Resource Configuration:
{json.dumps(clean_resource, indent=2)}

Return valid JSON only:
{{
    "fails_recommendation": true,
    "reasoning": "specific pass/fail explanation",
    "missing_properties": ["missing or incorrect properties"],
    "confidence": 0.0
}}"""
        
        try:
            LOGGER.debug(
                f"Deep LLM analysis for {aprl_guid} (impact: {impact}) on resource {resource.get('name')}"
            )
            
            response_text = self._generate_text_response(
                system_prompt=RESILIENCE_UTILITY_JSON_HINT,
                user_prompt=prompt,
                temperature=0.2,
                max_tokens=800,
            )
            
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
        if not self._llm_available():
            return "Manual review required. Please consult the Azure Well-Architected Framework documentation for guidance."
        
        deployment = self.llm_generation_config.get("model")
        if not deployment:
            return "Manual review required. Please consult the Azure Well-Architected Framework documentation for guidance."
        
        prompt = f"""Create concise manual validation guidance (plain text, max 150 words).

    Recommendation: {description}
    Impact: {impact}
    Details: {long_description}
    Benefits: {potential_benefits}

    Include what to check, where in Azure Portal, and optional Azure CLI verification."""
        
        try:
            guidance = self._generate_text_response(
                system_prompt=RESILIENCE_UTILITY_TEXT_HINT,
                user_prompt=prompt,
                temperature=0.3,
                max_tokens=200,
            )
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

        if not self._llm_available():
            return {
                item['id']: {
                    'quick_header': 'Manual review required',
                    'practical_guide': 'Please consult the Azure Well-Architected Framework documentation.'
                }
                for item in pending_items
            }
        
        deployment = self.llm_generation_config.get("model")
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

        prompt = f"""For each item below, generate:
1) quick_header (max 10 words)
2) practical_guide (max 150 words, actionable verification steps)

Return valid JSON only:
{{
    "recommendations": [
        {{
            "id": "uuid",
            "quick_header": "short check summary",
            "practical_guide": "portal path, key properties to verify, optional CLI example, official learn link"
        }}
    ]
}}

RECOMMENDATIONS TO PROCESS:{recommendations_text}

When including Azure CLI queries, ensure JSON-safe quoting."""

        try:
            LOGGER.info(f"Generating batch user guidance for {len(pending_items)} pending items")

            response_text = self._generate_text_response(
                system_prompt=RESILIENCE_UTILITY_JSON_HINT,
                user_prompt=prompt,
                temperature=0.2,
                max_tokens=4000,
            )

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
        properties = resource.get("properties", {}) if isinstance(resource, dict) else {}
        resource_config = {
            **resource,
            **(properties if isinstance(properties, dict) else {}),
        }

        # SQL logical server geo-replication / failover group checks
        if "sql/servers" in resource_type.lower():
            desc_lower = description.lower()
            if "geo replication" in desc_lower or "failover" in desc_lower or "secondary" in desc_lower:
                props = resource_config
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
            if any(term in description.lower() for term in ("geo", "redundancy", "redundant", "replication")):
                replication_type = resource_config.get("replication_type") or resource_config.get("account_replication_type")
                normalized_replication = str(replication_type or "").upper()
                if normalized_replication in {"ZRS", "GZRS", "RA-GZRS", "GRS", "RA-GRS"}:
                    return ("pass", f"Replication type {replication_type} provides zone or region redundancy")
                if normalized_replication == "LRS":
                    return ("fail", f"Replication type is {replication_type} - not geo-redundant")
                elif replication_type:
                    return ("fail", f"Replication type {replication_type} is not geo-redundant")

        if "search/searchservices" in resource_type.lower():
            description_lower = description.lower()
            if "az support" in description_lower or "multiple replicas" in description_lower:
                replica_count = resource_config.get("replica_count") or resource_config.get("replicaCount") or 0
                try:
                    replica_count = int(replica_count)
                except (TypeError, ValueError):
                    replica_count = 0
                if replica_count >= 2:
                    return ("pass", f"Search service has {replica_count} replicas configured for zone distribution")
                return ("fail", f"Search service has only {replica_count} replicas; at least 2 are required")
        
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
        
        if not self._llm_available():
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
        
        deployment = self.llm_generation_config.get("model")
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

        prompt = f"""Evaluate each resource/recommendation pair for resilience compliance.

Decision rules:
- pass: required resilience properties are present and enabled
- fail: the recommendation unconditionally applies and the configuration is definitively non-compliant
- pending: available static configuration is insufficient to decide
- not_applicable: the recommendation is conditional on a workload mode or requirement not evidenced here (for example batch processing, geographic routing, PAYG overflow, Citrix VDA, or data-residency requirements)

Use only the provided resource JSON and recommendation text.
Do not assume every catalog recommendation applies. Never mark a conditional recommendation failed solely because its optional workload prerequisite is absent.

Return valid JSON with this exact shape:
{{
    "evaluations": [
        {{
            "id": "uuid",
            "status": "pass|fail|pending|not_applicable",
            "reasoning": "1-2 specific sentences",
            "quick_header": "short status",
            "practical_guide": "actionable fix steps (Terraform property names and/or Azure Portal path)",
            "learn_more_url": "https://learn.microsoft.com/... or empty string"
        }}
    ]
}}

Check patterns to consider when relevant:
- zone redundancy: availability_zones, zones, zone_redundant, enable_zone_redundancy
- multi-region: locations, regions, geo-replication/failover config
- SQL resilience: zone_redundant, active geo-replication, failover groups
- AKS resilience: availability_zones/zones in default node pool

For learn_more_url, prefer a specific https://learn.microsoft.com/en-us/azure/ page. If uncertain, return an empty string.

RESOURCES AND RECOMMENDATIONS:{evaluations_text}"""
        
        try:
            LOGGER.info(f"Evaluating {len(pending_items)} resources without KQL with LLM")
            
            response_text = self._generate_text_response(
                system_prompt=RESILIENCE_UTILITY_JSON_HINT,
                user_prompt=prompt,
                temperature=0.2,
                max_tokens=4500,
            )
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
                status = str(eval_item.get('status', 'pending')).lower()
                if status not in {'pass', 'fail', 'pending', 'not_applicable'}:
                    status = 'pending'
                result[full_id] = {
                    'status': status,
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
            (is_failing, detailed_reason): True if resource fails the check, detailed reason explaining why
        
        Note: Sets strategy.escalate_to_llm flag when checks fail (for downstream escalation)
        """
        props = resource.get("properties", {})
        failed_checks = []
        passed_checks = []  # Track what passed
        check_details = {}  # Store details about what was found
        
        for check in strategy.property_checks:
            check_type = check.get("type")
            check_description = check.get("check", check_type or "property check")
            
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
                    check_details[prop_path] = {"expected": expected, "actual": "not found"}
                else:
                    # Property exists but we can't validate complex conditions
                    # Just log that we found it
                    LOGGER.debug(f"LLM check: found {prop_path} = {value}")
                    passed_checks.append(f"{prop_path}: found")
                    check_details[prop_path] = {"expected": expected, "actual": value}
            
            # Heuristic check types (hardcoded)
            elif check_type == "region_count":
                regions = self._get_nested_prop(props, "replicationSettings.regions")
                if not regions or len(regions) < 2:
                    actual_count = len(regions) if regions else 0
                    failed_checks.append(f"{check_description} (found {actual_count} region(s), need 2+)")
                    check_details["regions"] = {"expected": "2+", "actual": actual_count}
                else:
                    passed_checks.append(f"{check_description} (found {len(regions)} regions)")
                    check_details["regions"] = {"expected": "2+", "actual": len(regions)}
            
            elif check_type == "zone_check":
                zones = resource.get("zones", [])
                if not zones or len(zones) < 2:
                    actual_count = len(zones) if zones else 0
                    actual_zones = ", ".join(zones) if zones else "none"
                    failed_checks.append(f"{check_description} (found zones: {actual_zones}, need 2+)")
                    check_details["zones"] = {"expected": "2+ zones", "actual": actual_zones or "none"}
                else:
                    actual_zones = ", ".join(zones)
                    passed_checks.append(f"{check_description} (zones: {actual_zones})")
                    check_details["zones"] = {"expected": "2+ zones", "actual": actual_zones}
            
            elif check_type == "deployment_type":
                # Can't determine without sub-resource enumeration
                LOGGER.debug(f"Skipping deployment_type check (requires sub-resources)")
                pass
            
            elif check_type == "monitoring_enabled":
                extensions = props.get("extensions", [])
                tags = resource.get("tags", {})
                monitoring_tags = [t for t in tags if "monitor" in t.lower()]
                if not extensions and not monitoring_tags:
                    failed_checks.append(f"{check_description} (no extensions or monitoring tags found)")
                    check_details["monitoring"] = {"expected": "monitoring enabled", "actual": "not configured"}
                else:
                    details = []
                    if extensions:
                        details.append(f"{len(extensions)} extension(s)")
                    if monitoring_tags:
                        details.append(f"monitoring tags")
                    passed_checks.append(f"{check_description} ({', '.join(details)})")
                    check_details["monitoring"] = {"expected": "monitoring enabled", "actual": ", ".join(details)}
            
            elif check_type == "replication_configured":
                replication = self._get_nested_prop(props, "replication")
                if not replication:
                    failed_checks.append(f"{check_description} (not configured)")
                    check_details["replication"] = {"expected": "configured", "actual": "not found"}
                else:
                    passed_checks.append(f"{check_description} (configured)")
                    check_details["replication"] = {"expected": "configured", "actual": "enabled"}
            
            elif check_type == "encryption_enabled":
                encryption = props.get("encryption", {})
                https_only = props.get("enableHttpsTrafficOnly", False)
                if not encryption and not https_only:
                    failed_checks.append(f"{check_description} (not configured)")
                    check_details["encryption"] = {"expected": "enabled", "actual": "disabled"}
                else:
                    details = []
                    if encryption:
                        details.append("encryption at rest")
                    if https_only:
                        details.append("HTTPS only")
                    passed_checks.append(f"{check_description} ({', '.join(details)})")
                    check_details["encryption"] = {"expected": "enabled", "actual": ", ".join(details)}
        
        # Build detailed reason string
        if failed_checks:
            # Mark for potential LLM escalation (for critical recommendations)
            if not hasattr(strategy, 'failed_property_checks'):
                strategy.failed_property_checks = failed_checks
            detailed_reason = "Failed: " + " | ".join(failed_checks)
        else:
            # Include passed checks in the reason
            if passed_checks:
                detailed_reason = "Passed: " + " | ".join(passed_checks)
            else:
                strategy_desc = "LLM checks" if strategy.strategy_type == "llm" else "heuristic checks"
                detailed_reason = f"Passed {strategy_desc}"
        
        # Store check details and passed checks in strategy for later use
        strategy.check_details = check_details
        strategy.passed_checks = passed_checks
        strategy.failed_checks_details = failed_checks
        
        return bool(failed_checks), detailed_reason

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
