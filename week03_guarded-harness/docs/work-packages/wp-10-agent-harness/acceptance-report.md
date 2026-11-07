# WP-10 Agent 与 Harness 验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-10 Agent 与 Harness |
| 报告状态 | review |
| 报告版本 | 1.1.0 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.0.0 / in-progress |
| 代码基线 | `2e279af` + 本工作包未提交改动 |
| 验收日期 | 2026-11-07 |
| 平台 | Darwin 22.6.0 x86_64 |
| Python | 3.13.5（仓库 `.venv`） |
| smolagents | 1.26.0（精确锁定） |

本报告汇总 WP-10 实际实现和本次复验证据。报告为 `review`，不声明整个项目已完成，因为上游
WP-08/WP-09 的独立 `acceptance-report.md` 仍未形成最终 accepted 结论，真实模型和 P1 场景
也不在 WP-10/P0 范围。

## 2. 交付结果

| 能力 | 实现 |
| --- | --- |
| 公共 Run 契约 | `RunMode`、`RunRequest`、`RunResult`、WP-10 稳定错误码 |
| Fake Model | `tests/fake_model.py` 支持工具调用、最终回答、异常、token usage 和多调用序列 |
| 公开适配 | `GovernedTool`、`HarnessToolCallingAgent`、1.26.0 签名/字段兼容检查 |
| 唯一 Agent 入口 | `Harness.run(START/RESUME)` 装配既有 Registry、Policy、Gate、Sandbox、State、Audit、Checkpoint、Completion |
| 调用 ID | 原始 `tool_call_id` 保留到 Gate、ToolOutput 和 Audit |
| 多调用 | `max_tool_threads=1`，按声明顺序串行；审批/未知后停止同批后续调用 |
| 预算 | Step、tool、token-cost、wall-time；P0 每 token `0.000001` Decimal 资源单位 |
| 持久化 | migration v7 通用启动 WorkspaceBaseline、候选最终回答原子写入 |
| 恢复 | 新 Harness/Agent 使用 continuation context；PREPARED 重新过 Gate；UNKNOWN/MANUAL 不创建 Agent |
| Completion | Agent 停止后由 WP-08/WP-09 独立验收；`COMPLETED` 与 `ACCEPTED` 分离 |

主要文件：

- `src/guarded_harness/harness.py`
- `src/guarded_harness/agent_adapter.py`
- `src/guarded_harness/workspace_baseline.py`
- `src/guarded_harness/domain.py`
- `src/guarded_harness/state_store.py`
- `src/guarded_harness/storage.py`
- `tests/fake_model.py`
- `tests/test_harness_loop.py`

## 3. 实际命令与结果

执行环境：

```text
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

结果：editable 包和 `smolagents==1.26.0`、pytest、Ruff 安装成功。

目标测试：

```text
python -m pytest tests/test_harness_loop.py -q \
  -W error::pytest.PytestUnraisableExceptionWarning
