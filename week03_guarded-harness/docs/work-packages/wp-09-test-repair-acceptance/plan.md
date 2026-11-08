# WP-09 测试修复验收工作包实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-09 测试修复验收 |
| 文档状态 | done |
| 计划版本 | 1.0.0 |
| 创建日期 | 2026-11-07 |
| 批准日期 | 2026-11-07 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 前置依赖 | WP-06 v1.0.0 accepted；WP-08 v1.0.0 frozen，尚需实现并 accepted |
| 间接依赖 | WP-01/WP-03/WP-07 既有公共接口 |
| 当前结论 | 实现、32/32 AC 自动化证据、人工测试质量复核和 acceptance report 均已完成 |
| 下游影响 | WP-10 Harness 最终验收编排、WP-12 端到端测试修复场景 |

本计划不重新定义规格。规格评审中发现行为、接口、错误码、持久化或安全边界缺口时，必须先更新
`spec.md` 和必要的设计/决策文档，再调整本计划。

当前计划已批准。WP09-Q01 至 Q08 已关闭；实施按“先完成 WP-08 硬依赖，再完成 WP-09 运行时”
推进。

## 2. 计划目标与完成定义

WP-09 施工完成后必须通过公共接口、临时 Git 工作区、临时 SQLite 和确定性测试证明：

1. Agent 启动前由可信 Harness 捕获唯一基线，基线包含报告、节点和工作区版本；
2. 基线命令只能来自已校验任务配置，模型不能提供 argv、报告路径、cwd 或节点选择；
3. 测试进程仍经过 Gate、Sandbox、租约、状态、Evidence 和 Audit 的唯一执行边界；
4. JUnit XML 以有界、无外部实体的方式解析，测试节点身份和状态稳定可比较；
5. Agent 停止后以同一基础命令对最终工作区复跑，并得到完整、可解析报告；
6. 基线节点丢失、失败未修复、新增失败、新增跳过和参数用例缺失全部拒绝；
7. 测试报告与最终工作区版本绑定，测试后变化使旧证据失效；
8. 累计 diff 只落在 `allow_write`，既有测试文件不能越权修改、删除或重命名；
9. 基线、最终执行、节点、Evidence、Completion 和 Audit 可通过稳定 ID 关联；
10. 审批后新进程恢复时不重跑基线，测试结果未知时不自动重放；
11. 内部测试报告工具不进入模型工具快照；
12. `tests/test_completion.py` 覆盖 WP09-AC 全部已冻结场景；
13. 目标测试、全量测试、Ruff lint 和格式检查通过；
14. `acceptance-report.md` 由实际命令和故障注入证据驱动，不复制本计划预期值。

## 3. 计划范围与依赖方向

### 3.1 计划文件布局

```text
src/guarded_harness/
├── completion.py       # 扩展：测试修复基线、最终验收和检查项编排
├── test_reports.py     # 新增：JUnit XML 安全解析、节点键和状态比较
├── config.py           # 扩展：test_repair 报告格式严格契约
├── domain.py           # 扩展：测试执行/节点 DTO、错误码和审计事件
├── gate.py             # 扩展：固定内部测试验收执行入口
├── registry.py         # 扩展：独立内部测试工具定义
├── policy.py           # 按需扩展：内部执行角色/相位规则
├── sandbox.py          # 复用：固定命令、进程组、工作区与 Git 比较
├── storage.py          # 扩展：migration v6
├── state_store.py      # 扩展：基线和测试节点追加式持久化
├── audit.py            # 扩展：测试报告/节点比较审计事件
└── __init__.py         # 按需导出稳定公共类型

tests/
├── test_completion.py          # 扩展：WP09-AC 主测试矩阵
├── test_config.py              # 扩展：报告格式和命令组合校验
├── test_domain.py              # 扩展：DTO/错误码/模型契约
├── test_gate.py                # 扩展：内部测试执行和模型隔离
├── test_audit.py               # 扩展：审计关联和故障回滚
├── test_state_checkpoint.py    # 扩展：migration、基线重用和恢复
└── test_tools.py               # 按需验证模型 run_test 与验收执行隔离

docs/work-packages/wp-09-test-repair-acceptance/
├── spec.md
├── plan.md
└── acceptance-report.md        # 实测完成后创建
```

WP-09 不创建 `harness.py`、`agent_adapter.py` 或 `cli.py`，也不实现 WP-10 的 Run 终态编排。

