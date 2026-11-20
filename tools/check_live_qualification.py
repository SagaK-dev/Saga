#!/usr/bin/env python3
"""Fail closed when a manually dispatched live qualification lacks real evidence.

Hosted CI and an unset integration credential must never result in a green
live-qualification job. The signed/independently inspected lab record is a
separate human responsibility: this tool records only an operator declaration.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

REQUIRED = {
    "aws": {"aws-live-account": "PASS"},
    "physical": {
        "vulkan-swapchain-present": "PASS_PHYSICAL",
        "physical-gamepad": "PASS_PHYSICAL",
        "machine-control-software": "PASS",
    },
}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


def inspect_report(report: object, mode: str, environ: dict[str, str]) -> dict:
    """Return a publishable receipt; its status remains FAIL until every gate passes."""
    problems: list[str] = []
    gates: dict[str, dict] = {}
    if not isinstance(report, dict) or report.get("schema") != 1:
        problems.append("missing or unsupported qualification report schema")
    else:
        rows = report.get("gates")
        if not isinstance(rows, list):
            problems.append("qualification report has no gates list")
        else:
            for item in rows:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("status"), str):
                    problems.append("invalid gate record")
                    continue
                if item["id"] in gates:
                    problems.append("duplicate gate id: " + item["id"])
                gates[item["id"]] = item

    observed = {key: gates.get(key, {}).get("status", "MISSING") for key in REQUIRED[mode]}
    for key, expected in REQUIRED[mode].items():
        if observed[key] != expected:
            problems.append(f"{key}={observed[key]} (requires {expected})")

    for key, item in gates.items():
        if item["status"] == "FAIL":
            problems.append(f"qualification reported FAIL: {key}")

    sha = environ.get("GITHUB_SHA", "")
    if not GIT_SHA_RE.fullmatch(sha):
        problems.append("missing valid GITHUB_SHA for live qualification provenance")
    actor = environ.get("GITHUB_ACTOR", "")
    if not actor.strip():
        problems.append("missing GitHub actor for live qualification provenance")

    pending: dict[str, str] = {}
    declaration: dict[str, object] = {}
    if mode == "aws":
        evidence = gates.get("aws-live-account", {}).get("evidence", {})
        principal_hash = evidence.get("principal_sha256") if isinstance(evidence, dict) else None
        if not isinstance(principal_hash, str) or SHA256_RE.fullmatch(principal_hash) is None:
            problems.append("missing redacted STS principal identity evidence")
        # Never include the raw AWS principal ARN or credentials in this receipt.
        declaration = {"principal_sha256": principal_hash if isinstance(principal_hash, str) else ""}
    else:
        confirmed = environ.get("SAGA_PHYSICAL_OPERATOR_CONFIRMED", "").lower() == "true"
        gpu = environ.get("SAGA_PHYSICAL_GPU_DRIVER", "").strip()
        gamepad = environ.get("SAGA_PHYSICAL_GAMEPAD_ID", "").strip()
        if not confirmed:
            problems.append("physical operator confirmation is required")
        if not gpu:
            problems.append("operator-declared GPU and driver inventory is required")
        if not gamepad:
            problems.append("operator-declared real gamepad identity is required")
        declaration = {
            "operator_confirmed": confirmed,
            "gpu_and_driver": gpu,
            "gamepad": gamepad,
            "disclaimer": "Operator declaration is not an independent hardware attestation.",
        }
        for key, expected in (("physical-gpio", "PASS_OPERATOR_PIN"),
                              ("physical-machine-control", "PASS_OPERATOR_LAB")):
            actual = gates.get(key, {}).get("status", "MISSING")
            if actual != expected:
                pending[key] = actual
        if environ.get("SAGA_GPIO_LIVE") == "1" and "physical-gpio" in pending:
            problems.append(f"operator requested GPIO verification but physical-gpio={pending['physical-gpio']}")

    return {
        "schema": 1,
        "kind": mode + "-live-qualification",
        "status": "PASS" if not problems else "INCOMPLETE",
        "commit_sha": sha,
        "workflow_run_id": environ.get("GITHUB_RUN_ID", ""),
        "github_actor": actor,
        "report_generated_at_utc": report.get("generated_at_utc") if isinstance(report, dict) else None,
        "required_gates": observed,
        "optional_unverified_gates": pending,
        "operator_declaration" if mode == "physical" else "cloud_evidence": declaration,
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=sorted(REQUIRED))
    parser.add_argument("report", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        data = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        data = None
        read_failure = f"cannot read qualification report: {type(exc).__name__}: {exc}"
    else:
        read_failure = None
    receipt = inspect_report(data, args.mode, dict(os.environ))
    if read_failure is not None:
        receipt["problems"].append(read_failure)
        receipt["status"] = "INCOMPLETE"
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if receipt["problems"]:
        for problem in receipt["problems"]:
            print("LIVE QUALIFICATION INCOMPLETE:", problem)
        return 1
    print("Live qualification evidence gate passed; additional human and hardware review may still be required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
