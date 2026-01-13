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
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)


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
        self.aoai_client = aoai_client
        self.strategies_cache: Dict[str, ValidationStrategy] = {}

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
        
        # Get deployment name from environment
        import os
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not deployment:
            LOGGER.warning("AZURE_OPENAI_DEPLOYMENT not set, cannot use LLM")
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
            
            LOGGER.info(
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
        
        import os
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
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
            LOGGER.info(
                f"🔍 Deep LLM analysis for {aprl_guid} (impact: {impact}) on resource {resource.get('name')}"
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
            
            LOGGER.info(
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
        
        import os
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
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

        import os
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not deployment:
            return {
                item['id']: {
                    'quick_header': 'Manual review required',
                    'practical_guide': 'Please consult the Azure Well-Architected Framework documentation.'
                }
                for item in pending_items
            }

        recommendations_text = ""
        for item in pending_items:
            recommendations_text += f"""
ID: {item['id']}
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
      "id": "the-recommendation-id",
      "quick_header": "Check if backup is enabled and retention is set",
      "practical_guide": "Go to Azure Portal > [Resource Type] > Backup. Verify backup is enabled and retention policy meets your requirements. Can also use: az backup vault list --resource-group <rg-name>. See: https://learn.microsoft.com/azure/backup/backup-overview"
    }},
    ...
  ]
}}

RECOMMENDATIONS TO PROCESS:{recommendations_text}

Requirements for practical guides:
- Include specific Azure Portal navigation path
- Include Azure CLI command example if applicable
- Include official Microsoft documentation link
- Focus on what to verify, not how to implement
- Be actionable in 5 minutes
- Keep under 150 words per guide

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
                max_tokens=2000,
            )

            response_text = response.choices[0].message.content.strip()

            try:
                parsed = json.loads(response_text)
            except json.JSONDecodeError:
                import re
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if not json_match:
                    raise
                parsed = json.loads(json_match.group())

            result: Dict[str, Dict[str, str]] = {}
            for rec in parsed.get('recommendations', []):
                rec_id = rec.get('id')
                if rec_id:
                    result[rec_id] = {
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