### 3.2 依赖方向

```text
domain.py <──── completion.py ────> config.py
                         │
                         ├────────> test_reports.py
                         ├────────> gate.py
                         ├────────> sandbox.py
                         └────────> state_store.py + audit.py

gate.py ───> registry.py + policy.py + sandbox.py + state/audit

tests/test_completion.py
  ───> CompletionChecker + Gate + Sandbox + SQLiteStorage + temp Git repo
```

禁止：

- `completion.py` 直接调用 `subprocess`、测试 handler 或任意可执行文件；
- `test_reports.py` 执行命令、读取工作区或写入数据库；
- 在 Completion 中复制 Gate 的 schema、权限、路径或命令检查；
- 接受模型提供的报告文件路径、pytest 参数或测试节点白名单；
- 把内部测试报告工具注册到模型可见 Registry；
- 通过直接 SQL 修改 Run、Evidence、Completion 或 Audit；
- 在报告失败后自动修改测试、删除节点或重跑非幂等命令；
- 用原始 stdout 文本或模型自述替代 JUnit 报告节点。

## 4. 详细设计

### 4.1 决策门禁与依赖门禁

决策门禁已完成：

```text
WP09-Q01 报告格式与命令适配
WP09-Q02 Gate 内部测试执行边界
WP09-Q03 内部测试审批语义
WP09-Q04 migration v6 与跨进程基线
WP09-Q05 全绿基线语义
WP09-Q06 既有 skip/xfail 语义
WP09-Q07 报告原文保留策略
WP09-Q08 报告与节点上限
        -> D-65 至 D-72 已登记
        -> design.md、WP-04/WP-08 边界已同步
        -> spec v1.0.0 frozen
        -> plan v1.0.0 approved / done

WP-08 CompletionChecker/内部访问已实现并通过 acceptance-report
        -> 才允许 WP-09 首行代码
```

如果 WP-08 在施工期间改变 Completion 入口、内部身份、Evidence 消费或工作区基线语义，WP-09
必须重新评估本计划，不得用适配代码掩盖契约变化。

### 4.2 配置与报告命令适配

推荐配置契约：

```yaml
acceptance:
  strategy: test_repair
  test_command:
    - python
    - -m
    - pytest
    - -q
  baseline_required: true
  report_format: pytest_junit_xml
```

`config.py` 校验：

- `report_format` 只允许 `pytest_junit_xml`；
- `test_command` 必须非空、无 NUL/控制字符、无 shell 元字符包装；
- 基础命令必须由允许的解释器启动 pytest；
- 拒绝已存在的 `--junitxml`、`--junit-xml`、`-o junit_family` 或等价覆盖参数；
- 不允许命令自行指定 cwd、环境变量、报告目录、节点子集或超时；
- `report_format`、命令和已有关键字段全部进入配置指纹；
- 未知字段和未知格式继续由严格模型失败关闭。

报告适配器建议接口：

```text
build_pytest_report_argv(
    base_argv: tuple[str, ...],
    report_path: Path,
) -> tuple[str, ...]
```

适配器只追加：

```text
-o junit_family=xunit2
--junitxml=<verifier-owned-private-path>
```

有效 argv 与声明 argv 的差异必须可机械证明只有这组固定参数。报告路径在独立私有临时目录中按
Run/phase 创建，目录 `0700`、文件目标 `0600`，不与 State 数据库目录相邻，也不使用工作区
相对路径，不暴露给模型。

实现必须验证所选 pytest 版本确实生成 `xunit2` JUnit XML。若支持其他框架，应新增独立报告适配器
和决策，不在 P0 隐式识别任意输出。

### 4.3 JUnit XML 解析

`test_reports.py` 采用纯函数和冻结 DTO。候选接口：

```text
parse_junit_report(
    data: bytes,
    *,
    max_nodes: int = 100_000,
) -> ParsedTestReport

canonical_node_key(
    *,
    file: str,
    classname: str,
    name: str,
) -> str

compare_test_nodes(
    baseline: ParsedTestReport,
    final: ParsedTestReport,
) -> TestNodeComparison
```

解析步骤：

```text
check 1..8 MiB and no NUL/unsafe XML declaration
  -> reject DOCTYPE/ENTITY before ElementTree boundary
  -> parse root testsuites/testsuite with bounded depth
  -> iterate testcase elements from trusted XML tree
  -> normalize file/classname/name
  -> classify exactly one terminal child
  -> compute canonical node key
  -> reject duplicate node keys
  -> compute counts and redacted failure summary
  -> compare declared suite counts when present
  -> return immutable report facts
```

