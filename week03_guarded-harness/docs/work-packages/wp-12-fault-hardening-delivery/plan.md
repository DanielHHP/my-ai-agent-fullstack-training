# WP-12 故障加固与交付工作包实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-12 故障加固与交付 |
| 文档状态 | done |
| 计划版本 | 1.0.0 |
| 创建日期 | 2026-11-07 |
| 批准日期 | 2026-11-07 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 前置依赖 | WP-00 至 WP-11 全部 accepted |
| 当前依赖状态 | WP-00 至 WP-11 全部 accepted |
| 当前代码基线 | `956b4a0`；2026-11-07 全量测试 `372 passed in 257.41s` |
| 下游影响 | P0 最终交付、运维入口和项目完成声明 |
| 当前结论 | 实现、样例、补测、文档、372 项全量测试和人工验收完成；工作包 accepted |

本计划不重新定义规格。WP12-Q01 至 Q03 已按 D-84 至 D-86 关闭，规格冻结、计划批准。

## 2. 计划目标与完成定义

WP-12 完成后必须通过公共入口和实际证据证明：

1. WP-00 至 WP-11 均已 accepted，索引和验收报告一致；
2. 六个必须测试文件存在，且承担原设计映射和 WP-12 项目级覆盖；
3. 在现有 314 项测试之外，WP-12 新测试数量合理且全部通过，无关键 skip；
4. 代码理解 Fake Model E2E 最终 `ACCEPTED`，源码无越界修改；
5. 测试修复 Fake Model E2E 从失败基线到最终 JUnit 通过，最终 `ACCEPTED`；
6. 未审批写安全退出，审批后跨进程 resume 至多执行一次；
7. `STARTED` 后进程硬退出恢复为 `UNKNOWN_RESULT`，不自动重放；
8. 审计失败、DB 锁、Checkpoint 损坏和租约冲突均失败关闭；
9. 已确定写入不重复，预算/Step/ToolCall 计数恢复后不清零；
10. 两份正式样例由 `load_config` 校验，非法样例仍被拒绝；
11. 飞书 P0 G1-G4、S1、S3、C1-C4、A1、H1-H3、T1 均有确定性证据；
12. 飞书 P1 S2/S4/A2/T2 按确认后的策略记录 `PASS/PARTIAL/NOT_RUN/N/A`；
13. `docs/test-plan.md`、`docs/operations.md`、README 和索引与实现一致；
14. 全量测试、Ruff lint、格式和确定性复跑通过；
15. `acceptance-report.md` 基于实际证据给出最终结论。

## 3. 计划范围与文件布局

### 3.1 推荐文件布局

```text
tests/
├── fault_injection.py              # 新增：测试专用故障点、子进程 runner、观察器
├── test_fault_recovery.py          # 新增：跨事务故障和恢复矩阵
├── test_gate.py                    # 扩展：项目级拒绝/审计/零 handler
├── test_sandbox.py                 # 扩展：路径、敏感文件、命令和进程边界复核
├── test_state_checkpoint.py        # 扩展：崩溃、预算、租约、损坏和跨进程恢复
├── test_audit.py                   # 扩展：审计失败、链关联、脱敏和决策链
├── test_harness_loop.py            # 扩展：两类 Agent 任务项目级场景
└── test_completion.py              # 扩展：两类 Completion 与旧证据失效

examples/
├── config/
│   ├── code-understanding.yaml
│   └── test-repair.yaml
└── workspaces/                     # 待 WP12-Q02 确认；推荐自包含
    ├── code-understanding/
    └── test-repair/

docs/
├── README.md                       # 更新索引
├── test-plan.md                    # 新增
├── operations.md                   # 新增
├── task-breakdown.md               # 链接 WP-12 文档
└── work-packages/
    ├── index.md                    # 更新状态和链接
    └── wp-12-fault-hardening-delivery/
        ├── spec.md
        ├── plan.md
        └── acceptance-report.md    # 实测后新增

README.md                           # 最终交付时重写入口概览
```

