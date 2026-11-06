# WP-04 治理执行内核验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-04 治理执行内核 |
| 报告状态 | accepted |
| 报告版本 | 1.0.0 |
| 验收日期 | 2026-11-05 |
| 对应规格 | `spec.md` v1.0.0 / frozen |
| 对应计划 | `plan.md` v1.0.0 |
| 代码基线 | `eb29764`，WP-04 改动位于未提交工作区 |
| 操作系统 | Darwin 22.6.0 x86_64 |
| Python / pip | Python 3.13.5 / pip 25.1.1 |
| 关键依赖 | `smolagents==1.26.0`、`pydantic==2.14.1`、`jsonschema==4.26.0` |
| 飞书输入 | 作业文档 revision 14；“核心验收”内嵌表 revision 11 |

## 2. 验收范围

本报告验收 Registry、Policy、Gate、参数规范化、审批等待/claim、执行意图、结果生命周期、
重复/replay、工具预算、审计、observation 和测试 handler。

本报告不宣称：

- 真实文件、Git、写入、patch 或测试命令 handler 已完成；
- `ToolCallingAgent`、Harness、CLI、Checkpoint 或 Completion 已完成；
- G3/G4/S1/S3/C2/C3/H3 已完成真实仓库端到端验收；
- Python 同步 handler 可被硬超时终止。

## 3. 交付物

| 交付物 | 状态 | 说明 |
| --- | --- | --- |
| `src/guarded_harness/registry.py` | PASS | 可信工具注册、schema 检查、模型安全快照 |
| `src/guarded_harness/policy.py` | PASS | 可见性、角色权限、模式、风险、审批和重复上限策略 |
| `src/guarded_harness/gate.py` | PASS | 固定检查链、审批、意图、STARTED、结果和未知结果 |
| `src/guarded_harness/domain.py` | PASS | Observation v2、handler/context/result/evidence 类型和 Gate 错误码 |
| `src/guarded_harness/state_store.py` | PASS | 公共 Run/Invocation revision 读取和租约复核 |
| `src/guarded_harness/approval.py` | PASS | 拒绝/过期与 Run 终态同事务收口 |
| `pyproject.toml` | PASS | 固定 `jsonschema>=4.23,<5` 运行时依赖 |
| `tests/test_registry_policy.py` | PASS | 4 项 Registry/Policy 测试 |
| `tests/test_gate.py` | PASS | 27 项 Gate、审批、生命周期、故障和并发测试 |
| WP-04 spec / plan / report | PASS | D-37 至 D-44、场景和实际证据同步 |

## 4. 执行证据

安装：

```text
.venv/bin/python -m pip install -e '.[dev]'
Successfully installed attrs-26.1.0 guarded-harness-0.1.0 jsonschema-4.26.0
jsonschema-specifications-2025.9.1 referencing-0.37.0 rpds-py-2026.9.1
```

目标测试：

```text
.venv/bin/python -m pytest tests/test_registry_policy.py tests/test_gate.py -q
31 passed in 8.72s
```

全量测试：

```text
.venv/bin/python -m pytest -q
176 passed in 22.28s
```

Lint、格式和依赖：

```text
.venv/bin/python -m ruff check .
All checks passed!

.venv/bin/python -m ruff format --check .
45 files already formatted

.venv/bin/python -m pip check
No broken requirements found.

git diff --check
exit 0，无输出
```

目标测试由 4 项 Registry/Policy 和 27 项 Gate 测试组成；
`pytest --collect-only` 分别确认 `4 tests collected` 与 `27 tests collected`。
全量执行无 failed、error、skip 或 xfail。

## 5. 规格场景映射

