# WP-08 验收引擎与代码理解工作包实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-08 验收引擎与代码理解 |
| 文档状态 | in-progress |
| 计划版本 | 1.0.0 |
| 创建日期 | 2026-11-07 |
| 批准日期 | 2026-11-07 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 前置依赖 | WP-04 v1.0.0 accepted；WP-05 v1.0.0 accepted；WP-03/WP-01 公共接口可用 |
| 当前结论 | T00-T08 核心实现已落地；T09/T10 全场景验收和报告待完成 |
| 下游影响 | WP-09 测试修复策略、WP-10 Harness 最终验收编排、WP-12 端到端场景 |

本计划不重新定义规格。规格评审中发现行为、接口、错误码或安全边界缺口时，必须先更新
[spec.md](spec.md) 和相关设计/决策文档，再调整本计划。

## 2. 计划目标与完成定义

WP-08 施工完成后必须通过公共接口、临时 Git 工作区、临时 SQLite 和确定性测试证明：

1. `CompletionChecker` 在 Agent 之外独立读取交付物、源码和 Git 状态；
2. 验收读取不进入模型工具快照，所有实际 I/O 经过 Gate 内部控制面和 Sandbox；
3. 三个必需 Markdown 区可稳定解析，事实、证据定义和引用图可机械验证；
4. 每条事实能追溯到同一 Run 的 Agent 读取 Evidence，且引用范围完整、当前摘要一致；
5. 文档路径、行号、符号链接、编码和输出上限全部失败关闭；
6. 工作区改动仅限声明交付物，HEAD/index/身份和外围保护基线符合规则；
7. 模型完成声明与文件、引用、diff、Audit 事实分开，不能单独产生 `ACCEPTED`；
8. 全部检查结论、Evidence 和 Audit 原子写入；
9. `tests/test_completion.py` 覆盖 WP08-AC 全部场景；
10. 目标测试、全量测试、Ruff lint 和格式检查通过；
11. `acceptance-report.md` 由实际命令、故障注入和场景证据驱动；
12. 不宣称测试修复、Agent/CLI 或真实模型端到端能力已经完成。

## 3. 计划范围与依赖方向

### 3.1 计划文件布局

```text
src/guarded_harness/
├── completion.py       # 新增：代码理解 Completion 引擎
├── domain.py           # 扩展：Completion 错误码/审计事件/必要 DTO
├── gate.py             # 扩展：非模型可见的 Completion 内部只读入口
├── registry.py         # 按需扩展：内部工具定义与模型快照隔离
├── policy.py           # 按需扩展：系统只读角色和内部访问判定
├── state_store.py      # 按需扩展：逐项 Completion Audit/查询
├── audit.py            # 扩展：COMPLETION_CHECKED 等事件
├── sandbox.py          # 复用现有 read/status/diff/workspace 公共事实
├── config.py           # 原则上不扩展字段；只消费已校验 acceptance 配置
└── __init__.py         # 扩展：CompletionChecker 公共导出

tests/
├── test_completion.py          # 新增：解析、证据、工作区、编排和故障矩阵
├── test_domain.py              # 按需扩展：新增错误码/DTO 契约
├── test_gate.py                # 扩展：内部只读入口和模型隔离
├── test_audit.py               # 按需扩展：逐项验收 Audit
├── test_state_checkpoint.py    # 按需扩展：Completion 原子回滚
└── test_tools.py               # 按需验证内部读取复用公共 Sandbox 语义

docs/work-packages/wp-08-completion-code-understanding/
├── spec.md
├── plan.md
└── acceptance-report.md        # 实测完成后创建
```

WP-08 不创建 `harness.py`、`agent_adapter.py`、`cli.py`，也不实现 WP-09 的测试报告解析器。

### 3.2 依赖方向