安全要求：

- 不使用外部 DTD、网络、XInclude 或自定义实体解析；
- 不使用宽泛 `except Exception: pass`；每个失败映射稳定错误码；
- 不把 XML 文本直接加入异常消息；
- 文件路径经 Sandbox 路径规范化或纯 POSIX 相对路径校验，拒绝绝对路径、反斜杠、NUL、`..`
  和符号链接别名；
- 节点标签、classname 和失败摘要经脱敏和长度限制；
- 计数不一致、根元素未知、空报告、重复节点和未知终态均为不可验证；
- 解析器不读取原始工作区文件，只消费报告字节和受控事实。

### 4.4 持久化与 migration

推荐 migration v6 追加：

```text
test_repair_baselines
  run_id PK/FK
  baseline_id
  schema_version
  command_id
  declared_argv_json
  effective_argv_digest
  cwd
  config_fingerprint
  workspace_baseline_json
  workspace_baseline_digest
  command_evidence_id
  report_evidence_id
  parser_version
  created_at

test_executions
  test_execution_id PK
  run_id FK
  phase BASELINE|FINAL
  command_id
  tool_call_id
  command_evidence_id
  report_evidence_id
  declared_argv_json
  effective_argv_digest
  cwd
  config_fingerprint
  workspace_pre_digest
  workspace_post_digest
  execution_status
  exit_code
  report_digest
  report_size_bytes
  parser_version
  total_count
  passed_count
  failed_count
  error_count
  skipped_count
  failure_summary
  created_at
  UNIQUE(run_id, phase)

test_nodes
  test_execution_id FK
  node_key
  label
  file
  status
  failure_code
  failure_summary
  PRIMARY KEY(test_execution_id, node_key)
```

所有三表追加式写入并配置 UPDATE/DELETE 拒绝触发器。历史 migration 不修改。`workspace_baseline_json`
使用 canonical JSON 和 1 MiB 上限；超限失败关闭。

是否将全部工作区基线放入该表、是否另用压缩/分块方案，由 WP09-Q04 评审。任何替代方案都必须：

- 让审批后新进程能验证原始启动身份和 diff；
- 不依赖进程内存或旧 Agent memory；
- 能识别 Run/config/workspace 不匹配；
- 在 schema 未知或损坏时失败关闭。

### 4.5 基线捕获实现

候选流程：

```text
capture_test_baseline(run_id, lease_token, ...)
  -> validate type/config/task kind/run status/lease/revision/baseline absent
  -> validate startup WorkspaceBaseline identity/config fingerprint
  -> capture workspace before
  -> invoke Gate fixed BASELINE test execution
  -> verify command start/finish/timeout/unknown classification
  -> read report only from verifier-owned path with 8 MiB cap
  -> parse report and compute nodes
  -> capture workspace after and require before == after
  -> transaction:
       persist test_execution + report/command evidence
       persist baseline + nodes
       append TEST_BASELINE_CAPTURED audit
     commit
  -> return persisted baseline
```

基线测试非零是合法的“测试失败”事实，不自动映射为基础设施失败。以下情况不得生成有效基线：

- 命令不能启动；
- 进程超时、取消、终止失败或结果未知；
- 报告不存在、为空、超限或解析失败；
- 测试前后 Git-visible 工作区变化；
- Run/config/workspace 不匹配；
- 基线或节点无法持久化；
- Audit 链或租约失效。

基线记录先于 Agent 启动。WP-10 只有在基线返回并已持久化后才能创建 Agent 或允许第一次工具调用。

### 4.6 最终验收实现

候选流程：

```text
evaluate_test_repair(run_id, lease_token, ...)
  -> preflight Run=VERIFYING, lease/config/startup identity, audit chain
  -> load persisted baseline and valid baseline nodes
  -> if final test execution already exists, do not rerun it
  -> capture workspace pre
  -> invoke Gate fixed FINAL test execution
  -> require deterministic process status and exit_code == 0
  -> read/parse report and compute nodes
  -> capture workspace post, require pre == post
  -> load startup workspace and current workspace diff
  -> compare baseline node/status and final node/status
  -> compare changed paths to allow_write and test-file policy
  -> recapture current workspace and require equals post and report binding
  -> build fixed CompletionItem order
  -> transaction writes:
       completion-only TEST_REPORT/DIFF evidence
       test execution/nodes
       CompletionCheck/items
       per-check and final Audit
     commit
  -> return persisted CompletionCheck
```

