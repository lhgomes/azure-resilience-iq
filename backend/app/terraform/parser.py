"""
Terraform HCL parser for extracting Azure resource definitions.

Parses Terraform .tf files and extracts resource blocks, local values,
variables, and outputs to build a complete resource inventory.

Supports both plain HCL and parsed JSON (via terraform show -json).

Uses hcl2 library for proper HCL parsing, with fallback to regex for simple files.
Install: pip install python-hcl2
"""

import re
import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

try:
    import hcl2
    HAS_HCL2 = True
except ImportError:
    HAS_HCL2 = False


@dataclass
class LocalValue:
    """Terraform local value."""
    name: str
    value: Any


@dataclass
class Variable:
    """Terraform input variable."""
    name: str
    type: Optional[str]
    default: Optional[Any]
    description: Optional[str]


@dataclass
class TerraformResource:
    """Parsed Terraform resource."""
    type: str  # e.g., "azurerm_kubernetes_cluster"
    name: str  # e.g., "main"
    id: str    # constructed as type.name or from resource id attribute
    attributes: Dict[str, Any]  # all resource attributes


class TerraformParser:
    """
    Parse Terraform files and extract Azure resource definitions.
    
    Handles:
    - Resource blocks (resource "type" "name" { ... })
    - Local values
    - Variables
    - Outputs
    """
    
    # Map Terraform azurerm resource types to Azure REST API resource types
    RESOURCE_TYPE_MAP = {
        "azurerm_kubernetes_cluster": "Microsoft.ContainerService/managedClusters",
        "azurerm_resource_group": "Microsoft.Resources/resourceGroups",
        "azurerm_virtual_network": "Microsoft.Network/virtualNetworks",
        "azurerm_subnet": "Microsoft.Network/virtualNetworks/subnets",
        "azurerm_storage_account": "Microsoft.Storage/storageAccounts",
        "azurerm_storage_container": "Microsoft.Storage/storageAccounts/blobServices/containers",
        "azurerm_mssql_server": "Microsoft.Sql/servers",
        "azurerm_mssql_database": "Microsoft.Sql/servers/databases",
        "azurerm_cosmosdb_account": "Microsoft.DocumentDB/databaseAccounts",
        "azurerm_cosmosdb_sql_database": "Microsoft.DocumentDB/databaseAccounts/sqlDatabases",
        "azurerm_cosmosdb_sql_container": "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers",
        "azurerm_container_registry": "Microsoft.ContainerRegistry/registries",
        "azurerm_api_management": "Microsoft.ApiManagement/service",
        "azurerm_api_management_api": "Microsoft.ApiManagement/service/apis",
        "azurerm_api_management_api_operation": "Microsoft.ApiManagement/service/apis/operations",
        "azurerm_network_interface": "Microsoft.Network/networkInterfaces",
        "azurerm_virtual_machine": "Microsoft.Compute/virtualMachines",
        "azurerm_app_service": "Microsoft.Web/sites",
        "azurerm_function_app": "Microsoft.Web/sites",
        "azurerm_load_balancer": "Microsoft.Network/loadBalancers",
        "azurerm_application_gateway": "Microsoft.Network/applicationGateways",
        "azurerm_public_ip": "Microsoft.Network/publicIPAddresses",
        "azurerm_network_security_group": "Microsoft.Network/networkSecurityGroups",
    }
    
    def __init__(self):
        self.resources: List[TerraformResource] = []
        self.locals: Dict[str, LocalValue] = {}
        self.variables: Dict[str, Variable] = {}
        self.outputs: Dict[str, Any] = {}
        self.resolved_locals: Dict[str, Any] = {}  # Resolved local values
        self.resolved_variables: Dict[str, Any] = {}  # Resolved variable values
    
    def parse_file(self, file_path: Path) -> List[TerraformResource]:
        """
        Parse a single Terraform file and extract resources.
        
        Args:
            file_path: Path to .tf file
            
        Returns:
            List of extracted TerraformResource objects
        """
        content = file_path.read_text(encoding="utf-8")
        return self.parse_hcl(content)
    
    def parse_directory(self, dir_path: Path, user_variables: Optional[Dict[str, Any]] = None) -> List[TerraformResource]:
        """
        Parse all .tf files in a directory.
        
        Args:
            dir_path: Path to directory containing .tf files
            user_variables: Optional dict of variable values to use
            
        Returns:
            List of all extracted TerraformResource objects
        """
        all_resources = []
        
        for tf_file in sorted(dir_path.glob("*.tf")):
            resources = self.parse_file(tf_file)
            all_resources.extend(resources)
        
        self.resources = all_resources
        
        # Resolve variables and locals, then apply to resources
        self.resolve_variables_and_locals(user_variables)
        self.apply_resolutions_to_resources()
        
        return all_resources
    
    def parse_json_state(self, json_data: Dict[str, Any]) -> List[TerraformResource]:
        """
        Parse Terraform JSON state/plan output (terraform show -json).
        
        Args:
            json_data: Parsed JSON from 'terraform show -json'
            
        Returns:
            List of extracted TerraformResource objects
        """
        resources = []
        
        # Handle state format
        if "resources" in json_data:
            for resource_block in json_data["resources"]:
                resource_type = resource_block.get("type")
                
                for resource_instance in resource_block.get("instances", []):
                    name = resource_block.get("name")
                    attributes = resource_instance.get("attributes", {})
                    
                    resource = TerraformResource(
                        type=resource_type,
                        name=name,
                        id=self._build_resource_id(resource_type, name, attributes),
                        attributes=attributes,
                    )
                    resources.append(resource)
        
        # Handle plan format
        elif "resource_changes" in json_data:
            for change in json_data["resource_changes"]:
                resource_type = change.get("type")
                name = change.get("name")
                
                # Get attributes from after state
                after = change.get("after", {})
                if after:
                    resource = TerraformResource(
                        type=resource_type,
                        name=name,
                        id=self._build_resource_id(resource_type, name, after),
                        attributes=after,
                    )
                    resources.append(resource)
        
        self.resources = resources
        return resources
    
    def parse_hcl(self, content: str) -> List[TerraformResource]:
        """
        Parse HCL content and extract resource blocks.
        
        Uses hcl2 library if available (proper parsing).
        Falls back to regex parser for simple files or if hcl2 not installed.
        
        Args:
            content: HCL file content as string
            
        Returns:
            List of extracted TerraformResource objects
        """
        resources = []
        
        # Try HCL2 parser first (proper parsing)
        if HAS_HCL2:
            try:
                hcl_dict = hcl2.loads(content)
                
                # hcl2 returns resource as a LIST of dicts, not a dict
                # Format: [{"type": {"name": {...}}}, {"type": {"name": {...}}}]
                if "resource" in hcl_dict:
                    for resource_item in hcl_dict["resource"]:
                        # Each item is a dict with one key (resource type)
                        for resource_type, resources_dict in resource_item.items():
                            # resources_dict is a dict: {"name": {...}, "name2": {...}}
                            for resource_name, resource_config in resources_dict.items():
                                # Normalize nested list blocks into single item
                                # hcl2 returns blocks like "default_node_pool" as lists
                                normalized_config = self._normalize_hcl2_config(resource_config)
                                
                                resource = TerraformResource(
                                    type=resource_type,
                                    name=resource_name,
                                    id=self._build_resource_id(resource_type, resource_name, normalized_config),
                                    attributes=normalized_config,
                                )
                                resources.append(resource)
                
                # Extract locals - hcl2 returns as list of dicts
                if "locals" in hcl_dict:
                    for locals_item in hcl_dict["locals"]:
                        for local_name, local_value in locals_item.items():
                            self.locals[local_name] = LocalValue(name=local_name, value=local_value)
                
                # Extract variables - hcl2 returns as list of dicts
                if "variable" in hcl_dict:
                    for var_item in hcl_dict["variable"]:
                        for var_name, var_config in var_item.items():
                            # var_config might be a list with one dict
                            if isinstance(var_config, list) and var_config:
                                var_config = var_config[0]
                            self.variables[var_name] = Variable(
                                name=var_name,
                                type=var_config.get("type") if isinstance(var_config, dict) else None,
                                default=var_config.get("default") if isinstance(var_config, dict) else None,
                                description=var_config.get("description") if isinstance(var_config, dict) else None,
                            )
                
                self.resources = resources
                return resources
            except Exception as e:
                # Fall back to regex parser if HCL2 fails
                import logging
                logging.debug(f"HCL2 parsing failed: {e}, falling back to regex parser")
        
        # Regex fallback parser for simple files
        return self._parse_hcl_regex(content)
    
    def _normalize_hcl2_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize HCL2 parsed config.
        
        HCL2 returns nested blocks as lists (since you can have multiples).
        For single-instance blocks, extract the first item.
        
        Args:
            config: Config dict from hcl2 parser
            
        Returns:
            Normalized config with list blocks converted to single dicts
        """
        normalized = {}
        
        for key, value in config.items():
            # Common nested blocks that appear as lists
            single_block_types = {
                'default_node_pool', 'identity', 'network_profile',
                'consistency_policy', 'geo_location', 'storage_profile',
                'admin_user', 'database', 'sku', 'capabilities'
            }
            
            # If value is a list and key is a known single-instance block, extract first
            if isinstance(value, list) and key in single_block_types and value:
                # Recursively normalize the nested item
                if isinstance(value[0], dict):
                    normalized[key] = self._normalize_hcl2_config(value[0])
                else:
                    normalized[key] = value[0]
            elif isinstance(value, dict):
                # Recursively normalize nested dicts
                normalized[key] = self._normalize_hcl2_config(value)
            else:
                # Keep as-is (strings, numbers, booleans, lists of primitives)
                normalized[key] = value
        
        return normalized
    
    def _build_resource_id(
        self, resource_type: str, name: str, attributes: Dict[str, Any]
    ) -> str:
        """
        Build a resource ID from Terraform resource.
        
        Prioritizes:
        1. "id" attribute from Terraform (if available, e.g., from state)
        2. Constructed ID matching Azure REST API format
        
        Args:
            resource_type: Terraform resource type
            name: Terraform resource name
            attributes: Resource attributes
            
        Returns:
            Resource ID string
        """
        # If Terraform state includes id attribute, use it
        if "id" in attributes:
            return attributes["id"]
        
        # Otherwise construct an ID
        # For now, use a deterministic format that will be resolved during edge generation
        return f"/terraform/{resource_type}/{name}"
    
    def _parse_hcl_regex(self, content: str) -> List[TerraformResource]:
        """
        Regex-based HCL parser - fallback when hcl2 is not available.
        
        Handles basic resource blocks but may miss complex nested structures.
        For better results, install hcl2: pip install python-hcl2
        
        Args:
            content: HCL file content as string
            
        Returns:
            List of extracted TerraformResource objects
        """
        resources = []
        
        # Extract resource blocks: resource "type" "name" { ... }
        resource_pattern = r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{([^}]*)\}'
        
        for match in re.finditer(resource_pattern, content, re.DOTALL):
            resource_type = match.group(1)
            resource_name = match.group(2)
            resource_body = match.group(3)
            
            # Parse attributes
            attributes = self._parse_hcl_attributes(resource_body)
            
            resource = TerraformResource(
                type=resource_type,
                name=resource_name,
                id=self._build_resource_id(resource_type, resource_name, attributes),
                attributes=attributes,
            )
            resources.append(resource)
        
        # Extract locals
        self._extract_locals(content)
        
        # Extract variables
        self._extract_variables(content)
        
        self.resources = resources
        return resources
    
    def _parse_hcl_attributes(self, hcl_body: str) -> Dict[str, Any]:
        """
        Parse HCL attribute assignments.
        
        Handles simple cases:
        - key = "value" (string)
        - key = 123 (number)
        - key = true/false (boolean)
        - key = [list] (arrays)
        - key = { nested } (objects)
        
        Complex nested structures are stored as strings for later interpretation.
        
        Args:
            hcl_body: HCL block content
            
        Returns:
            Dictionary of attributes
        """
        attributes = {}
        
        # Simple key = value pattern
        # Handles strings, numbers, booleans
        attr_pattern = r'(\w+)\s*=\s*(?:"([^"]*)"|(\d+\.?\d*)|(\btrue\b|\bfalse\b)|(\[[^\]]*\])|(\{[^}]*\}))'
        
        for match in re.finditer(attr_pattern, hcl_body):
            key = match.group(1)
            string_val = match.group(2)
            number_val = match.group(3)
            bool_val = match.group(4)
            array_val = match.group(5)
            object_val = match.group(6)
            
            if string_val is not None:
                attributes[key] = string_val
            elif number_val is not None:
                attributes[key] = float(number_val) if "." in number_val else int(number_val)
            elif bool_val is not None:
                attributes[key] = bool_val.lower() == "true"
            elif array_val is not None:
                # Store as string, will be processed as needed
                attributes[key] = array_val
            elif object_val is not None:
                attributes[key] = object_val
        
        return attributes
    
    def _extract_locals(self, content: str):
        """Extract local values from HCL."""
        locals_pattern = r'locals\s*\{([^}]*)\}'
        match = re.search(locals_pattern, content, re.DOTALL)
        
        if match:
            locals_body = match.group(1)
            attr_pattern = r'(\w+)\s*=\s*"([^"]*)"'
            for attr_match in re.finditer(attr_pattern, locals_body):
                name = attr_match.group(1)
                value = attr_match.group(2)
                self.locals[name] = LocalValue(name=name, value=value)
    
    def _extract_variables(self, content: str):
        """Extract input variables from HCL."""
        var_pattern = r'variable\s+"([^"]+)"\s*\{([^}]*)\}'
        
        for match in re.finditer(var_pattern, content, re.DOTALL):
            var_name = match.group(1)
            var_body = match.group(2)
            
            # Extract default, type, description
            type_match = re.search(r'type\s*=\s*(\w+)', var_body)
            default_match = re.search(r'default\s*=\s*"([^"]*)"', var_body)
            desc_match = re.search(r'description\s*=\s*"([^"]*)"', var_body)
            
            var = Variable(
                name=var_name,
                type=type_match.group(1) if type_match else None,
                default=default_match.group(1) if default_match else None,
                description=desc_match.group(1) if desc_match else None,
            )
            self.variables[var_name] = var
    
    def resolve_variables_and_locals(self, user_variables: Optional[Dict[str, Any]] = None):
        """
        Resolve variables and locals to their actual values.
        
        Args:
            user_variables: Optional dict of user-provided variable values
        """
        # Step 1: Resolve variables (use user values or defaults)
        if user_variables is None:
            user_variables = {}
        
        for var_name, var in self.variables.items():
            if var_name in user_variables:
                # Use user-provided value
                self.resolved_variables[var_name] = user_variables[var_name]
            elif var.default is not None:
                # Use default value
                self.resolved_variables[var_name] = var.default
            else:
                # No value available, store None (will be handled in expression resolution)
                self.resolved_variables[var_name] = None
        
        # Step 2: Resolve locals (evaluate expressions)
        for local_name, local_val in self.locals.items():
            resolved = self._resolve_expression(local_val.value)
            self.resolved_locals[local_name] = resolved
        
        # Step 3: Generate deterministic values for random resources
        self._generate_random_values()
    
    def _generate_random_values(self):
        """Generate deterministic values for random_string and random_password resources."""
        import hashlib
        
        # Look for random_string and random_password resources in parsed resources
        for resource in self.resources:
            if resource.type == 'random_string':
                length = resource.attributes.get('length', 16)
                # Create deterministic value based on resource name
                seed = f"random_string_{resource.name}_{length}"
                hash_val = hashlib.md5(seed.encode()).hexdigest()
                # Generate alphanumeric string of specified length
                value = ''.join(c for c in hash_val if c.isalnum())[:length]
                self.resolved_locals[f"{resource.type}.{resource.name}.result"] = value
            
            elif resource.type == 'random_password':
                length = resource.attributes.get('length', 20)
                special = resource.attributes.get('special', True)
                # Create deterministic password based on resource name
                seed = f"random_password_{resource.name}_{length}_{special}"
                hash_val = hashlib.sha256(seed.encode()).hexdigest()
                if special:
                    # Mix in some special characters
                    value = hash_val[:length-4] + "@#$!"
                else:
                    value = ''.join(c for c in hash_val if c.isalnum())[:length]
                self.resolved_locals[f"{resource.type}.{resource.name}.result"] = value
    
    def _split_function_args(self, args_str: str) -> List[str]:
        """Split function arguments, respecting quotes and nested parentheses."""
        args = []
        current_arg = ""
        depth = 0
        in_quotes = False
        
        for i, char in enumerate(args_str):
            if char == '"' and (i == 0 or args_str[i-1] != '\\'):
                in_quotes = not in_quotes
                current_arg += char
            elif char == '(' and not in_quotes:
                depth += 1
                current_arg += char
            elif char == ')' and not in_quotes:
                depth -= 1
                current_arg += char
            elif char == ',' and depth == 0 and not in_quotes:
                args.append(current_arg.strip())
                current_arg = ""
            else:
                current_arg += char
        
        if current_arg.strip():
            args.append(current_arg.strip())
        
        return args
    
    def _resolve_expression(self, expr: Any) -> Any:
        """
        Resolve Terraform expressions to actual values.
        
        Handles:
        - Variable references: var.name
        - Local references: local.name  
        - Function calls: replace(), lower(), etc.
        
        Args:
            expr: Expression to resolve
            
        Returns:
            Resolved value
        """
        if not isinstance(expr, str):
            return expr
        
        # Iteratively resolve until no more changes
        max_iterations = 20  # Increased from 10 to handle nested functions better
        for _ in range(max_iterations):
            prev_expr = expr
            expr = self._resolve_expression_once(expr)
            if expr == prev_expr:
                break
        
        return expr
    
    def _resolve_expression_once(self, expr: str) -> str:
        """Single pass of expression resolution."""
        import re
        
        # 1. Resolve random_string.name.result and random_password.name.result first (both in ${} and bare)
        def replace_random_ref(match):
            resource_type = match.group(1)  # random_string or random_password
            res_name = match.group(2)  # resource name
            attr = match.group(3)  # attribute (usually 'result')
            
            key = f"{resource_type}.{res_name}.{attr}"
            val = self.resolved_locals.get(key, None)
            if val is None:
                return match.group(0)
            return str(val)
        
        # Replace ${random_*.name.result} references
        expr = re.sub(r'\$\{(random_(?:string|password))\.([^.}]+)\.([^}]+)\}', replace_random_ref, expr)
        # Replace bare random_*.name.result references (without ${})
        expr = re.sub(r'(?<!\$\{)(random_(?:string|password))\.([^.}]+)\.([^}]+)(?!\})', replace_random_ref, expr)
        
        # 2. Resolve var.x and local.x references (including those inside ${...})
        def replace_ref(match):
            ref_type = match.group(1)  # var or local
            ref_name = match.group(2)  # variable/local name
            
            if ref_type == "var":
                val = self.resolved_variables.get(ref_name, None)
                if val is None:
                    return match.group(0)
            elif ref_type == "local":
                val = self.resolved_locals.get(ref_name, None)
                if val is None:
                    return match.group(0)
            else:
                val = match.group(0)
            
            # If the value is a dict or list, convert to string representation
            if isinstance(val, (dict, list)):
                import json
                return json.dumps(val)
            return str(val)
        
        expr = re.sub(r'\$\{(var|local)\.([^}]+)\}', replace_ref, expr)
        
        # Also handle bare references (without ${})
        def replace_bare_ref(match):
            ref_type = match.group(1)  # var or local
            ref_name = match.group(2)  # variable/local name
            
            if ref_type == "var":
                val = self.resolved_variables.get(ref_name, None)
            elif ref_type == "local":
                val = self.resolved_locals.get(ref_name, None)
            else:
                return match.group(0)
            
            if val is None:
                return match.group(0)
            
            # Return as string for function arguments
            if isinstance(val, str):
                return f'"{val}"'
            elif isinstance(val, bool):
                return str(val).lower()
            elif isinstance(val, (int, float)):
                return str(val)
            else:
                import json
                return json.dumps(val)
        
        # Match var.x or local.x that are NOT inside ${}
        expr = re.sub(r'(?<!\$\{)(var|local)\.([a-zA-Z_][a-zA-Z0-9_]*)', replace_bare_ref, expr)
        
        # 3. Resolve Terraform functions
        
        # Helper for function evaluation
        def eval_replace_call(call_str):
            # call_str should be like: replace("text${ref}", "pattern", "repl")
            # Extract arguments respecting quotes and nested refs
            in_quotes = False
            paren_depth = 0
            parts = []
            current = ""
            
            # Skip "replace("
            i = 8
            while i < len(call_str) - 1:  # -1 for closing )
                char = call_str[i]
                if char == '"':
                    in_quotes = not in_quotes
                    current += char
                elif char == ',' and not in_quotes:
                    parts.append(current.strip())
                    current = ""
                else:
                    current += char
                i += 1
            if current:
                parts.append(current.strip())
            
            if len(parts) == 3:
                string_val = parts[0].strip('"')
                pattern_val = parts[1].strip('"')
                replace_val = parts[2].strip('"')
                
                # Apply replace
                if pattern_val.startswith('/') and pattern_val.endswith('/'):
                    pattern_val = pattern_val[1:-1]
                    return re.sub(pattern_val, replace_val, string_val)
                else:
                    return string_val.replace(pattern_val, replace_val)
            return call_str
        
        # Handle both ${replace(...)} and bare replace(...) calls
        # Using a smarter approach that handles nested parentheses
        def find_and_replace_functions(expr_str, func_name, eval_func):
            """Find function calls and evaluate them, handling nested parentheses."""
            result = ""
            i = 0
            while i < len(expr_str):
                # Look for ${func_name(...)}  or bare func_name(...)
                if expr_str[i:].startswith('${' + func_name + '('):
                    # Found wrapped function
                    start = i
                    i += 2  # Skip ${
                    func_start = i
                    i += len(func_name) + 1  # Skip func_name(
                    
                    # Find matching closing paren
                    paren_depth = 1
                    while i < len(expr_str) and paren_depth > 0:
                        if expr_str[i] == '(':
                            paren_depth += 1
                        elif expr_str[i] == ')':
                            paren_depth -= 1
                        i += 1
                    
                    # Extract the function call
                    func_call = expr_str[func_start:i-1] + ')'  # Include closing paren
                    
                    # Should be followed by }
                    if i < len(expr_str) and expr_str[i] == '}':
                        i += 1
                        # Evaluate the function
                        evaluated = eval_func(func_call)
                        result += evaluated
                    else:
                        # Malformed, just add as-is
                        result += expr_str[start:i]
                        
                elif expr_str[i:].startswith(func_name + '('):
                    # Found bare function
                    start = i
                    i += len(func_name) + 1  # Skip func_name(
                    
                    # Find matching closing paren
                    paren_depth = 1
                    while i < len(expr_str) and paren_depth > 0:
                        if expr_str[i] == '(':
                            paren_depth += 1
                        elif expr_str[i] == ')':
                            paren_depth -= 1
                        i += 1
                    
                    # Extract the function call
                    func_call = expr_str[start:i]
                    
                    # Evaluate the function
                    evaluated = eval_func(func_call)
                    result += evaluated
                else:
                    result += expr_str[i]
                    i += 1
            return result
        
        # INNER FUNCTIONS FIRST (lower, upper, substr)
        # These need to be evaluated before outer functions like replace()
        
        # lower(string) - make string lowercase
        def eval_lower(match):
            arg = match.group(1).strip()
            # Remove quotes if present
            if arg.startswith('"') and arg.endswith('"'):
                arg = arg[1:-1]
            result = arg.lower()
            # Return with quotes to preserve as string
            return f'"{result}"'
        expr = re.sub(r'lower\(([^)]+)\)', eval_lower, expr)
        
        # upper(string) - make string uppercase
        def eval_upper(match):
            arg = match.group(1).strip()
            # Remove quotes if present
            if arg.startswith('"') and arg.endswith('"'):
                arg = arg[1:-1]
            result = arg.upper()
            # Return with quotes to preserve as string
            return f'"{result}"'
        expr = re.sub(r'upper\(([^)]+)\)', eval_upper, expr)
        
        # OUTER FUNCTIONS (replace)
        # Apply replace() function evaluation AFTER inner functions
        expr = find_and_replace_functions(expr, 'replace', eval_replace_call)
        
        # Handle substr(...) - similar pattern
        def handle_substr(match):
            full = match.group(0)
            if full.startswith('${'):
                inner = full[2:-1]
            else:
                inner = full
            
            # Parse substr(string, offset, length)
            # Extract the three arguments
            in_quotes = False
            parts = []
            current = ""
            i = 7  # len("substr(")
            while i < len(inner) - 1:
                char = inner[i]
                if char == '"':
                    in_quotes = not in_quotes
                    current += char
                elif char == ',' and not in_quotes:
                    parts.append(current.strip())
                    current = ""
                else:
                    current += char
                i += 1
            if current:
                parts.append(current.strip())
            
            if len(parts) == 3:
                string_val = parts[0].strip('"')
                offset = int(parts[1].strip())
                length = int(parts[2].strip())
                return string_val[offset:offset+length]
            
            return full
        
        expr = re.sub(r'(\$\{)?substr\([^)]*(?:\$\{[^}]*\}[^)]*)*\)(\})?', handle_substr, expr)
        
        # Remove outer ${} wrapper if expression is fully resolved
        if expr.startswith('${') and expr.endswith('}'):
            inner = expr[2:-1]
            # Only unwrap if:
            # 1. There are no more ${ inside
            # 2. It's not a cross-resource reference (which needs the ${} wrapper for later resolution)
            if '${' not in inner and not inner.startswith('azurerm_'):
                expr = inner
        
        return expr
    
    def apply_resolutions_to_resources(self):
        """
        Apply resolved variables and locals to resource attributes.
        
        Replaces all ${var.x} and ${local.x} references with actual values.
        Also attempts to resolve cross-resource references.
        """
        # First pass: resolve variables and locals
        for resource in self.resources:
            resource.attributes = self._resolve_dict(resource.attributes)
        
        # Second pass: resolve cross-resource references
        # Build a map of resource references to their attributes
        resource_map = {}
        for resource in self.resources:
            key = f"{resource.type}.{resource.name}"
            resource_map[key] = resource.attributes
        
        # Now resolve cross-resource references
        for resource in self.resources:
            resource.attributes = self._resolve_resource_refs(resource.attributes, resource_map)
    
    def _resolve_resource_refs(self, obj: Any, resource_map: Dict[str, Dict]) -> Any:
        """Recursively resolve cross-resource references like ${azurerm_resource_group.rg.location}."""
        if isinstance(obj, dict):
            return {key: self._resolve_resource_refs(value, resource_map) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_resource_refs(item, resource_map) for item in obj]
        elif isinstance(obj, str):
            return self._resolve_resource_ref_string(obj, resource_map)
        else:
            return obj
    
    def _resolve_resource_ref_string(self, s: str, resource_map: Dict[str, Dict]) -> Any:
        """Resolve resource references in a string."""
        import re
        
        # Match ${azurerm_resource_group.rg.location} or ${azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id} format
        pattern = r'\$\{(azurerm_[a-z_]+)\.([a-z_][a-z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_\[\].]*)\}'
        
        def replace_resource_ref(match):
            resource_type = match.group(1)
            resource_name = match.group(2)
            attribute_path = match.group(3)  # Could be "location" or "kubelet_identity[0].object_id"
            
            # Look up the resource
            key = f"{resource_type}.{resource_name}"
            if key not in resource_map:
                return match.group(0)
            
            resource_attrs = resource_map[key]
            
            # Try to navigate the attribute path
            attr_parts = attribute_path.replace('][', '.').replace('[', '.').replace(']', '').split('.')
            current_val = resource_attrs
            
            for part in attr_parts:
                if isinstance(current_val, dict) and part in current_val:
                    current_val = current_val[part]
                elif isinstance(current_val, list):
                    try:
                        idx = int(part)
                        if idx < len(current_val):
                            current_val = current_val[idx]
                        else:
                            return match.group(0)
                    except (ValueError, IndexError):
                        return match.group(0)
                else:
                    return match.group(0)
            
            if isinstance(current_val, str):
                return current_val
            elif isinstance(current_val, (int, float, bool)):
                return str(current_val)
            else:
                return match.group(0)
        
        # Check if entire string is a single reference
        single_ref = re.match(r'^\$\{(azurerm_[a-z_]+)\.([a-z_][a-z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_\[\].]*)\}$', s)
        if single_ref:
            resource_type = single_ref.group(1)
            resource_name = single_ref.group(2)
            attribute_path = single_ref.group(3)
            
            key = f"{resource_type}.{resource_name}"
            if key in resource_map:
                resource_attrs = resource_map[key]
                
                # Navigate the attribute path
                attr_parts = attribute_path.replace('][', '.').replace('[', '.').replace(']', '').split('.')
                current_val = resource_attrs
                
                for part in attr_parts:
                    if isinstance(current_val, dict) and part in current_val:
                        current_val = current_val[part]
                    elif isinstance(current_val, list):
                        try:
                            idx = int(part)
                            if idx < len(current_val):
                                current_val = current_val[idx]
                            else:
                                return s
                        except (ValueError, IndexError):
                            return s
                    else:
                        return s
                
                return current_val
        
        # Replace embedded references
        return re.sub(pattern, replace_resource_ref, s)
    
    def _resolve_dict(self, obj: Any) -> Any:
        """Recursively resolve variables and locals in a dict/list/value."""
        if isinstance(obj, dict):
            return {key: self._resolve_dict(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_dict(item) for item in obj]
        elif isinstance(obj, str):
            return self._resolve_string_interpolation(obj)
        else:
            return obj
    
    def _resolve_string_interpolation(self, s: str) -> Any:
        """
        Resolve Terraform string interpolation.
        
        Examples:
        - "${var.location}" → "eastus"
        - "${local.name}-rg" → "myapp-rg"
        - "Standard_B2s" → "Standard_B2s" (unchanged)
        """
        import re
        
        # First, try full expression resolution (which handles functions and nested refs)
        resolved = self._resolve_expression(s)
        
        # If it changed, use the resolved value
        if resolved != s:
            return resolved
        
        # Otherwise, fall back to simple var/local substitution
        # Check if entire string is a single reference
        single_ref = re.match(r'^\$\{(var|local)\.([^}]+)\}$', s)
        if single_ref:
            ref_type = single_ref.group(1)
            ref_name = single_ref.group(2)
            
            if ref_type == "var":
                val = self.resolved_variables.get(ref_name)
                # If value is None (no default), keep the reference
                if val is None:
                    return s
                return val
            elif ref_type == "local":
                val = self.resolved_locals.get(ref_name)
                if val is None:
                    return s
                return val
        
        # Replace embedded references in string
        def replace_ref(match):
            ref_type = match.group(1)
            ref_name = match.group(2)
            
            if ref_type == "var":
                val = self.resolved_variables.get(ref_name)
                # If not found or None, keep as-is
                if val is None:
                    return match.group(0)
            elif ref_type == "local":
                val = self.resolved_locals.get(ref_name)
                if val is None:
                    return match.group(0)
            else:
                val = match.group(0)
            
            return str(val)
        
        return re.sub(r'\$\{(var|local)\.([^}]+)\}', replace_ref, s)


def load_terraform_resources(tf_path: Path, use_state_json: bool = False) -> List[TerraformResource]:
    """
    Load Terraform resources from file or directory.
    
    Args:
        tf_path: Path to .tf file or directory containing .tf files
        use_state_json: Parse as JSON state file instead of .tf files
    
    Returns:
        List of extracted TerraformResource objects
    """
    parser = TerraformParser()
    
    if use_state_json:
        # Parse JSON state
        data = json.loads(tf_path.read_text())
        return parser.parse_json_state(data)
    elif tf_path.is_file():
        return parser.parse_file(tf_path)
    else:
        return parser.parse_directory(tf_path)