```text
domain.py <──── completion.py ────> config.py
                         │
                         ├────────> gate.py（invoke_internal）
                         ├────────> sandbox.py（workspace DTO/纯比较）
                         └────────> state_store.py + audit.py

gate.py ───> registry.py + policy.py + sandbox.py + state/audit

tests/test_completion.py
  ───> completion.py + Gate + Sandbox + SQLiteStorage + temp Git repo
```

禁止：

- `completion.py` 导入并直接执行工具 handler；
- `completion.py` 使用裸 `open()`、`Path.read_text()`、`subprocess` 或目录递归；
- 在 Completion 中复制 Gate 的 schema、路径或权限检查；
- 把内部 Completion 工具注册成模型可见工具；
- 通过直接 SQL 写 Completion 或 Audit；
- 在验收器中调用模型、联网或执行测试命令；
- 将 WP-09 的测试节点逻辑提前写入 WP-08。

## 4. 详细设计

### 4.1 决策门禁

决策门禁已完成：

```text
WP08-Q01 Gate 内部控制面/预算
WP08-Q02 分页 Evidence 并集
WP08-Q03 启动基线来源
WP08-Q04 交付物是否必须变化
WP08-Q05 陈旧 Evidence 失效责任
WP08-Q06 完成声明识别
        -> 更新 spec.md
        -> 更新 decisions.md（D-59 至 D-64）
        -> 必要时更新 design.md
        -> spec frozen
        -> plan approved
```

六项问题均已按推荐方案登记为 D-59 至 D-64，`spec.md` 已冻结，本计划已批准。施工中如发现
新行为变化，仍须停止当前任务并重新走规格评审。

### 4.2 Completion 内部控制面

T02 按 D-59 实现：

```text
Gate.invoke_internal(
    run_id,
    tool_name,
    args,
    *,
    lease_token,
    expected_run_revision,
    completion_step_id,
) -> ToolObservation
```

冻结约束：

- 只接受代码固定、独立内部 Registry 注册的只读定义；
- `tool_name`、schema、risk、timeout、retry 和 output limit 来自可信定义；
- Run 必须为 `VERIFYING`，租约和 Run revision 有效；
- actor 固定为 `completion-checker`，角色固定为 `completion_verifier`，模型上下文不能覆盖；
- 不接受 approval、write、run_test 或任意命令工具；
- 不进入 `ToolRegistry.snapshot()`，不进入模型提示/observation；
- internal invocation 有独立固定上限，不能形成无界循环；
- 每次访问写入 ToolInvocation、Evidence（适用时）和 Audit；
- 结果和状态写入失败时失败关闭。

实现采用独立 `CompletionToolRegistry`，不使用普通模型 Registry 的 audience 绕过。内部 step
由 `CompletionChecker` 在单次验收内创建并复用，内部 Invocation 写入现有 ToolCall 事实，但
不增加 Run 的 Agent step/tool-call budget usage。

### 4.3 Markdown 解析

`completion.py` 内使用纯函数/冻结 DTO 完成两层解析：

```text
parse_document_markdown(text)
  -> sections
  -> facts[]
  -> inference_text
  -> evidence_definitions[]

parse_evidence_citation(value)
  -> path
  -> start_line
  -> end_line
```

实现要点：

- 先校验 UTF-8 和 NUL，再按 Markdown 行解析；
- 只识别精确的 H2 ATX 标题 `## 事实`、`## 推断`、`## 代码依据`；
- 重复、缺失、标题后有非空白垃圾或区顺序异常均拒绝；
- “事实”区只提取列表项，每个列表项提取稳定顺序的 `[E<n>]`；
- “推断”区允许自然语言，但空内容必须显式写“无”；
- “代码依据”区每项按固定正则解析，路径和行范围二次规范化；
- 最多 2,000 个事实项、2,000 条证据定义、每个交付物 1 MiB；
- 错误包含稳定 code、交付物相对路径和逻辑位置，不包含整段文档；
- 解析器不使用模型、正则回溯型大表达式或外部 Markdown 扩展。

