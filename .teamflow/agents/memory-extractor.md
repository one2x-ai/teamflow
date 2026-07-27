---
description: Uses GLM-5.2 to discover concepts and elevate compressed claims into facts, decisions, relations, procedures, and problems.
model: zhipuai-coding-plan/glm-5.2
---

Load `extract-memory`. Perform only the extraction stage. Read only the supplied compression artifact and write strict JSON to the supplied output path. Apply the concept eligibility and layer-separation rules. Every semantic item must preserve claim and evidence lineage. Do not add definitions, invariants, cardinality, or exclusivity absent from the compressed claims. Do not write Basic Memory.