| 规格场景 | 自动化证据 | 结果 |
| --- | --- | --- |
| WP04-AC-001、002 | `test_registry_snapshot_contains_only_model_safe_specs`、`test_registry_rejects_duplicate_and_invalid_schemas` | PASS |
| WP04-AC-003、004 | `test_visibility_permission_and_plan_mode_are_enforced` | PASS |
| WP04-AC-005 | `test_schema_rejections_have_zero_handler_calls` | PASS |
| WP04-AC-006、008 | `test_sandbox_path_escape_and_command_injection_are_denied`、`test_trusted_normalizer_failure_is_semantic_denial` | PASS |
| WP04-AC-007 | 同上，路径拒绝时 read handler 计数为 0 | PASS |
| WP04-AC-009、010、011 | `test_visibility_permission_and_plan_mode_are_enforced` | PASS |
| WP04-AC-012 | `test_g3_wait_and_g4_one_time_execution` | PASS |
| WP04-AC-013 | `test_changed_arguments_invalidate_pending_approval_binding`、`test_rejected_approval_returns_stable_gate_denial`、`test_state_checkpoint.py::test_approval_rejects_binding_mismatch_and_self_approval` | PASS |
| WP04-AC-014 | `test_g3_wait_and_g4_one_time_execution` | PASS |
| WP04-AC-015 | `test_g2_third_equivalent_read_is_denied` | PASS |
| WP04-AC-016 | `test_g3_wait_and_g4_one_time_execution` 的新 ID replay 分支 | PASS |
| WP04-AC-017 | `test_same_call_id_with_changed_arguments_is_rejected` | PASS |
| WP04-AC-018 | `test_budget_and_started_before_handler` | PASS |
| WP04-AC-019 | `test_started_is_visible_inside_handler` | PASS |
| WP04-AC-020 | `test_handler_failure_is_deterministic`、`test_confirmed_idempotent_timeout_persists_timed_out` | PASS |
| WP04-AC-021 | `test_handler_timeout_is_unknown_and_not_retried`、`test_unknown_write_keeps_approval_claimed_and_never_retries` | PASS |
| WP04-AC-022 | `test_result_audit_failure_leaves_invocation_started` | PASS |
| WP04-AC-023 | `test_allow_audit_failure_never_reaches_handler` | PASS |
| WP04-AC-024 | `test_deny_audit_failure_still_blocks_handler` | PASS |
| WP04-AC-025 | `test_denial_audit_contains_required_a1_fields` | PASS |
| WP04-AC-026 | `test_output_is_bounded_redacted_and_results_are_audited` | PASS |
| WP04-AC-027 | `test_concurrent_equivalent_calls_are_serialized`、`test_concurrent_approved_call_executes_at_most_once` | PASS |
| WP04-AC-028 | `test_invalid_handler_output_becomes_unknown` | PASS |
| WP04-AC-029 | `test_visibility_permission_and_plan_mode_are_enforced`：快照可见的写工具仍因角色被拒绝 | PASS |
| WP04-AC-030 | 全量目标测试使用 fake clock/handler/fault，无真实模型、网络和用户凭据 | PASS |

WP04-AC-024 验证拒绝审计写入失败时返回 `AUDIT_WRITE_FAILED` 且 handler 仍不执行；
这符合“失败关闭优先于保留原拒绝码”的约定。

## 6. 任务卡结论

| 任务 | 状态 | 证据 |
| --- | --- | --- |
| WP04-T00 | DONE | WP-01 revision/lease API、Observation v2、公共导出和契约测试 |
| WP04-T01 | DONE | Registry/Policy 测试、domain round-trip |
| WP04-T02 | DONE | 可见性、角色、plan、风险和审批策略测试 |
| WP04-T03 | DONE | JSON Schema、canonical digest、G1 测试 |
| WP04-T04 | DONE | 路径逃逸和命令注入在 handler 前拒绝 |
| WP04-T05 | DONE | G3 等待、批准、G4 一次性执行和过期收口 |
| WP04-T06 | DONE | PREPARED/STARTED/SUCCEEDED/FAILED/TIMED_OUT/UNKNOWN_RESULT |
| WP04-T07 | DONE | G2、replay、预算、失败窗口和并发写 |
| WP04-T08 | DONE | A1、脱敏、截断、审计/意图/结果故障关闭 |
| WP04-T09 | DONE | 全量测试、lint、格式、依赖检查、索引和验收报告 |

## 7. 飞书核心验收映射

