# Guarded Coding Harness 测试计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档状态 | P0 完成基线 + WP-13 P1 与 WP-14 CLI 指令升级完成 |
| 对应需求 | [requirements.md](requirements.md) |
| 对应设计 | [design.md](design.md) |
| 对应决策 | [decisions.md](decisions.md) |
| 对应工作包 | [WP-12 规格](work-packages/wp-12-fault-hardening-delivery/spec.md)；[WP-13 规格](work-packages/wp-13-real-model-provider/spec.md)；[WP-14 规格](work-packages/wp-14-cli-run-instruction/spec.md) |
| 飞书来源 | `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌工作簿 revision 11 |

本文定义 P0 测试层级、命令、安全/故障矩阵和证据格式，并记录 WP-13 P1 真实模型的测试草案。
测试计划不重新定义规格；行为冲突时以需求、设计、决策和已冻结工作包规格为准。

## 2. 目标与约束

默认测试必须：

1. P0 和 WP-13 默认测试完全离线，不调用真实模型 Provider、网络或用户凭据；
2. 使用临时 Git 工作区、临时 SQLite 和 Fake Model；
3. 可确定、可重复执行，不依赖开发者绝对路径或执行顺序；
4. 对拒绝、审批、执行、恢复和验收都检查持久化事实与副作用计数；
5. 不通过删除、跳过、弱化断言或额外测试替代六个必须测试文件；
6. 故障注入只在测试目录显式启用，生产配置、CLI、模型和普通环境变量不能开启；
7. 平台相关进程测试仅承诺 Linux/macOS；不满足进程组能力时必须失败关闭。
8. WP-13 live smoke 只能通过显式 marker/环境变量 opt-in，不能由普通配置、CLI 或模型输入
   自动启用。

## 3. 测试层级

| 层级 | 目录 | 验证目标 |
| --- | --- | --- |
| 领域/契约 | `tests/test_domain.py`、`tests/test_config.py`、`tests/test_registry_policy.py` | 严格模型、schema、配置、指纹和稳定错误码 |
| 持久化/审计 | `tests/test_audit.py`、`tests/test_state_checkpoint.py` | SQLite 事务、append-only、租约、Checkpoint 和恢复 |
| Sandbox/工具 | `tests/test_sandbox.py`、`tests/test_tools.py` | 路径、敏感文件、命令、进程、输出和真实工具 |
| Gate | `tests/test_gate.py` | 固定检查链、审批、重复、预算、零 handler 拒绝 |
| Completion | `tests/test_completion.py` | 代码理解/测试修复程序化验收和旧证据失效 |
| Agent/Harness | `tests/test_harness_loop.py` | Fake Model、原生 `ToolCallingAgent`、预算和终态 |
| CLI | `tests/test_cli.py` | run/resume/approval/inspect、`--instruction`、跨进程和退出码 |
| WP-12 故障矩阵 | `tests/test_fault_recovery.py` | 硬进程退出、DB 锁、Checkpoint 损坏、租约和正式样例 |
| P1 模型配置/运行时 | `tests/test_model_config.py`、`tests/test_model_runtime.py` | version 2 配置、凭据、Provider、响应、usage、超时和重试 |
| P1 模型调用生命周期 | `tests/test_model_calls.py` | STARTED/结果、预算 reservation、Audit、崩溃和 unknown |
| P1 live smoke | `tests/live/test_live_model_smoke.py` | 显式 opt-in 的真实只读任务端到端 |

## 4. 六个必须测试文件

| 文件 | 必须覆盖 |
| --- | --- |
| `tests/test_gate.py` | schema 注入、权限/风险、审批绑定/重放/过期、重复调用、预算、拒绝不触达 handler、审计关联 |
| `tests/test_sandbox.py` | `..`/绝对路径/符号链接逃逸、敏感读写、命令 allowlist、环境清理、超时、进程组和输出裁剪 |
| `tests/test_state_checkpoint.py` | 完整状态、合法状态机、幂等预算、租约、Checkpoint、跨重开恢复、已确定写入不重放 |
| `tests/test_audit.py` | append-only、事件顺序、ID 关联、脱敏、决策链回放、审计失败失败关闭 |
| `tests/test_harness_loop.py` | Fake Model 正常/拒绝/审批/预算/异常、统一入口、两类任务、smolagents 兼容 |
| `tests/test_completion.py` | 代码理解与测试修复验收、无证据声明、越界 diff、测试丢失/跳过、过期证据和未知结果 |

额外文件只能增加覆盖，不得减少或替代以上文件。

## 5. 目标与全量命令

安装与基础质量门禁：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m ruff check .
python -m ruff format --check .
```

