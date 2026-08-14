# Native Agents

This directory is the home of Code Puppy's opt-in native-agent runtime. The
runtime is intentionally not loaded by this design-only document; Phase 1 adds
the callback entry point and keeps the feature disabled by default.

## Phase 0 decisions

These decisions are the architecture contract for Tier A. They apply to the
runtime even when an implementation detail changes.

### Decision A — the plugin owns the feature

Ship native agents as the bundled `native_agents` plugin. Existing callback
hooks are the integration surface. Do not edit `code_puppy/command_line/` or
add a core execution path unless a later phase proves a hook is insufficient.
If that happens, the missing hook, smallest additive change, and regression
tests must be documented before changing core.

### Decision B — `BaseAgent` remains the integration shell

A native agent subclasses `BaseAgent` or combines it with a mixin. The normal
agent continues to own prompt construction, model selection, tools, MCP,
history, compaction, cancellation, and `run_with_mcp()` behavior. Native
execution builds through the existing Pydantic AI builder using an isolated
build proxy; it never hands the parent agent directly to a stateful builder.

### Decision C — strategies have separate trust boundaries

Tier A implements only the explicit `predict` strategy: one typed Pydantic AI
invocation with bounded validation repair. `codeact` remains a named,
unsupported strategy until a separate security-reviewed phase. It is never a
hidden fallback for prediction.

### Decision D — references are opaque handles

Pass-by-reference means an execution-scoped, cryptographically random handle
with a declared type, owner, expiry, and bounded preview. Model-visible data
never contains the host object or a way to walk back to the parent agent,
filesystem, shell, MCP client, session, or secret store.

### Decision E — events are immutable and state is versioned

Native state is a strict Pydantic JSON snapshot with an explicit revision.
Events are immutable, redacted, schema-versioned records stored separately.
Neither is inferred from transcript text, and neither depends on transcript
compaction.

### Decision F — existing safety controls remain authoritative

Native prediction reuses Code Puppy model/tool/MCP construction and lifecycle
hooks. Any future effectful capability must pass the existing tool and
permission policy paths. Native code never calls raw filesystem, shell,
network, browser, MCP, secret, or arbitrary reflection APIs.

## Component boundary

```text
@native_method / NativeAgentMixin
        |
NativeMethodRuntime
  |-- immutable MethodSpec
  |-- PredictStrategy (Tier A)
  |-- versioned JSON StateStore
  |-- immutable redacted EventStore
  |-- bounded ContextRenderer
  |-- execution-scoped ReferenceStore
  `-- read-only CapabilityRegistry (Tier A)
