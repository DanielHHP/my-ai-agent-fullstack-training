# WP-06 写入与命令能力包实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-06 写入与命令能力包 |
| 文档状态 | done |
| 计划版本 | 1.0.0 |
| 创建日期 | 2026-11-06 |
| 批准日期 | 2026-11-06 |
| 完成日期 | 2026-11-06 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 前置依赖 | WP-03 v1.1.0 accepted；WP-04 v1.0.0 accepted；WP-05 v1.0.0 accepted |
| 当前结论 | T00 至 T09 已完成，验收报告 accepted |
| 下游影响 | WP-07 恢复、WP-09 测试修复验收、WP-10 Agent 装配、WP-12 最终交付 |

本计划不重新定义规格。施工中发现行为、接口、错误码或安全边界缺口时，必须停止当前任务，先更新并
评审 `spec.md`；涉及 Sandbox 公共接口、审批摘要、持久化格式或跨包错误码时，还必须先更新
`design.md` / `decisions.md` 和对应上游规格。

## 2. 计划目标与完成定义

WP-06 完成后必须通过公共接口和临时 Git 工作区证明：

1. `write_file`、`apply_patch` 和 `run_test` 由可信工厂注册，只可通过 Gate 调用；
2. 模型可见快照不泄露 handler、Sandbox、绝对路径、argv 或可执行文件；
3. 写路径在审批前经过 Sandbox 与任务写范围双重检查，非法路径不会发生 I/O；
4. 未审批写入停在 `WAITING_APPROVAL`，审批只改变授权，真实文件保持不变；
5. 已批准写入只执行一次，参数/工作区/配置变化使旧审批失效；
6. `write_file` 使用 Sandbox 安全 I/O 原子创建或覆盖一个文件；
7. `apply_patch` 对单文件执行唯一、顺序、全有或全无的精确文本替换；
8. `run_test` 只执行任务配置中的 `acceptance_test` argv，拒绝一切模型命令注入；
9. 命令超时和取消真实终止 POSIX 进程组，非幂等未知结果不自动重试；
10. 写入和命令结果产生正确 Evidence，State、Audit、Approval 与 `tool_call_id` 可关联；
11. 目标测试、全量测试、Ruff lint 和格式检查通过；
12. 形成实际命令和故障证据驱动的 `acceptance-report.md`，再决定是否 `accepted`。

完成 WP-06 不等于测试修复最终验收、崩溃恢复、Agent 或 CLI 已完成。

## 3. 文件范围与依赖方向

### 3.1 计划文件布局

```text
src/guarded_harness/
├── tools/
│   ├── __init__.py       # 扩展：副作用工具定义、稳定排序和可信注册
│   ├── common.py         # 扩展：写入 schema、任务写范围、digest/输出辅助
│   ├── fs_write.py       # 新增：write_file 与 apply_patch handler
│   └── exec.py           # 新增：run_test handler 和进程结果映射
├── domain.py             # 扩展：WP-06 稳定错误码
└── __init__.py           # 扩展：按需导出公共契约

tests/
├── test_tools.py                  # 扩展：三工具正常、拒绝、边界与 Evidence
├── test_gate.py                   # 扩展：审批、串行副作用、未知结果集成
├── test_sandbox.py                # 扩展：真实命令超时/取消集成证据
└── conftest.py                    # 按需扩展测试命令与故障 fixture

docs/work-packages/wp-06-write-and-command/
├── spec.md
├── plan.md
└── acceptance-report.md           # 实测完成后创建
```

WP-06 不创建 `checkpoint.py`、`completion.py`、`harness.py`、`agent_adapter.py` 或 `cli.py`。

### 3.2 依赖方向

```text
domain.py
   ^
   |
sandbox.py ─────> config.py
   ^
   |
tools/common.py
   ^          ^
   |          |
fs_write.py  exec.py
   \          /
    \        /
     tools/__init__.py ─────> registry.py

Gate ─────> registry.py + policy.py + sandbox.py + state/audit/approval
tests ────> public factory + Gate + Sandbox + State/Audit
```

禁止：

