"""Pure, hardware-free regression tests for live qualification acceptance gates."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.check_live_qualification import inspect_report
from tools import platform_qualification


SHA = "a" * 40
ACTOR = "test-operator"
ARN = "arn:aws:sts::123456789012:assumed-role/saga-live-qualification/test-session"


def report(*rows: tuple[str, str], evidence: dict | None = None) -> dict:
    gates = [{"id": gate_id, "status": status} for gate_id, status in rows]
    if evidence:
        gates[-1]["evidence"] = evidence
    return {"schema": 1, "generated_at_utc": "2026-11-20T00:00:00+00:00", "gates": gates}


def environment(**kwargs: str) -> dict[str, str]:
    base = {
        "GITHUB_SHA": SHA, "GITHUB_ACTOR": ACTOR,
        "GITHUB_RUN_ID": "123456",
        "SAGA_PHYSICAL_OPERATOR_CONFIRMED": "true",
        "SAGA_PHYSICAL_GPU_DRIVER": "Test GPU, driver 123",
        "SAGA_PHYSICAL_GAMEPAD_ID": "Test USB controller",
    }
    base.update(kwargs)
    return base


class LiveQualificationEvidenceTest(unittest.TestCase):
    def physical(self, gpu="PASS_PHYSICAL", gamepad="PASS_PHYSICAL", gpio="READY_UNEXECUTED"):
        return report(
            ("vulkan-swapchain-present", gpu),
            ("physical-gamepad", gamepad),
            ("machine-control-software", "PASS"),
            ("physical-gpio", gpio),
            ("physical-machine-control", "READY_UNEXECUTED"),
        )

    def test_physical_core_pass_is_not_an_actuator_attestation(self):
        record = inspect_report(self.physical(), "physical", environment())
        self.assertEqual(record["status"], "PASS")
        self.assertEqual(record["optional_unverified_gates"]["physical-machine-control"], "READY_UNEXECUTED")
        self.assertFalse(record["problems"])
        self.assertIn("not an independent", record["operator_declaration"]["disclaimer"])

    def test_software_gpu_is_not_physical_success(self):
        record = inspect_report(self.physical(gpu="PASS_SOFTWARE_DEVICE"), "physical", environment())
        self.assertEqual(record["status"], "INCOMPLETE")
        self.assertTrue(any("vulkan-swapchain-present" in x for x in record["problems"]))

    def test_missing_gamepad_and_operator_confirmation_fail_closed(self):
        record = inspect_report(
            self.physical(gamepad="BLOCKED"), "physical",
            environment(SAGA_PHYSICAL_OPERATOR_CONFIRMED="false", SAGA_PHYSICAL_GAMEPAD_ID=""),
        )
        self.assertEqual(record["status"], "INCOMPLETE")
        self.assertTrue(any("gamepad" in x for x in record["problems"]))
        self.assertTrue(any("operator confirmation" in x for x in record["problems"]))

    def test_requested_gpio_verification_must_pass(self):
        env = environment(SAGA_GPIO_LIVE="1")
        self.assertEqual(inspect_report(self.physical(), "physical", env)["status"], "INCOMPLETE")
        self.assertEqual(inspect_report(self.physical(gpio="PASS_OPERATOR_PIN"), "physical", env)["status"], "PASS")

    def test_aws_skipped_gate_never_passes(self):
        record = inspect_report(report(("aws-live-account", "READY_UNEXECUTED")), "aws", environment())
        self.assertEqual(record["status"], "INCOMPLETE")
        self.assertIn("missing redacted STS principal identity evidence", record["problems"])

    def test_aws_requires_redacted_identity(self):
        hash_value = hashlib.sha256(ARN.encode()).hexdigest()
        record = inspect_report(
            report(("aws-live-account", "PASS"), evidence={"principal_sha256": hash_value}),
            "aws", environment(),
        )
        self.assertEqual(record["status"], "PASS")
        self.assertEqual(record["cloud_evidence"]["principal_sha256"], hash_value)
        self.assertNotIn("123456789012", json.dumps(record))

    def test_missing_sha_or_duplicate_gates_fail(self):
        data = self.physical()
        data["gates"].append({"id": "physical-gamepad", "status": "PASS_PHYSICAL"})
        record = inspect_report(data, "physical", environment(GITHUB_SHA="not-a-sha"))
        self.assertEqual(record["status"], "INCOMPLETE")
        self.assertTrue(any("duplicate gate" in x for x in record["problems"]))
        self.assertTrue(any("GITHUB_SHA" in x for x in record["problems"]))

    def test_cloud_runtime_evidence_redacts_account_number(self):
        def fake_run_source(_source, *, output, **_kwargs):
            output.append(ARN)

        with patch.object(platform_qualification, "_module_available", return_value=True), \
             patch("saga.api.run_source", side_effect=fake_run_source), \
             patch.dict("os.environ", {"SAGA_AWS_LIVE": "1"}):
            data = platform_qualification.qualify_aws()
        self.assertEqual(data["status"], "PASS")
        self.assertEqual(data["evidence"]["principal_sha256"], hashlib.sha256(ARN.encode()).hexdigest())
        self.assertNotIn("123456789012", json.dumps(data))
        self.assertNotIn(ARN, json.dumps(data))


if __name__ == "__main__":
    unittest.main()
