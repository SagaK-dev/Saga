# Live qualification operations (operator guide)

The ordinary `Saga core CI`, `Saga platform qualification` and native
desktop CI jobs test software on hosted infrastructure. Their success **does
not** prove that a physical GPU, a real gamepad, GPIO, or a live AWS account
was tested. A missing result is always pending, not passed.

## Before running on an actual lab machine (Issue #57)

1. A lab administrator registers a trustworthy, **Linux** self-hosted GitHub
   Actions runner with labels `self-hosted`, `linux` and
   `saga-hardware-lab`. The Vulkan swapchain qualification in this workflow
   only supports Linux. Other OS qualification requires separate real devices
   and evidence.
2. Restrict runner administration and repository dispatch privileges. Do not
   let unreviewed pull-request source run on a machine connected to actuators.
   Run the workflow against reviewed `main`, not an untrusted feature branch.
   Isolate network access and keep an emergency stop accessible if any physical
   device is connected.
3. Connect a real Vulkan-capable GPU and USB/Bluetooth gamepad; record the GPU
   model, exact driver version, controller model, connection method, host OS,
   and `main` source SHA. The physical workflow forces a system Vulkan ICD
   instead of its usual software fallback; ensure that a real GPU/driver is
   available to the runner. If the host has only SwiftShader/llvmpipe, the
   physical GPU gate must fail.
4. Optionally prepare a GPIO fixture with a safe, electrically isolated test
   output. A qualified operator must first approve the pin and wiring.
   Only then set `SAGA_GPIO_LIVE=1` and `SAGA_GPIO_OUTPUT_PIN`. Otherwise
   leave GPIO disabled/pending.
5. Run **Actions → Saga physical hardware lab → Run workflow**. Enter the
   observed GPU/driver and controller identity, and explicitly confirm that
   the operator inspected real hardware. Before hardware qualification starts,
   the workflow checks the operator confirmation and metadata. During the
   gamepad probe, **press the A button on the real controller** when prompted
   (`PHYSICAL_GAMEPAD_PRESS_A_NOW`, within twelve seconds); mere detection
   of a controller does not count as a successful input test. The Saga fixture
   is `validation/gamepad/physical_gamepad.saga`. To keep the test bounded,
   it uses the **first SDL-compatible controller** it finds. Disconnect other
   game controllers when qualifying a specific physical device.
6. Download the `saga-physical-lab` artifact. Keep both
   `validation/physical-lab.json` and `validation/physical-lab-receipt.json`
   with the workflow run link and operator date. The receipt records the SHA,
   GitHub actor, declared inventory and each required gate.

The job fails unless all these gates are observed:
- `vulkan-swapchain-present = PASS_PHYSICAL`, not `PASS_SOFTWARE_DEVICE`
- `physical-gamepad = PASS_PHYSICAL`
- `machine-control-software = PASS`
- `physical-gpio = PASS_OPERATOR_PIN` **only when GPIO live testing is requested**

`physical-machine-control` and optional GPIO remain *unverified* until
their separate operator-controlled validation is completed. A passing core
hardware workflow is **not** an independently signed hardware attestation,
an electrical readback of GPIO, or a machine safety certification. Attach
operator-reviewed test logs, any external fixture measurements, and formal
signatures outside the workflow as needed.

## AWS live qualification (Issue #58)

1. An AWS administrator creates a restricted STS-only test role. Configure
   federated GitHub Actions OIDC trust so that only the approved
   `SagaK-dev/Saga` repository, ref/environment and authorized workflow
   can assume it. Do not configure access keys or broaden permissions.
2. Review the GitHub environment `saga-aws-qualification` and, where
   appropriate, require independent approval. Set
   `SAGA_AWS_QUALIFICATION_ROLE_ARN` and optionally
   `SAGA_AWS_QUALIFICATION_REGION` as scoped GitHub variables.
3. Manually dispatch **Saga platform qualification** from reviewed `main`.
   The `aws-live` job is intentionally skipped for ordinary pushes and PRs.
4. Retrieve `saga-aws-live`, which now includes `validation/aws-live.json`
   and `validation/aws-live-receipt.json`. Verify the expected run/SHA and
   `aws-live-account = PASS`. The workflow explicitly fails if the role is
   not set or if STS identity validation did not complete.
5. The Saga qualification verifies the assumed-role principal's partition,
   account and role name against the configured approved IAM role ARN before
   returning `PASS`. Review the configured role trust policy independently.
   **Do not publish account identifiers**: the report and receipt contain a
   SHA-256 digest of the STS ARN, never the full principal ARN. Store any
   additional account-level comparison in authorized administration records.

A green software-only CI workflow **never** means AWS live validation passed.

## Repository administrator action (Issue #45)

`main` is currently unprotected. In **Settings → Rules → Rulesets**, create
an enforced rule targeting `main`: require pull requests, select the actual
check-run contexts from a recent successful PR, prohibit force pushes and
deletion, minimize bypasses, and test both blocked direct changes and blocked
merges with pending or failing checks. A repository write-capable bot cannot
replace these owner/admin controls. Do not close Issue #45 until verified.
