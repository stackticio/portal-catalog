---
description: Load this service's live stack facts (wiring, env contract, neighbors) into the working context.
---

## Load Stacktic stack facts

This service runs on Stacktic. Before planning, ground yourself in the REAL wiring —
never assume infrastructure.

**Gather, in order of preference:**

1. **Stacktic MCP connected?** Call:
   - `knowledge(action=component, component_type=external_source_code)` — platform rules for this component type
   - `graph(action=contract, component=<this component's name>)` — the exact ENV contract, exposure, data flows
   - `graph(action=data_flow, component=<name>)` — what data this service exchanges, with whom
2. **No MCP?** Read from the sibling STACK repo (see `dev_tools/AGENTS.md` for layout):
   - `stack-metadata.yaml` — this component's links, attributes, neighbors
   - `external_source_code/<component>/k8s/config/cloud.env` — the generated ENV names (the contract)
3. The constitution (`.specify/memory/constitution.md`) Principles I–V — these are hard gates.

**Produce a short "Stack Context" summary** (wiring, env contract source, data flows,
constraints) and carry it into the plan. Any dependency the feature needs that is NOT in
the current wiring must be flagged as a STACK CHANGE (handled by /speckit.stacktic.wire),
not as code or manifests.
