# Guarded Coding Harness

Guarded Coding Harness 是在 `smolagents.ToolCallingAgent` 外层增加确定性治理能力的编码 Agent
运行时。它保留 `smolagents` 原生的模型交互、工具选择和 ReAct 决策循环，由程序统一负责参数
校验、权限判断、审批、执行边界、状态持久化、审计、恢复和最终验收。

模型只提交候选工具名与参数。任何仓库读取、文件写入、命令执行和测试运行都必须经过同一
Gate；Agent 停止后，任务是否成功由交付物、工作区 diff、测试报告和 Audit 链独立判定，而不是
由模型自述决定。首期任务类型为代码理解与测试修复。

## 主要功能

- 保持原生 Agent 决策循环：通过公开 API 包装受控工具，不复制或修改 `smolagents` 核心。
- 唯一执行入口：每次工具调用重新校验 schema、权限、路径、审批、重复调用和预算，失败关闭。
- 工作区与进程边界：阻止路径穿越、符号链接逃逸和敏感文件访问；命令使用固定 argv、
  `shell=False`、最小环境、超时、输出上限和 POSIX 进程组终止。
- 一次性审批：审批绑定任务、Run、工作区、工具、规范化参数和配置指纹；参数变化、过期、
  重放、跨 Run 或跨工作区使用均拒绝。
- 持久化与恢复：SQLite 保存 Run 状态、步骤、工具调用、预算、Evidence、审批、Checkpoint
  和验收结果；崩溃后区分未开始、成功、确定失败和结果未知。
- 程序化最终验收：代码理解检查交付物、代码引用和越界修改；测试修复复跑可信测试命令，
  校验 JUnit 节点守恒、工作区范围和旧证据失效。
- 模型调用治理：支持经过严格校验的 OpenAI Chat Completions 兼容 Provider，记录模型调用
  usage、成本、超时和失败分类。
- CLI 运维能力：支持运行、审批后恢复、审批查看/批准/拒绝、Turn 树检查和机器可读 JSON 输出。

## 核心模块

源码位于 `src/guarded_harness/`。

| 模块 | 功能概述 |
| --- | --- |
| `harness.py` | 唯一 Agent Run 编排器；加载配置、创建或恢复 Run、装配组件、管理预算和租约，并在 Agent 停止后调用 Completion。 |
| `agent_adapter.py` | 通过 `smolagents` 公开 API 构造 `ToolCallingAgent` 和受控工具包装器，保留工具调用 ID 并将 observation 返回 ReAct 循环。 |
| `model_runtime.py` | 构造真实模型 Provider，管理凭据引用、模型调用、Token 预算、重试、超时和调用审计。 |
| `gate.py` | 所有工具执行和内部控制面动作的唯一入口；依次完成 schema、策略、审批、重复调用、预算和持久化检查。 |
| `registry.py` | 注册可信工具的名称、描述、输入 schema、风险、权限、超时、重试策略、幂等性和 handler。 |
| `policy.py` | 决定模型可见工具、运行时权限、任务模式、风险等级和审批要求。 |
| `approval.py` | 管理审批创建、批准、拒绝、过期、认领和一次性消费；校验完整绑定摘要。 |
| `sandbox.py` | 实现工作区根目录约束、安全文件 I/O、Git 基线、受信命令、最小环境、进程组和输出截断。 |
| `redaction.py` | 对 State、Audit、错误、工具 observation 和模型上下文统一脱敏并限制大小。 |
| `storage.py` | 提供 SQLite 连接、迁移、事务、锁、文件权限和完整性检查。 |
| `state_store.py` | 持久化 Run、Step、ToolInvocation、预算、Evidence、审批、验收和状态机转移。 |
| `audit.py` | 写入 append-only、按序编号并带哈希链的审计事实，校验事件链完整性。 |
| `checkpoint.py` | 在安全边界生成快照，校验工作区和审批后计算恢复动作，禁止未知副作用自动重放。 |
| `lease_heartbeat.py` | 在模型调用或长操作期间续租 Run lease，避免有效长任务被误接管。 |
| `workspace_baseline.py` | 规范化序列化和摘要工作区初始 status、diff、文件内容与外围保护基线。 |
| `completion.py` | 执行模型之外的最终验收，生成 `ACCEPTED`、`REJECTED` 或 `UNVERIFIABLE` 结论。 |
| `test_reports.py` | 解析和规范化 pytest/Node JUnit XML，比较基线节点、最终节点和测试文件。 |
| `config.py` | 严格加载 YAML，拒绝未知字段，规范化路径并生成配置指纹。 |
| `domain.py` | 定义共享领域模型、状态枚举、稳定错误码、ID 和组件协议。 |
| `tools/` | 提供只读文件/搜索/Git、单文件写入/patch、受控测试命令等单一职责 handler。 |
| `cli.py` | 暴露 `run`、`resume`、`approvals`、`inspect` 命令，并把 Run 结果映射为稳定退出码。 |
| `control.py` | 提供有界、只读或审批控制的本地控制面服务，不直接执行仓库 handler。 |
| `inspect_view.py` | 将持久化事实渲染为按 Turn 组织的人工可读视图。 |

