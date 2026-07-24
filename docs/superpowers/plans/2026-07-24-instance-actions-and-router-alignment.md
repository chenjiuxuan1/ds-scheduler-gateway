# DolphinScheduler Instance Actions and Router Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe official-API instance stop/force-fail actions, close every existing Router capability gap, update both repositories, and produce a directly importable n8n JSON derived from the user-supplied baseline.

**Architecture:** `ds-scheduler-gateway` owns official DolphinScheduler API actions and project resolution. `ds-skill-n8n` owns request construction, Router normalization, special n8n-only read paths, and user documentation. A static capability audit verifies that all 41 Router actions map to either a Gateway handler or an explicit six-country n8n implementation.

**Tech Stack:** Python 3, standard-library `unittest`, JavaScript/n8n Code nodes, JSON, Git.

---

### Task 1: Add Gateway tests and capability model

**Files:**
- Create: `tests/test_instance_actions.py`
- Create: `tests/test_capability_alignment.py`
- Modify: `gateway/models.py`
- Modify: `gateway/utils.py`
- Modify: `handlers/workflow_handlers.py`

- [ ] **Step 1: Write failing tests**

Create mock-client tests asserting:

```python
def test_stop_instance_dispatches_to_client():
    client = FakeClient()
    ok, result = dispatch_action(client, "stop_instance", {"project_code": "1", "instance_id": "2"})
    assert ok is True
    assert client.calls == [("stop_instance", {"project_code": "1", "instance_id": "2"})]

def test_force_fail_instance_dispatches_to_client():
    client = FakeClient()
    ok, result = dispatch_action(client, "force_fail_instance", {"project_code": "1", "instance_id": "2"})
    assert ok is True
    assert client.calls == [("force_fail_instance", {"project_code": "1", "instance_id": "2"})]
```

Add a capability-alignment assertion:

```python
assert {
    "resolve_project",
    "stop_instance",
    "force_fail_instance",
}.issubset(SUPPORTED_ACTIONS)
assert SUPPORTED_ACTIONS == set(workflow_action_names()) | {"search_country_git_sql"}
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest discover -s tests -v
```

Expected: failures because the actions and handlers do not exist.

- [ ] **Step 3: Add the capability model and dispatch entries**

Extend `CountryConfig`:

```python
instance_action_capabilities: Dict[str, Dict[str, Any]] = field(default_factory=dict)
```

Add `resolve_project`, `stop_instance`, and `force_fail_instance` to `SUPPORTED_ACTIONS`. Replace the local handler dictionary with an exported `workflow_action_names()` helper and add:

```python
"resolve_project": lambda: client.resolve_project(payload),
"stop_instance": lambda: client.stop_instance(payload),
"force_fail_instance": lambda: client.force_fail_instance(payload),
```

- [ ] **Step 4: Run tests**

Run `python3 -m unittest discover -s tests -v`.

Expected: dispatch/alignment tests pass; client behavior tests remain pending for Task 2.

- [ ] **Step 5: Commit**

```bash
git add gateway/models.py gateway/utils.py handlers/workflow_handlers.py tests
git commit -m "test: define instance action capability contract"
```

### Task 2: Implement project resolution and safe instance actions

**Files:**
- Modify: `clients/dolphinscheduler_client.py`
- Modify: `config/countries.example.json`
- Modify: `config/countries.json`
- Modify: `tests/test_instance_actions.py`
- Create: `tests/test_resolve_project.py`

- [ ] **Step 1: Write failing behavior tests**

Cover:

```python
def test_force_fail_defaults_to_unsupported():
    ok, result = client.force_fail_instance({"project_code": "1", "instance_id": "2"})
    assert ok is False
    assert result["code"] == "UNSUPPORTED"

def test_stop_uses_official_stop_execute_type():
    ok, result = client.stop_instance({"project_code": "1", "instance_id": "2"})
    assert ok is True
    assert request_log[-1]["form"]["executeType"] == "STOP"

def test_resolve_project_requires_unique_exact_name():
    ok, result = client.resolve_project({"project_name": "营销中台"})
    assert ok is True
    assert result["project_code"] == "123"
```

Also test stopped-state idempotency, terminal-state rejection, ambiguous project name, missing project, enabled configured force-fail, and non-converged accepted response.

- [ ] **Step 2: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_instance_actions tests.test_resolve_project -v
```

Expected: failures for missing methods.

- [ ] **Step 3: Implement minimal official-API behavior**

Implement:

```python
def stop_instance(self, payload):
    return self._run_configured_instance_action(payload, "stop_instance", "STOP")