WP-12 原则上不修改 `src/guarded_harness/`。若端到端测试发现生产缺陷，必须定位到权威上游
工作包修复，并在该包增加回归测试、更新 spec/plan/acceptance report，再重新进入 WP-12 全量
门禁。若仅需测试可观察性接口，优先在 `tests/` 内实现，不扩大生产公共 API。

### 3.2 当前资产核对

| 资产 | 当前状态 | WP-12 处理 |
| --- | --- | --- |
| 六个必须测试文件 | 均存在 | 保留文件名，追加项目级/故障映射 |
| `FaultInjector` | 已有生产依赖注入协议和测试 fixture | 复用协议，新增测试目录 helper |
| Harness/CLI | 已实现并有 314 项全量测试 | 只调用公开入口，发现缺陷回上游 |
| `examples/` | 尚不存在 | 按 Q02 决定创建配置和可选工作区 |
| `docs/test-plan.md` | 尚不存在 | 新增 |
| `docs/operations.md` | 尚不存在 | 新增 |
| 根 README | 仍描述“仅 WP-00 完成” | 最终交付时更新 |
| WP-08 至 WP-11 | 已全部 accepted | 项目级依赖闭环，可进入最终交付 |

## 4. 决策门禁

施工前必须关闭：

```text
WP12-Q01 上游依赖未 accepted 时允许提前施工 -> D-84
WP12-Q02 样例配套最小自包含工作区 -> D-85
WP12-Q03 飞书 P1 不作为 P0 阻断 -> D-86
        -> spec.md v1.0.0 frozen
        -> plan.md v1.0.0 approved
        -> task cards frozen
```

已确认基线：

- Q01：允许提前施工，但不满足依赖和项目完成声明条件；
- Q02：提供最小自包含样例工作区；
- Q03：仅 P0 阻断，P1 汇总既有证据并显式标注未执行。

## 5. 详细设计

### 5.1 测试专用故障注入层

`tests/fault_injection.py` 提供以下测试能力，不进入生产包：

```text
FaultPointRegistry
  - arm(point)
  - disable(point)
  - trigger(point)
  - count(point)
  - assert_hit_once(point)

CrashBoundaryRunner
  - 在独立子进程启动可信测试 runner
  - 指定 config、workspace、state DB、Fake Model 脚本和故障点
  - 在目标边界执行 os._exit / SIGKILL 等价硬退出
  - 父进程等待退出并读取持久化事实

DatabaseLockProbe
  - 使用第二个 SQLite 连接持有 BEGIN IMMEDIATE
  - 在有限 busy timeout 内调用生产入口
  - 释放锁并核对无部分提交

LeaseConflictProbe
  - 由测试进程持有有效 Run lease
  - 第二进程尝试接管或提交
  - 核对 fencing、旧 token 拒绝和 STARTED 未知结果
```

约束：

- Fault point 名称固定、可枚举、有断言；
- 未 arm 时完全 no-op；
- runner 输入路径必须经过 workspace/state 目录和临时根校验；
- 子进程不继承用户凭据，使用最小测试环境；
- 不把 `WP12_FAULT_POINT` 一类环境变量接入生产 CLI；
- 记录子进程退出信号/退出码、耗时和预期故障点，防止“测试进程根本没到故障点”也通过。

### 5.2 故障点与现有注入接口

优先复用现有可观察点，无法真实表示崩溃时由测试 runner 在点之后硬退出：

| 故障点 | 现有/新增位置 | 验证重点 |
| --- | --- | --- |
| `gate:before-start` | 现有 Gate | 执行意图/STARTED 前后窗口 |
| `sandbox.write.before_replace` | 现有 Sandbox | handler 已开始但物理文件替换前 |
| handler 已返回、结果提交前 | 测试包装的可信 handler 或 runner 边界 | 文件已变化但 DB 仍 STARTED |
| `uow:before-commit` | 现有事务 | 状态/审计/Checkpoint 原子提交前 |
| `audit:before-insert` | 现有 Audit | 允许/拒绝/结果审计失败 |
| `checkpoint:before-audit` | 现有 Checkpoint | snapshot 与索引/audit 原子失败 |
| `storage:migration-before-commit` | 现有 Storage | 仅复核迁移失败关闭，不作为 WP-12 新 migration |
| DB lock | SQLite 第二连接 | 写事务有界失败 |
| Checkpoint corruption | 测试专用数据库副本 | 哈希/schema/关联损坏 |
| Lease conflict | 现有 lease API + 子进程 | 单写者和 fencing |

