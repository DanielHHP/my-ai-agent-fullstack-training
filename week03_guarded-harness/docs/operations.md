# Guarded Coding Harness P0 运维说明

## 1. 适用范围

本文面向在本机运行 Guarded Coding Harness P0 的操作者、开发者和维护者，说明配置、运行、审批、
恢复、状态、日志、退出码和安全限制。

P0 支持：

- 单个本地 Git 工作区；
- 代码理解与测试修复两类任务；
- Linux/macOS 上的 POSIX 进程组；
- 本地 SQLite 状态和本地 CLI 审批；
- Fake Model 驱动的确定性自动化验收。

P0 不提供真实模型 Provider、网络调用、Web/TUI 审批、多仓库、分布式执行、Windows 进程组、
容器或虚拟机级隔离。

## 2. 安装与基线检查

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Python 版本以 `.python-version` 为准，依赖和开发工具以 `pyproject.toml` 为唯一来源。

## 3. 配置

通过全局参数指定配置：

```bash
guarded-harness --config PATH ...
python -m guarded_harness --config PATH ...
```

配置必须在加载后通过严格 Pydantic/YAML 校验。调用方不能通过 CLI 覆盖：

- workspace、干净/外围保护策略；
- state 目录；
- 预算；
- task id/kind、工具 allowlist；
- 路径策略、测试命令、交付物；
- 审批角色、必审工具和 TTL。

唯一例外是 `run --instruction TEXT`：它替换本次 Run 的有效任务描述，但不修改 YAML、
配置指纹或其他任务字段。提供该参数时，YAML `task.description` 不发送给模型；未提供时继续
使用 YAML 描述。有效指令会在 Run 创建时持久化，`resume` 不接受新指令并从 Run 状态继续。
不要通过命令行或任务描述传递 API Key、密码或令牌，因为它们可能出现在 shell 历史和持久化状态中。

状态目录必须位于目标工作区外。配置路径、未知字段、非法预算、越界路径和危险测试命令均应失败
关闭。

正式样例见：

- `examples/config/code-understanding.yaml`
- `examples/config/test-repair.yaml`
- `demo/config/1_login_demo.yaml`
- `demo/config/2_bug_fix.yaml`

样例使用 Fake Model 驱动验收；生产 console 标准入口没有受控模型来源时会返回
`CLI_MODEL_UNAVAILABLE`。

`config/` 下的两个真实模型 demo 使用 version 2 Provider。测试修复 demo 声明
`node_test_junit_xml`，基础命令固定为 `node ... --test`；适配器自行追加 JUnit reporter 和
临时报告路径。P0 `pytest_junit_xml` 行为不变。对应人工步骤和证据检查见
[demo-validation.md](demo-validation.md)。

## 4. 常用命令

```bash
guarded-harness --config PATH run [--instruction TEXT] \
  [--wait-for-approval SECONDS] [--json]
guarded-harness --config PATH resume RUN_ID [--wait-for-approval SECONDS] [--json]
guarded-harness --config PATH approvals list [--run-id RUN_ID] [--status STATUS] [--limit N] [--json]
guarded-harness --config PATH approvals approve APPROVAL_ID \
  --actor ACTOR --role ROLE [--json]
guarded-harness --config PATH approvals reject APPROVAL_ID \
  --actor ACTOR --role ROLE [--json]
guarded-harness --config PATH inspect RUN_ID \
  [--tool-call-id TOOL_CALL_ID] [--approval-id APPROVAL_ID] [--json]
```

`--instruction` 只允许用于 `run`，要求非空白且最长 4000 字符。它不能改变工具权限、路径、
预算、审批或验收；因此用户指令要求执行未授权操作时仍由 Gate 拒绝。

`approvals approve/reject` 只改变授权/规定 Run 终态，不执行 handler。仓库操作只能由 `resume`
重新经过 Gate 后执行。

## 5. 审批流程

默认流程：

```text
run
  -> WAITING_APPROVAL，进程退出，文件不变
  -> approvals list
  -> approvals approve APPROVAL_ID --actor ... --role ...
  -> resume RUN_ID
  -> Gate 重新校验并至多执行一次
  -> Completion 独立验收
```

规则：