def force_fail_instance(self, payload):
    capability = self._instance_action_capability("force_fail_instance")
    if not capability.get("supported"):
        return False, {
            "code": "UNSUPPORTED",
            "message": (
                "force_fail_instance is not supported by the official API "
                f"for country {self.config.country}"
            ),
        }
    return self._run_configured_instance_action(
        payload,
        "force_fail_instance",
        str(capability["execute_type"]),
    )
```

The shared helper must fetch the instance first, apply idempotent/terminal-state rules, call only `/projects/{project_code}/executors/execute`, and poll `get_instance` with bounded attempts. Implement `resolve_project` using official project-list/detail APIs and unique exact matching.

Add this default to all six country examples/configs:

```json
"instance_action_capabilities": {
  "stop_instance": {"supported": true, "execute_type": "STOP"},
  "force_fail_instance": {"supported": false}
}
```

- [ ] **Step 4: Run the full Gateway suite**

Run `python3 -m unittest discover -s tests -v`.

Expected: all tests pass without network access.

- [ ] **Step 5: Commit**

```bash
git add clients/dolphinscheduler_client.py config tests
git commit -m "feat: add safe workflow instance actions"
```

### Task 3: Align ds-skill-n8n CLI and request contract

**Files:**
- Modify: `../ds-skill-n8n/scripts/build_ds_webhook_payload.py`
- Modify: `../ds-skill-n8n/n8n/request_normalizer.js`
- Create: `../ds-skill-n8n/tests/test_payload_builder.py`
- Create: `../ds-skill-n8n/tests/test_action_alignment.py`

- [ ] **Step 1: Write failing CLI tests**

Assert all 41 action names are accepted and these actions enforce required fields:

```python
INSTANCE_ACTIONS = {"retry_instance", "stop_instance", "force_fail_instance"}
for action in INSTANCE_ACTIONS:
    result = run_builder(action, "--project-code", "1", "--instance-id", "2")
    assert result.returncode == 0
```

Add valid requests for `resolve_project`, `check_failed_instances`, `find_resource_usage`, and `search_country_git_sql`.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd ../ds-skill-n8n
python3 -m unittest discover -s tests -v
```

Expected: unsupported-action failures for the missing entries.

- [ ] **Step 3: Update builder and normalizer source**

Add the six missing builder actions:

```python
"resolve_project",
"check_failed_instances",
"find_resource_usage",
"search_country_git_sql",
"stop_instance",
"force_fail_instance",
```

Validate `project_code` plus `instance_id` for the three mutating/retry instance actions. Add `project_name` as an explicit payload/CLI field for `resolve_project`. Mirror the two new instance actions and their validation in `n8n/request_normalizer.js`.

- [ ] **Step 4: Run ds-skill-n8n tests**

Run `python3 -m unittest discover -s tests -v`.

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts n8n/request_normalizer.js tests
git commit -m "feat: align router action request contract"
```

### Task 4: Generate the importable Router from the exact user baseline

**Files:**
- Read: `/Users/jiangchuanchen/Downloads/ds-scheduler-router (2).json`
- Create: `../ds-skill-n8n/n8n/ds-scheduler-router.latest.json`
- Create: `../ds-skill-n8n/scripts/patch_router_instance_actions.py`
- Create: `../ds-skill-n8n/tests/test_router_artifact.py`
- Create: `/Users/jiangchuanchen/Documents/Codex/2026-07-23/weis/outputs/ds-scheduler-router.latest.json`

- [ ] **Step 1: Write the artifact regression test**

Test the baseline SHA-256, then assert:

```python
assert len(output["nodes"]) == 24
assert len(output["connections"]) == 19
assert action_set(output) == baseline_actions | {"stop_instance", "force_fail_instance"}
assert "resolve_project" in action_set(output)
assert unchanged_nodes_except_normalizer(baseline, output)
```

Verify node IDs, positions, connections, six execution branches, and audit nodes are unchanged.

- [ ] **Step 2: Run the artifact test**

Run:

```bash
cd ../ds-skill-n8n
python3 -m unittest tests.test_router_artifact -v
```

Expected: failure because the artifact does not exist.

- [ ] **Step 3: Implement a deterministic patcher**

The patcher must:

1. Reject any input whose SHA-256 differs from `16009d22a58df418684adfec09338ee804c6216c641e11cc1373ceb3baac4361`.
2. Locate the single node named `解析并标准化请求`.
3. Insert `'stop_instance'` and `'force_fail_instance'` after `'retry_instance'`.
4. Insert shared validation:

```javascript
if (['retry_instance', 'stop_instance', 'force_fail_instance'].includes(action)) {
  if (!payload.project_code) errors.push(`${action} requires project_code`);
  if (!payload.instance_id) errors.push(`${action} requires instance_id`);
}
```

5. Write stable UTF-8 JSON without modifying any other node.

- [ ] **Step 4: Generate both artifacts and run tests**

Run:

```bash
python3 scripts/patch_router_instance_actions.py \
  "/Users/jiangchuanchen/Downloads/ds-scheduler-router (2).json" \
  n8n/ds-scheduler-router.latest.json