失败分类：

- 已确定测试节点丢失、失败未修复、新增失败、新增 skip、越界 diff：`REJECTED`；
- 报告缺失/截断、命令未知、状态或 Audit 不可验证：`UNVERIFIABLE`；
- Completion/Evidence/Audit 写入失败：抛出稳定基础设施错误并整体回滚；
- 任一检查失败均不自动重新执行测试或修复工作区。

### 4.7 测试节点比较

比较至少生成以下分类：

```text
missing_baseline_nodes
baseline_failures_repaired
baseline_failures_remaining
new_nodes
new_node_failures
new_skips
unchanged_baseline_skips
final_failures/errors/unknown
```

规则：

- 基线节点键必须全部出现在最终报告中；
- 基线 `FAILED/ERROR` 节点最终必须为 `PASSED`；
- 最终所有节点必须为 `PASSED`；已存在 `SKIPPED/XFAIL` 的保留策略由 WP09-Q06 决定；
- 原本 `PASSED` 的节点变为 `SKIPPED/XFAIL` 视为新增跳过并拒绝；
- 新增节点不得 `FAILED/ERROR/SKIPPED/XFAIL`；
- 参数化节点使用完整 name 参与键，参数用例消失按节点丢失处理；
- 同节点重复、报告计数不一致或未知状态不得被集合比较静默吞掉；
- 工具输出只包含节点键、脱敏标签和计数，不回显断言正文或参数中的敏感值。

### 4.8 工作区、diff 与测试文件边界

启动 `WorkspaceBaseline` 在所有允许变更之前捕获并持久化。最终比较：

```text
baseline.workspace_entries vs current.workspace_entries
  -> changed paths
  -> allowed = matches any task.paths.allow_write
  -> changed outside allowed = WORKSPACE_OUT_OF_SCOPE
  -> baseline test files modified outside allow_write = TEST_FILE_MODIFIED
  -> baseline test files deleted/renamed = TEST_FILE_DELETED
  -> HEAD/index/identity changed = IDENTITY_CHANGED
  -> external protection changed = EXTERNAL_WORKSPACE_CHANGED
```

测试文件集合来自基线/最终报告的 `file` 字段。目录或扩展名匹配只能用于补充检测，不能弱化报告
节点证据。符号链接、目录替换、大小写和 Unicode 归一化变体使用规范化关系判断。

工作区限制是 Git-visible 语义，ignored 文件变化作为已知剩余风险记录；若任务或 WP09 决定升级为
全文件树指纹，必须重新评审性能和敏感文件处理。

### 4.9 H2 与 Evidence 版本

最终 `TEST_REPORT` Evidence 绑定：

- 报告原始字节 SHA-256；
- 最终测试前/后 workspace status/diff 摘要；
- config fingerprint；
- Run ID 和 final ToolCall ID。

Completion 提交前再次取得当前 workspace 摘要：

- 与最终测试后相同：继续同一最终事务；
- 不同：调用 WP-07 `invalidate_evidence`，追加 `EVIDENCE_INVALIDATED`/验收 Audit，判为拒绝；
- 失效操作失败：整体失败关闭；
- 不把旧报告改为有效，不删除历史节点，不自动重跑测试。

### 4.10 模型隔离与并发

内部测试工具定义：

- 名称、schema、risk、timeout、effect class 和 handler 只由可信装配提供；
- `ToolRegistry.snapshot()` 和模型提示中不可见；
- 只接受 `phase=BASELINE|FINAL` 的固定内部调用；
- 不接受 argv、路径、环境、测试节点或报告格式；
- 不占 Agent step/tool-call 预算，使用独立固定内部预算；
- 每次执行仍写 ToolCall、Evidence 和 Audit。

模型可见的 `run_test` 只能产生普通 Agent `COMMAND` Evidence，不能成为 WP09 baseline/final 记录。
Completion 按 Run 串行化；已存在最终测试执行时不得重复执行，报告/工作区不相同时返回冲突。

## 5. 施工任务卡

总体顺序如下。T00 已完成；T01 起按依赖推进，WP-08 必须先达到可验收状态。

