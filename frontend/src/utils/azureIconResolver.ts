/**
 * Azure Icon Resolver
 * 
 * Deterministically maps LLM-provided semantic metadata (category + service name)
 * to the most appropriate Azure architecture icon from the local icon repository.
 * 
 * Matching strategy:
 * 1. Normalize category to folder name
 * 2. Search for exact/partial match on service name within category folder
 * 3. Prefer generic service icons over plans/sub-resources
 * 4. Fallback to generic icon in same category
 * 5. Final fallback to general All-Resources icon
 * 
 * No hardcoded icon lists. No AI/LLM calls. Pure string matching.
 */

import AZURE_ICON_MANIFEST, { type IconManifest } from './azureIconManifest';

// Static icon manifest - in production this could be generated at build time
// For now, we'll use a dynamic approach that falls back gracefully
const ICON_BASE_PATH = "/Icons";

/**
 * Normalize Azure service category to folder name.
 * Handles case, spaces, and special characters.
 */
function normalizeCategoryToFolder(category: string): string {
  if (!category) return "general";
  
  // Common mappings for Azure Architecture Icons taxonomy
  const normalized = category
    .toLowerCase()
    .trim()
    .replace(/\s*\+\s*/g, " + ")  // Normalize "+" spacing
    .replace(/\s+and\s+/g, " + "); // Convert "and" to "+"
  
  // Direct category folder mappings
  const categoryMap: Record<string, string> = {
    "compute": "compute",
    "containers": "containers",
    "networking": "networking",
    "databases": "databases",
    "storage": "storage",
    "identity": "identity",
    "integration": "integration",
    "management + governance": "management + governance",
    "managementandgovernance": "management + governance",
    "security": "security",
    "ai + machine learning": "ai + machine learning",
    "analytics": "analytics",
    "devops": "devops",
    "iot": "iot",
    "monitor": "monitor",
    "web": "web",
    "mobile": "mobile",
    "migrate": "migrate",
    "general": "general",
  };
  
  return categoryMap[normalized] || normalized;
}

/**
 * Normalize service name for matching against icon filenames.
 * Removes common noise words and normalizes spacing/case.
 */
function normalizeServiceName(serviceName: string): string {
  if (!serviceName) return "";
  
  return serviceName
    .toLowerCase()
    .replace(/^azure\s+/i, "")  // Strip leading "Azure"
    .replace(/\s+service[s]?\s*$/i, "")  // Strip trailing "Service(s)"
    .replace(/[^\w\s-]/g, "")  // Remove special chars except hyphen
    .replace(/\s+/g, "-")  // Spaces to hyphens
    .trim();
}

/**
 * Extract searchable tokens from icon filename for matching.
 */
function extractIconTokens(filename: string): string[] {
  // Example: "10023-icon-service-Kubernetes-Services.svg"
  // Extract: ["kubernetes", "services"]
  
  const withoutExtension = filename.replace(/\.svg$/i, "");
  const parts = withoutExtension.split("-");
  
  // Skip numeric ID and "icon-service" prefix
  const tokens = parts
    .filter(p => !/^\d+$/.test(p))  // Skip pure numbers
    .filter(p => !["icon", "service"].includes(p.toLowerCase()))
    .map(p => p.toLowerCase());
  
  return tokens;
}

/**
 * Score how well an icon filename matches the service name.
 * Higher score = better match.
 */
function scoreIconMatch(filename: string, normalizedServiceName: string): number {
  const tokens = extractIconTokens(filename);
  const serviceTokens = normalizedServiceName.split("-").filter(Boolean);
  
  if (serviceTokens.length === 0) return 0;
  
  let score = 0;
  
  // Exact full match in filename (best case)
  const filenameContent = filename.toLowerCase();
  if (filenameContent.includes(normalizedServiceName)) {
    score += 100;
  }
  
  // Count matching tokens
  let matchingTokens = 0;
  for (const serviceToken of serviceTokens) {
    if (tokens.some(t => t.includes(serviceToken) || serviceToken.includes(t))) {
      matchingTokens++;
    }
  }
  
  // Percentage of service tokens found
  score += (matchingTokens / serviceTokens.length) * 50;
  
  // Penalize sub-resource/plan icons (prefer main service icons)
  const filename_lower = filename.toLowerCase();
  if (filename_lower.includes("-plan") || 
      filename_lower.includes("-certificate") ||
      filename_lower.includes("-extension") ||
      filename_lower.includes("-configuration")) {
    score -= 20;
  }
  
  // Boost generic service icons
  if (tokens.includes("services") || tokens.includes("service")) {
    score += 10;
  }
  
  return score;
}

