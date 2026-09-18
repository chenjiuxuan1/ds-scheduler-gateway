# ds-scheduler-gateway

面向 Codex + n8n 的多国家 DolphinScheduler 3.4 调度操作网关。

这个项目的目标是把 6 个国家的 DS 调度增删改查能力，从各国旧项目中抽离出来，统一成一套可维护的标准入口。

## 设计目标

- 一套代码支持多个国家
- 国家差异只放在配置层
- n8n 只负责接收请求、按国家分流、跳板机执行
- 目标机器只需要拉取这个项目，并提供本国 DS 网络访问能力
- Codex 通过统一 webhook 请求调用调度能力

## 当前支持动作

- `list_projects`
- `resolve_project`
- `list_alert_groups`
- `list_workflows`
- `create_workflow`
- `copy_workflow`
- `list_schedules`
- `get_schedule`
- `create_schedule`
- `update_schedule`
- `batch_update_schedule_alerts`
- `online_schedule`
- `offline_schedule`
- `schedule_blast_radius`
- `get_workflow`
- `online_workflow`
- `offline_workflow`
- `trigger_workflow`
- `list_instances`
- `get_instance`
- `list_task_instances`
- `get_task_log`
- `retry_instance`
- `stop_instance`
- `force_fail_instance`
- `check_failed_instances`
- `dump_workflow_graph`
- `append_task`
- `append_sql_task`
- `append_shell_task`
- `update_task`
- `update_sql_task`
- `update_shell_task`
- `update_workflow_environment`
- `batch_update_workflow_environment`
- `disable_task`
- `disable_tasks_except`
- `delete_task`
- `list_datasources`
- `get_datasource`
- `extract_task_runtime_config`
- `list_resources`
- `view_resource_file`
- `search_resource_sql`
- `search_country_git_sql`

## 实例控制安全约束

- `stop_instance` 先读取实例状态，再通过 DolphinScheduler 官方
  `executors/execute` 接口发送 `executeType=STOP`，并短轮询最终状态。
- `force_fail_instance` 仅在国家配置中存在经过验证的官方 API
  `execute_type` 时执行；默认返回 `UNSUPPORTED`。
- 两个动作都要求 `project_code`、`instance_id` 和有效的 DS Token。
- 网关绝不通过修改 DolphinScheduler 元数据库来停止或强制失败实例。
- `resolve_project` 支持按 code 或唯一精确项目名解析；名称歧义会返回
  `AMBIGUOUS_PROJECT`。

## 定时告警安全更新

### `list_alert_groups`

支持 `cn / ine / mx / ph / pk / th`，入参为 `search_val / page_no / page_size`。不传 `search_val` 返回标准化列表；传入后只接受 `group_name` 精确唯一匹配。0 条返回 `ALERT_GROUP_NOT_FOUND`，多条返回 `AMBIGUOUS_ALERT_GROUP`。

### `update_schedule` 告警字段模式

只传 `warning_type` 和/或 `warning_group_id` 时，网关先调用 `get_schedule` 保存完整原配置，再构建无损表单。支持的 `warning_type` 为 `NONE / SUCCESS / FAILURE / ALL`。

写后再次 `get_schedule`，逐项验证告警字段、cron、起止时间、时区、失败策略、worker group、tenant、environment、优先级、start params 和原 `releaseState`。任何不一致都会自动写回原快照并再次验证。网关不会自动调用定时上线/下线接口。

响应包含不含 token 的 `rollback_payload`；该 payload 可作为 `update_schedule` 的 payload 恢复原配置，恢复后仍应显式调用 `get_schedule` 回查。

### `batch_update_schedule_alerts`

必填：

- `project_names`：唯一非空项目名数组
- `warning_type`
- `warning_group_name`

安全限制：

- `dry_run` 默认 `true`
- `workflow_release_state` 和 `schedule_release_state` 必须都是 `ONLINE`
- 服务端实时解析项目 code、工作流、定时和本国告警组 ID，不使用旧 catalog code，也不跨国家复用告警组 ID
- 任一项目或告警组缺失/歧义时，整个国家预检以零写入结束
- 正式执行逐条串行，瞬时错误才做有限重试；不确定写入会先回查再决定是否重试
- 单条失败不掩盖，验证失败自动恢复原快照

