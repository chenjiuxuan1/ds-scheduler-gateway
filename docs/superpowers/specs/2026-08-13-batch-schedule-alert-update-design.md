# DolphinScheduler 批量修改定时告警配置设计

## 背景与目标

在 `ds-scheduler-gateway` 和 `ds-skill-n8n` 中增加批量修改 DolphinScheduler 定时告警配置的能力。首批业务对象是 `ph`、`mx`、`pk` 三个国家的指定数仓项目，仅修改工作流和定时都处于 `ONLINE` 的记录，将通知策略设为 `FAILURE`，并使用当前国家实时解析得到的 `n8n告警触发器` 告警组 ID。

设计的核心原则是：

- 不依赖旧 `ds_catalog` 中的 project code 或对象状态。
- 不主动下线或上线定时。
- 局部更新必须完整保留非目标字段。
- 正式更新前保存逐条快照，更新或验证失败时立即补偿回滚该条。
- 所有返回和审计数据中都不包含 DS token 明文。

## 范围

### Gateway 代码

修改 `ds-scheduler-gateway` 中的：

- `gateway/utils.py`
- `handlers/workflow_handlers.py`
- `clients/dolphinscheduler_client.py`
- 相关单元测试
- `README.md`

### Skill 与 n8n 契约

修改独立 `ds-skill-n8n` 仓库中的：

- `SKILL.md`
- `REFERENCE.md`
- `EXAMPLES.md`
- `README.md`
- `scripts/build_ds_webhook_payload.py`
- `n8n/request_normalizer.js`
- `n8n/workflow-template.json`
- `n8n/ds-scheduler-router.latest.json`
- 契约对齐与 router artifact 测试

### 首批业务项目

- `ph`：`DW_DWB`, `DW_TEMP`, `DW_MARKET`, `DW_DM`, `DW_DWD`, `DW_DWS`, `DW_ADS`, `DW_PRIVACY`, `DW_DIM`, `DW_DWT`
- `pk`：`DW_DWB_new`, `DW_DM_new`, `DW_MARKET_new`, `DW_DM_STRATEGY_new`, `DW_DWD_new`, `DW_DWS_new`, `DW_RPT_new`, `DW_PRIVACY_new`, `DW_EXPORT_new`, `DW_DWT_new`
- `mx`：`DW_DWB`, `DW_TEMP`, `DW_MARKET`, `DW_DM`, `DW_DWD`, `DW_DWS`, `DW_ADS`, `DW_PRIVACY`, `DW_DIM`, `DW_DWT`

项目名单只是本次运行的输入，不固化在 gateway 业务逻辑中。

## 方案选择

采用“Gateway 服务端预检 + 顺序逐条修改 + 逐条验证 + 失败补偿回滚”。

不采用下列方案：

- 不将批量逻辑分散到 n8n 多个节点，避免验证、回滚和限流语义无法单元测试。
- 不直接修改 DolphinScheduler 元数据库，避免绕过 DS API 状态机和审计链路。
- 不为了修改在线定时而自动下线、上线；如果某国 DS API 不允许在线更新，该条失败并保持原状。

## 动作设计

### `list_alert_groups`

支持国家：`cn`, `ine`, `mx`, `ph`, `pk`, `th`。

输入：

- `search_val`：可选。非空时作为告警组名称的精确查找条件。
- `page_no`：可选，默认 `1`。
- `page_size`：可选，默认 `100`，服务端设置合理上限。

行为：

1. 通过当前国家 DS API 实时查询告警组。
2. 标准化返回 `id`, `group_name`, `description`, `alert_instances`, `raw`。
3. DS API 可提供告警实例时返回实例 ID、名称、类型和启用状态；不可提供时返回空数组，不伪造信息。
4. `search_val` 非空时，服务端必须在拉取结果中再做一次完全相等匹配，不信任 DS API 的模糊搜索语义。

精确查找结果：

- 0 条：`ALERT_GROUP_NOT_FOUND`
- 1 条：成功返回唯一对象
- 多条：`AMBIGUOUS_ALERT_GROUP`，附候选列表，禁止自动选取

### 增强 `update_schedule`

新增对告警字段局部更新的支持：

- `warning_type`：`NONE`, `SUCCESS`, `FAILURE`, `ALL`
- `warning_group_id`：当前国家的告警组 ID

校验规则：

- `update_schedule` 仍需要 `project_code`，以及 `schedule_id` / `workflow_code` 之一。
- 允许仅提供告警字段，此时不再强制要求 `schedule_json` 或 `crontab`。
- `warning_type` 不在语义白名单内时返回 `INVALID_WARNING_TYPE`，不请求 DS API。
- 当用户显式传入 `warning_type != NONE` 时，`warning_group_id` 不能为空。

更新流程：