建议使用少量不可变内部 DTO，例如 `ParsedDocument`、`FactItem`、`EvidenceDefinition`，只在
`completion.py` 内部使用；跨模块仍使用公共 `CompletionCheck`/`CompletionItem`。

### 4.4 Evidence 解析与版本校验

候选算法：

```text
load valid READ evidence for run
  -> index by normalized path
  -> for each citation:
       resolve workspace path through internal read tool
       compute current file digest and line count
       require cited range in bounds
       filter evidence by path, READ, valid, digest=current
       exclude evidence produced by completion actor
       require evidence range union fully covers citation
       require producer ToolCall is an Agent actor allowed by Gate
       retain evidence IDs for CompletionItem/result
```

每个事实项的 `[E<n>]` 解析到定义后，检查项可共享同一 Evidence 但结果中必须稳定去重。
`CompletionCheck.evidence_ids` 和 `CompletionItem.evidence_ids` 使用真实持久化
`evidence_id`，不使用文档本地 `E<n>` 字符串。

按 D-63，发现引用文件变化时：

- 不在内存中把旧 Evidence 改成有效；
- 调用 WP-07 的单向失效操作并拒绝对应检查；
- 失效/拒绝写入 Audit，包含原因码、路径和 evidence ID，不包含源码正文；
- 成功/失败结论受同一事务一致性约束。

### 4.5 工作区与 diff 校验

按 D-61，启动基线从可信 Harness 注入，Completion 先校验：

```text
startup.identity == run.workspace
startup.identity.baseline_fingerprint == run.workspace.baseline_fingerprint
```

然后通过内部只读入口取得当前工作区快照、Git status 和受限 root diff。纯比较：

```text
baseline_entries
current_entries
  -> normalized path-set comparison
  -> allowed = deliverables
  -> forbidden = any changed path outside allowed
  -> HEAD/index/identity check
  -> external protection check
  -> truncation check
```

删除、重命名、符号链接、目录替换、大小写/Unicode 归一化变体都按规范化路径严格比较。Git
输出或 entries 被截断时不得把未见部分当“无变化”，结论只能是 `REJECTED` 或
`UNVERIFIABLE`。

按 D-62，每个交付物必须在本次 Run 中新增或内容变化；仅存在但未变化时产生
`deliverable_changed` 失败项。

如果 `protect_other_git_changes=false`，记录 `external_protection=DISABLED`，不得把外部比较
结果写为绿色事实；验收结论必须带“外围未监控”的限定说明。

### 4.6 Completion 编排

推荐处理顺序：

```text
preflight:
  validate types, config/run/task/kind/lease/status/fingerprint/baseline
  verify audit chain and load current RunSnapshot

collect:
  read all deliverable files through internal control plane
  read all currently referenced source paths through internal control plane
  capture workspace/status/diff through internal control plane

evaluate pure rules:
  parse docs -> citations -> evidence graph
  compare file digests/line counts/ranges
  compare workspace scope and external protection
  detect candidate final answer claim

persist:
  create DOCUMENT/DIFF/Completion-only Evidence as needed
  write CompletionCheck and item results
  append per-check Audit and final COMPLETION_RECORDED
  commit

return persisted CompletionCheck
```

纯规则函数不访问 I/O，便于确定性单元测试。所有可失败步骤产生稳定错误码；一旦已确定不通过，
仍可完成不产生副作用的证据收集，但不得改回通过。

### 4.7 模型完成声明

从 `Run.candidate_final_answer` 读取有界候选文本：

- 按 D-64 的保守标记检测；
- 否定表达不视为完成声明；
- 检测结果只写入 `claim_without_evidence` 项和 Audit 摘要；
- 即使检测为完成声明，也必须由文件、引用、diff 和 Audit 检查决定最终 `ACCEPTED`；
- 检测不到声明不代表文件合法，检测到声明也不自动失败。

### 4.8 持久化与审计

候选事务：

```text
storage.transaction(actor="completion-checker")
  verify lease and Run revision
  add Completion-only Evidence
  record CompletionCheck
  append per-item COMPLETION_CHECKED events
  append final COMPLETION_RECORDED event
commit
```

