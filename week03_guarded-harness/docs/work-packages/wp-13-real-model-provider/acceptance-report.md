# WP-13 真实模型 Provider 与模型调用治理验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-13 真实模型 Provider 与模型调用治理 |
| 报告状态 | accepted |
| 报告版本 | 1.3.0 |
| 验收日期 | 2026-11-10；补充复核 2026-11-14 |
| 对应规格 | [spec.md](spec.md) v1.1.0 / frozen（D-105 修订） |
| 对应计划 | [plan.md](plan.md) v1.0.0 / done |
| 代码基线 | `417d3fa`；本次 WP-13 改动仍在工作区 |
| 操作系统 | Darwin 22.6.0 x86_64 |
| Python | 3.13.5 |
| pip | 25.1.1 |
| 关键依赖 | `smolagents==1.26.0`、`openai==2.54.0`、`tiktoken==0.14.0` |

## 2. 结论

WP-13 的离线实现已经完成，并通过 P0 回归、模型配置、模型调用、预算、重试、超时、审计、
migration 和 OpenAI SDK Chat Completions 本地协议仿真。

需求方于 2026-11-10 在 DeepSeek OpenAI-compatible 服务上执行 `live_model` smoke test，
确认真实模型 Provider 构造和生成调用通过。该外部证据关闭 WP13-AC-018，报告状态更新为
`accepted`。本次反馈未提供原始 stdout、Provider 请求 ID 或 usage 明细，因此报告不补造这些值。

2026-11-14 补充复核发现，当时执行的 smoke test 只发送纯文本请求，没有携带工具定义，因此不能
证明真实 DeepSeek 服务支持 `ToolCallingAgent` 所需的结构化 tool call。需求方随后使用
`tool_choice="auto"` 和省略 `tool_choice` 两种方式执行真实 echo-tool 请求，均返回了带非空调用
ID 的 `echo` tool call。据此增加受控 `model.tool_choice` 配置，并将 live smoke 测试升级为工具
调用断言；完整 Governed Agent → Gate → Completion 的真实任务闭环仍需通过 demo 复跑记录。

同日增加模型失败分类审计：响应校验、工具调用和 usage 错误在保留通用 `MODEL_*` 错误码的同时，
把固定 `reason_code` 和经过脱敏、限长的诊断摘要写入对应 `MODEL_CALL_FAILED` 的
`output_summary`。该改动不增加数据库列或 migration，旧 Audit 链保持不变。

## 3. 交付物

| 交付物 | 状态 | 说明 |
| --- | --- | --- |
| `src/guarded_harness/model_runtime.py` | PASS | Provider、SecretResolver、TokenCounter、GovernedModel、ModelCallService |
| `config.py` version 2 model profile | PASS | 固定 Provider、端点、tokenizer、额度、重试和价格 |
| `storage.py` migration v8 | PASS | `model_calls`、reservation、Audit schema 字段 |
| `state_store.py` model-call lifecycle | PASS | STARTED、usage、成本、失败、unknown 和查询 |
| `domain.py` model failure classification | PASS | 固定失败分类、严格字段和 16 项工具名上限 |
| `audit.py` schema 2 | PASS | `model_call_id` 和 `MODEL_*` 事件，旧链保持 schema 1 |
| `harness.py` integration | PASS | 首次模型调用前创建 Step、Run 包装、unknown 恢复阻塞 |
| `cli.py` v2 Provider | PASS | run/resume 默认可构造 Provider；只读命令不创建 |
| `pyproject.toml` 默认依赖 | PASS | OpenAI SDK、tiktoken 默认运行依赖 |
| `tests/test_model_config.py` | PASS | v2 配置、端点、凭据和 SDK 构造 |
| `tests/test_model_runtime.py` | PASS | 预算、usage、重试、超时、Audit 和恢复 |
| `tests/test_model_provider.py` | PASS | 真实 OpenAI SDK + Chat Completions 本地协议仿真 |
| `tests/live/test_live_model_smoke.py` | PASS | 需求方 2026-11-10 在 DeepSeek 服务上显式执行通过 |
| `examples/config/model-openai-chat-completions.yaml` | PASS | `deepseek-flash` OpenAI-compatible 无密钥示例 |

## 4. 执行证据

### 4.1 安装

```bash
python -m pip install -e '.[dev]'
```

结果：PASS。默认安装引入 `openai==2.54.0`、`tiktoken==0.14.0`。

### 4.2 WP-13 目标测试

```bash
python -m pytest \
  tests/test_model_config.py \
  tests/test_model_runtime.py \
  tests/test_model_provider.py \
  tests/test_cli.py -q
```

结果：

```text
49 passed in 48.10s
```

覆盖：

- v1/v2 配置隔离、固定 Provider/tokenizer 和端点约束；
- OpenAI SDK 实际 Chat Completions 请求构造和 tool-call 响应解析；
- 无凭据在仓库副作用前失败；
- 模型调用 Step、STARTED、usage、actual cost 和 Audit schema 2；
- 缺失 usage、文本 fallback、超大响应和超时失败关闭；
- 429 显式 attempt、逻辑调用归组和成功结算；
- STARTED 后恢复为 `MODEL_CALL_UNKNOWN` 且不重发；
- `approvals` 不创建 Provider。