cp n8n/ds-scheduler-router.latest.json \
  /Users/jiangchuanchen/Documents/Codex/2026-07-23/weis/outputs/ds-scheduler-router.latest.json
python3 -m unittest discover -s tests -v
```

Expected: all tests pass and both artifacts have identical SHA-256.

- [ ] **Step 5: Commit**

```bash
git add n8n/ds-scheduler-router.latest.json scripts/patch_router_instance_actions.py tests
git commit -m "feat: publish aligned n8n router artifact"
```

### Task 5: Update both repositories' documentation

**Files:**
- Modify: `README.md`
- Modify: `../ds-skill-n8n/SKILL.md`
- Modify: `../ds-skill-n8n/README.md`
- Modify: `../ds-skill-n8n/REFERENCE.md`
- Modify: `../ds-skill-n8n/EXAMPLES.md`
- Modify: `../ds-skill-n8n/n8n/README.md`
- Modify: `../ds-skill-n8n/DS调度Skills快速上手使用文档.md`

- [ ] **Step 1: Add documentation consistency checks**

Extend the capability-alignment test to assert every one of the 41 actions appears in `REFERENCE.md` and that the two mutating actions appear in `SKILL.md` with explicit-confirmation and official-API-only warnings.

- [ ] **Step 2: Run tests and verify documentation failures**

Run both repositories' unittest suites.

Expected: documentation consistency assertions fail.

- [ ] **Step 3: Update documentation**

Document:

- `resolve_project` exact/unique resolution.
- `stop_instance` official `STOP`, state precheck, idempotency, and confirmation requirement.
- `force_fail_instance` official API capability matrix and `UNSUPPORTED`.
- No DS metadata database fallback.
- CLI and webhook examples for all newly aligned actions.
- Router artifact source SHA and import path.

- [ ] **Step 4: Run all tests and static checks**

Run:

```bash
python3 -m unittest discover -s tests -v
git diff --check
cd ../ds-skill-n8n
python3 -m unittest discover -s tests -v
python3 -m json.tool n8n/ds-scheduler-router.latest.json >/dev/null
git diff --check
```

Expected: all commands succeed.

- [ ] **Step 5: Commit documentation**

Commit Gateway docs:

```bash
git add README.md docs
git commit -m "docs: document instance control capabilities"
```

Commit Skill docs:

```bash
git add SKILL.md README.md REFERENCE.md EXAMPLES.md n8n/README.md DS调度Skills快速上手使用文档.md
git commit -m "docs: document all router actions"
```

### Task 6: Final verification and safe remote publication

**Files:**
- Verify: both repositories
- Verify: `/Users/jiangchuanchen/Documents/Codex/2026-07-23/weis/outputs/ds-scheduler-router.latest.json`

- [ ] **Step 1: Run final clean verification**

Run both test suites, JSON parsing, `git diff --check`, `git status --short`, and SHA-256 comparison between repository/output artifacts.

Expected: tests pass, JSON parses, artifacts match, and only intended commits exist.

- [ ] **Step 2: Fetch both GitHub remotes**

Run:

```bash
git fetch origin
git rev-list --left-right --count origin/main...main
```

Expected: local branch is not behind. If behind, stop and integrate without force.

- [ ] **Step 3: Push ds-scheduler-gateway**

Run:

```bash
git push origin main
```

Expected: fast-forward push succeeds.

- [ ] **Step 4: Push ds-skill-n8n**

Run the same fetch/divergence check, then `git push origin main`.

Expected: fast-forward push succeeds.

- [ ] **Step 5: Record delivery evidence**

Report both remote commit SHAs, all test counts, artifact SHA-256, and the clickable local output path.
