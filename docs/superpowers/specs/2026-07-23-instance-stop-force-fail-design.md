# DolphinScheduler 实例停止与强制失败能力设计

## 背景

`ds-scheduler-gateway` 已支持实例查询、任务日志和失败实例重跑，但缺少对运行中实例的停止能力。串行工作流出现长时间运行或等待时，后续实例会持续排队，当前只能由用户进入 DolphinScheduler 页面人工停止。

本次新增两个统一动作，并同步更新 `ds-skill-n8n` 的 Router、命令构造器、Skill 契约和使用文档：

- `stop_instance`
- `force_fail_instance`

六个国家 `cn / ine / mx / ph / pk / th` 均开放相同的请求契约。

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

1. 在 `n8n/request_normalizer.js` 和工作流模板中放行两个动作。
2. 对两个动作校验 `project_code` 与 `instance_id`。
3. 在 `scripts/build_ds_webhook_payload.py` 中加入动作、参数校验和可执行示例。
4. 更新 `SKILL.md`、`README.md`、`REFERENCE.md`、`EXAMPLES.md`、`n8n/README.md` 和中文快速上手文档。
5. 文档明确说明：
   - 两个动作会改变运行实例状态；
   - 必须由用户提供 `ds_token`；
   - 执行前必须获得用户对具体实例的明确确认；
   - `force_fail_instance` 可能按国家返回 `UNSUPPORTED`；
   - 不存在直接修改元数据库的降级路径。
6. 基于用户提供的最新 `ds-scheduler-router (1).json` 生成或更新可导入 Router 产物，保留现有审计和六国分流结构。

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

`ds-skill-n8n` 测试覆盖：

- request normalizer 接受两个新动作。
- 必填字段校验。
- payload builder 生成正确请求。
- workflow JSON 可解析并保留六国路由。
- 文档示例与实际 CLI 参数一致。

## 验收标准

1. 六国接受统一的 `stop_instance` 和 `force_fail_instance` 请求格式。
2. `stop_instance` 可通过官方 API 停止可停止状态的实例，并返回操作前后状态。
3. 未验证强制失败 API 的国家稳定返回 `UNSUPPORTED`。
4. 代码中不存在修改 DS 元数据库状态的路径。
5. n8n 可导入产物、Skill 契约、CLI 和全部文档保持一致。
6. 自动化测试通过，且不需要连接生产环境。