1. 通过 `get_schedule` 获取完整原始定时记录。
2. 标准化原定时配置，保留 DS API 更新接口需要的全部字段。
3. 仅覆盖请求中显式提供的目标字段。
4. 调用 DS schedule update API，不调用 online/offline API。
5. 通过 `get_schedule` 回查。
6. 验证目标字段、原 release state 和所有非目标字段。

必须保留并验证的字段至少包含：

- schedule id 与 workflow definition code
- `crontab`
- `startTime`, `endTime`, `timezoneId`
- `failureStrategy`
- `workerGroup`
- `tenantCode`
- `environmentCode`
- `processInstancePriority` / `workflowInstancePriority`
- `releaseState`
- `startParams`
- DS API 返回的其他可写原配置字段

返回包含：

- 修改前后的标准化配置
- 完整原始快照
- 验证结果
- 可直接作为 `update_schedule` 输入的 rollback payload

rollback payload 不包含 `country`, `ds_token` 或其他认证信息。调用方恢复时必须重新提供当前国家和 token。

### `batch_update_schedule_alerts`

输入：

- `project_names`：必填、非空、去重后的项目名白名单。
- `workflow_release_state`：本需求只接受 `ONLINE`。
- `schedule_release_state`：本需求只接受 `ONLINE`。
- `warning_type`：必填，支持语义值；首批运行为 `FAILURE`。
- `warning_group_name`：必填；首批运行为 `n8n告警触发器`。
- `dry_run`：默认 `true`，只有严格布尔值 `false` 才允许修改。
- `request_interval_ms`：可选，用于合理限流，服务端限定最小值和最大值。
- `max_retries`：可选，仅适用于可重试的临时网络或 5xx 错误，服务端设小上限。

#### 整国预检

修改前先完成当前国家的整体预检：

1. 通过 `list_alert_groups` 精确解析当前国家告警组。
2. 逐个通过实时 `list_projects` 精确解析项目名。
3. 任一项目缺失或同名多条，整个国家返回预检错误，不执行任何修改。
4. 对每个项目实时拉取全部工作流与定时。
5. 使用 workflow code 关联工作流和定时，不使用模糊名称关联。

项目和告警组的缺失/重名是预检级错误。工作流或定时不在线不是整国错误，而是逐条跳过。

#### 逐条分类

- 工作流或定时不是 `ONLINE`：`SKIPPED_NOT_ONLINE`
- 原配置已是目标 warning type 和 group：`SKIPPED_ALREADY_MATCHED`
- dry run 中会被修改：`DRY_RUN_MATCHED`
- 正式更新并验证成功：`UPDATED`
- 更新请求失败但确认原配置仍在：`FAILED_UNCHANGED`
- 更新请求失败或结果不明，补偿回滚成功：`FAILED_ROLLED_BACK`
- 更新后验证失败，补偿回滚成功：`VERIFICATION_FAILED_ROLLED_BACK`
- 补偿回滚或回滚后验证失败：`FAILED_ROLLBACK_FAILED`

#### 正式更新的逐条事务边界

1. 更新前立即再次获取工作流和定时，检查仍同时为 `ONLINE`。状态已漂移时跳过，不修改。
2. 保存完整原始记录和标准化可写配置。
3. 只覆盖 warning type 和 warning group ID。
4. 调用 DS update schedule API。
5. 无论 update 响应是成功还是结果不明，都回查当前定时。
6. 验证目标字段、原状态和所有非目标字段。
7. 更新或验证失败且当前记录与原快照不同时，立即用原快照执行补偿更新。
8. 补偿更新后再次回查，只有完整恢复才记为回滚成功。

已经更新并验证成功的其他记录不自动回滚。每条成功记录都返回可独立恢复的 rollback payload。

## 限流与重试

- 项目与定时顺序处理，不并发写 DS API。
- 相邻写请求之间使用可配置短间隔。
- 只重试超时、连接中断、HTTP 429 和 5xx。
- 使用带小幅随机抖动的指数退避。
- 不重试 4xx 权限/参数错误、项目/告警组缺失、同名冲突、状态不符和业务验证失败。
- 读请求的重试可单独封装；写请求重试前必须先回查，如果目标值已生效则转入验证，禁止盲目重复写。

## 返回契约

`batch_update_schedule_alerts` 返回至少包含：

- `country`
- `dry_run`
- `warning_group`：当前国家解析得到的 ID 和名称
- `filters`：项目白名单与状态条件
- `items`：逐条结果
- `summary`

每条 `items` 至少包含：

- 项目名与 project code
- 工作流名与 workflow code
- schedule id
- 工作流与定时 release state
- 原 warning type/group
- 目标 warning type/group
- 处理状态与错误（如有）
- 验证结果
- rollback payload（对有原始快照的记录）

`summary` 至少包含：

- `total`
- `matched`
- `updated`
- `skipped`
- `failed`
- `verification_failed`
- `rolled_back`
- `rollback_failed`

