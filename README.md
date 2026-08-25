# Saga

Saga is an independent general-purpose programming language focused on readable source, exact computation, explicit authority, native systems work, and progressively deeper machine-control capability.

The project includes a Python reference implementation, an independent Go implementation, native/WASM code-generation work, package and workspace tooling, language-server/debugging support, platform qualification, and machine-control profiles.

## Project status

- **Latest frozen release:** Saga 0.50.0 — Production GA Control Hardening
- **Current development version:** Saga 0.52.0 — Generic Abstraction Foundations
- **Frozen release branch:** `release/0.50.0-production-ga`
- **Development branch:** `main`
- **License:** MIT
- **Python requirement:** 3.13+

`release/source-manifest-0.50.0.json` describes the frozen 0.50.0 source candidate. Saga 0.52.0 on `main` is a development line, not a frozen GA release. Maintenance work on `main` may intentionally diverge from the historical manifest; a later frozen release must create new source-bound evidence instead of rewriting old release evidence.

The **Production GA** designation applies to the Saga 0.50 language/toolchain control profile. It is **not** a functional-safety certificate for a physical machine. Target-specific hard real-time, WCET, physical HIL, fieldbus, motor/drive, E-stop/STO/interlock, watchdog, SIL/PL, and other regulatory evidence remain deployment-specific.

## Quick start

Clone the repository and install Saga in an isolated environment:

```bash
git clone https://github.com/SagaK-dev/Saga.git
cd Saga
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Windows PowerShell activation:

```powershell
.\.venv\Scripts\Activate.ps1
```

Check the installed language:

```bash
saga --version
saga doctor
```

Create `hello.saga`:

```saga
fn twice(value: int) -> int {
    return value * 2
}

print(twice(21))
```

Then run it:

```bash
saga check hello.saga
saga run hello.saga
```

## Core CLI

Saga's CLI includes:

```text
run               execute checked Saga source
check             parse/type-check without execution
repl              interactive REPL
new               create a Saga project
lint / fmt        style and source checks
module            generate separate-compilation module interfaces
test              run Saga tests
lock / verify     reproducible project locking
production-check  project/workspace production gate
pack              deterministic .sagapkg creation
build             native executable or WebAssembly build
conformance       Standard Core self-conformance
lsp               Language Server Protocol server
debug / profile   debugging and profiling
registry          package-registry server/client workflows
mobile            generate mobile runtime projects
ecosystem         package/bridge SDK tooling
capabilities      static capability audit
doctor            environment diagnostics
```

Run `saga --help` or `saga <command> --help` for the complete command surface.

## Language design

Saga aims to keep common code approachable while retaining a serious systems path.

Key language/toolchain areas include:

- exact-number-oriented defaults and static contracts;
- `option` / `result`-style explicit failure handling;
- namespaced modules, public/internal visibility, separate compilation, and deterministic interfaces;
- `async` / `await`, lexical `taskgroup`, `defer`, deterministic `using`, and resource-focused `move`;
- native ABI/code-generation work and managed runtime/GC layers;
- deterministic packaging, workspaces, lock verification, and capability reporting;
- Python reference implementation plus an independent Go implementation;
- native desktop/mobile, graphics/game, cloud, IoT, registry, and platform qualification paths.

The language intentionally avoids turning ordinary managed values into a global borrow-checking burden. Explicit ownership/authority rules are concentrated around resources where lifetime and external authority matter.

## Machine-control profile

Saga 0.50 adds transitive control-call-graph hardening around the existing periodic-control stack.

```saga
use machine

@control_safe
fn clamp_command(value: decimal) -> decimal {
    if value > 1.0 { return 1.0 }
    if value < -1.0 { return -1.0 }
    return value
}

@control_tick(20000, 35)
fn current_tick(error: decimal) -> decimal {
    return clamp_command(error * 0.5)
}
```

`@control_safe` and `@control_tick` are source/toolchain contracts. They reject classes of hidden allocation/I/O, recursion, indirect calls, shared mutation, unapproved external calls, and other constructs that violate the production control profile.

Deployment qualification is performed with:

```bash
saga production-check --native --machine
```

The machine-production gate is fail-closed when required source-bound timing, hazard, WCET, HIL, and independent hardware-safety evidence is absent.

See:

- `spec/SAGA_PRODUCTION_GA_CONTROL_0.50.md`
- `docs/PRODUCTION_GA_CONTROL_0.50.md`
- `RELEASE_NOTES_0.50.0.md`
- `saga-REVIEW_REPORT-0.50.0.md`
- `saga-VALIDATION-0.50.0.md`

## Validation and CI

The repository separates software qualification from external/physical qualification.

The frozen 0.50 evidence includes Python and Go regression, Go vet/race checks, internal automated security review, specification linting, deterministic control invariant cases, native reproducibility/execution, and source-bound machine-production checks.

Current development CI runs core Python/Go checks on pushes to `main` and pull requests. Additional workflows cover:

- desktop native OS qualification;
- platform/runtime qualification;
- Android/iOS build evidence;
- live signed registry qualification;
- self-hosted physical hardware-lab qualification.

Physical-lab and credentialed live-service workflows remain explicit/manual gates and must not be interpreted as passed unless their actual evidence exists.

## Repository structure

```text
saga/               Python reference implementation
implementations/go/ independent Go implementation
spec/               language and profile specifications
docs/               design and usage documentation
tests/              Python regression tests
tools/              qualification/release/developer tooling
validation/         validation and qualification evidence
release/            frozen source manifests
examples/           Saga examples
.github/workflows/  CI and qualification workflows
```

## Contributing

See `CONTRIBUTING.md` for development and pull-request expectations.

Before submitting changes, at minimum run the relevant Python regression tests plus:

```bash
cd implementations/go
go test ./...
go vet ./...
```

Do not rewrite a historical source manifest merely to make it match a changed development tree.

## Security

See `SECURITY.md` for vulnerability-reporting guidance and the machine-control safety boundary.

Do not commit secrets, tokens, signing keys, production credentials, or sensitive third-party data.

## Release history

The repository retains release notes, review reports, specifications, validation documents, and source manifests for prior development milestones. Start with `RELEASE_NOTES_0.50.0.md` and `CHANGELOG.md` for the current release line.
