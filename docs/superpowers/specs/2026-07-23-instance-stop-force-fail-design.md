# DolphinScheduler 实例停止与强制失败能力设计

## 背景

`ds-scheduler-gateway` 已支持实例查询、任务日志和失败实例重跑，但缺少对运行中实例的停止能力。串行工作流出现长时间运行或等待时，后续实例会持续排队，当前只能由用户进入 DolphinScheduler 页面人工停止。

本次新增两个统一动作，并同步更新 `ds-skill-n8n` 的 Router、命令构造器、Skill 契约和使用文档：

- `stop_instance`
- `force_fail_instance`

六个国家 `cn / ine / mx / ph / pk / th` 均开放相同的请求契约。

同时修复基线 Router 中已经放行、但代码仓库尚未全链路对齐的动作：

- 在 `ds-scheduler-gateway` 中正式实现 `resolve_project`。
- 在 `ds-skill-n8n` 命令构造器中补齐 `resolve_project`、`check_failed_instances`、`find_resource_usage` 和 `search_country_git_sql`。
- 保留 `find_resource_usage` 与 `search_country_git_sql` 的 n8n 六国特殊执行链路，不错误迁移到 Gateway 主分发器。

完成后 Router 共放行 41 个动作，每个动作必须在 Gateway 主链路或有明确实现的 n8n 特殊链路中可达，并在 Skill/CLI 文档中有一致契约。

## 安全边界

1. 只调用 DolphinScheduler 官方 HTTP API。
2. 不读取、更新或绕过 DolphinScheduler 元数据库来改变实例状态。
3. 不通过试探性变更请求判断某个国家是否支持强制失败。
4. `force_fail_instance` 没有已配置且已验证的官方 API 映射时，返回 `UNSUPPORTED`。
5. `force_fail_instance` 不得降级为 `stop_instance`，避免调用方误判最终状态。
6. 本次代码交付不包含自动部署，也不直接操作生产实例。

## 请求契约

两个动作共用以下请求：

```json
{
  "source": "codex-skill",
  "country": "mx",
  "action": "stop_instance",
  "ds_token": "USER_PROVIDED_TOKEN",
  "request_id": "20260723-001",
  "payload": {
    "project_code": "13068695921632",
    "instance_id": "123456"
  }
}
```

必填字段：

- `country`
- `action`
- `ds_token`
- `payload.project_code`
- `payload.instance_id`

## 现有动作全链路规范化

### `resolve_project`

`resolve_project` 在 Gateway 中提供正式实现：

1. 接受 `project_code` 或项目名称查询值。
2. 显式传入 `project_code` 时，优先通过官方项目详情或项目列表验证。
3. 按名称解析时，只接受唯一的精确名称匹配。
4. 没有匹配返回 `PROJECT_NOT_FOUND`。
5. 多个匹配返回 `AMBIGUOUS_PROJECT`，并返回候选项目的非敏感摘要。
6. 成功响应统一返回项目名称和字符串形式的 `project_code`。

Router 和 CLI 的字段名必须与 Gateway 一致，不允许依赖未记录的隐式字段。

### n8n 特殊动作

- `find_resource_usage` 继续由六国 SSH 节点中的只读资源引用反查脚本处理。
- `search_country_git_sql` 继续由六国 SSH 节点中的只读 Git 搜索脚本处理。
- 两个动作必须出现在 CLI、Skill 契约和文档中，并通过 Router 静态测试证明特殊分支仍存在。
- `check_failed_instances` 走 Gateway 主链路，补齐 CLI 和文档入口。

## 能力矩阵

各国配置增加实例动作映射。映射描述官方 API 所需的执行类型，不允许配置 SQL 或任意脚本。

```json
{
  "instance_action_capabilities": {
    "stop_instance": {
      "supported": true,
      "execute_type": "STOP"
    },
    "force_fail_instance": {
      "supported": false
    }
  }
}
```

默认规则：

- `stop_instance` 默认支持，官方执行类型为 `STOP`。
- `force_fail_instance` 默认不支持。
- 只有某国的官方 API 行为经过验证后，才能在该国配置中启用 `force_fail_instance` 并指定官方执行类型。

配置解析需保持向后兼容，旧版 `countries.json` 没有此字段时仍可正常启动。

## 网关执行流程

### `stop_instance`

1. 校验 `project_code` 和正整数 `instance_id`。
2. 调用 `get_instance` 获取操作前状态。
3. 若实例已经停止，返回幂等成功，不再次发送变更请求。
4. 若实例已经成功、失败或完成，返回 `INVALID_INSTANCE_STATE`。
5. 根据国家能力配置调用官方 `executors/execute` 接口，发送 `executeType=STOP`。
6. 对实例详情做有界短轮询，等待状态进入停止终态。
7. 若操作已被官方接受但轮询未收敛，返回“已接受、状态待收敛”，不能伪报停止成功。

### `force_fail_instance`

1. 校验请求并读取实例当前状态。
2. 查询国家能力矩阵。
3. 未配置、被禁用或缺少官方执行类型时，返回：

```json
{
  "success": false,
  "error": {
    "code": "UNSUPPORTED",
    "message": "force_fail_instance is not supported by the official API for country mx"
  }
}
```

4. 仅在映射已启用时调用官方实例执行 API。
5. 有界短轮询并返回官方最终状态；不进行数据库兜底。

## 状态和响应

成功或已接受响应至少包含：