## 环境准备

运行环境要求：

- Python 3.10 至 3.13；仓库 `.python-version` 固定为 3.13。
- pip、Git，以及支持 POSIX 进程组的 Linux 或 macOS。
- 使用仓库现有 `.venv` 安装和验收依赖。

在仓库根目录执行：

```bash
source .venv/bin/activate
python --version
python -m pip install -e '.[dev]'
```

如果 `.venv` 尚不存在，先用与本项目 `.python-version` 一致的 Python 创建：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

依赖和开发工具只在 `pyproject.toml` 中维护。真实模型配置通过
`credential_ref: OPENAI_API_KEY` 引用当前 shell 的凭据，不读取 `.env`：

```bash
export OPENAI_API_KEY='<your-key>'
```

不要把密钥写入 YAML、命令行参数、文档或仓库文件。使用结束后可执行：

```bash
unset OPENAI_API_KEY
```

运行 Node 测试修复 demo 时，还需要 Node.js，并在 demo 工作区安装其本地依赖；具体步骤见
[Demo 验证说明](docs/demo-validation.md)。

## 命令入口

安装后可使用 console script，或等价的 Python module 入口：

```bash
guarded-harness --help
python -m guarded_harness --help
```

`--config` 是全局参数，YAML 中的相对路径从配置文件所在目录解析。配置负责声明工作区、
允许工具、可读写路径、预算、测试命令、审批角色和验收规则；CLI 不能临时覆盖这些安全设置。

### 启动任务

使用 version 2 真实模型配置启动代码理解任务：

```bash
guarded-harness \
  --config examples/config/model-openai-chat-completions.yaml \
  run --json
```

为单次 Run 提供更具体的任务指令：

```bash
guarded-harness \
  --config demo/config/1_login_demo.yaml \
  run --instruction "分析登录失败后的状态流转，并生成带文件行号依据的文档" \
  --json
```

`--instruction` 只替换本次 Run 的任务描述，最长 4000 个字符；权限、路径、预算、审批和验收
仍由 YAML 决定。指令会持久化，审批后的 `resume` 使用原指令，不能重新替换。

启动测试修复任务，并在同一进程内最多等待审批 600 秒：

```bash
guarded-harness \
  --config demo/config/2_bug_fix.yaml \
  run --instruction "修复当前失败的测试，已有测试保持只读" \
  --wait-for-approval 600 --json
```

version 1 配置只接受调用方注入的可信 `model_factory`，标准 console 入口不会从 argv 或
环境变量加载任意模型实现。因此直接运行 version 1 配置会以 `CLI_MODEL_UNAVAILABLE` 失败；
仓库中的 version 1 样例主要由离线测试通过可信 Fake Model 驱动。需要由 console 直接运行时，
使用带严格 `model` 块的 version 2 配置。

### 审批与恢复