| 任务 | 实现内容 | 对应规格 | 依赖 | 主要文件/测试 |
| --- | --- | --- | --- | --- |
| T00 | 确认 WP09-Q01 至 Q08，更新 spec/decisions/design 和上游契约 | 全部 | 无 | `spec.md`、`decisions.md`、`design.md` |
| T01 | 冻结 WP09 DTO、错误码、审计事件和配置字段 | 6、12、13 | T00 | `domain.py`、`config.py`、`test_domain.py`、`test_config.py` |
| T02 | 实现 JUnit XML 安全解析和节点键 | 7、4.3 | T01 | `test_reports.py`、`test_completion.py` |
| T03 | 实现固定报告命令适配和内部测试定义 | 6.2、4.2、4.10 | T01 | `registry.py`、`gate.py`、`test_gate.py` |
| T04 | 追加 migration v6 和追加式存储 API | 12、4.4 | T01 | `storage.py`、`state_store.py`、`test_state_checkpoint.py` |
| T05 | 实现基线捕获、工作区稳定性与持久化 | 8、4.5 | T02 至 T04 | `completion.py`、`test_completion.py` |
| T06 | 实现最终执行、节点比较和报告版本校验 | 9、4.6、4.7、4.9 | T05 | `completion.py`、`test_completion.py` |
| T07 | 实现 diff、allow_write 和既有测试文件边界 | 9.2、9.4、4.8 | T05 | `completion.py`、`test_completion.py` |
| T08 | 实现 Completion 事务、逐项 Audit 和 H2 失效 | 11、12、4.9 | T06、T07 | `completion.py`、`state_store.py`、`audit.py` |
| T09 | 实现恢复/幂等/并发与故障矩阵 | 11、14 | T03、T04、T08 | `test_state_checkpoint.py`、`test_completion.py`、`test_audit.py` |
| T10 | 运行目标/全量测试，生成文档索引和 acceptance-report | 完成定义 | T09 | `docs/work-packages/index.md`、`acceptance-report.md` |

### 5.1 T00 决策冻结

已完成。完成条件均满足：

1. 需求方确认 WP09-Q01 至 Q08；
2. `spec.md` 已标记 `frozen`；
3. D-65 至 D-72 已追加，`design.md` 和 WP-04/WP-08 边界已同步；
4. WP-09 使用独立测试执行入口，不改变 WP-08 只读控制面；
5. 工作包状态已从 `IN_PROGRESS` 推进到 `DONE`；
6. `plan.md` 已批准，WP-08 实现作为下一施工门禁。

### 5.2 T01 契约

只新增实际需要的公共错误码、枚举和 DTO。每个错误必须记录：

- 稳定 `ErrorCode`；
- 触发阶段；
- `REJECTED`、`UNVERIFIABLE`、抛错或回滚语义；
- 有界脱敏消息；
- domain/config 契约测试。

配置测试至少覆盖：

- 合法 `pytest_junit_xml`；
- 未知格式拒绝；
- 非法报告参数/覆盖项拒绝；
- 命令字段未知或 shell 注入拒绝；
- 命令/报告格式进入配置指纹。

### 5.3 T02 报告解析

先写测试再实现：

- 合法 pass/fail/error/skip/xfail/参数化报告；
- 根元素、计数、空报告、重复节点、未知终态；
- 超限、NUL、非法编码、DOCTYPE/ENTITY；
- 路径越界、绝对路径、反斜杠和符号链接别名；
- 脱敏/长度裁剪和节点键稳定性；
- 报告字节变化会改变 digest；
- 解析函数无 I/O、无命令执行、无时钟依赖。

### 5.4 T03 内部测试执行

实现最小固定入口，不复制整个 Gate 链。测试重点：

- `BASELINE`/`FINAL` 相位和 Run 状态正确；
- 模型/RUNNING 普通调用不能使用内部定义；
- 内部工具不在模型 snapshot；
- 任意 argv、报告路径、cwd、环境注入被 schema/Gate 在命令启动前拒绝；
- 命令启动失败、超时、取消和未知结果映射稳定；
- Audit 包含 actor、phase、rule、decision、tool_call_id；
- 测试前后工作区检查和报告读取均不绕过 Sandbox 边界。

### 5.5 T04 持久化

实现 v5 -> v6 兼容升级：