- `tools/*` 导入 `gate.py`、`state_store.py`、`audit.py`、`approval.py` 或 `checkpoint.py`；
- `fs_write.py` 直接打开、替换或删除目标仓库文件；
- `exec.py` 直接调用 `subprocess`、`os.system` 或 `Popen`；
- `exec.py` 接受模型 argv、env、cwd、shell、可执行文件、timeout 或 output limit；
- Handler 根据任务名称构造文件写入规则或测试命令；
- 在模型快照、错误、Audit 或 Evidence 中放入未脱敏命令输出或完整环境。

## 4. 详细设计

### 4.1 可信装配

`register_side_effect_tools(registry, sandbox, config)` 的候选流程：

```text
validate public argument types
  -> build write_file definition
  -> build apply_patch definition
  -> if task is test_repair and acceptance_test is configured:
       build run_test definition
  -> validate schemas, handlers and names
  -> preflight registry name conflicts
  -> register definitions in stable name order
  -> return registered names
```

写入和 patch 的 normalizer 闭包只捕获可信 `Sandbox` 与 `ResolvedHarnessConfig`：

```text
normalize_write(args, context):
  checked = sandbox.check_write(args.path)
  require_task_write_scope(config, checked.workspace_relative)
  return args with path = checked.workspace_relative
```

任务写范围匹配使用与 Sandbox 一致的规范化 POSIX 路径和 glob 语义。实现前必须用差异测试覆盖 `*`、
`**`、同前缀兄弟目录、大小写和 Unicode 归一化，不能因 helper 与 Sandbox 的 pattern 语义不同产生
第二条安全边界。

### 4.2 `write_file`

handler 流程：

```text
checked = sandbox.check_write(normalized_path)
data = content.encode("utf-8")
reject if data > 10 MiB
if target exists:
    current = sandbox.read_file(path)
    reject non-regular / non-UTF-8 target
    if current.data == data:
        return SUCCEEDED changed=false without physical replacement
    mode = REPLACE
    expected_digest = current.content_digest
else:
    mode = CREATE
    expected_digest = None
result = sandbox.write_file_atomic(
    path,
    data,
    mode=mode,
    expected_content_digest=expected_digest,
    create_parents=True,
)
return structured facts + DIFF EvidenceDraft
```

说明：

- 允许 `create_parents=True` 的唯一依据是可信 `write_file` ToolSpec；父目录仍由 Sandbox 逐级检查；
- 读取后到替换前若目标变化，`expected_content_digest` 冲突失败，不覆盖新内容；
- 相同内容返回 `changed=false`，不再次替换；该语义由 D-50 冻结；
- 所有 `SandboxViolation` 转成稳定 `HandlerResult(FAILED)`，不吞成成功；
- Handler 不直接访问 State、Audit 或 Approval。

### 4.3 `apply_patch`

纯语义阶段先在 Gate 拒绝：

- `edits` 为空；
- `oldText` 为空；
- 所有编辑均为 `oldText == newText`；
- 单段或总 patch 大小超过规格上限；
- 路径不是单一工作区相对路径。

handler 流程：

```text
checked = sandbox.check_write(normalized_path)
file = sandbox.read_file(path)
reject NUL, invalid UTF-8, oversize, non-regular target
current = file.data.decode("utf-8")
next = current
for edit in edits:
    count = non-overlapping_count(next, edit.oldText)
    if count == 0: return FAILED TOOL_PATCH_CONTEXT_NOT_FOUND
    if count > 1: return FAILED TOOL_PATCH_AMBIGUOUS
    index = next.index(edit.oldText)
    next = next[:index] + edit.newText + next[index + len(edit.oldText):]
if next == current: return FAILED TOOL_PATCH_NO_CHANGE
encoded = next.encode("utf-8")
reject if encoded > 10 MiB
sandbox.write_file_atomic(
    path,
    encoded,
    mode=REPLACE,
    expected_content_digest=file.content_digest,
    create_parents=False,
)
return edit count, byte delta, old/new digest, DIFF EvidenceDraft
```

全部文本替换先在内存中完成；只有整批成功后才做一次原子替换。任何上下文缺失、歧义、编码或大小
失败都不写入目标。字符串替换使用索引拼接，不调用会解释 `$`、`&` 或反向引用的替换 API。

### 4.4 `run_test`