逐条状态：`DRY_RUN_MATCHED / UPDATED / SKIPPED_ALREADY_MATCHED / SKIPPED_NOT_ONLINE / FAILED_UNCHANGED / FAILED_ROLLED_BACK / VERIFICATION_FAILED_ROLLED_BACK / FAILED_ROLLBACK_FAILED`。

汇总字段：`total / matched / updated / skipped / failed / verification_failed / rolled_back / rollback_failed`。

生产门禁固定为：精确查告警组 → 单国家单项目 dry-run → 单条正式更新 → `get_schedule` → rollback → 再次 `get_schedule`。恢复完全正确后才能扩大 dry-run，批量正式执行仍需用户明确批准。


## 任务失败重试（`fail_retry_times` / `fail_retry_interval`）

`update_task` / `update_sql_task` / `update_shell_task` 支持修改任务定义级的失败重试设置。

DS 里这两个字段是 `taskType` / `timeout` 的**同级字段**，不在 `taskParams` 内：

| 参数 | 含义 | 取值范围 |
|---|---|---|
| `fail_retry_times` | 失败重试次数（`failRetryTimes`） | 0–1000 |
| `fail_retry_interval` | 重试间隔（`failRetryInterval`），**单位分钟** | 0–10080（7 天） |

行为约定：

- 两个参数都可选；**不传 = 保持原值不变**。
- `0` 是有效值（表示不重试），不会被当成"未提供"。
- 同时兼容 DS 原生驼峰写法 `failRetryTimes` / `failRetryInterval`；数字字符串（`"3"`）会被归一化为整数。
- 校验在**任何 DS 调用之前**完成：`true` / `"abc"` / `1.5` 等类型错误返回
  `INVALID_INTEGER_FIELD`，越界返回 `INTEGER_FIELD_OUT_OF_RANGE`，且不会产生任何写入。
- 校验失败时整条请求拒绝，不会出现"一个字段合法另一个不合法却写了一半"。
- 响应回显落到任务上的 `fail_retry_times` / `fail_retry_interval`，
  `change_summary.changed_fields` 里列出实际变更的字段名。

这一能力只改这两个字段，其余任务属性（脚本、数据源、`environmentCode`、同级别的任务）
原样保留，已有测试逐字段比对确认。

由于 `update_sql_task` / `update_shell_task` 的既有契约要求同时提供 `sql` / `script`，
**只改重试时请用 `update_task`**（它只要求 `task_name` 或 `task_code`）。

> `append_task` 的重试次数继承自 `template_task_name` 指定的模板任务，暂不支持在 append 时覆盖；
> 需要的话先 append，再对该任务跑一次 `update_task`。


## 环境切换（`update_workflow_environment` / `batch_update_workflow_environment`）

用于把工作流批量切到另一个 DS 环境（`environmentCode`，例如 `dim_feature_dic -ds_develop` 切环境）。

### 为什么需要专用能力

`append_task` / `update_task` / `delete_task` 这类结构修改动作会**从请求 payload 重建整个工作流定义表单**，
因此当线上定义的 `globalParams` 已经为空、但任务脚本仍引用 `${dt}` 等变量时，网关会强制拒绝：

```text
workflow global params are empty but tasks still reference required workflow variables
```

**环境切换不需要重建任何东西**：本动作把线上定义原样读出来、原样写回去，只改 `environmentCode`。
所以它既能穿过上面那道门禁，又不会丢任何全局参数。

### 防丢参数（anti-wipe）保证

1. `globalParams` 从线上定义**逐字回写**，绝不用 payload 里的默认值重建。
2. 如果 `globalParams` / `globalParamList` 读取结果不一致，或某个值不是合法 JSON（说明这次读取不可信），
   直接返回 `GLOBAL_PARAMS_UNREADABLE` 并**拒绝写入**，而不是把参数写没。
3. 写后回读校验：每个任务的 `environmentCode`、全局参数名集合、任务数量都必须和预期一致；不一致自动回滚到原定义并再次校验。
4. 原本已经是「全局参数为空 + 任务引用变量」的工作流**允许**切换（因为回写是逐字的，不会变得更糟），
   但响应会带 `PRE_EXISTING_MISSING_GLOBAL_PARAMS` 警告和 `integrity_warning` 明细。
   需要硬门禁时传 `require_global_params: true`。