- migration 只追加，保留旧 Run/Evidence/Audit；
- baseline/test execution/node 表和唯一约束；
- UPDATE/DELETE 触发器；
- `UNIQUE(run_id, phase)` 的并发语义；
- 节点写入失败回滚测试执行事实；
- migration 重跑幂等和未知 schema 失败关闭；
- 追加式记录不能被普通 StateStore API 修改。

### 5.6 T05 至 T08 编排

这些任务按以下事务边界施工：

1. 读取/验证 Run、租约、配置和启动基线；
2. 测试命令执行由 Gate 提交；
3. 报告和节点持久化单独事务；
4. 最终 Completion 事实在同一事务提交；
5. 任一阶段失败都保留已有事实，不用删除/覆盖掩盖未知结果。

检查项顺序固定，`ACCEPTED` 只在全部必需项通过且 Completion/Evidence/Audit 同事务提交后构造。

### 5.7 T09 测试与故障

T09 至少覆盖：

- 基线完整失败报告；
- 最终全通过；
- 基线节点丢失、失败保留、新增失败、新增 skip；
- 参数化节点减少；
- 报告超限、XXE 样式输入、计数不一致、缺失/截断；
- 命令超时/取消/启动失败；
- 测试前后工作区变化；
- 最终测试后源码变化和 Evidence 失效；
- 源码/test 文件越界、删除、重命名和符号链接；
- State/Audit/Completion 故障回滚；
- 审批后跨进程复用基线；
- 模型自述/模型 `run_test` 不得充当证据；
- 并发验收只执行一次测试命令。

每个拒绝测试除错误码外还应断言：

- handler/PoPEN/文件变更计数符合预期；
- Audit 事件可关联；
- 不存在伪造的 TEST_REPORT；
- Run 不会被误标为 `ACCEPTED`。

### 5.8 T10 验收报告

T10 只汇总实际结果：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest tests/test_completion.py -q
python -m pytest tests/test_config.py tests/test_gate.py tests/test_state_checkpoint.py tests/test_audit.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

报告必须记录实际退出状态、测试数量、失败/skip 数、场景映射、故障注入、已知限制和下游影响。
命令失败或关键场景未覆盖时不得写 `accepted`。

## 6. 测试计划

### 6.1 单元测试

| 主题 | 关键断言 |
| --- | --- |
| 配置 | 报告格式、命令和覆盖项在加载阶段拒绝 |
| XML 根/计数 | 根元素、suite 计数和测试节点计数一致 |
| 节点身份 | file/classname/name 稳定，参数化 ID 保留，重复键拒绝 |
| 状态 | PASS/FAIL/ERROR/SKIP/XFAIL/XPASS/UNKNOWN 分类稳定 |
| 安全 | 8 MiB、100k 节点、DOCTYPE/ENTITY、字段上限全部失败关闭 |
| 比较 | 丢失、修复、新增失败和新增跳过可确定分类 |
| 脱敏 | 参数值、失败摘要、绝对路径不进入结果 |

### 6.2 集成测试

fixture 至少包含：

- 临时 Git root + workspace + 临时 SQLite；
- 可控 ID、UTC 时钟和故障注入；
- 可信内部测试 Registry 与 Sandbox；
- fake pytest/JUnit 报告，或临时 Python 测试命令生成确定性 xunit2 报告；
- 启动 WorkspaceBaseline；
- Run `RUNNING` 基线阶段和 `VERIFYING` 最终阶段；
- 可注入 State/Audit/Evidence/Gate 故障。

测试不访问网络、真实模型、用户凭据或开发者工作区。

### 6.3 版本与恢复测试

| 场景 | 要求 |
| --- | --- |
| 审批后新进程 | 读取原基线，不重跑测试 |
| DB reopen | 基线、节点、报告 digest、预算和证据仍可读 |
| migration v5 -> v6 | 旧 Run/Completion/Audit 保留，新增表可用 |
| 清理/篡改 | 节点/执行记录 UPDATE/DELETE 被拒绝 |
| 租约变化 | 基线/最终验收拒绝 |
| 报告落后工作区 | 旧 TEST_REPORT 失效，不支撑 ACCEPTED |
| 已 STARTED 无结果 | `UNKNOWN_RESULT`，不自动重放 |

## 7. 规格场景到测试映射