WP-12 故障与交付目标测试：

```bash
python -m pytest tests/test_fault_recovery.py -q
python -m pytest \
  tests/test_gate.py \
  tests/test_sandbox.py \
  tests/test_state_checkpoint.py \
  tests/test_audit.py \
  tests/test_harness_loop.py \
  tests/test_completion.py -q
```

全量验收：

```bash
python -m pytest -q
```

最终报告必须记录实际退出码、通过/失败/错误/skip 数和执行日期。不能复制本文示例计数作为结果。

WP-13 离线目标测试：

```bash
python -m pytest \
  tests/test_model_config.py \
  tests/test_model_runtime.py \
  tests/test_model_calls.py -q
```

WP-13 显式 live smoke：

```bash
GUARDED_HARNESS_LIVE_MODEL=1 \
python -m pytest tests/live/test_live_model_smoke.py -q -m live_model
```

live smoke 不得替代默认离线测试、P0 全量测试、lint 或格式检查。

## 6. 飞书核心验收映射

| 编号 | 优先级 | WP12 场景 | 主要自动化证据 |
| --- | --- | --- | --- |
| G1 | P0 | 参数缺字段/类型错误，handler 为 0 | `tests/test_gate.py`、`tests/test_harness_loop.py` |
| G2 | P0 | 三次相同只读调用，第三次拒绝，handler 为 2 | `tests/test_gate.py`、`tests/test_tools.py` |
| G3 | P0 | 未审批写进入 `WAITING_APPROVAL`，文件不变 | `tests/test_gate.py`、`tests/test_harness_loop.py`、`tests/test_cli.py` |
| G4 | P0 | 审批后只执行一次，重放不重复写 | `tests/test_gate.py`、`tests/test_harness_loop.py`、`tests/test_cli.py` |
| S1 | P0 | `artifacts/../../.env` 逃逸被拒绝且 handler 为 0 | `tests/test_gate.py`、`tests/test_sandbox.py`、`tests/test_harness_loop.py` |
| S2 | P1 | 符号链接/敏感目录访问拒绝并审计 | `tests/test_sandbox.py`、`tests/test_tools.py`；WP-12 汇总，不阻断 P0 |
| S3 | P0 | 配置外命令不执行 | `tests/test_gate.py`、`tests/test_tools.py` |
| S4 | P1 | 超时/取消真实终止进程组 | `tests/test_sandbox.py`、`tests/test_tools.py`；WP-12 汇总，不阻断 P0 |
| C1 | P0 | 每个安全步骤 Checkpoint 字段完整 | `tests/test_state_checkpoint.py`、`tests/test_harness_loop.py` |
| C2 | P0 | 结果未保存时恢复为 `UNKNOWN_RESULT` 且不重放 | `tests/test_fault_recovery.py`、`tests/test_state_checkpoint.py` |
| C3 | P0 | 新进程恢复，进度/计数保留且不重复写 | `tests/test_state_checkpoint.py`、`tests/test_harness_loop.py`、`tests/test_cli.py` |
| C4 | P0 | 工作区变化后恢复阻塞 | `tests/test_state_checkpoint.py`、`tests/test_harness_loop.py`、`tests/test_cli.py` |
| A1 | P0 | 拒绝审计含 rule/decision/actor/toolCallId | `tests/test_audit.py`、`tests/test_gate.py`、`tests/test_cli.py` |
| A2 | P1 | 完整任务关联 Gate/Sandbox/State/Approval/执行结果 | 两类 E2E 中汇总关联 ID；不阻断 P0 |
| H1 | P0 | 模型称完成但无交付物被拒绝 | `tests/test_completion.py`、`tests/test_harness_loop.py` |
| H2 | P0 | 测试后源码变化使旧证据失效 | `tests/test_completion.py`、`tests/test_state_checkpoint.py` |
| H3 | P0 | 预算耗尽固定 `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED` | `tests/test_harness_loop.py`、`tests/test_cli.py` |
| T1 | P0 | 全量 P0 确定性测试可重复通过 | 全量 pytest、WP-12 复跑记录 |
| T2 | P1 | 真实模型跑通一类任务 | D-07 下 `NOT_RUN/P1`；Fake Model 不得冒充 |