5. 没有任何任务需要改时不提交定义（避免无意义的版本变更）。

### `update_workflow_environment`

必填：`project_code`（或 `project_name`）、`workflow_code`、`environment_code`。

可选：

| 参数 | 默认 | 说明 |
|---|---|---|
| `dry_run` | `true` | **默认只预演不写入**；必须显式传布尔 `false` 才真正切换 |
| `include_schedule` | `false` | 一并切换该工作流定时的 `environmentCode` |
| `restore_original_state` | `true` | 改完恢复原来的上下线状态 |
| `auto_offline` | `true` | 先下线再改（DS 要求） |
| `require_global_params` | `false` | `true` 时全局参数缺失直接阻断，而不是只告警 |

```json
{
  "project_code": "158514956085248",
  "workflow_code": "174599383687393",
  "environment_code": "12813621425120",
  "dry_run": true
}
```

响应关键字段：`status`（`DRY_RUN_MATCHED` / `UPDATED` / `SKIPPED_ALREADY_MATCHED` /
`VERIFICATION_FAILED_ROLLED_BACK` / `FAILED_ROLLBACK_FAILED` / `FAILED_UNCHANGED`）、
`changed_tasks`、`global_params_preserved`、`warnings`、`verification`、`rollback`、
以及可直接回传的 `rollback_payload`。

### `batch_update_workflow_environment`

必填：`project_code`、`workflow_codes`（唯一非空数组）、`environment_code`。

安全约束：

- `dry_run` 默认 `true`，只有布尔 `false` 才进入正式切换。
- **两阶段执行**：先对全部工作流做零写入预检（逐个校验读取可信度），任一工作流预检失败则整批中止、零写入；
  预检通过后才逐条串行切换。
- 单条失败不掩盖其它结果，逐条状态与汇总计数一并返回。

逐条状态：`DRY_RUN_MATCHED / UPDATED / SKIPPED_ALREADY_MATCHED / FAILED_UNCHANGED /
VERIFICATION_FAILED_ROLLED_BACK / FAILED_ROLLBACK_FAILED / NOT_REQUESTED / SCHEDULE_SNAPSHOT_UNRELIABLE`。

汇总字段：`total / matched / updated / skipped / failed / verification_failed / rolled_back / rollback_failed / schedule_failed`。

`schedule_failed` 单独计数：工作流本身切换成功、但被请求的定时切换失败（`FAILED_UNCHANGED` /
`VERIFICATION_FAILED_ROLLED_BACK` / `FAILED_ROLLBACK_FAILED`）时也会 +1，
避免「工作流全绿」掩盖定时没切成功。单条结果里同名布尔字段含义相同。

### 读回可信度与告警

| 警告 | 含义 |
|---|---|
| `GLOBAL_PARAMS_UNREADABLE`（错误，阻断） | `globalParams` / `globalParamList` / `globalParamMap` 有一项不是合法 JSON 或类型不对，本次读取不可信，**拒绝写入** |
| `GLOBAL_PARAMS_ABSENT_ASSUMED_EMPTY` | 响应里完全没有全局参数字段，按「确实没有」处理并写回空列表；出现它说明这次判断依赖了「字段缺失」这一假设 |
| `GLOBAL_PARAMS_FROM_MAP_FALLBACK` | 只有 `globalParamMap`，据此重建参数列表 |
| `GLOBAL_PARAMS_MAP_EXTRA_NAMES` | 有仅存在于 `globalParamMap` 的参数名，不会被写回；这些名字在 `global_params_map_only` 里列出 |
| `PRE_EXISTING_MISSING_GLOBAL_PARAMS` | 该工作流本来就「全局参数为空 + 任务引用变量」，不是本次造成的 |

响应里 `global_params_preserved` 是**实际写回**的参数名（写后校验也用它作为期望集合），
`global_params_map_only` 是只存在于 map、未写回的名字。
`rollback_payload.restorable` 只有在所有任务本来就有**同一个**环境编码时才为 `true`；
有任务原本没有环境编码时为 `false`（否则回滚会给它写上一个从未有过的编码）。