/**
 * Find the best matching icon in a category folder.
 * Returns the icon filename or null if no good match found.
 */
function findBestIconInCategory(
  categoryFolder: string,
  serviceName: string,
  availableIcons: string[]
): string | null {
  if (!availableIcons || availableIcons.length === 0) return null;
  
  const normalizedService = normalizeServiceName(serviceName);
  if (!normalizedService) return null;
  
  // Score all icons
  const scored = availableIcons.map(icon => ({
    filename: icon,
    score: scoreIconMatch(icon, normalizedService)
  }));
  
  // Sort by score descending
  scored.sort((a, b) => b.score - a.score);
  
  // Only return if we have a reasonable match (score > 20)
  if (scored.length > 0 && scored[0].score > 20) {
    return scored[0].filename;
  }
  
  return null;
}

/**
 * Get a generic fallback icon from the category folder.
 * Prefers icons with "service" or the category name in the filename.
 */
function getGenericCategoryIcon(
  categoryFolder: string,
  availableIcons: string[]
): string | null {
  if (!availableIcons || availableIcons.length === 0) return null;
  
  // Look for generic service icon in this category
  const generic = availableIcons.find(icon => {
    const lower = icon.toLowerCase();
    return (
      lower.includes("-services.svg") ||
      lower.includes(`-${categoryFolder.toLowerCase()}.svg`)
    );
  });
  
  if (generic) return generic;
  
  // Return first icon as last resort
  return availableIcons[0] || null;
}

/**
 * Main resolver function.
 * 
 * @param category - Azure service category from LLM (e.g. "Compute", "Containers")
 * @param serviceName - Azure service name from LLM (e.g. "Azure Kubernetes Service", "Virtual Machines")
 * @param iconManifest - Optional pre-built manifest of available icons per category
 * @returns Relative path to icon (e.g. "/Icons/containers/10023-icon-service-Kubernetes-Services.svg")
 */
export function resolveAzureIcon(
  category: string,
  serviceName: string,
  iconManifest: IconManifest = AZURE_ICON_MANIFEST
): string {
  // Step 1: Normalize category to folder name
  const categoryFolder = normalizeCategoryToFolder(category);
  
  // Step 2: Get available icons for this category from pre-built manifest
  const availableIcons = iconManifest[categoryFolder] || [];
  
  // Step 3: Try to find best match
  let matchedIcon: string | null = null;
  
  if (availableIcons.length > 0) {
    matchedIcon = findBestIconInCategory(categoryFolder, serviceName, availableIcons);
    
    // Step 4: Fallback to generic icon in same category
    if (!matchedIcon) {
      matchedIcon = getGenericCategoryIcon(categoryFolder, availableIcons);
    }
  }
  
  // Step 5: Construct path
  if (matchedIcon) {
    return `${ICON_BASE_PATH}/${categoryFolder}/${matchedIcon}`;
  }
  
  // Step 6: Final fallback to general All-Resources icon
  return `${ICON_BASE_PATH}/general/10001-icon-service-All-Resources.svg`;
}

/**
 * Helper to generate icon manifest at build time (optional).
 * This could be run as a build script to scan public/Icons and generate a JSON manifest.
 */
export function generateIconManifest(iconsDir: string): IconManifest {
  // This would use Node.js fs to scan the icons directory
  // For browser usage, the manifest should be pre-generated and imported
  throw new Error("generateIconManifest should be called at build time, not runtime");
}

/**
 * Hardcoded fallback manifest for common categories.
 * This ensures basic icon resolution works even without a full manifest.
 * Populated with representative icons from each category.
 */
export const FALLBACK_ICON_MANIFEST: IconManifest = {
  "compute": [
    "10023-icon-service-Kubernetes-Services.svg",
    "10028-icon-service-Virtual-Machines-(Classic).svg",
    "10104-icon-service-Container-Instances.svg",
  ],
  "containers": [
    "10023-icon-service-Kubernetes-Services.svg",
    "10104-icon-service-Container-Instances.svg",
    "10105-icon-service-Container-Registries.svg",
    "10035-icon-service-App-Services.svg",
  ],
  "networking": [
    "10061-icon-service-Virtual-Networks.svg",
    "10062-icon-service-Load-Balancers.svg",
  ],
  "databases": [
    "10130-icon-service-SQL-Databases.svg",
  ],
  "storage": [
    "10086-icon-service-Storage-Accounts.svg",
  ],
  "identity": [
    "10224-icon-service-Azure-Active-Directory.svg",
  ],
  "security": [
    "10245-icon-service-Key-Vaults.svg",
  ],
  "general": [
    "10001-icon-service-All-Resources.svg",
  ],
};