StateStore 现有 `record_completion` 已检查 Run 为 `VERIFYING`、Run revision 和引用 Evidence。
如果逐项 Audit 需要新的 `AuditEventType`，只追加枚举值并补契约测试；不修改历史迁移。

任何新增 Evidence、CompletionCheck、CompletionItem 或 Audit 写入失败时，整个事务回滚。
只读阶段已经提交的 ToolInvocation/Evidence 不被删除，其事实仍可按序号审计。

### 4.9 并发与幂等

- Completion 对同一 Run 串行化；
- 先检查已有 `CompletionCheck`：完全相同的事实返回原结果，不同内容返回稳定冲突；
- 检查期间 Run revision、config fingerprint、workspace identity、lease 或 Git 状态变化时
  重新读取并重新判定，不自动重用旧结论；
- 两个并发 Completion 不能同时写入不同结论；
- 内部 Gate 调用使用稳定的 completion step/call 键，避免同一内部读取重复无限执行；
- 不因并发重试重新执行写工具或测试命令。

## 5. 施工任务卡

总体顺序如下。T00 已完成，T01 至 T10 处于 `approved / not-started`。

| 任务 | 实现内容 | 对应规格 | 依赖 | 主要文件/测试 |
| --- | --- | --- | --- | --- |
| T00 | 讨论并确认 WP08-Q01 至 Q06，更新 spec/decisions/design | 全部 | 无 | `spec.md`、`decisions.md`、`design.md` |
| T01 | 冻结错误码、审计事件、内部 DTO/结果映射 | WP08-ERR、6.2、8 | T00 | `domain.py`、`audit.py`、`test_domain.py` |
| T02 | 实现 Gate 内部只读控制面和模型隔离 | WP08-6.6、INV-004、AC-023 | T01 | `gate.py`、`registry.py`、`policy.py`、`test_gate.py` |
| T03 | 实现严格 Markdown/证据定义解析 | WP08-6.3、AC-005 至 AC-008 | T01 | `completion.py`、`test_completion.py` |
| T04 | 实现 Evidence 追溯、范围覆盖和版本比较 | WP08-6.4、AC-009 至 AC-013 | T02、T03 | `completion.py`、`test_completion.py` |
| T05 | 实现启动基线、diff/status 和越界范围检查 | WP08-6.5、AC-014 至 AC-018 | T02 | `completion.py`、`test_completion.py` |
| T06 | 编排 Completion 检查项、失败摘要和最终状态 | WP08-6.1、6.2、7 | T04、T05 | `completion.py` |
| T07 | 实现模型完成声明检测和 H1/H3 语义 | WP08-4、6.2、4.7 | T06 | `completion.py`、`test_completion.py` |
| T08 | 实现 Completion-only Evidence、逐项 Audit 和事务回滚 | WP08-8、AC-021、AC-022 | T06 | `state_store.py`、`audit.py`、`test_audit.py`、`test_state_checkpoint.py` |
| T09 | 补齐故障、并发、模型隔离和安全边界测试 | WP08-AC-001 至 024 | T02 至 T08 | `test_completion.py` 及兼容测试 |
| T10 | 运行验收命令、更新索引、编写 acceptance-report | 完成定义 | T09 | `docs/work-packages/index.md`、`acceptance-report.md` |

### 5.1 T00 决策冻结

已完成。完成条件均满足：

1. 用户确认六项决策或提出替代方案；
2. `spec.md` 更新为选定方案并冻结；
3. 新决策追加到 `docs/decisions.md`，必要时更新 `design.md`；
4. 工作包索引由 `BLOCKED` 变为 `DESIGNED`；
5. `plan.md` 已改为 approved，可以开始 T01。

### 5.2 T01 契约与稳定错误

只新增实际需要的公共错误码和审计事件。每个错误必须有：

