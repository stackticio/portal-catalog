---
description: Verify the implementation against the DEPLOYED stack (live tests, env contract).
---

## Verify against the deployed stack

Code that compiles is not "done" on Stacktic — done means the DEPLOYED service honors its
contract. With the Stacktic MCP connected:

1. **Env contract**: `graph(action=contract, component=<name>)` — confirm the code consumes
   exactly these variable names (grep the codebase for each; flag hardcoded endpoints).
2. **Live checks**: `stack_test(action=predefined)` — run the stack's validation tests;
   report failures with the failing connection/credential/route.
3. **Flows**: `graph(action=data_flow, component=<name>)` — confirm every flow the spec
   promised has its wiring in place (topic exists, sink connected, route exposed).
4. Map each spec acceptance criterion to evidence: a passing live test, a verified env
   consumption, or an explicit gap. Output a short verification table:
   criterion | evidence | PASS/GAP.

Without the MCP: verify env names against the stack repo's generated `cloud.env` and list
which live checks the operator should run.