新增故障点只可作为测试注入协议的一部分；如果必须修改生产模块才能让硬退出准确落在某个事务
边界，应先评估是否属于上游测试可观察性缺陷，按工作包回归规则处理。

### 5.3 崩溃矩阵

| 场景 ID | 注入步骤 | 故障后事实 | resume 期望 | 测试归属 |
| --- | --- | --- | --- | --- |
| WP12-FI-01 | Gate 允许前退出 | 无 Invocation/无副作用 | 可重新决策，handler 0 | `test_gate.py`、`test_fault_recovery.py` |
| WP12-FI-02 | intent+claim 提交后、STARTED 前退出 | `PREPARED`、审批 `CLAIMED` | 全量复核后 resume_claim，最多一次 | `test_gate.py`、`test_state_checkpoint.py` |
| WP12-FI-03 | STARTED 后、handler 实际写入前退出 | `STARTED`，无法证明未执行 | `UNKNOWN_RESULT`，禁止自动重放 | `test_state_checkpoint.py` |
| WP12-FI-04 | handler 写入后、结果提交前退出 | 文件变化，Invocation `STARTED` | `UNKNOWN_RESULT`，不得按文件猜测成功 | `test_fault_recovery.py` |
| WP12-FI-05 | 结果+审计已提交、Checkpoint 未提交 | 确定结果存在 | 复用结果；可归因时补 Checkpoint | `test_state_checkpoint.py` |
| WP12-FI-06 | 结果已提交后再次 resume | 结果确定 | 不重复 handler/文件提交 | `test_state_checkpoint.py`、`test_harness_loop.py` |
| WP12-FI-07 | 允许审计失败 | 无放行事实 | handler 0，失败关闭 | `test_audit.py`、`test_gate.py` |
| WP12-FI-08 | 副作用后结果审计失败 | 文件可能变化，结果未成功落盘 | 未知或失败关闭，禁止重放 | `test_fault_recovery.py` |
| WP12-FI-09 | DB 锁冲突 | 事务未提交 | 有界失败/稳定错误 | `test_fault_recovery.py` |
| WP12-FI-10 | 当前 Checkpoint 损坏 | 损坏事实保留 | 拒绝恢复/验收 | `test_state_checkpoint.py` |
| WP12-FI-11 | 租约冲突 | 旧 token 不可提交 | fencing；STARTED 按未知 | `test_state_checkpoint.py` |

### 5.4 基准与计数比较

每个恢复测试保存故障前后快照：

- `run_id`、`trace_id`、Run 状态和 StopReason；
- Step 数量、已经完成的 Step ID；
- ToolCall 数量、状态、规范化参数摘要；
- `budget_ledger` 汇总和 Run usage；
- Approval revision/status/claim/consume 时间；
- Checkpoint sequence 和 snapshot digest；
- 工作区文件 SHA-256、Git status/diff 和外围保护基线；
- Audit 最大 sequence 和关键关联 ID。

恢复后不得仅比较“文件看起来没重复”，还必须比较 handler 调用计数、预算、审批和审计事实。

### 5.5 代码理解 E2E

推荐流程：

```text
materialize sample workspace
  -> load_config
  -> Fake Model:
       list/read source and tests
       read exact line ranges
       propose write_file for configured deliverable
  -> Gate WAITING_APPROVAL
  -> assert file absent/unchanged
  -> approvals approve in a separate main() call
  -> resume in a new process/main()
  -> Gate executes write once
  -> model final_answer claiming completion
  -> Completion validates document + Evidence + diff + Audit
  -> ACCEPTED
```

必须断言：

