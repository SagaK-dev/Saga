# Saga 0.10.0 security model

Saga combines static checking with deny-by-default hosted capabilities and explicit isolation boundaries.

## Language guarantees

- no pointer arithmetic or manual memory release in Standard Core;
- immutable `let` bindings and explicit `var`;
- no language-level null; absence uses `option[T]`;
- bounds checks and exact-number diagnostics;
- identity equality for mutable class instances;
- private members excluded from normal reflection, display, and serialization;
- task arguments and results cross Send-checked snapshot boundaries;
- no host traceback unless debug mode is explicitly requested;
- diagnostic identity is carried by stable machine-readable IDs, never inferred from Japanese or other localized prose.

## Hosted capabilities

Read, write, database, network endpoint, GUI, process, environment, plugin, and cloud access are separately granted. Redirect destinations are rechecked. Process execution does not use a shell by default. SQL APIs support parameters.

## Python plugin isolation

The secure plugin path never imports plugin code into the Saga process. A plugin is executed in a separate isolated Python interpreter (`-I -S`) and exchanges only serialized values. The plugin AST rejects imports and dunder-based introspection, builtins are allowlisted, and standard-library exposure uses read-only function facades rather than raw module objects.

On Linux where unprivileged user namespaces are available, strict plugin execution additionally uses separate user, mount, PID, IPC, UTS, and network namespaces, `no_new_privs`, a private mount tree, masked host paths, a minimal environment, and OS resource controls. If strong isolation is requested but unavailable, Saga fails closed rather than silently downgrading.

`--unsafe-processor` is an explicit compatibility escape hatch for trusted annotation processors and is not part of the secure plugin profile.

## Untrusted-source resource policy

Saga itself keeps the `no-fixed-normative-ceilings` resource model. Hosted services can separately pass a `ResourceBudget` to the Python API to bound source bytes, tokens, AST nodes, source-import depth, source-unit count, and interpreter steps without changing language conformance. `UNTRUSTED_RESOURCE_BUDGET` is a conservative reference preset for playgrounds, bots, and similar services; operators should tune or replace it for their workload.

If both `step_limit` and `ResourceBudget.max_steps` are supplied, Saga uses the stricter value so an execution request cannot relax the host policy. Omitting `resource_budget` preserves the previous unlimited-by-policy behavior. These application-level budgets complement rather than replace OS/process memory, CPU, filesystem, and network isolation.

## Whole-program OS sandbox

`--os-sandbox strict` currently has a Linux implementation. It creates user/PID/IPC/UTS/network namespaces; file access remains governed by Saga's path capabilities. This is defense in depth, not a claim of a formally verified sandbox. Windows AppContainer/Job Object and macOS seatbelt implementations are not present in 0.10.0; strict mode on unsupported platforms refuses to run.

## Independent review status

Project-internal security review and attack-oriented tests are included with the release. They are **not** an independent third-party audit. The external-audit handoff package defines the required scope and evidence so an unrelated assessor can produce an authentic independent report.
