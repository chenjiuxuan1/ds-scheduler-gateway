# Batch Schedule Alert Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe, dry-run-first, rollback-capable DolphinScheduler alert-group lookup and batch schedule alert updates, then expose the same validated contract through ds-skill-n8n.

**Architecture:** Keep transport and orchestration in `DolphinSchedulerClient`, but isolate schedule snapshot/merge/comparison rules in a focused helper module so existing schedule creation remains unchanged. Batch execution performs a complete country-level preflight before any mutation, then processes eligible schedules sequentially with post-write verification and per-record compensation. n8n only validates and transports requests; tokens remain transient and are never included in results, rollback payloads, logs, or repository files.

**Tech Stack:** Python 3.9 standard library and `unittest`; JavaScript n8n Code nodes; JSON workflow artifacts; Markdown documentation.

---

## File map

- Create `clients/schedule_alerts.py`: warning-type validation, DS schedule unwrapping, lossless writable-form construction, snapshots, comparisons, and token-free rollback payloads.
- Create `tests/test_schedule_alerts.py`: focused unit tests for alert-group lookup, partial update preservation, dry-run selection, verification, and rollback.
- Modify `clients/dolphinscheduler_client.py`: add public actions and transactional orchestration without changing existing create/online/offline behavior.
- Modify `gateway/utils.py` and `handlers/workflow_handlers.py`: whitelist and dispatch the two new actions.
- Modify `README.md`: document inputs, outputs, state guarantees, and production gate.
- Modify the sibling `ds-skill-n8n` repository's builder, normalizer, workflow artifacts, docs, and tests so the public contract stays aligned.

### Task 1: Register alert-group lookup and exact-match behavior

**Files:**
- Modify: `gateway/utils.py`
- Modify: `handlers/workflow_handlers.py`
- Modify: `clients/dolphinscheduler_client.py`
- Create: `tests/test_schedule_alerts.py`

- [ ] **Step 1: Write failing action and alert-group tests**

Add tests asserting both new actions are supported and dispatched, paginated `GET /alert-groups` uses `pageNo`, `pageSize`, and `searchVal`, normalized items contain `id`, `group_name`, `description`, and alert-instance data, and exact search returns `ALERT_GROUP_NOT_FOUND` or `AMBIGUOUS_ALERT_GROUP` rather than selecting a candidate.

```python
def test_exact_alert_group_rejects_duplicates(self):
    client = FakeClient(config(), [(True, {"code": 0, "data": {"totalList": [
        {"id": 7, "groupName": "n8n告警触发器"},
        {"id": 8, "groupName": "n8n告警触发器"},
    ]}})])
    ok, result = client.list_alert_groups({"search_val": "n8n告警触发器"})
    self.assertFalse(ok)
    self.assertEqual("AMBIGUOUS_ALERT_GROUP", result["code"])
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `python3 -m unittest tests.test_schedule_alerts -v`

Expected: FAIL because `list_alert_groups` and action registrations do not exist.

- [ ] **Step 3: Implement lookup, normalization, registration, and dispatch**

Use the DolphinScheduler `/alert-groups` paging endpoint and exact-match only when a non-empty `search_val` is provided. Preserve the raw item under `raw`, and normalize likely API variants (`groupName`/`name`, `alertInstanceIds`/`alertInstances`/`instanceIds`). Never include request headers or the DS token in returned structures.

- [ ] **Step 4: Run focused and gateway regression tests**

Run: `python3 -m unittest tests.test_schedule_alerts tests.test_instance_actions tests.test_resolve_project tests.test_search_resource_sql -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add gateway/utils.py handlers/workflow_handlers.py clients/dolphinscheduler_client.py tests/test_schedule_alerts.py
git commit -m "feat: add exact DolphinScheduler alert group lookup"
```

### Task 2: Add lossless alert-only schedule updates

**Files:**
- Create: `clients/schedule_alerts.py`
- Modify: `clients/dolphinscheduler_client.py`
- Modify: `tests/test_schedule_alerts.py`

- [ ] **Step 1: Write failing preservation and validation tests**

Cover semantic warning types `NONE`, `SUCCESS`, `FAILURE`, and `ALL`; reject any other value. For a request containing only `warning_type` and/or `warning_group_id`, assert `get_schedule` occurs before `PUT`, the write form preserves schedule JSON (start/end/crontab/timezone), failure strategy, priority, worker group, tenant, environment, start params, workflow code, and release state, and only the requested alert fields change.

```python
self.assertEqual("0 0 * * * ?", put_form["schedule"]["crontab"])
self.assertEqual("STOP", put_form["failureStrategy"])
self.assertEqual("ONLINE", put_form["releaseState"])
self.assertEqual("FAILURE", put_form["warningType"])
self.assertEqual("42", put_form["warningGroupId"])
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `python3 -m unittest tests.test_schedule_alerts.ScheduleAlertUpdateTests -v`