- 模型只看到 GovernedTool 和 `final_answer`；
- 文档包含“事实/推断/代码依据”；
- E 编号引用真实行范围且来自 Agent READ 证据；
- 只新增/修改声明交付物；
- 审批前无文件写入；
- 最终状态来自 Completion，不来自 candidate answer。

### 5.6 测试修复 E2E

推荐使用一个最小 Python 样例工作区，包含：

- 一个独立 `pytest` 测试；
- 一个导致失败、可被受控 `apply_patch` 修复的源码缺陷；
- 无网络、无外部依赖；
- 既有测试默认只读；
- 基线 JUnit 至少一个失败节点，最终全部通过。

流程：

```text
copy sample to temporary Git workspace
  -> load sample test-repair config
  -> Harness captures failing baseline through internal Gate
  -> Fake Model reads failure/source
  -> proposes apply_patch
  -> approval wait; source unchanged
  -> approve + resume in new process
  -> patch executes once
  -> model final_answer
  -> Completion reruns same test command with JUnit
  -> checks node conservation, final exit 0, diff scope, current fingerprint
  -> ACCEPTED
```

必须同时有拒绝变体：基线全绿、最终节点丢失、新增 skip、测试后源码再修改、diff 越界、报告
截断或结果未知。

### 5.7 正式样例

推荐样例配置：

```text
examples/config/code-understanding.yaml
examples/config/test-repair.yaml
```

若 Q02 选择自包含，配置从 `examples/config/` 相对解析到：

```text
examples/workspaces/code-understanding
examples/workspaces/test-repair
```

状态目录放到工作区外、测试临时根内，不提交 SQLite/WAL/SHM 或运行产物。样例工作区可在测试时
复制到临时目录并初始化为独立 Git 仓库，避免污染项目仓库和历史。

样例验收：

1. `load_config` 成功并得到稳定配置指纹；
2. 未知字段、路径逃逸、非法预算仍由现有负例拒绝；
3. 配置中的 allowed tools 与样例任务一致；
4. `state_dir` 不在 workspace 内且权限路径安全；
5. 样例不含真实密钥、绝对开发者路径、网络依赖或被 gitignore 的隐藏输入。

### 5.8 文档与证据映射

`docs/test-plan.md` 至少包含：

- 测试层级和目录；
- 六个必须文件及额外 WP-12 文件；
- P0/P1 飞书矩阵；
- WP12-AC 到测试函数映射；
- 故障点/崩溃窗口/恢复动作矩阵；
- 离线、隔离、重复和平台要求；
- 目标、全量、lint、格式和复跑命令；
- 证据字段和失败报告规范。

`docs/operations.md` 至少包含：

- 安装和配置校验；
- `run`、审批、`resume`、`inspect`；
- SQLite/WAL 文件位置、权限、备份和保留；
- 状态/验收/退出码解释；
- 日志与脱敏；
- 卡在 `WAITING_APPROVAL`、`BLOCKED`、`UNKNOWN_RESULT` 时的人工核对；
- 禁止“强放行”、直接改审批、删除 STARTED、重放未知写；
- macOS/Linux 限制和非目标；
- 故障恢复后如何决定新 Run。

## 6. 任务卡