未审批写操作会使 Run 进入 `WAITING_APPROVAL`，目标文件保持不变。查看待审批项：

```bash
guarded-harness --config PATH approvals list \
  --run-id RUN_ID --status pending --json
```

批准或拒绝：

```bash
guarded-harness --config PATH approvals approve APPROVAL_ID \
  --actor reviewer --role local_operator --json

guarded-harness --config PATH approvals reject APPROVAL_ID \
  --actor reviewer --role local_operator --json
```

审批命令只改变授权状态，不执行工具 handler。批准后需要显式恢复 Run：

```bash
guarded-harness --config PATH resume RUN_ID --json
```

审批已被消费、过期、参数或工作区发生变化、审批角色不匹配以及自批都会被拒绝。`resume`
会重新校验配置、工作区、审批绑定和 Checkpoint，再通过 Gate 至多执行一次待处理操作。

### 查看运行事实

默认和 `--pretty` 输出按 Turn 组织的人工阅读视图，`--json` 输出稳定的机器可读结果：

```bash
guarded-harness --config PATH inspect RUN_ID
guarded-harness --config PATH inspect RUN_ID --pretty
guarded-harness --config PATH inspect RUN_ID --json
```

可按工具调用、审批或模型调用关联检查，并限制 Audit 条数：

```bash
guarded-harness --config PATH inspect RUN_ID \
  --tool-call-id TOOL_CALL_ID \
  --approval-id APPROVAL_ID \
  --model-call-id MODEL_CALL_ID \
  --audit-limit 200 \
  --json
```

`approvals` 和 `inspect` 不创建模型、不读取凭据、不访问网络，也不会执行仓库操作。

## 测试与质量检查

默认测试完全离线，使用临时 Git 工作区、临时 SQLite 和 Fake Model：

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

常用目标测试：

```bash
python -m pytest tests/test_gate.py tests/test_sandbox.py -q
python -m pytest tests/test_state_checkpoint.py tests/test_audit.py -q
python -m pytest tests/test_harness_loop.py tests/test_cli.py -q
python -m pytest tests/test_completion.py tests/test_fault_recovery.py -q
python -m pytest tests/test_model_config.py tests/test_model_runtime.py -q
```

真实模型 smoke test 需要显式 opt-in，会访问外部服务并可能产生费用：

```bash
export OPENAI_API_KEY='<your-key>'
export GUARDED_HARNESS_LIVE_MODEL=1
python -m pytest tests/live/test_live_model_smoke.py -q -m live_model
```

如需使用其他受支持的测试配置，可设置
`GUARDED_HARNESS_LIVE_CONFIG=/path/to/config.yaml`。live smoke 不能替代默认离线测试、lint
或格式检查。

## 配置与样例

主要配置样例：

| 配置 | 用途 |
| --- | --- |
| `examples/config/model-openai-chat-completions.yaml` | version 2 OpenAI Chat Completions 兼容 Provider 代码理解任务。 |
| `examples/config/code-understanding.yaml` | version 1 代码理解配置，供可信模型注入和离线验收。 |
| `examples/config/test-repair.yaml` | version 1 测试修复配置，供可信模型注入和离线验收。 |
| `demo/config/1_login_demo.yaml` | 非阻塞审批的真实模型代码理解 demo。 |
| `demo/config/2_bug_fix.yaml` | Node 测试修复、阻塞审批和 JUnit 最终验收 demo。 |

配置在加载后使用严格模型校验，未知字段、非法预算、工作区外状态目录、越界路径和不可信测试
命令都会失败关闭。完整配置字段、审批、恢复、退出码和故障处理见
[运维说明](docs/operations.md)。

## 文档

- [文档索引](docs/README.md)
- [需求](docs/requirements.md)
- [系统设计](docs/design.md)
- [设计决策](docs/decisions.md)
- [任务拆解](docs/task-breakdown.md)
- [测试计划](docs/test-plan.md)
- [运维说明](docs/operations.md)
- [Demo 验证](docs/demo-validation.md)
- [正式样例说明](examples/README.md)