| 规格场景 | 计划测试 | 主要证据 |
| --- | --- | --- |
| WP09-AC-001 | `test_valid_test_repair_accepted` | Completion accepted、基线/最终节点和 Audit |
| WP09-AC-002 至 004 | `test_baseline_required_and_command_consistent` | 缺失/损坏/命令不匹配拒绝 |
| WP09-AC-005 至 006 | `test_test_command_failure_matrix` | 启动失败、超时、取消、未知不重放 |
| WP09-AC-007 | `test_failing_baseline_is_captured` | 基线失败节点持久化 |
| WP09-AC-008 至 009 | `test_report_size_and_xml_safety` | 超限、DOCTYPE/ENTITY、空报告 |
| WP09-AC-010 | `test_baseline_workspace_must_be_stable` | 前后事务/哈希不等 |
| WP09-AC-011 至 012 | `test_final_exit_and_report_required` | exit code 与报告双条件 |
| WP09-AC-013 至 018 | `test_node_set_and_status_conservation` | 丢失、保留失败、新增失败、skip、参数化 |
| WP09-AC-019 至 021 | `test_test_file_and_allow_write_scope` | 修改、删除、新增测试边界 |
| WP09-AC-022 至 024 | `test_workspace_diff_identity_and_external` | allow_write、HEAD/index、外围 |
| WP09-AC-025 至 026 | `test_final_workspace_stability_and_stale_report` | 测试中变化、测试后变化 |
| WP09-AC-027 | `test_truncated_stdout_with_complete_report` | stdout 截断不充当报告证据 |
| WP09-AC-028 | `test_model_run_test_is_not_acceptance_evidence` | 模型命令/自述不影响程序验收 |
| WP09-AC-029 | `test_non_verifying_or_budget_stopped_not_accepted` | NOT_EVALUATED/拒绝 |
| WP09-AC-030 | `test_completion_transaction_failure_rolls_back` | State/Audit 故障 |
| WP09-AC-031 | `test_internal_test_tool_not_in_model_snapshot` | snapshot 不含内部工具 |
| WP09-AC-032 | `test_concurrent_test_repair_completion` | 测试命令最多一次或稳定冲突 |

测试函数名可在实现阶段调整，但 `acceptance-report.md` 必须记录最终真实名称、命令和结果。

## 8. 推荐施工与提交顺序

```text
T00 决策与契约冻结
  -> T01 DTO/配置/错误码
  -> T02 JUnit 解析器
  -> T03 内部测试执行入口
  -> T04 migration v6/持久化
  -> T05 基线
  -> T06 最终报告/节点比较
  -> T07 工作区与测试文件范围
  -> T08 Completion/Audit/事务
  -> T09 故障/恢复/并发测试
  -> T10 验收报告
```

建议的独立提交边界：

1. docs：WP09 spec/plan、索引、飞书映射和开放问题；
2. contracts：配置、错误码、DTO 和审计事件；
3. parser：安全 JUnit 解析与节点比较；
4. persistence：migration v6、基线/执行/节点表；
5. gate：固定内部测试入口和模型隔离；
6. baseline：Agent 前捕获与跨进程事实；
7. completion：最终测试、diff、H2 和 Completion；
8. tests：目标测试 + 全量兼容；
9. docs：acceptance report 和状态更新。

## 9. 风险、阻塞与人工处理

| 风险 | 影响 | 控制 |
| --- | --- | --- |
| D-59 不允许内部非只读测试命令 | 基线无法经过唯一 Gate 执行 | WP09-Q02；先修订上游规格再施工 |
| 基线只在内存 | 审批后新进程无法比较原测试集合 | WP09-Q04；migration/WorkspaceBaseline 持久化 |
| 只解析 stdout | 测试集合和节点证据不可靠 | JUnit XML + 完整报告校验 |
| 报告超限/恶意外部实体 | 资源耗尽或解析越界 | 8 MiB、节点上限、禁用 DTD/ENTITY |
| 测试命令非幂等 | 超时/崩溃后重试可能重复副作用 | 不自动重试；UNKNOWN_RESULT/新 Run |
| 测试命令修改工作区 | 验收被测试自身污染 | 前后快照必须一致 |
| 最终测试后源码变化 | H2 失效 | 工作区摘要绑定 + Evidence 单向失效 |
| 基线节点丢失/skip | 测试覆盖悄悄减少 | 节点键守恒、新增 skip 检测 |
| 基线全绿 | 无修复目标也可能通过 | WP09-Q05；候选拒绝或显式任务模式 |
| 历史 skip/xfail | 过度严格或误放行 | WP09-Q06 明确状态迁移规则 |
| 原始报告含秘密/断言内容 | 泄漏到 State/Audit | 临时读取、解析后删除、摘要脱敏 |
| 弱化断言但节点保留 | 机械验收无法识别语义弱化 | 记录 P0 限制；人工审查/diff 证据 |
| migration 冲突或旧数据损坏 | 无法可靠恢复 | 追加 migration、兼容测试、失败关闭 |
| State/Audit 写失败 | 可能出现假成功 | 同事务回滚，不返回 ACCEPTED |