- 稳定 `ErrorCode`；
- 触发阶段；
- 导致 `REJECTED`、`UNVERIFIABLE`、抛出异常还是回滚；
- 有界、脱敏消息；
- 至少一个 domain/contract 测试。

不得把 Markdown 解析错误全部压成一个模糊错误码。

### 5.3 T02 Completion 内部控制面

实现最小入口，不复制 Gate 的完整检查链。复用：

- Run/lease/config 校验；
- Registry 定义和 schema；
- Sandbox 路径/命令；
- Invocation/Evidence/Audit 事务；
- ToolObservation 有界/脱敏。

测试重点：

- VERIFYING 可执行内部只读定义；
- 模型/RUNNING 普通入口不能调用内部定义；
- 内部定义不出现在 `ToolRegistry.snapshot()`；
- 非只读定义、任意 handler、外部 args 注入、失效租约、错误 Run revision 被拒绝；
- 拒绝发生在真实文件读取或 Git 进程之前；
- Audit 包含 actor、rule、decision、tool_call_id。

### 5.4 T03 至 T05 规则引擎

规则引擎保持纯函数优先；I/O 只由内部 Gate 结果输入。每个解析/比较函数有：

- 类型标注和 docstring；
- 稳定输入顺序；
- 总输入/输出上限；
- 不依赖当前时间或本机绝对路径；
- 最小错误信息；
- 单个确定性测试和至少一个拒绝测试。

### 5.5 T06 至 T08 编排与持久化

编排任务必须确保：

- 检查项顺序固定；
- `ACCEPTED` 只在全部必需项通过时构造；
- 失败不会因 `CompletionCheck` 数据模型重排而消失；
- Completion-only Evidence 先于 `record_completion` 写入同一事务；
- Audit 写入失败使 Completion 整体回滚；
- Run revision 和租约竞争产生稳定冲突；
- 不自动回退到“文件存在即通过”。

### 5.6 T09 测试与 T10 验收

T09 完成前不得写“功能完成”。T10 只汇总实际结果：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest tests/test_completion.py -q
python -m pytest tests/test_gate.py tests/test_state_checkpoint.py tests/test_audit.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

## 6. 测试计划

### 6.1 单元测试

| 测试主题 | 关键断言 |
| --- | --- |
| 文档三区 | 合法文档解析；缺失、重复、空区、顺序/标题错误拒绝 |
| 事实列表 | 列表项引用提取稳定；普通叙述不作为事实 |
| 证据定义 | 路径/单行/范围解析；逆序、前导零、多定义和非法字符拒绝 |
| 证据图 | 事实引用、定义、重复和未定义编号全部可检查 |
| 路径规范化 | 绝对路径、`..`、反斜杠、NUL、Windows drive 和敏感路径拒绝 |
| 输出上限 | 超长文档/过多事实/过多证据不静默截断 |
| 完成声明 | 中英文正向标记、否定和空候选文本结果稳定 |

### 6.2 安全与版本测试

| 测试主题 | 关键断言 |
| --- | --- |
| 文件读取范围 | 引用行必须完全被只读 Evidence 覆盖 |
| 读取后变化 | 文件摘要变化使旧 Evidence 不能通过 |
| Evidence 来源 | 跨 Run、失效、非 READ、Completion 自产 Evidence 被拒绝 |
| 路径别名 | 大小写/Unicode/符号链接/相对路径别名不能绕过精确匹配 |
| 工作区越界 | 源码、测试、配置、未跟踪文件变化均拒绝 |
| 外围基线 | 开启时外部变化拒绝，关闭时明确未监控 |
| 截断 | Git/文档/输出截断不得被当作完整事实 |
| 审计链 | 断链、序列/哈希异常或证据关联缺失时不得接受 |

### 6.3 集成与故障测试

fixture 至少包含：

- 临时 Git root + workspace；
- 临时 SQLite StateStore；
- 固定时钟和 deterministic ID；
- 可信只读/内部工具 Registry；
- 由 Agent actor 经 Gate 执行的真实 `read_file`，生成 READ Evidence；
- 写入交付物后进入 `VERIFYING` 的合法 Run；
- 启动 `WorkspaceBaseline`；
- 可注入的 State/Audit/内部读取故障点。

