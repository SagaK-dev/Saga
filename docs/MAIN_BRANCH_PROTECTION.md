# Protect `main` with a repository ruleset

Tracking issue: [#45](https://github.com/SagaK-dev/Saga/issues/45).

**Repository administrator action is required.** This repository contains a
reviewable JSON template, but merely committing it does not turn on branch
protection. A GitHub repository owner or account with **Administration: write**
must apply it through GitHub's Rules settings or REST API.

## Before enabling enforcement

The status-check names are GitHub **job contexts**, not workflow titles:

| Required context | Workflow | PR trigger |
| --- | --- | --- |
| `python-and-go` | `Saga core CI` | Every PR |
| `clean-package-install` | `Saga core CI` | Every PR |
| `linux-hosted-real` | `Saga platform qualification` | Every PR |

The platform qualification workflow deliberately runs on **all** pull
requests, including documentation-only changes. GitHub otherwise leaves a
required path-filtered workflow pending, blocking unrelated merges. The
`aws-live` job is excluded because it is intentionally skipped except on
authorized manual dispatch and must not be mistaken for proof of cloud
qualification. The native Windows/macOS/Linux workflows and contest package
workflow currently use path filters; do not mark those jobs required until
their event filters have been adjusted. Never make the physical lab or cloud
credential workflows required for routine PR merges.

## Apply once with GitHub CLI

Run the following from a trusted local checkout after checking the template
and authenticating as a repository administrator:

```bash
gh auth status
gh api repos/SagaK-dev/Saga/rulesets
cat .github/rulesets/main-protection.json
# Only execute this POST when the administrator has inspected and approved it.
gh api --method POST \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  repos/SagaK-dev/Saga/rulesets \
  --input .github/rulesets/main-protection.json
gh api repos/SagaK-dev/Saga/rulesets
```

Do **not** blindly re-run the POST: it creates another ruleset. If one already
exists, review and update the existing ruleset instead. Alternatively, use
**Repository → Settings → Rules → Rulesets → New branch ruleset**, selecting
the exact three check contexts from recent successful PR check runs.

The template is configured to:

- Match only `refs/heads/main`.
- Require changes via a PR using squash or rebase, with review threads resolved.
- Require up-to-date, passing core tests, wheel/source installation and Linux
  software platform qualification.
- Reject force pushes and branch deletion, without default bypass actors.

For a single-maintainer repository, **review approvals are set to zero**
so the only maintainer can still ship after the mandatory PR and CI gates.
This does *not* constitute mandatory independent code review. If qualified
reviewers are available, increase `required_approving_review_count` to 1
and review the appropriate bypass permissions before activating the ruleset.

## Verify actual enforcement before closing #45

1. Confirm a ruleset targeting `main` reports `enforcement=active` and that
   the branch is protected, not merely described in a committed JSON file.
2. On a disposable branch, propose a PR with a pending or deliberately failing
   required check. Verify GitHub **refuses the merge**.
3. Make a legitimate passing PR, including a documentation-only change. Verify
   all three required contexts appear and the PR can be merged.
4. Have an authorized administrator verify a direct push, force push and
   deletion of `main` are rejected **without performing destructive
   operations on production `main`**. Use GitHub's rule inspection/test
   facilities or a disposable test repository with the same ruleset when
   testing destructive paths.
5. Record the ruleset URL, administrator, verification date and blocked-merge
   evidence in Issue #45. Only then close the issue.

Notes: GitHub rulesets require the correct repository plan and permissions.
The attached GitHub connector currently has no ruleset-write permission.
No GitHub Actions runner, repository source file or bot can substitute for
the administrator's enforcement action.

References:
- https://docs.github.com/en/rest/repos/rules
- https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