- `country`
- `project_code`
- `instance_id`
- `action`
- `execute_type`
- `previous_state`
- `final_state`
- `accepted`
- `converged`
- `result`

错误码：

- `INVALID_REQUEST`
- `INSTANCE_NOT_FOUND`
- `INVALID_INSTANCE_STATE`
- `UNSUPPORTED`
- `DS_API_ERROR`

网关沿用现有统一响应包装，不将 token、认证头或内部凭据写入响应。

## n8n 与 Skill 同步

`ds-skill-n8n` 需要同步：

1. Router 修改必须以用户提供的 `/Users/jiangchuanchen/Downloads/ds-scheduler-router (2).json` 为唯一基线。该文件的 SHA-256 为 `16009d22a58df418684adfec09338ee804c6216c641e11cc1373ceb3baac4361`。
2. 在 `n8n/request_normalizer.js` 和工作流模板中放行两个动作。
3. 对两个动作校验 `project_code` 与 `instance_id`。
4. 在 `scripts/build_ds_webhook_payload.py` 中加入动作、参数校验和可执行示例。
5. 更新 `SKILL.md`、`README.md`、`REFERENCE.md`、`EXAMPLES.md`、`n8n/README.md` 和中文快速上手文档。
6. 文档明确说明：
   - 两个动作会改变运行实例状态；
   - 必须由用户提供 `ds_token`；
   - 执行前必须获得用户对具体实例的明确确认；
   - `force_fail_instance` 可能按国家返回 `UNSUPPORTED`；
   - 不存在直接修改元数据库的降级路径。
7. 直接在 `(2).json` 的“解析并标准化请求”节点中追加动作和字段校验；保留其现有 `resolve_project` 动作、24 个节点、19 组连接、六国代码拉取/执行分支和完整审计链路。
8. 不重建节点，不更换节点 ID，不移动节点，不覆盖 `(2).json` 中比 `(1).json` 新增的 `resolve_project` 能力。
9. 输出新的可导入 Router JSON，同时保留原始 `(2).json` 不变，便于逐项比较和回滚。
10. 对 Router 的 41 个动作执行静态能力对账：
    - Gateway 主链路动作必须同时存在于 `SUPPORTED_ACTIONS` 和 handler。
    - 特殊动作必须在对应的六国 n8n SSH 节点中存在实现。
    - 所有动作必须被命令构造器、Skill 契约和参考文档收录。

## 远端 Git 交付

在全部本地测试通过后：

1. `ds-scheduler-gateway` 的实现、测试、配置样例和文档提交到 `chenjiuxuan1/ds-scheduler-gateway`。
2. `ds-skill-n8n` 的 Router 模板、命令构造器、Skill 契约、示例和文档提交到 `chenjiuxuan1/ds-skill-n8n`。
3. 推送前拉取并确认远端分支没有新提交；若远端已前进则停止推送，先安全整合，不覆盖远端历史。
4. 不使用强制推送。
5. 可导入 Router JSON 同时保存为 `ds-skill-n8n` 仓库产物和当前任务 `outputs` 交付物。
6. 交付结果列出两个远端提交 SHA、Router 文件 SHA-256 和测试结果。

## 测试策略

网关单元测试覆盖：

- 两个动作进入正确 handler。
- 缺少项目或实例 ID 时拒绝。
- `stop_instance` 发送官方 `STOP`。
- 已停止实例幂等成功。
- 已完成实例返回 `INVALID_INSTANCE_STATE`。
- `force_fail_instance` 默认返回 `UNSUPPORTED`。
- 只有显式能力配置能启用强制失败。
- 操作后状态收敛与超时未收敛响应。
- 六国旧配置无新增字段时保持兼容。
- `resolve_project` 按 code、唯一精确名称、未找到和名称歧义的行为。
- Gateway 主链路动作与 handler 映射完整一致。

`ds-skill-n8n` 测试覆盖：

- request normalizer 接受两个新动作。
- 必填字段校验。
- payload builder 生成正确请求。
- workflow JSON 可解析并保留六国路由。
- 修改后的 Router 仍包含 `(2).json` 的 `resolve_project`，节点数和连接关系不变。
- 除“解析并标准化请求”节点中两个新动作及其校验外，Router 的已有节点参数、节点 ID、位置和连接均与 `(2).json` 一致。
- 文档示例与实际 CLI 参数一致。
- Router 41 个动作的全链路能力矩阵没有未实现或未记录项。
- `resolve_project`、`check_failed_instances`、`find_resource_usage` 和 `search_country_git_sql` 均可由命令构造器生成合法请求。

## 验收标准

1. 六国接受统一的 `stop_instance` 和 `force_fail_instance` 请求格式。
2. `stop_instance` 可通过官方 API 停止可停止状态的实例，并返回操作前后状态。
3. 未验证强制失败 API 的国家稳定返回 `UNSUPPORTED`。
4. 代码中不存在修改 DS 元数据库状态的路径。
5. n8n 可导入产物、Skill 契约、CLI 和全部文档保持一致。
6. 自动化测试通过，且不需要连接生产环境。
7. Router 产物可证明由指定 `(2).json` 增量修改而来，未丢失 `resolve_project` 或现有审计能力。
8. Router 放行的 41 个动作全部能映射到 Gateway handler 或已验证存在的 n8n 特殊实现。
9. 两个 GitHub 仓库包含经测试的最新代码和文档，且提交历史未被强制覆盖。