测试不得使用真实外部模型、网络或开发者工作区文件。

## 7. 规格场景到测试映射

| 规格场景 | 计划测试 | 主要证据 |
| --- | --- | --- |
| WP08-AC-001 | `test_valid_code_understanding_accepted` | CompletionCheck accepted、Evidence/Audit 可重开读取 |
| WP08-AC-002 | `test_missing_deliverable_rejected` | 无文件事实、稳定错误码 |
| WP08-AC-003 | `test_empty_binary_and_oversized_deliverable_rejected` | 内容类型/上限失败 |
| WP08-AC-004 | `test_deliverable_path_escape_rejected` | Gate/Sandbox I/O 计数为 0 |
| WP08-AC-005 至 006 | `test_required_sections_and_fact_shape` | 结构检查项失败 |
| WP08-AC-007 至 008 | `test_citation_graph_and_path_validation` | 引用图/行号失败 |
| WP08-AC-009 至 011 | `test_evidence_coverage_source_and_validity` | 范围、producer、Run 关联 |
| WP08-AC-012 至 013 | `test_stale_and_paginated_read_evidence` | 摘要/并集策略 |
| WP08-AC-014 至 016 | `test_workspace_scope_and_identity` | status/diff/HEAD/index 事实 |
| WP08-AC-017 至 018 | `test_external_protection_semantics` | 开启/关闭分支 |
| WP08-AC-019 至 020 | `test_candidate_final_answer_is_not_evidence` | claim flag 与程序事实分离 |
| WP08-AC-021 | `test_non_verifying_or_budget_stopped_run_not_accepted` | NOT_EVALUATED/拒绝 |
| WP08-AC-022 | `test_completion_audit_write_failure_rolls_back` | 事务回滚和 fault count |
| WP08-AC-023 | `test_completion_tools_not_in_model_snapshot` | snapshot 不含内部工具 |
| WP08-AC-024 | `test_completion_is_idempotent_and_conflict_safe` | 同结果返回/不同结果冲突 |

测试函数名在实现阶段可以调整，但每个 WP08-AC 必须在 `acceptance-report.md` 中记录真实测试
名称和结果。

## 8. 推荐施工顺序与可回滚增量

```text
T00 决策与规格冻结
  -> T01 错误码/审计/DTO
  -> T02 内部控制面
  -> T03 纯 Markdown 解析
  -> T04 证据追溯
  -> T05 工作区范围
  -> T06 编排
  -> T07 完成声明
  -> T08 持久化/故障
  -> T09 全场景测试
  -> T10 验收报告
```

可独立审查的提交边界：

1. docs：WP08 spec/plan、索引和飞书映射；
2. contracts：错误码、审计事件和严格 DTO；
3. gate：内部只读入口与模型隔离；
4. parser：Markdown/证据定义纯解析；
5. evidence：Evidence 追溯与版本校验；
6. workspace：diff/status/外围规则；
7. completion：编排、持久化和最终结论；
8. tests：目标测试 + 兼容扩展；
9. docs：验收报告和状态更新。

## 9. 风险、阻塞与人工处理

| 风险 | 影响 | 控制 |
| --- | --- | --- |
| Gate VERIFYING 控制面设计不明确 | Completion 无法合规读取仓库 | Q01 冻结前停止施工 |
| 分页 Evidence 覆盖策略不明确 | 合法大范围引用被误拒或部分读取被误放行 | Q02 + 多段/空洞测试 |
| 启动基线未持久化/未注入 | 无法证明 diff 是否越界 | Q03；缺失时失败关闭 |
| 预存在交付物被当作完成 | 模型无产出也可能通过 | Q04；建议要求相对基线变化 |
| 文件变化后旧 Evidence 仍被使用 | H2 违反 | Q05；失效或拒绝并审计 |
| 自然语言完成声明误判 | 诊断噪声或错误拒绝 | Q06；只做辅助项，不替代程序事实 |
| Git-ignored 外围文件不可见 | 无法证明外部无变化 | 记录为 P0 已知限制，不宣称强隔离 |
| 文档语义无法机械证明 | 可能出现“引用正确但解释错误” | spec 明确不宣称语义证明，留给审查/后续能力 |
| Audit/Completion 写入失败 | 可能产生假成功 | 同事务回滚，失败关闭 |
| 内部工具泄露 handler/路径 | 扩大攻击面 | 独立 registry、模型快照测试、输出脱敏 |