`run_test` 只注册给测试修复任务。Sandbox 已从 `acceptance.test_command` 注册：

```text
command_id = "acceptance_test"
argv = config.config.task.acceptance.test_command
effect_class = NON_IDEMPOTENT
timeout/output limits = trusted CommandPolicy
```

handler 流程：

```text
if arguments != {"command_id": "acceptance_test"}: fail closed
result = sandbox.execute("acceptance_test")
convert SandboxProcessResult to bounded HandlerResult
attach COMMAND EvidenceDraft only for deterministic, untruncated results
```

结果映射：

| Sandbox 结果 | HandlerResult | Gate Invocation | 说明 |
| --- | --- | --- | --- |
| `SUCCEEDED` | `SUCCEEDED` | `SUCCEEDED` | 退出码 0，不代表最终验收通过 |
| `FAILED` | `FAILED` / `TOOL_TEST_FAILED` | `FAILED` | 退出码非零 |
| `START_FAILED` | `FAILED` / `SANDBOX_COMMAND_START_FAILED` | `FAILED` | 命令没有启动 |
| `TIMED_OUT` | `TIMED_OUT` | `TIMED_OUT` | 仅用于明确幂等命令；当前 `run_test` 预期不会产生 |
| `CANCELLED` | `UNKNOWN_RESULT` | `UNKNOWN_RESULT` | 非幂等命令取消 |
| `UNKNOWN_RESULT` | `UNKNOWN_RESULT` | `UNKNOWN_RESULT` | 无法确认副作用 |

`run_test` 不接受任何模型超时参数；超时和取消来自可信 Sandbox/CommandPolicy。当前 WP-06 不新增
Harness 取消编排；真实取消测试使用可信注入的 `CancellationToken` 验证 Sandbox 结果和 handler
映射，最终由 WP-10/WP-11 接线到运行生命周期。

### 4.5 Evidence 与 digest

新增纯辅助：

- `digest_file_version(data) -> sha256:...`；
- `digest_command_result(result) -> sha256:...`，至少覆盖 command_id、exit code、signal、超时、
  取消、截断和已采集输出；
- `write_evidence(path, digest) -> EvidenceDraft(kind=DIFF, ...)`；
- `command_evidence(command_id, digest) -> EvidenceDraft(kind=COMMAND, ...)`。

要求：

- digest 使用 UTF-8、稳定键序和严格 JSON，不包含绝对路径、时间戳、宿主环境或进程 PID；
- EvidenceDraft 只携带摘要事实，不携带文件全文或原始环境；
- 截断命令不产生“完整报告”证据；
- `UNKNOWN_RESULT` 不产生新成功 Evidence；
- 文件变化后保留旧摘要，由 WP-09 比较当前文件版本决定失效。

### 4.6 审批与并发

- `write_file` 与 `apply_patch` 的 ToolSpec 使用 `ApprovalRequirement.REQUIRED`；
- `run_test` 使用 `NEVER`，但任务列表命中时由 `PolicyEngine.requires_approval` 提升；
- 三个工具均为 `ConcurrencyClass.SERIAL`、`RetryPolicy.NEVER`、`idempotent=false`；
- Gate Run 级锁负责同一 Run 串行，审批只能 claim/consume 一次；
- 不同工具调用对同一文件的内容/路径变化会改变摘要，使旧审批失效；
- Handler 不依赖内存审批状态，只从 Gate 获得的已授权调用执行。

## 5. 施工任务卡