Expected: FAIL because the current builder substitutes defaults and can clear original fields.

- [ ] **Step 3: Implement snapshot, merge, rollback payload, and verification helpers**

Define `normalize_schedule_record`, `build_schedule_write_form`, `build_rollback_payload`, `compare_non_target_fields`, and `verify_alert_update` in `clients/schedule_alerts.py`. The rollback payload must contain country/project/workflow/schedule locators plus the full original writable configuration, but never `ds_token`.

- [ ] **Step 4: Route alert-only updates through read-merge-write-verify**

Keep `create_schedule` on the existing builder. In `update_schedule`, detect alert-only mode when the provided mutable keys are a subset of `{warning_type, warning_group_id}`; read the original schedule, merge it, issue one update, read it back, verify target and all non-target fields, and return the original snapshot and rollback payload. Do not call online/offline APIs. If verification fails, restore the original form and verify the restoration before returning a precise failure status.

- [ ] **Step 5: Run focused and regression tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: all gateway tests PASS.

- [ ] **Step 6: Commit**

```bash
git add clients/schedule_alerts.py clients/dolphinscheduler_client.py tests/test_schedule_alerts.py
git commit -m "feat: preserve schedule configuration during alert updates"
```

### Task 3: Implement safe batch preflight and dry-run

**Files:**
- Modify: `clients/dolphinscheduler_client.py`
- Modify: `tests/test_schedule_alerts.py`

- [ ] **Step 1: Write failing preflight and dry-run tests**

Test validation for a non-empty unique `project_names` list, state filters, semantic warning type, warning-group name, and boolean `dry_run`. Assert projects are resolved live by exact name, the alert group is resolved once per country, every selected project's workflows and schedules are re-listed, only workflow `ONLINE` plus schedule `ONLINE` rows are matched, and no `PUT` occurs during dry run.

Assert result statuses and counts include `DRY_RUN_MATCHED`, `SKIPPED_ALREADY_MATCHED`, `SKIPPED_NOT_ONLINE`, project/workflow names and codes, schedule id, original and target alert configuration, plus `total`, `matched`, `updated`, `skipped`, `failed`, and `verification_failed`.

- [ ] **Step 2: Run tests and confirm failure**

Run: `python3 -m unittest tests.test_schedule_alerts.BatchScheduleAlertTests -v`

Expected: FAIL because `batch_update_schedule_alerts` does not exist.

- [ ] **Step 3: Implement complete country-level preflight**

Resolve every whitelisted project and the exact local alert group before constructing executable records. Treat missing or duplicate projects and alert groups as preflight failure with zero writes. Paginate list APIs until the reported total is consumed so stale catalog codes and first-page truncation cannot affect selection.

- [ ] **Step 4: Implement dry-run result construction**

Return one result per discovered schedule with explicit status and token-free rollback payload only for matched items. Mark already-correct schedules `SKIPPED_ALREADY_MATCHED` and any workflow/schedule outside requested `ONLINE` state `SKIPPED_NOT_ONLINE`.

- [ ] **Step 5: Run focused and regression tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: all gateway tests PASS and no fake-client dry-run test observes a write request.