- actor 和至少一个 role 必须显式提供；
- actor 不得等于请求 actor，自批拒绝；
- role 必须命中任务允许审批角色；
- 审批绑定工具名、规范化参数、Run、任务、工作区、配置指纹和有效期；
- 审批一次性消费，重放、过期、跨 Run、跨工作区和参数变化均拒绝；
- Gate 在创建 PREPARED 调用或审批前检查规范化参数的脱敏可恢复性；若持久化脱敏会改变参数，
  返回 `TOOL_ARGUMENTS_NOT_RECOVERABLE` 给模型，不创建审批。模型必须使用新 `tool_call_id`
  并改写参数，不能复用原 ID 搭配新参数；
- `CLAIMED` 不按审批 TTL 自动释放；
- 拒绝或过期会原子停止 Run，模型不能换工具继续同一目标。

`--wait-for-approval SECONDS` 只改变是否在同一进程等待；本地超时保持
`WAITING_APPROVAL`，不是审批过期，也不会自动批准或拒绝。

resume 重新校验 PREPARED 调用时若 Gate 拒绝，Run 进入 `BLOCKED/GATE_BLOCKED`，保留具体拒绝
错误码；不得反复 resume 或手工改写摘要绕过绑定检查。

## 6. 状态数据库

默认数据库：

```text
<state_dir>/harness.sqlite3
```

可能同时存在 SQLite WAL/SHM。POSIX 下新建状态目录使用 `0700`，数据库/WAL/SHM 使用 `0600`。
状态目录和文件必须由当前有效用户拥有且类型安全，否则失败关闭。

主要事实：

- `runs`、`run_plans`、`steps`；
- `tool_calls`、`budget_ledger`、`evidence`；
- `approvals`、`checkpoints`、`recovery_records`；
- `completion_results`、`audit_events`、`run_leases`。

Audit 为 append-only。不要手工修改审批、Run 状态、Invocation、Checkpoint 或 Audit 链。

### 备份

运行中不要只复制主数据库文件，因为 WAL 中可能有尚未合并的事务。维护窗口内先停止所有
Harness/CLI 进程，再使用 SQLite 官方 backup API 或 `VACUUM INTO` 生成一致副本。恢复备份前应在
隔离目录运行状态完整性和审计链检查。

## 7. 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | run/resume 为 `ACCEPTED`，或审批/只读控制命令成功完成 |
| 2 | usage/config 错误 |
| 3 | `WAITING_APPROVAL` |
| 4 | `STOPPED` |
| 5 | `BLOCKED` |
| 6 | `FAILED` |
| 7 | `COMPLETED` 但验收 `REJECTED` |
| 8 | `UNVERIFIABLE` |
| 9 | 内部查询失败 |

审批和 inspect 命令成功执行返回 0，但其输出中的 Run 真实终态仍可能不是成功。

## 8. 终态与人工处理

### WAITING_APPROVAL

检查 `approvals list` 和 `inspect`。批准后显式 `resume`；拒绝会停止 Run。不要直接改 SQLite
审批行。

### BLOCKED

常见原因：工作区/配置/HEAD/index/外围保护基线变化，Checkpoint/审批失效，或结果未知。

处理：

1. 用 `inspect` 查看 Run、Approval、Recovery、Completion 和 Audit 关联；
2. 核对工作区 diff/status 和实际文件；
3. 不把 `UNKNOWN_RESULT` 或 `BLOCKED` 改写为成功；
4. 需要继续工作时启动新 Run；
5. 保留原 Run 的事实用于审计。

### UNKNOWN_RESULT

表示无法确认非幂等副作用是否完成。P0 不提供同 Run 自动恢复、释放 CLAIMED 审批或重放未知写。
操作者只能核对事实，必要时人工处理工作区后启动新 Run。

### STOPPED / BUDGET_EXHAUSTED

预算耗尽固定为 `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED`，不会执行最终验收。部分产物只能作为
诊断证据。

### FAILED

检查稳定错误码、关联 Audit 和本地受控日志。修复配置/环境中可处理的问题后启动新 Run；不要
通过修改历史状态伪造恢复。

## 9. 日志与脱敏

日志、Audit、State 和模型 observation 在落盘/返回前统一脱敏并限长。不得记录：

- API Key、Authorization、令牌、密码；
- 审批 lease token；
- 未脱敏敏感环境变量；
- 完整模型消息或隐藏推理；
- 完整工具输出或测试断言正文；
- 本机用户凭据路径。