| 任务 | 实现内容 | 依赖 | 对应规格 | 主要测试 | 完成条件 |
| --- | --- | --- | --- | --- | --- |
| WP06-T00（已完成） | 关闭 WP06-OPEN-001 至 005，冻结 spec 并批准 plan | 无 | 第 14 节 | 文档评审 | D-48 至 D-52 已记录，版本已提升 |
| WP06-T01 | 新增错误码、严格 schema、写范围与 digest 纯辅助 | T00 | 6.2、6.3、6.4、11 | `test_tools.py` schema/helper | 无 I/O；边界纯测通过 |
| WP06-T02 | 实现 `write_file` 原子创建/覆盖 | T01 | 6.3、7、8、10 | `test_tools.py` write 组 | CREATE/REPLACE/no-op/超限/冲突 |
| WP06-T03 | 实现 `apply_patch` 单文件全有或全无替换 | T01、T02 | 6.4、7、8、10 | `test_tools.py` patch 组 | 空/缺失/歧义/多段失败不写 |
| WP06-T04 | 实现 `run_test` 与进程结果映射 | T01 | 6.5、9、10 | `test_tools.py` exec 组；`test_sandbox.py` | 固定 argv、退出码、超时、截断 |
| WP06-T05 | 扩展可信注册、任务写范围和模型快照 | T02 至 T04 | 4、5、6、7 | `test_tools.py` factory 组 | 工具快照稳定；不泄内部信息 |
| WP06-T06 | Gate 审批/执行/重放与真实写入集成 | T05 | 8、10、12 | `test_gate.py` side-effect 组 | 审批前无写入，成功后最多一次 |
| WP06-T07 | 故障、脱敏、并发和 Evidence 关联 | T06 | 9、10、11、12 | `test_gate.py` fault 组 | 未知不重放；审计可关联 |
| WP06-T08 | 全量回归、Ruff、格式和文档一致性 | T07 | 15 | 全量测试 | 无失败、无未解释 skip |
| WP06-T09 | 编写 acceptance-report 并决定能否进入 WP-07 | T08 | 飞书映射、15 | 复跑证据 | 报告 accepted 或明确 rejected/blocked |

## 6. T00 冻结记录

需求方已于 2026-11-06 确认以下方案：

1. `apply_patch` 采用单文件 `oldText`/`newText` 编辑列表；
2. `run_test` 只接受 `{"command_id":"acceptance_test"}`；
3. `apply_patch` 拒绝 no-op，`write_file` 相同内容返回 `changed=false`；
4. 两个写工具固定必审，`run_test` 由任务配置决定是否必审；
5. `run_test` 固定非幂等，任何超时/取消进入 `UNKNOWN_RESULT`。

完成状态：

- `spec.md` 已提升为 `1.0.0 / frozen`；
- 本计划已提升为 `1.0.0 / approved`；
- 已追加 D-48 至 D-52，并同步 `docs/README.md`、`docs/design.md` 和索引；
- 当前方案不改变 WP-03 的公开路径匹配或命令取消契约，无需上游兼容修订。

## 7. 测试计划

### 7.1 测试基础设施

在现有 `tests/test_tools.py` fixture 基础上扩展：

- 支持指定 `TestRepairAcceptance` 的 test command；
- 提供可计数 handler/进程包装以证明拒绝前调用次数为 0；
- 提供精确审批 fixture：创建待审批调用、批准、重新 invoke；
- 提供可配置 CommandPolicy 超时、输出上限和 cancel token；
- 使用临时 Git 工作区和 fake 输出中的伪密钥，不访问网络或真实模型。

### 7.2 严格 schema 与注册

- 额外字段、缺失字段、错误类型和空 edits 在 Gate schema 阶段拒绝；
- `run_test` 只允许 `command_id` 常量，argv/env/cwd/shell/timeout 字段全部拒绝；
- 快照字段稳定，按工具名排序，不含 handler、Sandbox、本机绝对路径和真实 argv；
- 已注册冲突时工厂整批失败，不留下部分注册。

### 7.3 写入安全

- 路径穿越、绝对越界、符号链接逃逸、敏感文件和平台禁写；
- 代码理解只写 deliverable；
- 测试修复只写 `allow_write`；
- 未审批写入不选择 CREATE/REPLACE，不创建父目录；
- 审批后参数/路径变化、跨 Run、重放和过期全部拒绝；
- CREATE、REPLACE、content digest 冲突、10 MiB 边界和原子失败无部分文件；
- 同一 Run 并发写入只产生一个成功副作用。

### 7.4 Patch 安全

- 空数组和全 no-op 在 Gate handler 前拒绝；
- `oldText` 0 次、1 次、多次和重叠/顺序应用；
- 第二段失败时第一段也不落盘；
- 非 UTF-8、NUL、目录和超限目标拒绝；
- 成功后输出内容摘要正确，Evidence 绑定新版本；
- 文件后来被改写时旧 Evidence 保持历史但被 WP-09 视为过期。