- [ ] **Step 6: Commit**

```bash
git add clients/dolphinscheduler_client.py tests/test_schedule_alerts.py
git commit -m "feat: add dry-run schedule alert batch planning"
```

### Task 4: Add transactional batch writes, retry limits, and compensation

**Files:**
- Modify: `clients/dolphinscheduler_client.py`
- Modify: `clients/schedule_alerts.py`
- Modify: `tests/test_schedule_alerts.py`

- [ ] **Step 1: Write failing mutation and rollback tests**

Test sequential updates, verification reads, unchanged release state and crontab, partial failure visibility, and these statuses: `UPDATED`, `FAILED_UNCHANGED`, `FAILED_ROLLED_BACK`, `VERIFICATION_FAILED_ROLLED_BACK`, and `FAILED_ROLLBACK_FAILED`. Assert one failed item does not erase prior successful item results.

Test that validation errors are never retried; transient transport/5xx failures respect bounded attempts and delay inputs; after an uncertain write, current state is read before retrying the write.

- [ ] **Step 2: Run tests and confirm failure**

Run: `python3 -m unittest tests.test_schedule_alerts.BatchScheduleAlertMutationTests -v`

Expected: FAIL because mutation orchestration and compensation are absent.

- [ ] **Step 3: Implement sequential mutation and compensation**

For each matched item save the original complete writable snapshot, call the alert-only update primitive, re-read and verify target fields, release state, and non-target fields. On any failed or mismatched update, restore the original form, re-read, and classify rollback success or failure. Do not toggle release state at any point.

- [ ] **Step 4: Implement bounded retry rules**

Retry only transport failures, HTTP 429, and HTTP 5xx. Never retry validation, ambiguity, not-found, or DolphinScheduler business errors. Before retrying a write after an uncertain response, call `get_schedule`; if target state already matches, verify and classify success without another write.

- [ ] **Step 5: Run focused and regression tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: all gateway tests PASS; summaries additionally include `rolled_back` and `rollback_failed`.

- [ ] **Step 6: Commit**

```bash
git add clients/dolphinscheduler_client.py clients/schedule_alerts.py tests/test_schedule_alerts.py
git commit -m "feat: make batch schedule alert updates transactional"
```

### Task 5: Synchronize ds-skill-n8n validation and payload building

**Files:**
- Modify: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/scripts/build_ds_webhook_payload.py`
- Modify: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/n8n/request_normalizer.js`
- Modify: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/tests/test_action_alignment.py`
- Create: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/tests/test_schedule_alert_actions.py`

- [ ] **Step 1: Write failing builder and normalizer tests**

Assert both action sets contain `list_alert_groups` and `batch_update_schedule_alerts`. Execute builder validation tests for all six countries, exact search paging, alert-only `update_schedule`, batch project JSON, state filters, warning-type enum, non-empty warning-group name, strict boolean dry-run, and absence of tokens from any serialized rollback fixture.

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `python3 -m unittest tests.test_action_alignment tests.test_schedule_alert_actions -v`

Expected: FAIL because the new actions and arguments are absent.

- [ ] **Step 3: Implement builder arguments and validation**

Add `--project-names-json`, `--workflow-release-state`, `--schedule-release-state`, `--warning-group-name`, `--dry-run`/`--execute`, `--retry-attempts`, and `--retry-delay-ms`. Permit `update_schedule` with alert fields and no crontab; reject invalid warning types and malformed project lists.

- [ ] **Step 4: Implement equivalent n8n normalization**

Normalize the new fields without coercing arbitrary strings to booleans. Add field-specific errors and ensure the normalized output never copies `ds_token` into `payload`.

- [ ] **Step 5: Run focused tests**

Run: `python3 -m unittest tests.test_action_alignment tests.test_schedule_alert_actions -v`

Expected: all focused Skill tests PASS.

- [ ] **Step 6: Commit in the ds-skill-n8n repository**

