---
description: Turn the plan's infrastructure needs into Stacktic stack changes (confirmed, via the MCP design tool).
---

## Wire infrastructure via the platform

Collect every infrastructure need from the plan (new datastore, queue/topic, cache,
storage, exposure, scaling). For each one:

1. **Resolve the platform expression**: `knowledge(action=connect, source_filter=
   external_source_code, target_filter=<service>)` — returns the valid link, reverse
   orientation, intermediary, or the manual `configmap` env fallback.
2. **Present the intended changes to the user as a short table** (what, how: link/component/
   attribute, exact `design(...)` call) and **WAIT for their confirmation**.
3. On confirmation, execute via the Stacktic MCP: `design(action=add|connect|modify, ...)`.
   NEVER write Kubernetes manifests, Terraform, or connection plumbing in the app repo —
   that is a constitution Principle I violation.
4. **Never deploy/build implicitly.** Building the stack is a separate, explicit user ask.
5. After changes: remind the user that regeneration refreshes the constitution — re-run
   `dev_tools/spec-kit/install.sh` from the stack repo to sync it here.

If the MCP is not connected, output the table with a note that the user (or their
operator) should apply it via the Stacktic UI, and reference the exact link types.
