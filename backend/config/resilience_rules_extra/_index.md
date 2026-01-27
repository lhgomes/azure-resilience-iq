# Zone Resiliency Recommendations - APRL Integration
# Resiliency Rules Extra (APRLv3 Candidate)

## Overview

This directory contains zone resilience recommendations for Azure resources, following the APRL (Azure Proactive Resiliency Library) model structure.

**Status**: Ready for potential integration into APRLv3

## Structure

The recommendations are organized by Azure service, following the APRL approach:

```
resilience_rules_extra/
├── Compute/
│   ├── virtualMachines/
│   │   └── zone_recommendations.yaml
│   ├── virtualMachineScaleSets/
│   │   └── zone_recommendations.yaml
│   └── disks/
│       └── zone_recommendations.yaml
├── Network/
│   ├── publicIPAddresses/
│   │   └── zone_recommendations.yaml
│   └── networkInterfaces/
│       └── zone_recommendations.yaml
├── Storage/
│   └── storageAccounts/
│       └── zone_recommendations.yaml
├── Sql/
│   └── servers/
│       └── zone_recommendations.yaml
├── DocumentDB/
│   └── databaseAccounts/
│       └── zone_recommendations.yaml
└── _index.md
```

## Recommendation Format

Each `zone_recommendations.yaml` file contains:

1. **APRL-compatible recommendations** (documentation reference)
2. **Zone pattern mappings** (engine-driven recommendations)

### Zone Deployment Patterns

The engine maps deployment patterns to specific recommendations:

- `single_zone` - Resource deployed in single AZ
- `multi_zone_2` - Resource spans 2 AZs
- `multi_zone_3plus` - Resource spans 3+ AZs
- `zone_redundant` - Automatic zone redundancy (ZRS, GZRS)
- `not_applicable` - Resource type doesn't support AZs
- `unknown` - Zone configuration cannot be determined

## APRL Alignment

### Similarities with APRL
- ✅ YAML-based configuration
- ✅ Recommendation metadata (impact, control category)
- ✅ Official Microsoft Learn links
- ✅ Service-organized directory structure
- ✅ APRLGuid references for cross-mapping
- ✅ Contributor-friendly format

### Differences from APRL
- 🔧 Zone-specific pattern mappings (unique to this implementation)
- 🔧 Automatic pattern-to-recommendation routing
- 🔧 Generic fallback for unmapped patterns

## Contributing

### To Add a New Service

1. Create directory: `backend/config/resilience_rules_extra/Service/resourceType/`
2. Add `zone_recommendations.yaml` with pattern mappings
3. Follow the format in existing files
4. Test with real Azure resources

### Example: Adding App Service

```yaml
# backend/config/resilience_rules_extra/Web/serverFarms/zone_recommendations.yaml

patterns:
  single_zone:
    description: "Deploy App Service across availability zones"
    recommendationControl: HighAvailability
    recommendationImpact: High
    longDescription: |
      App Service should be deployed across multiple zones for resilience.
    potentialBenefits: "99.95% SLA"
    learnMoreLink:
      - name: "App Service reliability"
        url: "https://learn.microsoft.com/azure/reliability/reliability-app-service"
```

## Integration Path for APRLv3

### Phase 1: ✅ Complete
- [x] Directory structure aligned with APRL
- [x] Per-service YAML files
- [x] Pattern mapping format
- [x] Engine-driven recommendation loading

### Phase 2: Future
- [ ] Merge with APRL official recommendations
- [ ] Align naming conventions
- [ ] Harmonize metadata fields
- [ ] Community feedback and refinement

### Phase 3: Potential APRLv3 Integration
- [ ] Proposal to Azure Proactive Resiliency Library team
- [ ] Review and acceptance into APRL
- [ ] Merged as official zone resilience recommendations

## Technical Details

### Engine Implementation
- **File**: `backend/app/resilience/zone_recommendation_engine.py`
- **Smart path inference**: Automatically maps directory structure to resource types
- **Multi-file loading**: Loads all zone_recommendations.yaml files from directory tree
- **Hot reload**: Supports configuration reload without restart

### Resource Type Inference
The engine infers Azure resource types from file paths:

```
Compute/virtualMachines/ → Microsoft.Compute/virtualMachines
Network/publicIPAddresses/ → Microsoft.Network/publicIPAddresses
Storage/storageAccounts/ → Microsoft.Storage/storageAccounts
```

### Backward Compatibility
- Supports both old single-file and new multi-file structure
- Automatic fallback to hardcoded defaults if loading fails
- No breaking changes to existing recommendation engine

## Files Covered

| Service | Resource Type | Patterns | Status |
|---------|---------------|----------|--------|
| Compute | virtualMachines | single_zone, multi_zone_2, multi_zone_3plus, zone_redundant | ✅ Complete |
| Compute | virtualMachineScaleSets | single_zone, multi_zone_2, multi_zone_3plus, zone_redundant | ✅ Complete |
| Compute | disks | single_zone, zone_redundant | ✅ Complete |
| Network | publicIPAddresses | single_zone, zone_redundant | ✅ Complete |
| Network | networkInterfaces | single_zone, zone_redundant | ✅ Complete |
| Storage | storageAccounts | single_zone, zone_redundant | ✅ Complete |
| Sql | servers | single_zone, zone_redundant | ✅ Complete |
| DocumentDB | databaseAccounts | single_zone, zone_redundant | ✅ Complete |

## Next Steps

1. **Review and feedback** - Community and APRL team review
2. **Additional services** - Add recommendations for more resource types
3. **Testing** - Comprehensive testing with real Azure environments
4. **Documentation** - Create formal APRLv3 proposal documentation
5. **Integration** - Merge into APRL official repository

## References

- [APRL Repository](https://github.com/Azure/Azure-Proactive-Resiliency-Library-v2)
- [Azure Reliability](https://learn.microsoft.com/azure/reliability/)
- [Availability Zones](https://learn.microsoft.com/azure/reliability/availability-zones-overview)

## License

This implementation follows the same license as the parent project.