### 7.5 命令安全

- 配置外 `command_id`、任意 argv、shell 字符、env、cwd、timeout 注入在 Popen 前拒绝；
- 成功、非零退出、启动失败、超时、取消和未知分类；
- 超时后父进程和后代进程均终止；
- 输出截断、脱敏和 observation 上限；
- 非幂等超时/取消不重试，不产生成功 Evidence；
- `run_test` 的 Gate 结果与 Audit/State/Approval 关联。

### 7.6 规格场景到测试映射

| 规格场景 | 测试文件 | 计划测试函数 | 最小证据 |
| --- | --- | --- | --- |
| WP06-AC-001 | `tests/test_tools.py` | `test_side_effect_factory_registers_stable_snapshot` | 快照与元数据 |
| WP06-AC-002 | `tests/test_tools.py` | `test_side_effect_schemas_reject_before_handler` | handler counter=0 |
| WP06-AC-003 | `tests/test_gate.py` | `test_write_path_escape_denied_before_io` | 文件未变、审计 |
| WP06-AC-004 | `tests/test_gate.py` | `test_write_sensitive_and_protected_paths_denied` | 敏感目录不变 |
| WP06-AC-005 | `tests/test_gate.py` | `test_code_understanding_write_requires_deliverable` | handler=0 |
| WP06-AC-006 | `tests/test_gate.py` | `test_test_repair_write_requires_allow_write` | 目录外不变 |
| WP06-AC-007、WP06-AC-008 | `tests/test_gate.py` | `test_unapproved_writes_wait_without_side_effect` | WAITING、文件摘要 |
| WP06-AC-009 | `tests/test_gate.py` | `test_approved_write_executes_at_most_once` | 物理写入计数 1 |
| WP06-AC-010 | `tests/test_gate.py` | `test_changed_write_binding_invalidates_approval` | 旧审批不执行 |
| WP06-AC-011、WP06-AC-012 | `tests/test_tools.py` | `test_write_file_create_replace_and_atomic_conflict` | 模式、摘要、Evidence |
| WP06-AC-013 | `tests/test_tools.py` | `test_write_rejects_oversize_and_special_targets` | 无部分文件 |
| WP06-AC-014 | `tests/test_gate.py` | `test_patch_empty_and_noop_rejected_before_handler` | handler=0 |
| WP06-AC-015、WP06-AC-016 | `tests/test_tools.py` | `test_patch_context_missing_and_ambiguous` | 文件摘要不变 |
| WP06-AC-017 | `tests/test_tools.py` | `test_patch_later_failure_does_not_commit_earlier_edits` | 原子失败 |
| WP06-AC-018 | `tests/test_tools.py` | `test_patch_sequential_edits_and_versioned_evidence` | 最终文本、摘要 |
| WP06-AC-019 | `tests/test_gate.py` | `test_run_test_rejects_command_injection_before_popen` | Popen=0 |
| WP06-AC-020、WP06-AC-021 | `tests/test_tools.py` | `test_run_test_reports_success_and_nonzero_exit` | 退出码、Evidence |
| WP06-AC-022、WP06-AC-023 | `tests/test_sandbox.py`、`tests/test_tools.py` | `test_run_test_timeout_and_cancel_are_unknown` | PID/进程组、UNKNOWN |
| WP06-AC-024 | `tests/test_gate.py` | `test_command_output_is_bounded_and_redacted` | 截断、密钥不存在 |
| WP06-AC-025 | `tests/test_gate.py` | `test_result_persistence_failure_never_claims_success` | 不可重放状态 |
| WP06-AC-026 | `tests/test_tools.py` | `test_later_file_change_invalidates_old_write_evidence` | 两版 digest |
| WP06-AC-027 | `tests/test_gate.py` | `test_parallel_writes_are_serialized` | 单一成功副作用 |
| WP06-AC-028 | `tests/test_tools.py` | `test_write_command_suite_is_offline_and_repeatable` | 无网络/模型/凭据 |

## 8. 推荐施工顺序与可回滚增量