### 状态与错误码速查

`update_workflow_environment` 的 `status`：

| status | 含义 | 是否已写库 |
|---|---|---|
| `DRY_RUN_MATCHED` | 预演：有任务需要改 | 否 |
| `UPDATED` | 已切换且写后回读校验通过 | 是 |
| `SKIPPED_ALREADY_MATCHED` | 任务已是目标环境，未提交定义 | 否 |
| `FAILED_UNCHANGED` | DS 拒绝了本次写入 | 否 |
| `VERIFICATION_FAILED_ROLLED_BACK` | 写后校验不通过，已回滚且回滚校验通过 | 否（已回滚） |
| `FAILED_ROLLBACK_FAILED` | 写后校验不通过，且回滚也未通过 | **需人工介入** |

批量动作整体 `status` 为 `BATCH_COMPLETED`；预检失败时为 `BATCH_PREFLIGHT_FAILED`（整批零写入，
失败原因在 `errors` 里逐条列出）。

`schedule.status`：`NO_SCHEDULE`（该工作流无定时）、`NOT_REQUESTED`（定时环境与目标不同但未请求切换）、
`SCHEDULE_READ_FAILED`、`SCHEDULE_SNAPSHOT_UNRELIABLE`（读取不可信，拒绝写入）、
`SKIPPED_ALREADY_MATCHED`、`DRY_RUN_MATCHED`、`UPDATED`、`FAILED_UNCHANGED`、
`VERIFICATION_FAILED_ROLLED_BACK`、`FAILED_ROLLBACK_FAILED`。

错误码：`PROJECT_CODE_REQUIRED`、`WORKFLOW_CODE_REQUIRED`、`ENVIRONMENT_CODE_REQUIRED`、
`INVALID_WORKFLOW_CODES`、`INVALID_RATE_LIMIT`、`INVALID_BOOLEAN_FIELD`、
`GLOBAL_PARAMS_UNREADABLE`、`GLOBAL_PARAMS_REQUIRED`、`OFFLINE_BEFORE_UPDATE_FAILED`、
`WORKFLOW_UPDATE_FAILED`；批量预检的逐条原因另有 `WORKFLOW_READ_FAILED`、
`WORKFLOW_DETAIL_EMPTY`、`WORKFLOW_HAS_NO_TASKS`。

### 定时（schedule）环境字段

定时记录自己也有 `environmentCode`，定时跑起来时用的是它。本动作**默认不动定时**，只检查并在不一致时返回
`SCHEDULE_ENVIRONMENT_NOT_SWITCHED` 警告；需要一起切就传 `include_schedule: true`。
切换定时时会用只替换 `environmentCode` 的完整表单写回（cron、起止时间、时区、告警组、优先级、worker group、
tenant、startParams 全部保留），并且读取不可信（缺 cron / 起止时间）时拒绝写入。

### 生产门禁建议

与定时告警一致：单国家单工作流 `dry-run` → 单条正式切换 → 回读校验 → 用 `rollback_payload` 回滚 → 再次回读，
确认完全正确后再扩大批量。


## 新增：定时任务失败监控

### check_failed_instances

检查指定项目中有哪些定时任务出现了连续失败，以及当天尚未恢复的失败实例。

推荐 payload：



参数说明：

- : 可选，默认使用国家配置中的 project_code
- : 可选数组，指定要检查的工作流 code；不传则检查项目下所有有定时的工作流
- : 可选，默认 3，连续失败多少次判定为卡住
- : 可选，默认 20，每个工作流拉取最近的实例数量

返回说明：



每个 stuck_workflow 包含：

- : 工作流 code
- : 工作流名称
- : 定时配置 ID
- : 定时状态（ONLINE/OFFLINE）
- : 连续失败次数
- : 检查的实例总数
- : 最近失败的实例详情（含 instance_id、schedule_time、end_time、state）

当请求包含 `include_failed_workflows: true` 时，响应还会返回
`failed_workflows`。每项均为在线定时工作流当天的最新未恢复失败；若失败后重跑
（`START_FAILURE_TASK_PROCESS`）仍失败，也会作为同一条失败链路返回。普通手动启动
的失败不会进入该列表。若失败之后已有成功实例，则不会告警。