## 状态对比与字段标准化

DS 不同国家可能返回 `workflowDefinitionCode` / `processDefinitionCode`、`workflowInstancePriority` / `processInstancePriority` 等字段变体。Gateway 内部建立单一的 schedule snapshot 标准化结构，但保留 raw record 用于调试和兼容。

非目标字段比较时：

- 对 JSON 字符串先解析再比较，避免字段顺序导致误报。
- 数字 ID 统一转成字符串比较。
- 枚举值统一大写。
- 不比较 DS 每次写入必然变化的审计字段，如 `updateTime`。
- 验证允许忽略的字段必须集中列入白名单并通过测试固化。

## Token 与日志安全

- token 只存在进程内存中和发往 DS API 的 `token` header 中。
- Gateway 异常不包含请求 header。
- URL、form、rollback payload 和逐条结果不包含 token。
- n8n 审计数据仅记录 `ds_token_present: true/false`。
- 生成的 artifact 和测试 fixture 不写入真实 token。
- 错误标准化层对已知 token 做二次替换脱敏，防止底层库将 header 意外嵌入异常文本。

## n8n 与 Builder 校验

### 新增字段

- `project_names`
- `workflow_release_state`
- `schedule_release_state`
- `warning_group_name`
- `dry_run`
- `request_interval_ms`
- `max_retries`

### 动作校验

- `list_alert_groups`：只要求 country 和 token；分页参数必须为正整数。
- `update_schedule`：必须有 project code 及 schedule/workflow 定位字段；允许 schedule JSON、crontab 或任一告警字段作为更新内容。
- `batch_update_schedule_alerts`：必须有非空 project names、合法 warning type 和非空 warning group name。
- `dry_run` 在 n8n 中保留严格布尔类型，不把字符串 `"false"` 解释为正式执行。

## 测试设计

### Gateway 单元测试

使用 fake client/request 覆盖：

- 告警组分页和标准化。
- 告警组精确匹配的 0/1/多条。
- warning type 语义值校验与 DS 形态转换。
- 仅告警字段更新时的完整配置合并。
- ONLINE 和 OFFLINE 状态均保持不变。
- 非目标字段验证。
- 项目缺失/重名、告警组缺失/重名的整国预检终止。
- `SKIPPED_NOT_ONLINE` 和 `SKIPPED_ALREADY_MATCHED`。
- dry run 零写请求。
- 更新成功、更新超时但已生效、验证失败后回滚成功、回滚失败。
- 写前状态漂移。
- 重试分类，确保校验错误不重试。
- rollback payload 完整性和 token 不泄漏。

### Skill / n8n 契约测试

- Gateway、builder、request normalizer、workflow template 与 latest artifact 动作集完全对齐。
- Builder 能生成三个新语义的请求体。
- Normalizer 保留数组和布尔类型，拒绝错误字段。
- Workflow template 的内嵌 normalizer 与源文件一致。
- 审计节点不存储 token 明文。

## 生产实测门禁

实测必须使用用户当次提供的当前国家 token，不从日志、文件或历史请求提取 token。

每个国家进入批量前，先在单项目、单条定时执行：

1. `list_alert_groups`，精确解析 `n8n告警触发器`。
2. `batch_update_schedule_alerts` + `dry_run=true`。
3. 单条 `update_schedule` 正式更新。
4. `get_schedule` 回查。
5. 使用返回的 rollback payload 恢复。
6. `get_schedule` 再次回查，确认目标字段、非目标字段和 release state 均恢复。

单条闭环成功后，才允许对当前国家的完整项目白名单执行 dry run。三国 dry run 结果由人工复核并明确确认后，才执行 `dry_run=false`。

## 部署与回退

1. 先在本地两个仓库完成测试和 artifact 对齐。
2. 先部署 gateway，再发布 n8n workflow，避免 n8n 提前放行远端未支持动作。
3. 按国家逐个确认远端 gateway 版本。
4. 使用生产实测门禁验证。
5. 应用回退优先通过 Git 回退 gateway 和 n8n workflow 版本；业务数据回退通过每条返回的 schedule rollback payload 执行。

## 验收标准

- 三个动作在 gateway、builder、n8n normalizer 和 artifacts 中一致可用。
- 告警组必须当国实时解析，缺失或重名时零修改。
- 仅修改同时满足工作流 `ONLINE` 和定时 `ONLINE` 的记录。
- dry run 不产生任何 DS 写请求。
- 仅更新 warning type/group，非目标字段和定时 release state 不变。
- 每条更新都有快照、回查、验证和 rollback payload。
- 更新或验证失败时，该条尽可能自动恢复原配置，并明确报告回滚失败。
- token 不出现在日志、错误、artifact、rollback payload 或持久化审计数据中。
- 未完成单条闭环验证前，禁止任何国家批量正式更新。
