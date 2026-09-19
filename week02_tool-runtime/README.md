## 环境准备

推荐使用 Python 3.12。项目要求 Python 3.10+；当前本地 `.venv` 若为 Python 3.14，运行测试时可能出现 `pytest-asyncio` 的 deprecation warning，不影响功能。

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

测试命令执行

```bash
python -m pytest tests/test_tool_governance.py -v -k "transfer"
```

## 这个模块是什么

`tool_governance_demo.py` 是一个不依赖真实模型的「工具治理与权限状态机」演示：
从不可信参数进来到结果回到模型，中间每一道边界都是确定性代码，而不是提示词。

核心主张只有一句：**模型只能提交候选参数，能不能执行由治理层决定**。

| 文件 | 作用 |
| --- | --- |
| `tool_governance_demo.py` | 治理框架 + 4 个演示工具（`get_order` / `create_refund` / `run_shell` / `transfer`） |
| `tests/test_tool_governance.py` | 转账工具 5 个链路用例（参数校验 / 预检 / 权限 / 审批+脱敏 / 超时） |
| `conftest.py` | 让 pytest 从项目根目录导入模块 |

## 核心设计

### 1. 唯一执行入口

任何工具调用都必须经过 `ToolRuntime.invoke()`——模型、CLI、测试、未来的 Provider 都一样。
好处是治理、审计、脱敏只有一份实现，绕不过去。

### 2. 五段式调度流程

```text
        ToolCall(name, arguments, tool_call_id) + ExecutionContext
                              │
                              ▼
        ┌───────────────────────────────────────────────────────┐
        │  ToolRuntime.invoke()   ← 唯一执行入口                 │
        └───────────────────────────────────────────────────────┘
                              │
   ① resolve ────────────────┤ _tools.get(call.name)
                              ├── 不在注册表 ──► DENY  TOOL_NOT_FOUND
                              │
   ② prepare ────────────────┤ parameters_model.model_validate(不可信 dict)
   (Pydantic)                 ├── 校验失败 ───► DENY  INVALID_ARGUMENT
                              │        extra="forbid" 挡住模型注入的 approved/user_id 等字段
                              │
   ③ decide ─────────────────┤ PermissionEngine.decide()  ← 9 级优先级，见下节
   (状态机)                   ├── DENY ──────► 直接返回，handler 永不执行
                              ├── CONFIRM ───► APPROVAL_REQUIRED / DANGEROUS_OPERATION
                              └── ALLOW ─────► 继续
                              │
   ④ execute ────────────────┤ asyncio.timeout(policy.timeout_seconds)
                              │   仅 READ 或幂等工具按 max_retries 重试 TransientToolError
                              │   handler(tool_call_id, arguments, context)  ← 副作用只可能在这里
                              ├── TimeoutError ─► TIMEOUT / TIMEOUT_UNKNOWN
                              ├── PolicyDenied ─► 业务错误码（如 ACCOUNT_NOT_FOUND）
                              └── 其他异常 ─────► TOOL_ERROR
                              │
   ⑤ finalize ───────────────┤ _redact(dict(raw))  先脱敏，再交给模型
                              └── ALLOW  OK   ToolResult(ok=True, content=脱敏后结果)
```

三个阶段各自沉淀：`decide` 之后写一条 `phase="decision"` 审计，执行后写一条 `phase="execution"` 审计。

### 3. 权限状态机：9 级固定优先级

顺序就是安全边界，前面的硬判定不会被后面的 `allow` / `bypassPermissions` 覆盖：

| 顺序 | 检查 | 失败结果 | source |
| --- | --- | --- | --- |
| 1 | deny 规则（按 `canonical_target` 前缀匹配） | DENY `DENY_RULE` | rule |
| 2 | plan 模式下非 READ 工具 | DENY `PLAN_MODE_DENIED` | mode |
| 3 | 执行期白名单 `allowed_tools` | DENY `TOOL_NOT_ALLOWED` | whitelist |
| 4 | RBAC 权限 `permissions` | DENY `PERMISSION_DENIED` | rbac |
| 5 | `precheck` 业务预检（归属 / 状态 / 额度 / 余额） | DENY 业务码 | business |
| 6 | 高风险或需审批工具 → 一次性参数绑定审批 | ALLOW `APPROVED` / CONFIRM `APPROVAL_REQUIRED` | approval |
| 7 | `bypassPermissions` 模式 | ALLOW `BYPASS_ALLOWED` | mode |
| 8 | allow 规则 | ALLOW `ALLOW_RULE` | rule |
| 9 | Shell 危险命令兜底正则 | CONFIRM / DENY `DANGEROUS_OPERATION` | risk |

两个容易误解的点：

- **发现期不等于执行期**。`model_tools()` 只把白名单内的工具 Schema 投影给模型（不含 handler 与 policy），执行时仍然要重新查白名单和 RBAC。
- **plan 是执行层只读契约**，不是一句系统提示词：第 2 关直接拒绝 plan 模式下的写操作和 Shell。

### 4. 高风险写操作：一次性、参数绑定审批

`ApprovalStore.approve()` 把审批摘要钉在 `tool_name + 规范化参数` 的 SHA-256 上，`consume()` 命中后立刻置 `used=True`：