| 任务 | 目标 | 依赖 | 涉及文件 | 对应规格 | 完成条件 |
| --- | --- | --- | --- | --- | --- |
| WP12-T00 | 依赖与基线门禁 | 无 | 索引、验收报告、命令输出 | WP12-AC-001、003、004 | 记录上游未 accepted；按 D-84 允许施工；安装、全量、lint、格式有实际基线 |
| WP12-T01 | 关闭 Q01-Q03 并冻结规格 | T00 | `spec.md`、`plan.md`、`decisions.md` | 第 13 节 | 问题有确认结论；spec 冻结、plan 批准 |
| WP12-T02 | 故障注入辅助层 | T01 | `tests/fault_injection.py` | WP12-BEH-011/012、AC-040 | 故障点确定、未 arm 为 no-op、路径隔离、子进程硬退出可断言 |
| WP12-T03 | 崩溃与恢复矩阵 | T02 | `test_fault_recovery.py`、三个状态相关必须测试 | WP12-AC-017/030/031/034/038/039 | FI-01 至 FI-11 中适用的恢复路径有确定性证据 |
| WP12-T04 | 审计/DB/Checkpoint 故障 | T02 | `test_audit.py`、`test_state_checkpoint.py` | WP12-AC-020/030/032/033 | 审计失败、锁冲突、损坏和链关联均失败关闭 |
| WP12-T05 | 安全边界与门禁复核 | T02 | `test_gate.py`、`test_sandbox.py` | WP12-AC-010/011/014/015/020/025/026 | P0 零副作用证据 + P1 证据状态明确 |
| WP12-T06 | 正式样例与校验 | T01、Q02 | `examples/config/*`、可选 `examples/workspaces/*`、测试 | WP12-AC-006 | 两份配置由正式加载器校验；负例仍拒绝 |
| WP12-T07 | 代码理解 E2E | T02、T05、T06 | `test_harness_loop.py`、`test_completion.py`、CLI runner | WP12-AC-012/013/016/018/021/035/037 | 审批前文件不变；跨进程 resume；Completion `ACCEPTED` |
| WP12-T08 | 测试修复 E2E | T02、T05、T06 | `test_harness_loop.py`、`test_completion.py`、JUnit 证据 | WP12-AC-017/022/023/036/039 | 失败基线到最终通过；节点/diff/证据有效；拒绝变体覆盖 |
| WP12-T09 | 飞书与需求全矩阵 | T03-T08 | `docs/test-plan.md`、六份必须测试 | WP12-AC-007/010 至 028 | 每条 P0/P1 有结果与证据，无遗漏/重复替代 |
| WP12-T10 | 运维与入口文档 | T09 | `docs/operations.md`、`docs/README.md`、根 README、工作包索引 | WP12-AC-008、WP12-BEH-019/020/021 | 文档与实现/命令/限制一致，旧状态描述移除 |
| WP12-T11 | 全量质量门禁与复跑 | T03-T10 | 全仓库 | WP12-AC-002/003/004/005 | 指定六文件存在；pytest/lint/format 全绿；目标与全量可重复 |
| WP12-T12 | 最终验收报告 | T11 | WP-12 `acceptance-report.md` | WP12-API-005/006 | 实测命令、场景/任务/证据/限制完整，状态可复核 |
| WP12-T13 | 封版与交接 | T12 accepted | 索引、任务拆解、最终状态 | WP-12 退出条件 | 工作包 DONE；项目 P0 完成声明与证据一致 |

## 7. 测试计划

### 7.1 分层

| 层级 | 目的 | 示例 |
| --- | --- | --- |
| 契约/单元 | 保证失败语义、字段和错误码稳定 | Gate schema、Checkpoint verify、Audit append-only |
| 集成 | 真实 Sandbox/SQLite/Approval/Gate 协作 | 审批后写一次、审计失败回滚 |
| 故障/恢复 | 事务和进程窗口 | 子进程硬退出、DB 锁、租约冲突 |
| Agent E2E | Fake Model 驱动原生 ToolCallingAgent | 两类任务完整 Run |
| CLI E2E | 跨进程操作与退出码 | run -> approve -> resume；inspect |
| 交付检查 | 文档、样例、索引和证据一致 | load_config、README 链接、报告矩阵 |

### 7.2 目标测试

推荐第一轮目标命令：

```bash
python -m pytest tests/test_fault_recovery.py -q
python -m pytest tests/test_gate.py tests/test_sandbox.py -q
python -m pytest tests/test_state_checkpoint.py tests/test_audit.py -q
python -m pytest tests/test_harness_loop.py tests/test_completion.py tests/test_cli.py -q
```

最终门禁：

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

### 7.3 场景映射