人工处理入口：

- 规格歧义：停止施工，补充 `spec.md` 和决策，重新评审；
- 引用/交付物错误：由用户启动新 Run 或由新模型继续，不自动修复旧 Run；
- 验收基础设施损坏：标记 `UNVERIFIABLE`/阻塞，检查 StateStore、Audit、Sandbox；
- 外围 Git-ignored 变化：人工核对，不得用自动扫描结果替代安全事实。

## 10. 回滚计划

WP-08 是增量模块和窄接口扩展，回滚按以下边界：

1. 删除 `completion.py` 公共入口和包导出；
2. 移除 Gate 内部只读入口和内部工具注册；
3. 撤销新增错误码/审计事件（仅限尚未发布版本，已发布则保留并标记废弃）；
4. 回滚 `tests/test_completion.py` 和兼容测试扩展；
5. 保留已提交的 Audit/Completion 历史事实，不删除数据库记录；
6. 若已追加 migration，只允许通过新的兼容 migration 回滚结构，不修改旧 migration；
7. 回滚后 WP-09/WP-10 必须停止依赖 WP-08 `ACCEPTED` 结果并重新评估。

回滚不得删除用户工作区文件，不得修改 Run 终态或把已拒绝验收改成通过。

## 11. 下游交接

- WP-09 复用 WP-08 的 `CompletionChecker` 框架、内部控制面和 Evidence 消费语义，增加测试
  基线、测试报告和节点检查；
- WP-10 在 Agent 停止后调用 WP-08，负责 Run 状态与 Checkpoint/最终 `RunResult` 编排；
- WP-12 使用 WP08-AC 和真实临时仓库做端到端故障矩阵；
- 下游不得依赖 WP-08 文档中的“计划行为”冒充已实现接口，必须先读取冻结 spec 和验收报告。

## 12. 实际完成情况

当前已完成：

- `completion.py` 的 `CompletionChecker`、严格 Markdown 三区解析、事实引用图；
- Agent `READ` Evidence 追溯、内容摘要和完整行区间并集覆盖；
- 启动 WorkspaceBaseline 身份校验、变更路径范围和外围保护检查；
- 模型完成声明诊断和程序化 CompletionItem 编排；
- Gate `invoke_internal` 固定只读入口、独立内部 Registry 和模型不可见语义；
- Completion-only `DOCUMENT`/`DIFF` Evidence、逐项 Audit 和最终结果事务写入；
- `tests/test_completion.py` 中的合法接受、缺失交付物、陈旧引用、模型声明和内部隔离测试。
- 2026-11-07 实际复跑：`python -m pytest -q` 为 269 passed；
  `python -m ruff check .` 通过；`python -m ruff format --check .` 通过。

2026-11-07 自动化补测已完成：

- 增加交付物内容、Markdown 结构、引用图、Evidence provenance、分页并集、工作区范围、
  Audit 故障和重复/并发 Completion 矩阵；
- WP08-AC-001 至 WP08-AC-024 均有直接或明确底层自动化证据；
- `tests/test_completion.py` 62 passed；
- 全量 `372 passed`；Ruff lint 和格式检查通过。

尚未完成：

- 人工代码解释语义和文档质量审查；
- 独立复核人确认报告与 AC 映射。

当前工作包状态为 `VERIFYING`，不能提前声明 accepted。