若调度列表的状态与工作流详情中的 `scheduleReleaseState` 冲突，网关仅在当天存在
候选失败时回查详情并以详情状态为准，避免列表缓存滞后造成漏报。


## 已知问题

- `extract_task_runtime_config` 在任务定义刚被 `update_sql_task` / `update_shell_task` 更新后的短时间内，
  个别环境可能仍返回旧的 `task_params.rawScript / sql` 视图。
- 如需做变更后的强校验，当前优先使用 `get_workflow` 或 `dump_workflow_graph` 回读任务定义。

## 访问控制（用户权限与极端情况管控）

网关内置轻量访问控制：每次请求在执行前先做权限与限额校验，命中即拒绝，防止
**大量新建/删除**、**未授权删除**等违规操作。

- 校验模块：`gateway/access.py`（仅标准库）。
- 策略文件：`config/access_policy.json`（由值班平台「DS网关使用统计 → 用户权限与管控」生成并下发；
  示例见 `config/access_policy.example.json`；该文件与状态文件 `config/access_state.json` 已加入
  `.gitignore`，`git clean -fd` 不会误删）。
- 判定优先级：用户被禁用 → 动作黑名单 → 动作白名单 → 角色/删除开关 → 限额（小时/日滚动计数）。
- 兜底：环境变量 `DS_ACCESS_DISABLE=1` 可紧急放行；策略 `enforce: false` 只保存不拦截。

动作分类与角色默认权限、限额定义见 `gateway/access.py`，与值班平台 `src/ds-scheduler-access.mjs` 保持一致。
下发与部署流程见值班平台仓库文档 `docs/ds-scheduler-access-control.md`。

```bash
# 网关侧测试
python3 -m unittest tests.test_access -v
```

## 目录结构

```text
.
├── README.md
├── requirements.txt
├── config/
│   ├── countries.example.json
│   └── countries.json
├── gateway/
│   ├── __init__.py
│   ├── main.py
│   ├── models.py
│   ├── response.py
│   ├── router.py
│   └── utils.py
├── clients/
│   ├── __init__.py
│   └── dolphinscheduler_client.py
├── handlers/
│   ├── __init__.py
│   └── workflow_handlers.py
└── scripts/
    └── ds_scheduler_entry.py
```

## 请求模型

项目入口脚本接收这些参数：

- `--country`
- `--action`
- `--ds-token`
- `--request-id`
- `--payload-b64`

其中 `payload-b64` 是 webhook 中 `payload` 的 base64 编码，避免命令行转义问题。

## 配置说明

默认读取：

```text
config/countries.json
```

可以通过环境变量覆盖：

```bash
export DS_COUNTRIES_CONFIG=/root/ds-scheduler-gateway/config/countries.json
```

`countries.json` 当前已经预置了 6 个国家的基础配置：

- `cn`
- `ine`
- `mx`
- `ph`
- `pk`
- `th`

其中 `trigger_workflow` 依赖的 `environment_code` / `tenant_code` / `worker_group` /
`start_endpoint` / `start_code_field` 现在支持多层覆盖，优先级如下：

1. 请求 payload 显式传入
2. `workflow_overrides[workflow_code]`
3. `project_overrides[project_code]`
4. `action_overrides[action]`
5. 国家默认配置

这样做的目的是兼容“同一个国家里，不同工作流或不同触发方式使用不同运行参数”的情况，
同时不影响你现有的 n8n 调用方式。

## 当前内置国家配置来源

这些值来自各国现有 `Intelligent-Alarm-Repair-Assistant` 仓库中的运行配置：

- `cn`: `DS_BASE_URL=http://172.20.0.235:12345/dolphinscheduler`
- `ine`: `DS_BASE_URL=http://192.168.21.236:12345/dolphinscheduler`
- `mx`: `DS_BASE_URL=http://172.20.220.165:12345/dolphinscheduler`
- `ph`: `DS_BASE_URL=http://127.0.0.1:12345/dolphinscheduler`
- `pk`: `DS_BASE_URL=http://10.20.84.176:12345/dolphinscheduler`
- `th`: `DS_BASE_URL=http://192.168.102.7:12345/dolphinscheduler`