```bash
git add scripts/build_ds_webhook_payload.py n8n/request_normalizer.js tests/test_action_alignment.py tests/test_schedule_alert_actions.py
git commit -m "feat: validate schedule alert batch actions"
```

### Task 6: Synchronize n8n artifacts and documentation

**Files:**
- Modify: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/n8n/workflow-template.json`
- Modify: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/n8n/ds-scheduler-router.latest.json`
- Modify: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/n8n/README.md`
- Modify: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/SKILL.md`
- Modify: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/REFERENCE.md`
- Modify: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/EXAMPLES.md`
- Modify: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/README.md`
- Modify: `/Users/jiangchuanchen/Desktop/codex使用/ds-skill-n8n/tests/test_router_artifact.py`

- [ ] **Step 1: Replace the external-download test dependency**

Make the checked-in workflow template the approved structural baseline, or store its expected hash inside the test. Assert only the request-normalizer Code node changes when patching action support and fields; preserve node ids, credentials, connections, and unrelated node JSON exactly.

- [ ] **Step 2: Patch both workflow JSON artifacts mechanically**

Inject the tested `request_normalizer.js` into the `解析并标准化请求` node of both JSON files. Do not reconstruct the workflow or change credentials/connections.

- [ ] **Step 3: Document the public contract and safety gate**

Document exact-match failure semantics, alert-only schedule preservation, dry-run output/statuses, rollback payload usage, retry policy, and the staged production sequence. Examples must use placeholders such as `<DS_TOKEN>` and must not contain a real token.

- [ ] **Step 4: Run the full Skill suite and token scan**

Run: `python3 -m unittest discover -s tests -v`

Run: `rg -n "ds_token\s*[:=]\s*['\"][^<{]" SKILL.md REFERENCE.md EXAMPLES.md README.md scripts n8n tests`

Expected: all tests PASS and the token scan finds no credential literals.

- [ ] **Step 5: Commit in the ds-skill-n8n repository**

```bash
git add SKILL.md REFERENCE.md EXAMPLES.md README.md scripts/build_ds_webhook_payload.py n8n tests
git commit -m "docs: publish safe schedule alert batch workflow"
```

### Task 7: Gateway documentation and final regression

**Files:**
- Modify: `README.md`
- Modify: `tests/test_schedule_alerts.py`

- [ ] **Step 1: Add contract and safety documentation**

Document all required request/response fields, supported countries, exact-match errors, dry-run default, no state toggles, per-item rollback statuses, and production rollout gate. Use only placeholder tokens.

- [ ] **Step 2: Run both repository suites**

Run in gateway worktree: `python3 -m unittest discover -s tests -v`

Run in ds-skill-n8n: `python3 -m unittest discover -s tests -v`

Expected: both suites PASS.

- [ ] **Step 3: Review diffs for non-target changes and secrets**

Run: `git diff --check` in each repository.

Run targeted secret scans against changed files and confirm neither the user-provided token nor any credential-shaped literal is present.

- [ ] **Step 4: Commit gateway documentation**

```bash
git add README.md tests/test_schedule_alerts.py
git commit -m "docs: document schedule alert batch safeguards"
```

### Task 8: Prepare—but do not bypass—the production validation gate

**Files:**
- No repository changes required unless validation reveals a compatibility defect.

- [ ] **Step 1: Confirm deployment and country tokens explicitly**

Do not push/deploy or call production writes until the updated gateway/n8n workflow is deployed through the user's normal process and fresh per-country tokens are supplied transiently.

- [ ] **Step 2: Validate one country, one project, one schedule**

Execute in order: `list_alert_groups`; single-project `batch_update_schedule_alerts` with `dry_run=true`; one formal alert-only `update_schedule`; `get_schedule`; apply the returned rollback payload; `get_schedule` again. Stop immediately on any mismatch.

- [ ] **Step 3: Expand only after restoration is verified**

Run complete dry-runs for ph, pk, and mx using their specified project allowlists. Present counts and per-record results for explicit approval before any bulk `dry_run=false` request.