人工处理入口：

- 开放问题未决：停止施工，补充决策和上游契约；
- 基线不可用：人工检查配置/命令/报告；需要继续时启动新 Run；
- 测试命令未知结果：人工核对副作用，不确认成功、不自动重放；
- 报告/节点损坏：核对 State/Audit 和测试报告摘要，不修改历史节点；
- 旧证据失效：检查源码版本，由新 Run 或明确恢复流程重新验收。

## 10. 回滚计划

WP-09 是增量扩展，回滚边界：

1. 移除测试修复 Completion 入口和公开导出；
2. 撤销 WP-09 内部测试执行定义及 Gate 薄接入；
3. 回滚新增测试报告解析器和测试；
4. 若 migration v6 已发布，只能用新的兼容 migration 回退结构，不修改旧 migration；
5. 保留已提交 Audit、Evidence、基线和节点历史，不删除用户工作区或改写失败结论；
6. 依赖 WP-10/WP-12 的实现必须停止把 WP-09 计划行为当作已实现；
7. 回滚后重新确认 WP-08/WP-06 公共契约没有被隐式破坏。

## 11. 下游交接

- WP-10 在 Agent 启动前调用基线捕获，Agent 停止后进入 `VERIFYING` 再调用最终验收；
- WP-10 负责把 `ACCEPTED/REJECTED/UNVERIFIABLE/NOT_EVALUATED` 映射到 Run 终态和 CLI 退出码；
- WP-11 恢复时复用持久化基线，不重跑非幂等命令，不把 `UNKNOWN_RESULT` 人工确认当成自动成功；
- WP-12 使用 WP09-AC 和真实临时项目端到端验证，不向模型暴露隐藏测试；
- 下游只能依赖冻结后的接口和 acceptance report，不得依赖本 draft 中的候选字段。

## 12. 实际完成情况

当前已完成：

- WP-09 文档目录初始化；
- `spec.md` v1.0.0 frozen，包含飞书“核心验收”H1/H2/H3/A2/T1 映射；
- `plan.md` v1.0.0 approved / done，包含设计、任务卡、测试矩阵、风险和回滚；
- WP09-Q01 至 WP09-Q08 已由 D-65 至 D-72 关闭。
- `report_format=pytest_junit_xml` 严格配置、JUnit XML 安全解析和节点身份；
- Gate `invoke_test_verification`、独立内部测试执行和模型工具隔离；
- migration v6、WorkspaceBaseline/测试执行/节点追加式持久化；
- Agent 前基线捕获、最终复跑、节点守恒、skip/xfail、工作区稳定和 H2 Evidence 失效；
- 跨数据库重开复用基线，不重跑非幂等基线命令；
- `tests/test_completion.py` 的 WP-08/WP-09 目标测试，以及全量回归。
- 后续补测复跑：`tests/test_completion.py` 62 passed；
  `python -m pytest -q` 372 passed；`python -m ruff check .` 和格式检查通过。

2026-11-07 自动化补测已完成：

- 增加基线损坏/命令绑定、启动失败/超时、报告上限、空最终报告、节点守恒、测试文件
  rename/symlink、允许生成测试、外围变化、输出截断、模型自述、写入故障和并发验收矩阵；
- 修复测试修复 Completion 未检查仓库外围变化的问题；
- WP09-AC-001 至 WP09-AC-032 均有直接或明确底层自动化证据；
- `tests/test_completion.py` 62 passed；
- 全量 `372 passed`；Ruff lint 和格式检查通过。

后续复核：

- 2026-11-08 人工测试 diff、断言弱化、节点守恒和审批恢复复核 PASS；
- 32 个 AC 的自动化证据和报告映射已确认；
- [acceptance-report.md](acceptance-report.md) 已升级为 `accepted`。

当前工作包状态为 `DONE`。