```

Strategies receive a narrow execution context. They do not reach into another
component's private storage. Tier A has no CodeAct worker and no model-written
Python.

## Phase 0 design spikes

The spikes were run against the checked-in public dependency set (`pydantic-ai`
1.56.0) with deterministic `TestModel` inputs:

1. **Typed result contract:** `Agent.run()` returns `AgentRunResult`; its
   `.output` is the validated Pydantic model, while `.all_messages()` and
   `.new_messages()` expose transcript data. Native code will return only
   `.output` and record bounded metadata, not parent history.
2. **Builder isolation:** `build_pydantic_agent()` mutates the supplied owner
   (`_code_generation_agent`, `pydantic_agent`, `_mcp_servers`, and model
   fields) but leaves its history untouched. Native execution must therefore
   build against a proxy/clone whose mutable build fields are isolated, then
   discard the proxy. This is a real boundary, not an optimization.
3. **Execution context:** a `ContextVar` nested in an async context manager
   preserved outer values, isolated child-task values, and reset cleanly. The
   runtime will use the same `try/finally` discipline and never use a global
   current-execution singleton.
4. **Storage location:** use Code Puppy's existing `STATE_DIR` beneath the
   user data directory, in a `native_agents/` subdirectory. The plugin source
   directory is never used for SQLite, caches, logs, or runtime state.
5. **DBOS behavior:** DBOS is optional. Its wrapper declines when DBOS has not
   launched; native execution must treat DBOS absence or wrapper failure as an
   optional integration failure, never as permission to bypass normal safety.
6. **Tool boundary:** `predict` may use the same explicitly listed Pydantic AI
   tools and MCP bindings as the opted-in host agent. Reference capabilities
   are a separate typed, policy-checked surface; raw tool callables and live
   objects are never passed to a native method or model.

## Threat model and invariants

Protected assets include files, shell authority, credentials, MCP/browser
sessions, DBOS state, other sessions, and plugin trust/approval controls. The
primary threats are prompt injection, generated-code escape, handle guessing,
resource exhaustion, capability confusion, unsafe persistence, audit gaps,
and compatibility regressions.

Tier A preserves these invariants:

- native functionality is disabled unless explicitly enabled;
- strict Pydantic/JSON contracts reject unknown persisted or external fields;
- invalid typed output never becomes a successful result;
- state/event/reference rendering is bounded;
- a handle cannot cross execution scope or outlive its expiry;
- storage failure fails closed and cannot grant access;
- disabling the plugin restores the ordinary agent path with no native tools,
  prompt fragments, or agent registrations;
- raw `exec()` and CodeAct are out of scope.

## Ownership and rollback

Implementation owner: `code-puppy-4d9b9d`. Rollback owner: the repository
maintainer accepting the Tier A pull request. Rollback is configuration-first:
disable the native-agent flag, then remove/revert the focused plugin commits.
No automatic migration of ordinary agents, sessions, MCP bindings, or Kennel
memory is planned.

No missing core hook was identified in Phase 0. The plugin can compose the
existing builder, wrapper, run-context, lifecycle, tool-policy, and feature
capability seams without changing core or the command-line package.

## Phase 1 substrate

Phase 1 adds strict contracts and an inert storage substrate. Importing the
package performs no I/O; `register_callbacks.py` registers only a startup
initializer and a feature-capability probe. Startup creates storage only when
`native_agents_enabled` is explicitly truthy. A failed migration leaves the
feature unavailable and does not affect ordinary agents.

State lives at `STATE_DIR/native_agents/native_agents.sqlite3`, or at the
explicit `CODE_PUPPY_NATIVE_AGENTS_DB` path used by disposable test sandboxes.
SQLite migrations are transactional and foreign keys are enabled on every
connection. Execution records, revisioned state snapshots, and append-only
redacted events use only JSON-compatible Pydantic data. State updates require
an expected revision, so stale writers fail with `StateConflictError`.

Phase 1 configuration defaults are bounded:

- `native_agents_enabled`: false;
- `native_agents_store_retention_days`: 30 (1–3650);
- `native_agents_context_max_chars`: 12,000 (256–100,000);
- `native_agents_event_max_per_view`: 30 (0–500);
- `native_agents_state_max_bytes`: 65,536 (1,048–1,048,576);
- diagnostics and the reserved CodeAct flag remain off.

Phase 1 did not expose a native method, generic native tool, prompt fragment,
agent registration, CodeAct worker, handle store, or capability adapter. Those
behaviors belong to later phases and keep this substrate's fail-closed
contract.

## Phase 2 typed prediction

Phase 2 adds the explicit `@native_method` declaration, `NativeAgentMixin`,
an ephemeral `NativeInvocationAgent`, and `NativeMethodRuntime`. A
prediction accepts exactly one Pydantic input model and returns exactly the
declared Pydantic output model. Declarations must be `async`, have a stub body,
match their input/return annotations, and declare no capabilities. `codeact` is
rejected rather than silently treated as prediction.

The invocation adapter delegates stable model/tool/MCP configuration to the
parent but owns history, builder fields, identity, and transient resources.
Calls on one parent are serialized by a runtime-local lock. The builder's new
generic `retries` argument is the smallest core seam needed to make the
method's bounded validation-repair setting truthful; its default remains the
existing value of three and it contains no native-agent knowledge.

The opt-in `native-reviewer` agent is registered only while
`native_agents_enabled` is truthy. Its `summarize_change` method returns
structured findings that ordinary Python can group by severity. It does not
read files, write files, run shell commands, browse, or use MCP side effects.
The model prompt is bounded by `ContextBudget`, and the runtime records start,
validation failure, completion/failure, and cancellation events without
adding native transcript messages to the parent.


Phase 1 targeted evidence: `tests/plugins/native_agents` passes 20
deterministic tests covering strict contracts, migrations, state revisions,
stale conflicts, redaction, immutable events, scope cleanup, cancellation,
and disabled/failed registration. The repository's existing unrelated
untracked documentation remains intentionally untouched.

## Phase 3 durable context

Phase 3 adds `StateService` for declared Pydantic state. Replacements validate
the exact state type, enforce a bounded serialized byte size, require a stable
developer-authored reason identifier, compare the expected revision before the
write, and append a changed-field summary atomically with the new snapshot.
State is still JSON-only and redacted; it is never a transcript or provider
object.

`EventQuery`, `EventService`, and `EventSummary` provide hard-bounded,
execution-owned reads. Raw payloads are omitted by default from queries;
summary text is deterministic and redacted. The low-level store remains useful
for trusted host diagnostics, while the service facade cannot read another
execution's events by accident.

`ContextRenderer` produces priority-ordered `NativeContextView` blocks for the
method contract, execution status, allowlisted typed state, and recent event
summaries. Every block and the total view are bounded. Truncation is explicit,
state is labeled untrusted data, secret-looking fields are excluded, and
omitted content is not echoed in the marker. `PredictStrategy` receives a
fresh rendered view; it is not appended to normal parent history.

`NativeHistoryContext` is an inspection-only compaction seam: a native
follow-up asks the durable renderer for current context instead of trusting a
compacted transcript. `inspect_resume` reports terminal/interrupted status,
retention, and method-version eligibility but never automatically replays a
crashed model call.

## Phase 4 opaque references and capabilities

Phase 4 adds `ReferenceStore`, `CapabilityRegistry`, `CapabilityPolicy`, and
one real read-only `search_results` adapter. Reference handles use secure
random IDs and contain only resource type, execution/session ownership,
expiry, and a bounded preview. Live values stay in an in-memory host mapping;
no raw object, pickle, filesystem handle, shell handle, MCP client, or secret
is persisted or placed in a prompt. Unknown, expired, wrong-type, and
cross-execution handles fail with the same `HandleUnavailableError`.

Capability names are explicit namespaced declarations with strict Pydantic
request/response models. Policy checks feature enablement, exact method
allowlisting, resource/owner scope, and effect class before resource
resolution. Tier A permits only `observe`, `compute`, and `propose`; modify,
execute, and secret effects are denied. Requested, denied, and completed
capability attempts are recorded with hashed handle IDs and bounded metadata.

The initial adapter accepts host-created `SearchResultSet` values and supports
bounded paging, relative-prefix filtering into a derived handle, deterministic
path grouping, and bounded sampling. It performs no filesystem or shell work;
creation must come from an already-approved host search result. Context previews
show a symbolic description and allowed operation names, never the opaque
handle ID or full result set. Reference cleanup is execution-scoped and
idempotent.

## Phase 6 ecosystem integration

Tier A keeps Code Puppy's existing seams authoritative. PredictStrategy builds
through `_builder.build_pydantic_agent`, receives the parent's model/tool/MCP
configuration, uses existing run contexts/lifecycle callbacks, and carries no
parallel provider or MCP client. The DBOS adapter reports availability and
launched state without importing/launching a second runtime; absent or
not-launched DBOS is normal and safe.

Kennel remains long-term memory, not native state. A method must opt in with
`MethodSpec.memory_opt_in`; recall is bounded and duplicate content already in
the parent's prompt is not added again. Native completion never writes Kennel
implicitly. Session restore correlates execution metadata but never revives
process-local handles or automatically resumes a model call. Future effectful
capabilities must use existing permission/tool/shell hooks; Tier A has none.

`/native status`, `/native diagnostics`, and `/native cleanup` are namespaced,
localized, feature/diagnostics-gated surfaces. Diagnostics expose only bounded
status/schema/count/integration state and never raw database paths, prompts,
handle IDs, payloads, or stack traces. Cleanup reports that process-local
handles expire automatically until a dedicated bounded cleanup operation is
needed.