## 7. WP-12 故障矩阵

| 故障点 | 注入方式 | 期望事实 | 验证文件 |
| --- | --- | --- | --- |
| 允许前审计失败 | 测试 `FaultInjector` | 无放行事实，handler 0 | `test_gate.py`、`test_audit.py` |
| intent/claim 后、STARTED 前退出 | 子进程硬退出/既有注入 | `PREPARED`，可全量复核后续接 | `test_state_checkpoint.py`、`test_gate.py` |
| STARTED 后退出 | 子进程硬退出 | `UNKNOWN_RESULT`，不自动重放 | `test_fault_recovery.py` |
| 文件写入后、结果提交前退出 | 测试 handler 写文件后 `os._exit` | 文件已变化、Invocation 仍 STARTED；恢复未知且不重放 | `test_fault_recovery.py` |
| 结果提交后、Checkpoint 未提交 | 既有故障点/状态重建 | 使用确定结果，必要且可归因时补 Checkpoint | `test_state_checkpoint.py` |
| 结果已提交后重复 resume | 重放同调用 | 不增加 handler/文件提交 | `test_state_checkpoint.py`、`test_harness_loop.py` |
| DB 写锁 | 第二 SQLite 连接持锁 | 有界失败、无部分提交 | `test_fault_recovery.py`、`test_audit.py` |
| Checkpoint 损坏 | 隔离临时库篡改并校验哈希/关联 | 拒绝恢复/读取，不猜测成功 | `test_fault_recovery.py`、`test_state_checkpoint.py` |
| 租约冲突 | 有效租约 + 第二 owner | 第二次 acquire 拒绝；过期接管后旧 token 失效 | `test_fault_recovery.py`、`test_state_checkpoint.py` |

测试进程必须保留故障命中标记、子进程退出码/信号和持久化快照。普通 Python 异常不能替代硬退出
来证明进程崩溃恢复。

## 8. 正式样例测试

`examples/config/code-understanding.yaml` 与 `examples/config/test-repair.yaml` 必须：

1. 由正式 `load_config` 加载，不接受测试专用字段；
2. 从仓库相对路径定位工作区；
3. 状态目录位于工作区外；
4. 复制到临时目录后初始化 Git 即可运行；
5. 不含绝对开发者路径、网络依赖、真实凭据或运行产物；
6. 代码理解从审批等待到文档验收；
7. 测试修复从失败基线到最终 JUnit 通过和节点守恒。

## 9. 证据格式

自动化测试至少保存：

- 命令、退出码和 pytest 计数；
- `run_id`、`trace_id`、最终 `RunStatus` 和 `acceptance_status`；
- 原始 `tool_call_id`、`approval_id`、`checkpoint_id`；
- handler 调用数、文件摘要/mtime、进程退出码；
- 基线/最终测试节点摘要和工作区指纹；
- Audit chain 有效性和稳定事件顺序；
- 失败注入点、命中次数与恢复动作；
- 报告状态和未运行 P1 项。

日志和证据不得包含 API Key、令牌、密码、审批 lease token、完整未脱敏参数或模型隐藏推理。

## 10. 失败判定

以下任一情况不得判为通过：

- pytest 失败、错误、未解释 skip 或收集异常；
- 关键场景只靠计划预期、模型自述或人工口头确认；
- 拒绝发生后 handler、写入或进程副作用计数非 0；
- 结果未知被自动重放或改写成功；
- DB/Checkpoint 损坏后仍恢复为可执行状态；
- 旧测试报告、旧文件 Evidence 或旧审批被复用；
- 正式样例无法用生产配置加载器加载；
- P0 场景缺少自动化证据；
- 文档把 P1 未执行项写成已验证。

## 11. WP-13 P1 模型调用矩阵