| WP12-AC | 主要测试文件 | 计划测试/证据 |
| --- | --- | --- |
| AC-001 | 文档/索引 | 工作包验收报告状态核对记录 |
| AC-002 | 六个必须文件 | pytest 收集清单和文件存在检查 |
| AC-003/004/005 | 全仓库 | 实际命令、退出码、计数、重复运行 |
| AC-006 | config/tests | 两份样例加载 + 现有配置负例 |
| AC-007/008 | docs/test-plan | 需求/决策/场景/测试矩阵和链接检查 |
| AC-010 | `test_gate.py` | 参数注入拒绝、handler 0 |
| AC-011 | `test_gate.py` | 三次重复只读调用计数 2 |
| AC-012/013 | `test_harness_loop.py`、`test_cli.py` | run/approve/resume 一次写入 |
| AC-014/015 | `test_gate.py`、`test_sandbox.py` | 路径/命令拒绝和零副作用 |
| AC-016 | `test_state_checkpoint.py` | Checkpoint 字段与边界 |
| AC-017 | `test_fault_recovery.py` | handler/结果事务间硬退出 |
| AC-018/019 | `test_state_checkpoint.py`、CLI | 正常恢复、工作区变化阻塞 |
| AC-020 | `test_audit.py` | rule/decision/actor/tool_call_id |
| AC-021/022 | `test_completion.py` | 无证据声明、旧测试证据 |
| AC-023 | `test_harness_loop.py` | 预算终态 |
| AC-024 | 全部目标文件 | P0 场景矩阵 |
| AC-025/026/027/028 | 上游报告/现有测试 | P1 汇总或 `NOT_RUN` |
| AC-030/031 | `test_audit.py`、`test_fault_recovery.py` | 允许/结果审计失败 |
| AC-032/033/034 | `test_fault_recovery.py`、`test_state_checkpoint.py` | 锁、损坏、租约 |
| AC-035 | `test_harness_loop.py`、`test_completion.py` | 代码理解 E2E |
| AC-036 | `test_harness_loop.py`、`test_completion.py` | 测试修复 E2E |
| AC-037/038/039 | `test_cli.py`、state/harness tests | 新进程、replay、预算 |
| AC-040 | 配置/CLI/生产路径测试 | 无测试故障旁路 |

测试函数名在任务施工时按实际实现冻结；不得只写文件映射而不提供可定位函数。

## 8. 推荐施工顺序

```text
T00 dependency/baseline gate
  -> Q01-Q03 review
  -> T01 spec freeze + plan approval
  -> T02 fault injection toolkit
  -> T03/T04 failure kernel
  -> T05 security/P0 gate verification
  -> T06 examples
  -> T07 code-understanding E2E
  -> T08 test-repair E2E
  -> T09 full Feishu/requirements matrix
  -> T10 docs and README
  -> T11 full quality gate + repeat
  -> T12 acceptance report review
  -> T13 close
```

每个增量应形成可独立审查和回滚的证据。推荐提交拆分：

```text
test(wp-12): add deterministic fault injection helpers
test(wp-12): cover crash and recovery transaction windows
test(wp-12): cover audit, database, checkpoint and lease failures
feat(wp-12): add validated example task configurations
test(wp-12): run code-understanding end to end
test(wp-12): run test-repair end to end
docs(wp-12): add test plan and operations guide
docs(wp-12): finalize delivery evidence and index
```

## 9. 风险、阻塞与处理

| 风险/阻塞 | 影响 | 处理 |
| --- | --- | --- |
| WP-08 至 WP-11 未 accepted | 曾不符合最终准入 | 已通过后续补测和人工验收闭环，当前均已 accepted |
| 普通异常被误当进程崩溃 | 无法证明真实持久化恢复 | 使用子进程硬退出，断言退出信号和故障点命中 |
| DB 锁测试无限阻塞 | 测试不稳定或挂起 | 使用有界 busy timeout、子进程超时和清理锁连接 |
| 修改测试文件直接篡改 DB | 证据不可信 | 损坏只在隔离副本/临时数据库进行，不改历史事实 |
| 样例依赖开发者路径 | 交付不可复现 | 自包含相对路径；测试复制到 temp 并初始化 Git |
| Fake Model 直接调用 handler | E2E 绕过治理层 | 只通过 CLI/Harness 公开入口；断言 Gate/Audit/Approval ID |
| 旧测试报告被复用 | H2 假阳性 | 报告绑定当前工作区指纹、Run、文件 SHA 和测试执行 ID |
| 为追绿放宽断言 | 安全回归 | 禁止默认 skip/xfail、测试删除和弱化；代码审查 diff |
| 文档继续写成已实现 | 交付误导 | README/报告按实测状态写作，P1 明确 NOT_RUN |
| 新增生产故障开关 | 生产后门 | 故障注入仅在 `tests/`，生产入口不读取测试开关 |