- 改一个字节参数（比如 `amount` 从 1200 改成 9000）摘要就对不上，退回 `CONFIRM`。
- 同一个 `approval_id` 重放第二次同样退回 `CONFIRM`。
- 审批在第 5 关 `precheck` **之后**才消费，所以「余额不足」这类业务失败不会白白烧掉审批。
- `dontAsk` 模式无法弹确认，直接以 `APPROVAL_REQUIRED` 拒绝，而不是默认放行。

### 5. 超时与恢复

`_execute_with_recovery()` 用 `asyncio.timeout(policy.timeout_seconds)` 包住 handler，语义是**协作式中断**：到点后 `task.cancel()` 把 `CancelledError` 注入当前 await 点，栈展开后由 `async with` 翻译成 `TimeoutError`。

| 情形 | 行为 |
| --- | --- |
| 超时点落在 `await`（如 `asyncio.sleep`） | 被抢断，await 之后的代码不执行 |
| 同步阻塞（`time.sleep`、CPU 密集、阻塞式 IO） | 抢不断的，等控制权回到事件循环才生效，副作用可能已落地 |
| `finally` 里的 `await` | 仍会执行，清理逻辑若有副作用要自行 shield 并保证幂等 |
| `except Exception` 想吞掉取消 | 吞不掉，`CancelledError` 继承 `BaseException` |

超时后的错误码按「重试是否会重复副作用」分类：READ 或幂等工具返回 `TIMEOUT`，非幂等写返回 `TIMEOUT_UNKNOWN`——钱可能已经动了，必须让人工或对账介入，而不是自动重试。

### 6. 出口脱敏

`_redact()` 只在返回给模型前做一次投影：

| 输入 | 输出 |
| --- | --- |
| `alice@example.com` | `***@***` |
| `ACC-A-123456` | `ACC-A-****3456` |
| key 名含 `token` / `secret` / `password` / `authorization` | 整个值替换为 `***` |

handler 内部照常使用明文账号，脱敏只影响模型可见的那一层。

### 7. 审计追踪

每次调用至少写两条 `AuditRecord`：`decision` 阶段（放行/拒绝/待确认及原因码）和 `execution` 阶段（`OK` / 失败码 / `TIMEOUT_UNKNOWN`，并带 `latency_ms`）。
记录里只保留 `argument_keys` 而非参数值，既能定位问题，又不把业务数据复制进日志。

## 内置工具与策略

| 工具 | effect | risk | permission | 需审批 | timeout | max_retries | 幂等 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `get_order` | READ | MEDIUM | `order:read` | 否 | 1.0s | 2 | 是 |
| `create_refund` | WRITE | HIGH | `refund:create` | 是 | 2.0s | 0 | 否 |
| `run_shell`（教学模拟，不建真实子进程） | SHELL | MEDIUM | `shell:run` | 否 | 1.0s | 0 | 否 |
| `transfer` | WRITE | HIGH | `transfer:execute` | 是 | 2.0s | 0 | 否 |

`transfer` 的链路细节：

- `TransferArgs`：`from_account` / `to_account` 受 `^ACC-[A-Z]-[0-9]{6}$` 约束，`amount` 满足 `0 < amount <= 100000`，并继承 `StrictArgs` 的 `extra="forbid"`。
- `transfer_precheck`：只判断不改余额。`50000 < amount <= 80000` 命中教学拦截区间返回 `EXCEED_LIMIT`；转出账户余额不足返回 `INSUFFICIENT_BALANCE`。`amount > 80000` 故意放行，留给超时演示。
- `transfer_handler`：`amount > 80000` 时先 `await asyncio.sleep(3.0)`（必须在扣款之前，让 2.0s 的 timeout 掐断），转入账户不存在返回 `ACCOUNT_NOT_FOUND`，然后同租户内一扣一加。
- `canonical_target` 用 `from:to:amount` 三元组，不同参数组合不会塌缩成同一个目标。
- 模拟账户数据：`tenant_a` 下 `ACC-A-123456` / `ACC-A-654321` / `ACC-A-888888` 分别为 100000 / 5000 / 20000，`tenant_b` 下 `ACC-B-111111` 为 50000。`ACCOUNTS` 是模块级可变状态，测试按用例快照还原。

## 新增一个工具要改哪里

以 `transfer` 为例，一个工具需要六处接线：

1. 模拟数据（如 `ACCOUNTS`）。
2. 参数模型：继承 `StrictArgs`，用 `Field` 表达类型与约束。
3. 业务预检 `precheck`（可选）：只读判断，失败 `raise PolicyDenied(code, message)`。
4. handler：真正的副作用，签名 `(tool_call_id, arguments, context)`。
5. `build_tools()` 里注册 `ToolDefinition`（policy、handler、precheck、`canonical_target`）。
6. 若返回值含敏感字段，确认 `_redact` 能覆盖。

## 运行与验收

```bash
# 转账工具验收（5 个链路用例）
.venv/bin/python -m pytest tests/test_tool_governance.py -v -k "transfer"

# 离线演示：正常查询、审批前待确认、审批后执行、参数注入、bypass、plan 只读
.venv/bin/python tool_governance_demo.py

# 可选：接真实模型跑 Agent Loop（需要 openai 依赖与 DEEPSEEK_API_KEY）
.venv/bin/python tool_governance_demo.py --agent --input "请查询订单 ord_1001 的状态和可退金额"
```