### 4.3 全量回归

```bash
python -m pytest -q
```

结果：

```text
395 passed, 1 skipped in 231.40s (0:03:51)
```

唯一 skip 是自动离线模式下未启用 `tests/live/test_live_model_smoke.py`；该测试已作为独立外部
验收项执行并通过。

### 4.4 Lint 与格式

```bash
python -m ruff check .
python -m ruff format --check .
git diff --check
```

结果：PASS，全部检查通过。

### 4.5 外部 live smoke

执行命令模板：

```bash
GUARDED_HARNESS_LIVE_MODEL=1 \
GUARDED_HARNESS_LIVE_CONFIG=examples/config/model-openai-chat-completions.yaml \
python -m pytest tests/live/test_live_model_smoke.py -q -m live_model
```

结果：PASS（文本生成 smoke）。需求方反馈 smoke test 已在 DeepSeek OpenAI-compatible 服务上
通过；该次证据未携带工具，不能用于证明真实工具调用能力。外部请求的原始输出、Provider 请求
ID、usage 和账单明细未纳入本报告。

补充工具探测（2026-11-14）：PASS。`tool_choice="auto"` 与 `tool_choice=None` 均返回单个
`echo` 结构化 tool call，调用 ID 非空。该结果确认真实 Provider 协议兼容，但不替代完整任务
Completion 验收。

### 4.6 模型失败分类审计补充回归

```bash
python -m pytest \
  tests/test_model_config.py \
  tests/test_model_runtime.py \
  tests/test_model_provider.py \
  tests/test_cli.py -q
```

结果：`58 passed in 109.73s`。

覆盖工具调用超限、空/重复 ID、空工具名、`final_answer` 混用、usage 缺失、文本 fallback
和响应过大。测试确认失败事件记录固定 `reason_code`、调用数量、允许上限、无效位置/数量和
最多 16 个工具名，不记录 tool-call arguments，并保持 Audit 哈希链有效。

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
git diff --check
```

结果：`411 passed, 1 skipped in 297.42s`；Ruff lint、格式检查和 diff 检查均 PASS。唯一 skip
仍为默认离线模式跳过显式 live smoke。

## 5. 验收条件映射

| ID | 结果 | 证据 |
| --- | --- | --- |
| WP13-AC-001 | PASS | `test_model_config.py` |
| WP13-AC-002 | PASS | SDK Factory、Harness v2 和本地协议测试 |
| WP13-AC-003 | PASS | Fake/Mock structured tool call；文本 fallback 被拒绝 |
| WP13-AC-004 | PASS | 无凭据和无预算时 Provider 调用为 0 |
| WP13-AC-005 | PASS | 配置不接收密钥值；环境解析失败脱敏 |
| WP13-AC-006 | PASS | STARTED step、logical ID、attempt 和 usage 可查询 |
| WP13-AC-007 | PASS | usage 缺失和超限失败关闭 |
| WP13-AC-008 | PASS | tokenizer/预算前置拒绝，网络调用为 0 |
| WP13-AC-009 | PASS | reservation、成本结算和 unknown 保留 |
| WP13-AC-010 | PASS | 429 显式重试和 attempt Audit |
| WP13-AC-011 | PASS | timeout 不重试并阻塞恢复 |
| WP13-AC-012 | PASS | 大小、文本 fallback、结构化工具校验 |
| WP13-AC-013 | PASS | GovernedModel 后仍进入原生 ToolCallingAgent/Gate |
| WP13-AC-014 | PASS | STARTED 恢复测试 |
| WP13-AC-015 | PASS | 配置指纹和 Harness 既有恢复校验 |
| WP13-AC-016 | PASS | v2 approvals 零 Provider 测试 |
| WP13-AC-017 | PASS | `MODEL_*` 错误码和 CLI 映射 |
| WP13-AC-018 | PASS | 文本 live smoke 于 2026-11-10 通过；结构化 tool-call 补充探测于 2026-11-14 通过；完整 Governed E2E 待 demo 记录 |
| WP13-AC-019 | PASS | v7→v8 migration 和旧 Audit 链测试 |
| WP13-AC-020 | PASS | 395 passed、Ruff lint 和格式检查 |

## 6. 剩余风险

- live smoke 已通过，但原始 Provider 请求 ID、usage 和账单输出未归档；
- 2026-11-10 live smoke 未携带工具；真实工具调用已另行探测，但尚未归档完整 Gate/Completion E2E；
- `tiktoken` 编码资源首次使用可能需要本地缓存或可用下载路径；生产应预置缓存并再次审计网络边界；
- 配置价格是治理预算，不等于 Provider 最终账单；
- `deepseek-flash` 是移动模型别名，实际版本可能漂移；
- 本地 HTTP/MockTransport 证明 SDK 协议链路，不证明真实服务质量和可用性。

## 7. 后续建议

1. 将外部 live smoke 的脱敏 stdout、Provider 请求 ID、usage 和 model call Audit 归档为补充
   证据；
2. 对 `deepseek-flash` 的模型版本漂移保持监控；
3. 如 Provider 价格变化，更新样例中的可信治理价格上限。