说明：

- `ph` 的 DS 地址是 `127.0.0.1`，意味着网关脚本需要部署在菲律宾目标机本机执行
- `pk` 的 `trigger_workflow` 使用 `start-workflow-instance` + `workflowDefinitionCode`
- 其余国家当前按 DS 3.4 标准 `start-process-instance` + `processDefinitionCode` 处理

## 本地示例

```bash
cd /root/ds-scheduler-gateway && python3 scripts/ds_scheduler_entry.py \
  --country cn \
  --action list_workflows \
  --ds-token 'your_token' \
  --request-id 'req-001' \
  --payload-b64 'eyJwYWdlX25vIjoxLCJwYWdlX3NpemUiOjIwfQ=='
```

## n8n 中国节点示例

```bash
ssh -p 36000 root@10.20.47.14 "cd /root/ds-scheduler-gateway && python3 scripts/ds_scheduler_entry.py --country cn --action '{{$json.action}}' --ds-token '{{$json.ds_token}}' --request-id '{{$json.request_id}}' --payload-b64 '{{$json.payload_b64}}'"
```

## n8n 多国家命令模板

把下面命令中的 SSH 主机替换成各国真实目标机即可：

```bash
ssh -p <port> root@<country-host> "cd /root/ds-scheduler-gateway && python3 scripts/ds_scheduler_entry.py --country <country> --action '{{$json.action}}' --ds-token '{{$json.ds_token}}' --request-id '{{$json.request_id}}' --payload-b64 '{{$json.payload_b64}}'"
```

示例：

- 中国：

```bash
ssh -p 36000 root@10.20.47.14 "cd /root/ds-scheduler-gateway && python3 scripts/ds_scheduler_entry.py --country cn --action '{{$json.action}}' --ds-token '{{$json.ds_token}}' --request-id '{{$json.request_id}}' --payload-b64 '{{$json.payload_b64}}'"
```

- 泰国：

```bash
ssh -p 36000 root@192.168.20.236 "cd /root/ds-scheduler-gateway && python3 scripts/ds_scheduler_entry.py --country th --action '{{$json.action}}' --ds-token '{{$json.ds_token}}' --request-id '{{$json.request_id}}' --payload-b64 '{{$json.payload_b64}}'"
```

- 巴基斯坦：

```bash
ssh -p 36000 root@<pk-host> "cd /root/ds-scheduler-gateway && python3 scripts/ds_scheduler_entry.py --country pk --action '{{$json.action}}' --ds-token '{{$json.ds_token}}' --request-id '{{$json.request_id}}' --payload-b64 '{{$json.payload_b64}}'"
```

## 落地建议

第一版建议：

1. 先把 `ds-scheduler-gateway` 部署到 6 个国家实际执行机
2. 按国家校验 `countries.json` 中的 `environment_code` / `tenant_code`
3. 在 n8n 中把每个国家分支接到对应跳板机命令
4. 逐个国家先测 `list_workflows`
5. 再统一回归测试 `trigger_workflow`
6. 后续再扩展：
   - 创建工作流
   - 更新工作流定义
   - 上下线 schedule
   - 停止实例
   - 查询任务节点详情

## 查询任务实例与运行日志

### 查询某次工作流实例里的任务实例

`list_task_instances` 用于查看某个工作流实例内部的任务执行明细。

推荐 payload：

```json
{
  "project_code": "158514956085248",
  "instance_id": "1040772",
  "page_no": 1,
  "page_size": 100
}
```

也支持直接传：

- `process_instance_id`
- `search_val`
- `state_type`

### 拉取任务运行日志

`get_task_log` 支持两种调用方式：

1. 直接传 `task_instance_id`
2. 传 `instance_id/process_instance_id + task_name/task_code`，由网关先解析任务实例，再拉日志

推荐 payload：

```json
{
  "project_code": "158514956085248",
  "instance_id": "1040772",
  "task_name": "dwb_user_info"
}
```

返回里会尽量带上：

- `task_instance_id`
- `process_instance_id`
- `task_name`
- `task_code`
- `state`
- `host`
- `log_path`
- `log`
- `log_endpoint_used`