```

结果：`23 passed`；WP-12 补测另增加 Harness 级故障与并发证据。

全量测试：

```text
python -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning
```

结果：`372 passed in 257.41s`。

静态检查：

```text
python -m ruff check .
python -m ruff format --check .
```

结果：

```text
All checks passed!
105 files already formatted
```

`git diff --check` 通过，无关键 skip，测试使用临时 Git 工作区与临时 SQLite，不访问网络或真实
模型。

## 4. 核心场景证据

| 规格场景 | 验证证据 | 结果 |
| --- | --- | --- |
| WP10-AC-001 原生 Agent/只读调用 | 首次模型收到的 tools 为 GovernedTool + `final_answer`，无 handler 属性；真实 Gate allow | PASS |
| WP10-AC-002 单调用关联 | read call ID、ToolInvocation、ToolOutput、Step、Audit 一致 | PASS |
| WP10-AC-003 多调用顺序 | 同批 read/write/status 在 write 审批处分界，前两项按声明顺序保存，后续 status 未执行 | PASS |
| WP10-AC-004 重复调用 ID | 副作用前失败，Invocation 为 0 | PASS |
| WP10-AC-005 未知工具 | Gate `GATE_TOOL_NOT_FOUND`，模型收到结构化拒绝，Audit 保留原 ID | PASS |
| WP10-AC-006 Schema 注入 | 额外字段在 Gate schema 阶段拒绝，无 ToolInvocation | PASS |
| WP10-AC-007 路径逃逸 | `artifacts/../../.env` 被 Sandbox 拒绝，无业务 handler 身份 | PASS |
| WP10-AC-008 未审批写 | 返回 WAITING_APPROVAL，目标文件不存在，审批 Checkpoint 保存待执行调用 | PASS |
| WP10-AC-009 审批后 resume | 新 Harness/Agent 执行写入一次；重放提案被抑制；最终 Completion ACCEPTED | PASS |
| WP10-AC-010 Step 预算 | STOPPED / BUDGET_EXHAUSTED / NOT_EVALUATED，无 Completion | PASS |
| WP10-AC-011 token-cost 预算 | 可信 Decimal 策略计量，usage 封顶并停止 | PASS |
| WP10-AC-012 wall-time 预算 | 固定时钟推进后 STOPPED，usage=1 秒上限，无 Completion | PASS |
| WP10-AC-013 模型异常 | FAILED / INTERNAL_ERROR，无伪最终回答或 Completion | PASS |
| WP10-AC-014 Checkpoint | 实际观察到 RUN_STARTED、TOOL_SUCCEEDED、APPROVAL_REQUESTED、STEP_COMPLETED、APPROVAL_SETTLED、COMPLETION_RECORDED | PASS |
| WP10-AC-015 PREPARED 重启 | 审批后新 Harness 通过 Gate reenter，不重复扣减工具调用 | PASS |
| WP10-AC-016 STARTED 无结果 | synthetic unknown handler 进入 BLOCKED/UNKNOWN_RESULT；resume 不调用模型 | PASS |
| WP10-AC-017 确定结果/缺 Checkpoint | `test_result_committed_before_checkpoint_resumes_without_replay`：结果已提交、TOOL_SUCCEEDED Checkpoint 未提交后，新进程复用结果且 read handler 仍只有一次 | PASS |
| WP10-AC-018 工作区变化 | 审批后修改源码，resume 返回 BLOCKED/RECOVERY_WORKSPACE_CHANGED，审批失效，无 Agent | PASS |
| WP10-AC-019 预算停止与完成声明 | 预算停止路径不进入 Completion，保持 NOT_EVALUATED | PASS |
| WP10-AC-020 无交付物 final answer | candidate 被持久化，Completion REJECTED，`claim_without_evidence` 失败 | PASS |
| WP10-AC-021 Candidate Audit 故障 | 候选回答事务回滚，Run FAILED，未进入 VERIFYING | PASS |
| WP10-AC-022 并发 resume | `test_concurrent_resume_processes_execute_write_once`：两个真实 CLI 子进程并发 resume，只产生一次 write_file，另一进程获得稳定 lease 冲突或已终态 | PASS |
| WP10-AC-023 smolagents 不兼容 | 版本检查在数据库/仓库写入前失败，模型调用为 0 | PASS |
| WP10-AC-024 测试修复闭环 | 基线失败 -> 审批 patch -> 新进程 resume -> 最终 JUnit 全绿 -> ACCEPTED | PASS |
| WP10-AC-025 离线确定性 | 目标测试和全量测试无网络、真实模型、凭据、关键 skip | PASS |

## 5. 飞书核心验收映射

| 编号 | 本次证据 | 结论 |
| --- | --- | --- |
| G1 | Schema 注入测试，无 Invocation | PASS |
| G2 | 三次等价 read，仅两次进入 handler，第三次 Gate deny + Audit | PASS |
| G3 | 未审批写 WAITING，目标文件不变 | PASS |
| G4 | 审批后写入一次，后续 replay suppression | PASS |
| S1 | Agent 路径逃逸被 Sandbox 拒绝 | PASS |
| S3 | 模型工具 schema 只暴露受控参数；测试修复命令由可信配置执行 | PASS |
| C1 | 多类 Checkpoint 实际落盘 | PASS |
| C2 | Unknown result 不自动重放 | PASS |
| C3 | 新 Harness 恢复，计数保留，写入不重复 | PASS |
| C4 | 工作区变化导致 BLOCKED，旧审批失效 | PASS |
| A1 | 拒绝 Audit 保留原 toolCallId、rule、decision、actor | PASS |
| H1 | 无交付物 final answer REJECTED | PASS |
| H2 | 测试修复最终报告绑定当前工作区；WP-09 既有失效测试通过 | PASS（跨包） |
| H3 | Step/token/wall-time 预算均明确 STOPPED/NOT_EVALUATED | PASS |
| T1 | 372 项全量测试确定性通过 | PASS |

P1 的 S2、S4、A2、T2 没有作为 WP-10 的独立完成门禁；其中依赖底层实现和真实模型的最终项目级
证据仍属于 WP-12/P1。

## 6. 持久化与兼容性

- schema version 从 6 升至 7；
- 新表 `run_workspace_baselines` 使用 Run 主键、摘要、配置指纹和 append-only 触发器；
- v3 -> v7、v4 -> v7、v5 -> v7、v6 -> v7 migration 均保留既有 Run；
- `RUN_STARTED` Checkpoint boundary 已加入稳定枚举；
- `smolagents` 版本、方法签名、ToolCall/ToolOutput/ActionOutput 字段有启动前契约检查；
- 没有修改 site-packages、复制 Agent Loop 或 monkey patch smolagents。

## 7. 已知限制与剩余门禁

1. 自动化只使用 Fake Model；真实模型仍为 D-07 明确排除的 P1。
2. `ToolCallingAgent.process_tool_calls` 是公开方法，但适配仍与 1.26.0 精确绑定；升级必须重跑
   WP-10 契约测试并评审。
3. 工具 wrapper 的模型 schema 对部分 JSON Schema 细节采用兼容表示，最终安全校验仍由 Gate
   原始 draft 2020-12 schema 完成。
4. P0 多调用全部串行，没有启用只读并发。
5. 运行时 token-cost 是资源单位，不是某真实 Provider 的账单价格。
6. WP-08/WP-09 的自动化 AC 已补齐，但人工语义和测试质量审查尚未完成；在这些依赖验收前，
   WP-10 不进入 `DONE`。
7. 当前报告为 `review`，需要与 WP-11 下游接口评审后再转为最终 accepted 证据。

## 8. 验收结论

WP-10 的本地公共接口、Fake Model Agent 闭环、审批恢复、预算、Checkpoint、Completion 和
smolagents 1.26.0 适配已通过目标测试与全量回归。实现满足 WP-10 规格声明的 P0 能力，但受上游
WP-08/WP-09 acceptance 和真实模型/P1 非目标约束，工作包当前应保持 `VERIFYING`，不能标记为
`DONE` 或宣称整个项目已最终交付。