| 风险 | 测试 | 期望 |
| --- | --- | --- |
| v2 schema 和 Provider 注入 | `test_model_config.py` | 合法配置可加载；未知字段、动态 Provider、非法 URL 被拒绝 |
| 默认安装 | clean venv + `python -m pip install .` | OpenAI SDK 与 `tiktoken` 默认可导入，无需 extra |
| tokenizer 与上下文 | `test_model_config.py`、`test_model_runtime.py` | 未知 tokenizer/输入超限时 Provider calls=0，不截断或摘要 |
| 凭据泄漏 | config/redaction/fingerprint | 密钥值不进入配置指纹、SQLite、Audit、日志和 CLI |
| 端点绕过 | endpoint parser/fake client | userinfo、非 HTTP(S)、跨主机 redirect 被拒绝 |
| 代理环境绕过 | fake transport + `HTTP_PROXY` | `trust_env=False`，代理变量不影响请求 |
| 预算提交前失败 | fake transport | provider calls=0，Run 不进入网络调用 |
| 调用成本预留 | model call + budget | 成功按 usage 结算，unknown 保守保留 reservation |
| 隐式重试 | fake transport count | SDK/smolagents 无额外请求，显式 attempt 均有 Audit |
| Step 关联 | first model call | 调用前创建 Step，STARTED 的 `step_id` 非空 |
| usage 缺失/非法 | fake response | `MODEL_USAGE_INVALID`，不得按 0 计费 |
| 文本 tool-call fallback | fake response with content only | `MODEL_RESPONSE_INVALID`，不调用文本解析 |
| 缺失/重复 tool-call ID | fake structured call | `MODEL_TOOL_CALLS_INVALID`，不合成 ID |
| 响应过大/工具调用非法 | fake response | 不进入 Agent/Gate，失败关闭 |
| Agent/Gate 穿透 | fake tool call | 模型只看到 GovernedTool，实际执行仍经过 Gate |
| `tool_choice` 兼容 | config + fake model kwargs | 默认 required；auto/null 精确转发；thinking 模式真实 smoke 返回结构化 tool call |
| Audit 开始失败 | fault injector | `MODEL_CALL_STARTED` 未提交，network call=0 |
| Audit schema 兼容 | v7 database + v8 migration | 旧事件按 schema 1 校验，新 `MODEL_*` 事件按 schema 2 校验 |
| STARTED 后崩溃 | 子进程硬退出 | 恢复为 `MODEL_CALL_UNKNOWN` 且不重发 |
| 模型配置漂移 | resume with changed profile | `HARNESS_CONFIG_RELOAD_FAILED`/明显配置错误 |
| 只读命令零网络 | CLI inspect/approvals | Provider 创建和网络调用均为 0 |
| live smoke | explicit marker + fixed model snapshot | 真实只读任务产生模型调用、工具调用和 Completion 结果 |

WP-13 测试必须使用 fake client/transport 覆盖正常和故障路径；真实服务调用只允许出现在
`tests/live/` 的显式 opt-in 测试中。

## 12. Node 测试修复 demo 适配

`node_test_junit_xml` 的离线测试必须覆盖：

- 配置拒绝 Node 基础命令与报告格式不匹配；
- 配置拒绝模型或任务预置 reporter/报告路径；
- 固定适配器只追加程序拥有的 Node JUnit 参数；
- Node 绝对测试路径仅在当前工作区内归一，越界路径失败关闭；
- Node 节点键与 pytest 节点键使用不同域分隔值；
- 程序基线失败节点以有界、脱敏的不可信上下文进入首个模型调用；
- 模型 `run_test` observation 与程序最终测试验收保持隔离；
- 既有 pytest 基线、最终报告、节点守恒、工作区范围和 Audit 测试继续通过。

真实 Node 进程测试不进入默认离线门禁；由
`demo/workspace/2_bug_fix/demo-app` 的人工 demo 显式验证。

## 12. 最终复跑

WP-12 验收前至少执行：

1. 目标故障测试；
2. 六个必须测试文件；
3. 全量 pytest；
4. Ruff lint；
5. Ruff format check；
6. 至少一次目标或全量确定性复跑。

所有结果和限制写入
[WP-12 acceptance-report](work-packages/wp-12-fault-hardening-delivery/acceptance-report.md)。

WP-13 验收前还必须复跑模型目标测试、P0 全量测试、Ruff lint、格式检查和显式 live smoke；
离线结果、真实 Provider 结果及剩余限制分别写入
`work-packages/wp-13-real-model-provider/acceptance-report.md`。

WP-14 至少复跑 `tests/test_cli.py` 和 `tests/test_harness_loop.py`，再执行全量测试、Ruff lint
和格式检查。测试必须证明指令替换、持久化恢复、RESUME 拒绝重传、输入边界和配置指纹不变；
结果写入 `work-packages/wp-14-cli-run-instruction/acceptance-report.md`。

WP-08 至 WP-11 的语义审查、CLI 操作走查和测试 diff 审查使用
[manual-acceptance-wp08-wp11.md](manual-acceptance-wp08-wp11.md)；人工结果不能替代失败的自动化门禁。