## 新增 SQL 任务

`append_sql_task` 会先读取当前工作流定义，然后：

1. 自动寻找当前工作流中的一个 SQL 节点作为模板
2. 继承它的 datasource / tenant / worker / environment 等运行参数
3. 追加一个新的 SQL 任务节点
4. 默认把新节点挂到模板节点后面；也支持显式指定上游节点
5. 更新完成后会恢复到工作流原来的发布状态
6. 如果原工作流存在已上线的定时配置，也会恢复到原来的定时上线状态

推荐 payload：

```json
{
  "project_code": "19427088052704",
  "workflow_code": "174599383687393",
  "task_name": "测试2",
  "sql": "select 2",
  "template_task_name": "dwd_okr_dashboard"
}
```

说明：

- `task_name`: 新任务名，必填
- `sql`: 新 SQL 文本，必填
- `template_task_name`: 可选。建议显式指定一个现有 SQL 节点名，避免模板选择错误
- `upstream_task_name`: 可选。显式指定把新节点挂到哪个已有节点后面
- `upstream_task_code`: 可选。优先级高于 `upstream_task_name`
- `restore_original_state`: 可选，默认 `true`
- `auto_offline`: 可选，默认 `true`
- `sql_type`: 可选。推荐传 `query` / `non_query`，旧的 `0` / `1` 仍兼容

示例 base64 原文：

```json
{"project_code":"19427088052704","workflow_code":"174599383687393","task_name":"测试2","sql":"select 2","template_task_name":"dwd_okr_dashboard"}
```

## 复制工作流（触发式）

`copy_workflow` 用于把已有工作流复制成**触发式（按需）版本**，适合把「固定 5 分钟轮询」改造成「DS API 按需触发」：新副本完整保留源工作流的任务节点、依赖关系、坐标、全局参数和运行配置，但**不创建任何定时**，且默认直接上线（可触发）。

推荐 payload：

```json
{
  "project_code": "15843450427744",
  "workflow_code": "15843450427744_原工作流code",
  "workflow_name": "DWD_5M_TRIGGER",
  "release_workflow": true
}
```

说明：

- `project_code` / `project_name`: 目标项目（默认取国家配置项目）
- `workflow_code`: 源工作流 code，必填
- `workflow_name`: 新工作流名称，必填，需在项目内唯一
- `release_workflow`: 可选，默认 `true`，创建后立即上线（触发式工作流需 ONLINE 才能被 API 触发）
- 副本会重新生成任务 / 关系 / 坐标 code，避免与源工作流冲突
- `schedule_created` 恒为 `false`，`trigger_style` 恒为 `true`

返回重点：

- `source_workflow_code` / `source_workflow_name`
- `workflow_name` / `workflow_code`（新副本）
- `task_definition_count` / `task_relation_count` / `location_count`
- `release_state`（`ONLINE` / `OFFLINE` / `ONLINE_ATTEMPT_FAILED`）

## 按白名单禁用任务

`disable_tasks_except` 会读取当前工作流定义，并把命中目标范围、但不在保留白名单内的任务统一改成 `flag=NO`。

推荐 payload：

```json
{
  "project_code": "13068695921632",
  "workflow_code": "13068714157024",
  "target_task_name_prefixes": ["ods.", "ods_security.", "fox_ods.", "hive.ods.", "bi.fox_ods."],
  "keep_task_names": ["ods.ods_dsp_third_data", "ods.ods_dsp_third_data_h"]
}
```

说明：

- `target_task_name_prefixes`: 可选。只处理这些前缀下的任务；不传则默认所有任务都进入候选范围
- `keep_task_names`: 必填其一。白名单任务名
- `keep_task_codes`: 必填其一。白名单任务 code
- `restore_original_state`: 可选，默认 `true`
- `auto_offline`: 可选，默认 `true`

## 导出工作流图结构

`dump_workflow_graph` 用来排查工作流 DAG 结构问题，返回：

1. `workflow_summary`
2. `task_definitions`
3. `task_relations`
4. `locations`
5. `raw_workflow_detail`

推荐 payload：

```json
{
  "project_code": "19427088052704",
  "workflow_code": "174599383687393"
}
```