```text
WP06-T00 决策冻结
  -> WP06-T01 契约与纯辅助
  -> WP06-T02 write_file
  -> WP06-T03 apply_patch
  -> WP06-T04 run_test
  -> WP06-T05 factory / snapshot
  -> WP06-T06 Gate 审批与真实副作用集成
  -> WP06-T07 故障、脱敏、并发和 Evidence
  -> WP06-T08 全量验证
  -> WP06-T09 acceptance-report 与下游交接
```

每个可提交增量必须包含对应测试。T02 至 T04 可分别回滚，但 T05 之后回滚必须同时移除工具注册、
新增错误码导出和测试假设，避免出现“模型可见但无法安全执行”的半成品。

回滚点：

- 构造成员或 schema 失败：移除该工具的注册增量，不影响已验收 WP-05；
- Gate 集成失败：保留纯 handler 测试，但不对外注册工具；
- 命令超时/进程组失败：停止 WP-06，不能用放宽 Sandbox 超时或关闭非幂等检查绕过；
- Evidence 持久化失败：不进入验收报告，先修复 Gate/State 事务语义；
- 公共契约变更无法与上游兼容：将工作包标记 `blocked`，回到规格评审。

## 9. 风险、限制与人工处理

| 风险 | 影响 | 缓解/边界 |
| --- | --- | --- |
| 多文件 patch 无法原生原子提交 | 部分文件可能已修改 | P0 限定单文件；多文件留待独立设计 |
| 测试命令本身可能修改工作区 | 测试结果或后续状态不确定 | 固定 argv、非幂等、超时/取消进入 UNKNOWN；最终 diff 由 WP-09 检查 |
| 输出截断导致测试报告不完整 | 不能证明测试集合完整 | 标记截断；WP-09 拒绝截断报告 |
| 可信测试代码拥有当前用户权限 | 可访问网络或工作区外资源 | WP-03 威胁模型限制；只用于可信仓库 |
| 审批本地身份未密码学认证 | 不能证明真实操作者 | 沿用 D-24，P0 明确限制 |
| 路径 pattern helper 与 Sandbox 语义不一致 | 可能扩大写范围 | 共享/差分测试；不一致时失败关闭 |
| Handler 后结果事务失败 | 文件已修改但无成功记录 | 进入 UNKNOWN，禁止自动重放；WP-07 人工核对 |
| 同内容 write 仍消耗审批 | 审批额度浪费 | 返回 `changed=false`，不物理写入；是否保留由 OPEN-003 确认 |

## 10. 下游交接

WP-06 完成后向 WP-07 交付：

- `STARTED` 后无结果的真实写入/命令不可重放语义；
- 文件内容摘要、命令结果摘要和 `tool_call_id` 关联；
- 超时、取消、进程组终止和 `UNKNOWN_RESULT` 事实。

向 WP-09 交付：

- 可执行验收命令的 `COMMAND` Evidence 和退出码；
- 写入后的文件内容版本和 `DIFF` Evidence；
- 文件变化后旧 Evidence 保留、当前版本可比较的事实；
- 截断命令不得被当成完整测试报告。

向 WP-10/WP-11 交付：

- 三个工具的严格模型 schema 和快照；
- 审批需求与 Gate 重放行为；
- 不新增 Agent 侧命令解释或 handler 直达路径的约束。

## 11. 实际完成情况

截至本计划批准时：

- WP06-T00 已完成，D-48 至 D-52 已冻结；
- WP06-T01 至 T07 已按规格完成，未扩大 frozen spec 的公共行为；
- `tests/test_tools.py`、`tests/test_gate.py`、`tests/test_sandbox.py` 已扩展；
- WP06-T08 实测目标测试 `124 passed`、全量测试 `221 passed`，Ruff 和格式检查通过；
- WP06-T09 已生成 [acceptance-report.md](acceptance-report.md)，结论为 accepted；
- 未修改运行时依赖，未侵入 `smolagents` 核心。

下游状态：

- WP-07 可以依赖真实写入/命令的 STARTED、UNKNOWN_RESULT 和结果摘要事实；
- WP-09 可以消费 DIFF/COMMAND Evidence，并继续实现测试基线与节点验收；
- WP-10/WP-11 需要完成运行生命周期取消接线和跨进程审批恢复。