发现敏感值进入输出时，应停止交付、定位统一脱敏边界并补充回归测试；不要只删除日志文件。

## 10. 进程与平台限制

命令只接受可信 `command_id`，实际 argv、cwd、环境、超时和输出限制来自任务配置或固定策略。
子进程使用最小环境、固定工作区 cwd、有界输出和 POSIX 进程组终止。

超时只证明本地停止等待，不证明外部副作用未发生。非幂等命令超时、取消、进程组终止失败或结果
采集失败必须按 `UNKNOWN_RESULT` 处理。

Windows 或不支持可靠进程组终止的平台应拒绝进程执行。Sandbox 不承诺容器/虚拟机级文件系统或
网络隔离；Git-ignored 仓库外围文件也不在保护基线内。

## 11. 禁止操作

不得：

- 修改 `.git/`、依赖目录或任务只读文件；
- 在模型工具参数中提交 shell、解释器包装、任意 argv、env、cwd 或重定向；
- 直接调用 Gate 之后的 handler 绕过治理入口；
- 手工把 `STARTED`、`UNKNOWN_RESULT` 或 `BLOCKED` 改写为成功；
- 强放行已消费、过期、篡改或跨 Run 的审批；
- 删除 append-only Audit、Checkpoint 或测试证据；
- 通过 CLI、环境变量或模型输入启用测试故障注入；
- 用 Fake Model 结果宣称真实模型能力；
- 泄露测试状态数据库中的审批/参数摘要。

## 12. 事故处理顺序

1. 停止继续运行/恢复，不要自动重试非幂等操作；
2. 保留工作区、SQLite/WAL/SHM 和相关日志，不手工清理；
3. 用 `inspect` 和只读查询确定 Run、Step、ToolCall、Approval、Checkpoint、Audit 关联；
4. 核对文件内容摘要、Git status/diff、测试报告摘要和进程退出信息；
5. 区分“未开始”“确定失败”“已成功”“结果未知”；
6. 结果未知或审计链损坏时保持阻塞；
7. 修复根因后创建新 Run，并在验收报告中记录原 Run ID、事故原因和限制。

## 13. P1 真实模型 Provider 运维边界

WP-13 已通过外部 live smoke 验收；外部调用继续遵守以下边界：

- 只有 version 2 配置可以声明真实 Provider；
- 首个 Provider 固定为 `openai_chat_completions_v1`，OpenAI SDK 和固定 TokenCounter 属于默认
  安装，不需要额外 optional extra；
- 配置中的 `credential_ref` 是非秘密引用，真实值由受控环境变量或 SecretResolver 提供；
- 配置必须声明固定 `tokenizer_id`，未知 tokenizer、输入超限、不支持结构化 tool calls 或仅返回
  文本 tool-call 时拒绝；
- `model.tool_choice` 默认 `required`。Provider 不支持强制工具选择时可设为 `auto`，或设为
  `null` 省略请求字段；该值进入配置指纹，不能在 Run 中临时覆盖；
- 不读取 `.env`，不把密钥写入配置、State、Audit、日志或子进程环境；
- HTTP client 不继承 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`，不跟随跨主机 redirect；
- `run/resume` 才创建 Provider；`approvals` 和 `inspect` 不读取凭据、不访问网络；
- 模型调用超时或结果未落盘时进入模型调用 unknown，不自动重试；
- 真实 smoke test 必须显式 opt-in，不能由普通测试或模型输入开启。
- Harness 在取得 Run 租约后启动独立心跳线程，模型调用阻塞时间可以超过单次 TTL；连续两次续租
  失败返回 `STATE_LEASE_HEARTBEAT_FAILED` 并停止后续动作。TTL 不是任务总时长或模型超时；
- 心跳发生在独立 SQLite 短事务中；释放租约前必须先停止并 join 心跳。若心跳停止失败，保持
  Run 不可释放并交给恢复流程处理；
- 心跳丢失后，已经发出的模型请求仍可能完成并计费。恢复必须按 `MODEL_CALL_UNKNOWN` 处理，
  不得自动重放。

version 1 配置仍返回 `CLI_MODEL_UNAVAILABLE`，除非调用方注入可信模型工厂。version 2 配置
使用 `examples/config/model-openai-chat-completions.yaml` 作为起点。缺少凭据或切换端点、
模型后，应重新执行对应环境的 live smoke。