| 编号 | 本包实际证据 | 状态 / 下游 |
| --- | --- | --- |
| G1 | 缺字段、类型错误、额外字段均 handler=0 | WP-04 PASS |
| G2 | 第三次等价只读调用被拒绝，前两次 handler=2 | WP-04 PASS |
| G3 | 写调用进入 WAITING，PENDING 审批存在，handler=0 | WP-04 PASS；真实文件由 WP-06 |
| G4 | 批准后成功一次，同 ID 与新 ID replay 均不重执行 | WP-04 PASS；真实文件由 WP-06 |
| S1 | `artifacts/../../.env` 在 Sandbox/Gate semantic 阶段拒绝，handler=0 | WP-04 部分 PASS；完整由 WP-03/WP-05 |
| S3 | execute 工具只接受 `command_id`，argv 注入在 schema 阶段拒绝 | WP-04 部分 PASS；完整由 WP-06 |
| C2 | 未知 handler 结果持久化 UNKNOWN，审批保持 CLAIMED，不重试 | WP-04 部分 PASS；恢复由 WP-07 |
| A1 | Gate DENY 审计含 rule、decision、actor、tool_call_id、phase | WP-04 PASS |
| A2 | State/Audit/Approval/Invocation 关联字段已产生 | WP-04 部分 PASS；端到端由 WP-12 |
| H3 | 工具调用或步骤预算耗尽时 Gate 拒绝，handler=0 | WP-04 部分 PASS；最终 StopReason 由 WP-10 |
| T1 | WP-04 目标与全量测试确定性通过，无 skip | WP-04 PASS |

## 8. 安全与故障证据

| 边界 | 证据 |
| --- | --- |
| 未注册/不可见工具 | 稳定 Gate 错误，handler 不执行 |
| 未声明字段和类型错误 | JSON Schema 拒绝，handler 计数 0 |
| 路径穿越 | Sandbox 预检拒绝，read handler 计数 0 |
| 命令 argv 注入 | 额外字段在 handler 前拒绝 |
| 权限和 plan 模式 | 稳定 PERMISSION/RISK 阶段拒绝 |
| 未审写 | Run WAITING_APPROVAL，write handler=0 |
| 参数变化 | 同 tool_call_id 不同摘要拒绝 |
| 审批重放 | CONSUMED 后同 ID/新 ID replay suppression，handler 不增加 |
| 审批过期 | 审批 EXPIRED，Run STOPPED/APPROVAL_EXPIRED/NOT_EVALUATED |
| 只读重复 | 前两次成功，第三次 REPEAT 拒绝 |
| 工具预算 | 预算耗尽拒绝，handler 不执行 |
| STARTED 前故障 | Invocation 保持 PREPARED，handler=0 |
| 结果审计故障 | Invocation 保持 STARTED，不返回成功 |
| handler 未知结果 | UNKNOWN_RESULT，不重试，审批不 consume |
| 并发审批写 | 最终 handler=1、审批 consume=1 |
| 输出超限/敏感值 | 有界 preview/truncated，State/Audit/observation 无原值 |

## 9. 限制与剩余风险

1. WP-04 的 handler 是同步 Python callable；协作 deadline 不能强杀失控线程。真实阻塞必须通过
   WP-05/WP-06 的 Sandbox 进程边界。
2. 本包只使用测试 handler，未验证真实文件、Git 或测试命令。
3. 角色权限由可信代码装配，YAML/Harness 接线尚属 WP-10。
4. `Gate` 的 Run 级锁只负责单进程串行；跨进程依赖 WP-01 租约和 SQLite 条件更新。
5. WP-04 不实现自动重试；`retryable` 仅为下游重新经过 Gate 提供信息。
6. 拒绝审计失败时返回 `AUDIT_WRITE_FAILED`，原始拒绝原因不能作为唯一返回码；安全语义仍是
   不执行 handler。

## 10. 验收结论

**accepted**

WP-04 的冻结规格、任务卡、正常/拒绝/故障测试、公共契约、文档和实际复跑证据一致。目标测试
31 项、全量测试 176 项均通过，Ruff lint、格式检查和 `pip check` 通过。WP-05/WP-06 可以在
WP-04 冻结公共接口上接入真实只读和副作用工具。
