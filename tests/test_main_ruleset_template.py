"""Prevent branch-protection check names from drifting away from PR CI.

The JSON template is *not* automatically enforced; only an administrator can
create the live GitHub ruleset in the repository settings.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULESET = ROOT / ".github/rulesets/main-protection.json"
CORE = ROOT / ".github/workflows/saga-ci.yml"
PLATFORM = ROOT / ".github/workflows/saga-platform-qualification.yml"


def job_contexts(source: str) -> set[str]:
    # These sources have a top-level 'jobs:' mapping with two-space job keys.
    jobs = source.split("\njobs:\n", 1)[1]
    return set(re.findall(r"(?m)^  ([a-z][a-z0-9-]*):\s*$", jobs))


class MainRulesetTemplateTests(unittest.TestCase):
    def test_main_ruleset_scope_and_restrictions(self):
        data = json.loads(RULESET.read_text(encoding="utf-8"))
        self.assertEqual(data["target"], "branch")
        self.assertEqual(data["enforcement"], "active")
        self.assertEqual(data["bypass_actors"], [])
        self.assertEqual(data["conditions"]["ref_name"]["include"], ["refs/heads/main"])
        self.assertEqual(data["conditions"]["ref_name"]["exclude"], [])
        rules = {rule["type"]: rule for rule in data["rules"]}
        self.assertEqual(set(rules), {
            "pull_request", "required_status_checks", "non_fast_forward", "deletion"
        })
        self.assertEqual(rules["pull_request"]["parameters"]["allowed_merge_methods"], ["squash", "rebase"])
        self.assertIs(rules["pull_request"]["parameters"]["required_review_thread_resolution"], True)
        self.assertIs(rules["required_status_checks"]["parameters"]["strict_required_status_checks_policy"], True)

    def test_required_contexts_exist_and_run_for_every_pr(self):
        data = json.loads(RULESET.read_text(encoding="utf-8"))
        checks = next(rule for rule in data["rules"] if rule["type"] == "required_status_checks")
        names = {item["context"] for item in checks["parameters"]["required_status_checks"]}
        self.assertEqual(names, {"python-and-go", "clean-package-install", "linux-hosted-real"})
        self.assertEqual(len(checks["parameters"]["required_status_checks"]), len(names))

        core_source = CORE.read_text(encoding="utf-8")
        platform_source = PLATFORM.read_text(encoding="utf-8")
        self.assertIn("\n  pull_request:\n", core_source)
        self.assertIn("\n  pull_request:\n", platform_source)
        self.assertNotIn("paths:", platform_source.split("\n  pull_request:", 1)[1].split("\npermissions:", 1)[0])
        contexts = job_contexts(core_source) | job_contexts(platform_source)
        self.assertTrue(names <= contexts, f"unknown required job contexts: {names - contexts}")
        # Credential-bearing AWS qualification remains manual-only and MUST
        # NOT become a required check on ordinary PRs.
        self.assertNotIn("aws-live", names)


if __name__ == "__main__":
    unittest.main()