## 10. 回滚计划

1. 移除 `tests/fault_injection.py`、`tests/test_fault_recovery.py` 和 WP-12 扩展测试；
2. 移除 `examples/config/` 与 Q02 确认的样例工作区；
3. 撤回 `docs/test-plan.md`、`docs/operations.md`、README 和索引更新；
4. 保留未验收/失败报告及原始证据，不删除历史数据库或审计事实；
5. 若 WP-12 暴露上游缺陷，回滚目标是对应上游增量，并停止依赖 WP-12 的最终完成声明；
6. 不修改已发布 migration；需要结构回退时只能通过上游新 migration；
7. 回滚后重新运行受影响目标测试、全量测试、lint 和格式，并更新索引状态。

## 11. 下游交接与完成声明

WP-12 向项目交付：

- 可运行的两类正式样例；
- 项目级故障矩阵和恢复证据；
- 可追溯的 P0 需求/飞书/测试/报告映射；
- 测试与运维入口；
- 明确的 P1 未实现项和平台限制；
- 最终 accepted/rejected/blocked 报告。

只有 WP-12 `acceptance-report.md` 为 `accepted`，且六个必须模块、两类 E2E、全量质量门禁和文档
一致性均通过时，项目才可声明 P0 完成。WP-12 计划完成、测试绿或模型声称成功都不构成项目完成。

## 12. 当前实际完成情况

截至 2026-11-07，已完成：

- 从 `docs/task-breakdown.md` 读取 WP-12 目标、依赖、主要范围、退出条件和 P6 任务；
- 从 `requirements.md`、`design.md`、`decisions.md` 读取项目级验收、故障关闭、状态和交付约束；
- 从飞书 revision 14 的“核心验收”内嵌工作簿 revision 11 读取 `A1:D20` 全部验收条目；
- 核对现有六个必须测试文件、Harness/CLI/FaultInjector 和测试基线；
- 初始化 WP-12 文档目录，spec 经评审后冻结为 v1.0.0，本计划批准为 v1.0.0；
- 新增 `tests/fault_injection.py` 和 `tests/test_fault_recovery.py`；
- 完成真实子进程退出、DB 锁、Checkpoint 损坏、租约 fencing 和 CLI 无故障旁路测试；
- 新增两个正式 YAML 样例和两个自包含最小工作区；
- 完成代码理解与测试修复 Fake Model E2E，两者均最终 `ACCEPTED`；
- 新增 `docs/test-plan.md`、`docs/operations.md`，更新根 README、文档索引和任务拆解；
- 补充 WP-08/WP-09 的 44 个 Completion 场景和 WP-10/WP-11 的直接 CLI/恢复证据；
- 新增 `tests/manual_acceptance_runner.py` 与 WP-08 至 WP-11 手动验收说明；
- 执行 `372 passed in 257.41s`、Ruff lint、格式检查和 WP-12 目标复跑；
- 生成 `acceptance-report.md`，并在依赖与人工验收闭环后升级为 accepted；
- 更新 `docs/README.md`、`docs/work-packages/index.md` 和任务拆解中的 WP-12 文档入口。

封版确认：

- WP-08 至 WP-11 已全部 accepted；
- WP-12 最终报告为 accepted；
- 工作包与项目 P0 状态可标记 DONE；
- 后续公共契约变化仍须重新执行受影响门禁并提升报告版本。
