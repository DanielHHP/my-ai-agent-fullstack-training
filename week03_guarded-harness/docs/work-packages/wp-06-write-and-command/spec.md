# WP-06 写入与命令能力包规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-06 写入与命令能力包 |
| 文档状态 | frozen |
| 规格版本 | 1.0.0 |
| 创建日期 | 2026-11-06 |
| 冻结日期 | 2026-11-06 |
| 依赖 | WP-04 v1.0.0 / accepted；WP-05 v1.0.0 / accepted；WP-03 v1.1.0 / accepted |
| 下游工作包 | WP-07、WP-09、WP-10、WP-11、WP-12 |
| 对应需求 | `REQ-GATE-001` 至 `REQ-GATE-005`、`REQ-SANDBOX-001` 至 `REQ-SANDBOX-006`、`REQ-STATE-001`、`REQ-AUDIT-001` 至 `REQ-AUDIT-004`、`REQ-TASK-001`、`REQ-TASK-003`、`REQ-TASK-004`、`REQ-COMPLETE-002` 至 `REQ-COMPLETE-004` |
| 对应设计 | `design.md` 第 5.2、5.3、6.2、7、8、9、11.3、13、14 节 |
| 相关决策 | D-03、D-05、D-17、D-20、D-22、D-25、D-26、D-28、D-30 至 D-34、D-37 至 D-44、D-48 至 D-52 |
| 飞书输入 | 作业文档 `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌工作簿 revision 11 |
| 原文件任务 | P3-05、P3-07、P3-08 后半、P3-10、P3-11 后半 |

本文是 WP-06 已冻结的行为与验收契约。它明确工具、审批、执行、证据和失败语义，但不宣称相关能力
已经实现。实现计划和任务拆分见 [plan.md](plan.md)。文档冲突时按
`requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 的顺序处理。

## 2. 目标

WP-06 必须把真实写操作和受控命令执行接入 WP-04 Gate，同时复用 WP-03 Sandbox 与 WP-05 证据语义：

1. 可信装配只注册 `write_file`、`apply_patch` 和 `run_test` 三个副作用工具；
2. 模型只提交工具名和严格 schema 内的参数，所有调用在执行前重新经过 Gate；
3. `write_file` 与 `apply_patch` 只能修改任务显式允许的文件，不能借 `allow_write`、通配符、
   符号链接或未声明路径扩权；
4. 首次未审批写入进入 `WAITING_APPROVAL`，在审批前不得改变目标文件；
5. 已批准写入只执行一次；参数、工作区、任务或配置变化后旧审批失效；
6. `apply_patch` 只对单个已有 UTF-8 文件执行精确、唯一、顺序文本替换，整批成功或整批不写入；
7. `run_test` 只执行任务配置中的固定 argv，不接受模型提供的 shell、argv、cwd、env、超时或
   可执行文件；
8. 命令结果包含退出码、信号、超时、取消、截断、耗时和已采集输出，且真实终止 POSIX 进程组；
9. 非幂等写入或命令无法确认最终结果时进入 `UNKNOWN_RESULT`，不得自动重试或判为成功；
10. 成功写入和确定性命令结果产生可关联 `run_id`、`tool_call_id` 的 Evidence，供 WP-07/WP-09
    后续恢复与验收消费；
11. 拒绝、审批、执行和未知结果均写审计，错误信息不包含密钥、令牌或敏感环境变量值。

## 3. 范围与非目标

### 3.1 范围内

- `src/guarded_harness/tools/fs_write.py`；
- `src/guarded_harness/tools/exec.py`；
- `src/guarded_harness/tools/common.py` 的共享 schema、路径写策略、输出和 Evidence 辅助；
- `src/guarded_harness/tools/__init__.py` 的可信副作用工具装配；
- `src/guarded_harness/domain.py` 的新稳定错误码和必要契约导出；
- `src/guarded_harness/__init__.py` 的必要公共导出；
- `tests/test_tools.py`、`tests/test_gate.py` 的写入和真实命令部分；
- `tests/test_sandbox.py` 的 WP-06 进程组集成扩展；
- WP-06 文档、索引和后续验收报告。

### 3.2 非目标

- 不实现 Checkpoint、崩溃恢复动作选择或 continuation prompt，由 WP-07 负责；
- 不实现测试基线、测试报告解析、测试节点比较或最终测试修复判定，由 WP-09 负责；
- 不实现 Harness 新建/恢复运行、Agent 适配、预算停止编排或 CLI；
- 不提供任意命令、shell 片段、可执行文件、环境变量、cwd、Git revision 或测试文件子集参数；
- 不提供多文件 patch 事务；
- 不实现操作系统级文件系统或网络隔离；
- 不允许 `write_file` 或 `apply_patch` 绕过文件路径、敏感文件、`.git/`、依赖目录和平台写入规则；
- 不把模型的写入摘要、命令自述或最终回答当作任务完成证据；
- 不在 WP-06 内宣称测试修复任务已经可被最终验收，最终判定仍属于 WP-09。

## 4. 飞书“核心验收”吸收

来源为指定飞书文档 revision 14 中“核心验收”内嵌工作簿 revision 11。下表只列 WP-06 直接负责、
提供证据或消费上游结果的条目；跨工作包条目由最终归属工作包关闭。

| 编号 | 优先级 | 飞书通过标准 | WP-06 责任 | 最终归属 |
| --- | --- | --- | --- | --- |
| G1 | P0 | 参数缺字段或类型错误，handler 调用次数为 0 | 直接：三个工具使用严格 schema，额外字段和错误类型在 Gate 拒绝 | WP-04 / WP-06 / WP-12 |
| G2 | P0 | 连续三次相同工具和参数，第三次在执行前拦截，handler 只执行两次 | 直接：副作用工具默认最多执行一次；等价写入/命令不得重复产生副作用 | WP-04 / WP-06 / WP-12 |
| G3 | P0 | 未审批写操作进入 `WAITING_APPROVAL`，目标文件不变 | 直接：写工具首次调用停在 Gate，approval 前不触达文件 I/O | WP-02 / WP-04 / WP-06 / WP-12 |
| G4 | P0 | 审批后重复执行同一调用，第一次只执行一次，第二次拒绝且不重复写入 | 直接：Gate 消费审批，真实写 handler 只成功一次 | WP-02 / WP-04 / WP-06 / WP-12 |
| S1 | P0 | `artifacts/../../.env` 路径逃逸由 Sandbox 拒绝，handler 不执行 | 直接：真实写工具把路径声明为受治理 path argument，审批前完成路径校验 | WP-03 / WP-04 / WP-06 / WP-12 |
| S3 | P0 | `run_test` 配置外命令不执行 | 直接：只接受固定 `command_id`，argv 来自可信测试配置 | WP-03 / WP-04 / WP-06 / WP-12 |
| S4 | P1 | 命令超时或用户取消时进程组真实终止，不只是停止等待 | 直接：真实 `run_test` 复用 Sandbox POSIX 进程组终止和结果分类 | WP-03 / WP-06 / WP-12 |
| C2 | P0 | 写入完成后结果未保存时恢复标记 `UNKNOWN_RESULT`，不得自动重放 | 支撑：写工具是真实非幂等副作用；WP-06 不纳入可安全重放的旁路 | WP-04 / WP-06 / WP-07 / WP-12 |
| A1 | P0 | 被拒绝调用审计含 `rule`、`decision`、`actor`、`toolCallId` | 直接：拒绝写和命令在 Gate 生成标准审计，handler/进程计数为 0 | WP-04 / WP-06 / WP-12 |
| A2 | P1 | 完整任务可关联 Gate、Sandbox、State、Approval 和执行结果 | 支撑：真实写入/命令结果通过 `run_id`、`tool_call_id`、Evidence 和 approval 关联 | WP-08 / WP-09 / WP-12 |
| H2 | P0 | 测试通过后又修改源码，旧测试证据失效 | 支撑：写入产生新内容版本；WP-09 必须拒绝旧版本测试证据 | WP-07 / WP-09 / WP-12 |
| H3 | P0 | 达到最大轮数或预算时以明确 StopReason 停止 | 支撑：所有真实调用仍消耗 Gate/State 预算，不得在 handler 内绕过 | WP-04 / WP-10 / WP-12 |
| T1 | P0 | 所有 P0 场景都有确定性测试且可重复通过 | 直接：WP-06 的写入、审批、命令、超时和未知结果测试必须离线可重复 | WP-06 / WP-12 |

WP-06 不负责关闭 C1/C3/C4、H1、T2，但写入结果、命令结果和 Evidence 必须足以让 WP-07/WP-09
在不信任模型自述的前提下继续判定。

## 5. 参与组件与依赖方向

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| `tools/__init__.py` | 构造并注册三个可信副作用工具 | 不执行未授权调用，不决定模型可见性 |
| `tools/fs_write.py` | `write_file` 与 `apply_patch` 的单一文件 handler | Gate 门禁、审批、State/Audit 写入 |
| `tools/exec.py` | `run_test` 的可信命令 handler | 模型命令解释、shell、恢复、最终测试验收 |
| `tools/common.py` | 严格 schema、任务写范围、输出和 Evidence digest 辅助 | 直接绕过 Sandbox 访问仓库 |
| `registry.py` | 保存 ToolSpec 与 handler，生成模型可见快照 | 执行工具或复核审批 |
| `policy.py` | 可见性、角色权限、模式和审批要求 | 具体写入或进程操作 |
| `gate.py` | 固定检查链、审批、预算、执行意图、结果和 Evidence 持久化 | 解释测试语义或判定任务完成 |
| `sandbox.py` | 路径、安全原子 I/O、可信命令、进程组、环境、超时和输出边界 | 选择工具或直接写业务状态 |
| `domain.py` | 稳定错误码和必要公共输出契约 | 执行 handler |

依赖方向：

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
tests ────> public registration + Gate + Sandbox + State/Audit
```

禁止：

- handler 直接调用 `open()`、`Path.write_text()`、`shutil.copy()` 或 `subprocess` 操作目标仓库；
- handler 直接写 State、Audit、Approval、Budget 或 Checkpoint；
- handler 接收或使用模型提供的 argv、可执行文件、cwd、env、shell、超时和重试参数；
- 使用模型提供字符串拼接 shell 命令；
- 根据 `understand`、`fix_tests` 等任务名称散落分支；只使用经过校验的任务类型和配置；
- 在 Gate、State 或 Audit 中保存完整敏感环境变量、凭据或未脱敏命令输出。

## 6. 公共接口

### 6.1 可信注册入口

WP-06 新增一个由可信装配代码调用的入口：

```text
register_side_effect_tools(
    registry: ToolRegistry,
    sandbox: Sandbox,
    config: ResolvedHarnessConfig,
) -> tuple[str, ...]
```

前置条件：

- `registry`、`sandbox` 和 `config` 均已由可信代码构造并通过类型校验；
- `config.workspace_root`、`config.state_dir`、任务路径策略和配置指纹已完成 WP-00 校验；
- Sandbox 已绑定唯一工作区和其 Git 根；
- 测试修复配置的 `test_command` 已由 Sandbox 注册为可信命令。

后置条件：

- 成功时注册稳定排序的 `write_file`、`apply_patch` 和适用的 `run_test`；
- 任何定义冲突、非法 schema 或 handler 构造失败均整批失败，不宣称部分注册成功；
- 模型可见 `ToolSpec` 快照不包含 handler、绝对路径、argv 或 Sandbox；
- 注册不授予执行权限，Gate 每次调用仍重新检查可见性、权限、风险、审批、重复和预算。

WP-06 不改变已冻结的 `register_readonly_tools` 公共行为。Harness 可分别调用只读与副作用注册入口，
或由后续 WP-10 提供组合装配。

### 6.2 工具元数据

| 工具 | risk | 权限 | 审批候选策略 | 幂等 | 重试 | 并发 |
| --- | --- | --- | --- | --- | --- | --- |
| `write_file` | `WRITE` | `WRITE` | 固定 `REQUIRED` | 否 | `NEVER` | `SERIAL` |
| `apply_patch` | `WRITE` | `WRITE` | 固定 `REQUIRED` | 否 | `NEVER` | `SERIAL` |
| `run_test` | `EXECUTE` | `EXECUTE` | 由任务 `approval.required_tools` 提升 | 否 | `NEVER` | `SERIAL` |

所有工具的超时、输出上限、风险、权限和重试元数据必须进入 `ToolSpec`。模型可见描述说明工具目的和
输入边界，但不得暴露具体文件系统路径、完整测试 argv、可执行文件位置或 handler。

### 6.3 `write_file`

输入 schema：

```text
{
  "type": "object",
  "properties": {
    "path": {"type": "string", ...},
    "content": {"type": "string", ...}
  },
  "required": ["path", "content"],
  "additionalProperties": false
}
```

语义：

- `path` 是工作区相对 POSIX 路径，`..`、绝对路径、Windows drive/UNC、反斜杠、NUL、控制字符和
  过长路径拒绝；
- `content` 以 UTF-8 编码，编码后不得超过 Sandbox 的 10 MiB 硬上限；
- 一个调用只写一个文件；
- 目标不存在时使用 `CREATE`，目标已存在且为普通 UTF-8 文件时使用带内容版本校验的 `REPLACE`；
- 目标为目录、特殊文件、符号链接逃逸或大小超限时拒绝；
- 父目录创建只能由可信工具规格显式开启，且逐级经过写策略；
- 成功返回路径、创建/覆盖模式、字节数、前后内容摘要和是否发生变化；
- 文件内容相同返回成功且 `changed=false`，不得产生第二次物理替换；该语义由 D-50 冻结。

### 6.4 `apply_patch`

候选输入 schema：

```text
{
  "type": "object",
  "properties": {
    "path": {"type": "string", ...},
    "edits": {
      "type": "array",
      "minItems": 1,
      "maxItems": 100,
      "items": {
        "type": "object",
        "properties": {
          "oldText": {"type": "string", "minLength": 1, ...},
          "newText": {"type": "string", ...}
        },
        "required": ["oldText", "newText"],
        "additionalProperties": false
      }
    }
  },
  "required": ["path", "edits"],
  "additionalProperties": false
}
```

语义：

- 只修改一个已有普通 UTF-8 文件，不使用整文件覆盖创建新文件；
- 按 `edits` 数组顺序应用；每段 `oldText` 必须在前一段应用后的文本中恰好出现一次；
- 任一 `oldText` 出现 0 次、出现多次、空数组、全空操作或输入超限时整批失败；
- 所有编辑先在内存中计算完成，随后对原文件摘要执行一次 `Sandbox.write_file_atomic(REPLACE)`；
- 任一段失败不得写入目标文件，不得留下临时文件或部分修改；
- `newText` 按字面量写入，不解释正则、shell、`$1`、`&` 或其他替换语法；
- 成功返回应用段数、前后摘要、字节数变化和变化行或差异摘要；
- 第一版不支持跨文件 patch、二进制 patch、文件创建/删除/重命名或统一 diff 格式。该选择由
  D-48 冻结。

### 6.5 `run_test`

候选输入 schema：

```text
{
  "type": "object",
  "properties": {
    "command_id": {"type": "string", "const": "acceptance_test"}
  },
  "required": ["command_id"],
  "additionalProperties": false
}
```

语义：

- `command_id` 必须是可信装配注册的唯一测试命令 `acceptance_test`；
- 完整 argv 来自当前任务 `acceptance.test_command`，不来自模型；
- Sandbox 使用 `shell=False`，固定 `cwd=workspace_root`，使用 WP-03 最小环境和 POSIX 进程组；
- 模型不能提交 test file、额外 argv、shell 片段、cwd、env、timeout、output limit、可执行文件或
  重试策略；
- 只注册并暴露给测试修复任务；其他任务若尝试调用，在 Gate 可见性阶段拒绝；
- 任务配置变化会改变配置指纹并使其旧审批失效；
- 第一版只运行完整配置命令，不支持测试文件子集。该选择由 D-49 冻结。

### 6.6 Gate 的额外语义检查

WP-06 的路径 normalizer 在 Gate schema 校验后、权限与审批前完成：

1. 调用 Sandbox 写路径检查，将路径规范化为工作区相对 POSIX 路径；
2. 检查规范化路径落在当前任务允许的写范围；
3. 对 `apply_patch` 做纯输入语义检查，包括非空 edits、总大小和全空操作；
4. 对 `run_test` 确认唯一 `command_id` 且当前任务存在可信测试命令；
5. 任一失败返回 `GATE_SEMANTIC_INVALID` 或专用稳定错误码，handler 计数为 0；
6. 规范化后的路径和参数摘要进入审批绑定，路径别名不能复用已批准调用。

## 7. 任务写范围

写范围由已校验任务配置决定，不由模型、提示词、文件名猜测或 handler 内硬编码任务名决定。

### 7.1 代码理解

- 唯一允许写入的路径是 `deliverables` 中已规范化的非空路径；
- 交付物路径匹配必须是规范化后的精确相等，不能把同前缀兄弟路径或 pattern 当作交付物；
- `write_file` 可以创建或更新该交付物；
- `apply_patch` 只能修改已经存在的交付物；
- 任务配置的 `allow_write` 与 deliverables 必须在前置配置校验中一致；
- 源码、测试、配置、状态目录和任何未声明路径写入在 Gate 拒绝。

### 7.2 测试修复

- 目标路径必须匹配至少一个非空的 `task.paths.allow_write` 模式；
- `deny_write` 命中但被同一路径的窄范围 `allow_write` 覆盖时，仍须通过普通 Sandbox 写规则；
- `deny_read`、敏感文件、`.git/`、依赖目录和其他平台保护不能被 `allow_write` 覆盖；
- `allow_write` 为空时，WP-06 不提供隐式源码写权限；
- 所有写入仍须审批；已有测试是否允许修改、节点是否丢失和覆盖是否减少由 WP-09 最终判定。

### 7.3 不支持的写范围

- 任务类型未知、路径策略缺失、deliverables 为空、allow_write 为空或配置版本不受支持时失败关闭；
- 不根据文件内容、文件名或模型声称的用途自动扩权；
- 不允许通过临时文件、候选项目录、大小写变体、Unicode 归一化或符号链接改写绕过目标路径。

## 8. 审批、执行与状态

### 8.1 固定顺序

真实副作用调用按以下顺序处理：

```text
lookup registry
  -> Gate visibility
  -> strict schema
  -> path/task write semantics + canonical args digest
  -> role permission + plan mode/risk
  -> approval create-or-validate
  -> repeat/replay + budget
  -> persist PREPARED execution intent and approval claim
  -> persist STARTED before handler
  -> handler revalidates actual Sandbox target and performs one operation
  -> persist deterministic result/Evidence or UNKNOWN_RESULT
  -> bounded, redacted observation
```

任一门禁失败都发生在 handler、文件原子替换或进程启动之前。

### 8.2 审批

- `write_file` 与 `apply_patch` 固定要求审批；
- `run_test` 在其名称出现在任务 `approval.required_tools` 时要求审批；
- 审批绑定工具名、规范化参数、目标工作区、任务、`run_id`、`tool_call_id`、配置指纹、角色和 TTL；
- 首次未审批调用进入 `WAITING_APPROVAL`，不得创建、覆盖、删除文件或启动进程；
- 已批准调用再次经过 Gate 并根据持久化参数重新检查；
- 审批只可成功 claim/consume 一次；
- 参数变化、路径别名变化、工作区/配置/任务变化、过期、跨 Run、重放或撤销的审批拒绝；
- 确定 `SUCCEEDED/FAILED/TIMED_OUT` 可消费审批；`UNKNOWN_RESULT` 保持不可重放且不消费为成功。

`run_test` 保留任务配置提升能力，由 `approval.required_tools` 决定是否必审；该语义由 D-51
冻结。

### 8.3 执行边界与未知结果

- handler 不从模型参数选择命令或可执行文件；
- `write_file` 和 `apply_patch` 执行前重新调用 Sandbox `check_write`/`revalidate_path`，防止检查后
  目标或父级被替换为逃逸符号链接；
- 写入使用同目录临时文件、`fsync` 和原子替换；失败时不得留下部分目标内容；
- `run_test` 使用 Sandbox 独立 POSIX 进程组；超时或取消先终止进程组，宽限期后强杀；
- 命令超时、取消、终止失败或采集失败不能证明外部副作用未发生；
- `write_file`、`apply_patch` 和 `run_test` 均声明非幂等且不自动重试；
- 进程已 `STARTED` 但结果事务未提交时，只能由 WP-07 进入 `UNKNOWN_RESULT`，不能重放；
- 迟到结果、未确认结果、异常 handler 结果或无效输出不得伪装为成功。

## 9. 命令结果与输出

`run_test` 的成功结果至少包含：

```text
command_id
exit_code
signal
timed_out
cancelled
stdout / stderr
stdout_truncated / stderr_truncated
process_group_terminated
duration_ms
result_code
```

映射规则：

| Sandbox 状态 | Gate/模型前结果 | 说明 |
| --- | --- | --- |
| `SUCCEEDED` | `SUCCEEDED` | 退出码为 0；不代表最终验收通过 |
| `FAILED` | `FAILED` / `TOOL_TEST_FAILED` | 保留非零退出码和已采集输出 |
| `START_FAILED` | `FAILED` | 命令未启动，不得声称测试已执行 |
| `TIMED_OUT` | `TIMED_OUT` 或 `UNKNOWN_RESULT` | `run_test` 为非幂等，P0 预期进入 `UNKNOWN_RESULT` |
| `CANCELLED` | `UNKNOWN_RESULT` | 取消不等于确定没有副作用 |
| `UNKNOWN_RESULT` | `UNKNOWN_RESULT` | 禁止自动重试或判成功 |

输出规则：

- stdout 和 stderr 在 Sandbox 层各自有 1 MiB 上限；
- handler 再生成不超过 ToolSpec 输出上限的结构化结果，超限必须显式标记；
- 进入模型、State、Audit 前继续执行 Redactor；
- 发生截断时保留真实退出码和截断事实，但不得声明完整测试报告；
- 截断或无法解析的输出不能作为 WP-09 的通过依据；
- 仅确定性且未截断的命令结果产生完整 `COMMAND` Evidence；
- 不保存宿主环境变量或完整原始进程环境。

## 10. Evidence 与审计

### 10.1 Evidence

- `write_file` 成功替换后产生 `DIFF` Evidence，绑定规范化路径和新文件完整内容摘要；
- `apply_patch` 成功后产生一个 `DIFF` Evidence，绑定规范化路径、原摘要、新摘要和编辑数量；
- `run_test` 在结果确定且输出未截断时产生 `COMMAND` Evidence，绑定 `command_id=acceptance_test`
  和命令结果摘要；
- `UNKNOWN_RESULT` 不产生声称新成功事实的 Evidence；
- Evidence 的权威生产者和 `tool_call_id` 由 Gate 分配，handler 只提交 `EvidenceDraft`；
- 文件再次变化时旧 Evidence 保留，但 WP-09 必须通过内容版本比较将其判为过期；
- Completion 可以消费 Evidence，但不得把“命令退出 0”直接等同于“测试修复任务通过”。

### 10.2 审计

以下事实必须可查询：

- Gate 放行或拒绝、phase、rule、actor 和 `tool_call_id`；
- 审批请求、决定、claim 和确定性消费；
- 工具 `STARTED`、成功、失败、超时、取消和 `UNKNOWN_RESULT`；
- 命令退出码、信号、耗时和截断；
- 写入路径、内容版本摘要和 Evidence ID；
- 状态/结果写入失败和安全拒绝。

审计失败时：

- 拒绝审计无法写入时，仍不得执行 handler；
- 执行意图/STARTED 无法写入时，不得产生新的副作用；
- handler 已产生副作用但结果审计无法提交时，不得返回成功，也不得自动重放；
- 错误信息先脱敏再进入模型 observation、State 或 Audit。

## 11. 错误契约

沿用 WP-04/WP-05 已有稳定错误码，并按实现需要新增：

| 错误码 | 触发条件 | handler/进程执行 |
| --- | --- | --- |
| `GATE_WRITE_NOT_ALLOWED` | 规范化路径不在任务交付物或 `allow_write` 写范围 | 0 |
| `TOOL_PATCH_EMPTY` | `edits` 为空；若 schema 已拒绝则使用 `GATE_INVALID_ARGUMENT` | 0 |
| `TOOL_PATCH_CONTEXT_NOT_FOUND` | 任一 `oldText` 在当前文本中不存在 | 0 次写入 |
| `TOOL_PATCH_AMBIGUOUS` | 任一 `oldText` 在当前文本中出现多次 | 0 次写入 |
| `TOOL_PATCH_NO_CHANGE` | 所有编辑应用后内容没有变化 | 0 次写入 |
| `TOOL_PATCH_INPUT_TOO_LARGE` | patch 文本或目标编码后超过 10 MiB | 0 次写入 |
| `TOOL_TEXT_ENCODING_INVALID` | 目标不是合法 UTF-8，或包含不支持的二进制内容 | 0 次写入 |
| `TOOL_TEST_COMMAND_MISSING` | 测试修复任务没有可用 `acceptance_test` 命令 | 0 次启动 |
| `TOOL_TEST_FAILED` | 测试命令确定性非零退出 | 命令已执行一次，不重试 |
| `SANDBOX_COMMAND_TIMEOUT` | 命令超时并终止进程组 | 命令至多启动一次，结果未知 |
| `SANDBOX_COMMAND_CANCELLED` | 命令在完成前被取消 | 命令至多启动一次，结果未知 |
| `SANDBOX_RESULT_UNKNOWN` | 采集、终止或结果分类无法确认 | 不重试 |

模型可见错误必须包含稳定错误码、阶段和无敏感值的原因；不得伪造文件内容、退出码或“测试通过”。

## 12. 不变量

| 不变量 | 内容 |
| --- | --- |
| WP06-INV-001 | 所有写入和命令只能通过 Gate 进入 handler。 |
| WP06-INV-002 | 审批、schema、路径、权限或预算拒绝发生在真实 I/O 和进程启动之前。 |
| WP06-INV-003 | 审批前目标文件字节不变，命令进程启动次数为 0。 |
| WP06-INV-004 | 写入目标是规范化、工作区内、任务显式允许的单一路径。 |
| WP06-INV-005 | `apply_patch` 任一编辑失败时目标文件没有部分修改。 |
| WP06-INV-006 | 一个已批准写调用最多产生一次物理写入。 |
| WP06-INV-007 | 相同调用重放返回原事实或 replay suppression，不再次写文件。 |
| WP06-INV-008 | 命令 argv、cwd、env、可执行文件、超时和输出限制均来自可信配置。 |
| WP06-INV-009 | 命令超时或取消必须真实终止进程组，不能只结束本地等待。 |
| WP06-INV-010 | 未知结果不自动重试、不消费为成功、不产生成功 Evidence。 |
| WP06-INV-011 | 写入和命令结果必须可通过 `run_id`、`tool_call_id`、approval 和 Evidence 关联。 |
| WP06-INV-012 | State、Audit、审批和输出中不出现密钥、令牌、密码或敏感环境变量值。 |
| WP06-INV-013 | 模型可见工具快照不包含 handler、Sandbox、真实 argv、绝对路径或内部命令。 |
| WP06-INV-014 | 同一 Run 的写入、命令和审批调用串行执行。 |

## 13. 验收场景

每个场景最终至少映射到一个自动化测试。`WP06-AC-001` 至 `WP06-AC-028` 是 WP-06 冻结与验收的
最小集合。

| 编号 | 场景 | 通过条件 |
| --- | --- | --- |
| WP06-AC-001 | 注册三个副作用工具并生成快照 | 名称稳定；元数据正确；快照无 handler、绝对路径和 argv |
| WP06-AC-002 | 三个工具缺字段、错误类型或额外字段 | Gate schema 拒绝；handler/进程计数为 0 |
| WP06-AC-003 | 写入路径穿越、工作区外绝对路径和符号链接逃逸 | 在审批和写 I/O 前拒绝；目标不变 |
| WP06-AC-004 | 写入 `.env`、密钥、`.git/` 和依赖目录 | 平台规则拒绝；不能由 `allow_write` 覆盖 |
| WP06-AC-005 | 代码理解任务写非交付物路径 | `GATE_WRITE_NOT_ALLOWED`；handler 与文件 I/O 计数为 0 |
| WP06-AC-006 | 测试修复任务写 `allow_write` 外路径 | Gate 拒绝；目录外源码或测试不变化 |
| WP06-AC-007 | 未审批 `write_file` | Run 进入 `WAITING_APPROVAL`；目标文件不存在或字节不变 |
| WP06-AC-008 | 未审批 `apply_patch` | 审批等待；原文件和临时文件均不变化 |
| WP06-AC-009 | 已批准写入，随后用相同调用重放 | 首次实际写入一次；第二次拒绝或返回原事实；无第二次写入 |
| WP06-AC-010 | 已批准后修改内容、路径或目标工作区 | 旧审批失效；新路径/内容不写入 |
| WP06-AC-011 | `write_file` 创建新文件 | 返回 `created=true`、UTF-8 字节数、摘要和 DIFF Evidence |
| WP06-AC-012 | `write_file` 原子覆盖既有文件 | 返回前后摘要；写入失败时旧内容完整保留 |
| WP06-AC-013 | write 内容超过 10 MiB 或目标为特殊文件 | 稳定拒绝；不留下部分文件或临时文件 |
| WP06-AC-014 | `apply_patch` 空编辑、纯 no-op 或非法 schema | 在 handler 前拒绝；文件不变 |
| WP06-AC-015 | 任一段 `oldText` 不存在 | `TOOL_PATCH_CONTEXT_NOT_FOUND`；整批不写 |
| WP06-AC-016 | 任一段 `oldText` 命中多次 | `TOOL_PATCH_AMBIGUOUS`；整批不写 |
| WP06-AC-017 | 多段编辑中后段失败 | 前段也不落盘；文件字节和摘要保持不变 |
| WP06-AC-018 | 多段合法编辑按顺序应用 | 最终文本正确；生成一个带新版本摘要的 DIFF Evidence |
| WP06-AC-019 | `run_test` 提交 argv、env、cwd、shell、timeout 或配置外 command_id | Gate schema/语义拒绝；Popen 计数为 0 |
| WP06-AC-020 | `run_test` 正常退出 0 | 返回结构化成功事实和命令证据；不声明任务最终通过 |
| WP06-AC-021 | `run_test` 非零退出 | 返回确定失败、退出码和已采集输出；不自动重试 |
| WP06-AC-022 | `run_test` 超时 | 进程组真实终止；Invocation 为 `UNKNOWN_RESULT`；启动次数为 1 |
| WP06-AC-023 | `run_test` 被可信取消令牌取消 | 进程组终止；结果为 `UNKNOWN_RESULT`；不自动重试 |
| WP06-AC-024 | stdout/stderr 超限或输出含伪密钥 | 输出有界、显式截断；模型/State/Audit 中无明文密钥 |
| WP06-AC-025 | 写入后结果事务或审计失败 | 不返回成功；进入不可重放事实；不自动重复写入 |
| WP06-AC-026 | 写入后文件再次变化 | 新内容版本可见；旧 Evidence 被识别为旧版本，不能支持最终通过 |
| WP06-AC-027 | 同一 Run 并发提交等价写入 | Gate 串行执行；只产生一次成功物理写入，审计顺序稳定 |
| WP06-AC-028 | 测试离线、确定和可重复 | 不访问真实模型、网络或用户凭据；重复运行结果一致 |

## 14. 已关闭决策

以下事项已由需求方于 2026-11-06 确认，并记录为跨工作包决策 D-48 至 D-52：

| 来源 | 正式决策 | 冻结结论 | 影响 |
| --- | --- | --- | --- |
| WP06-OPEN-001 | D-48 单文件 patch 语义 | `apply_patch` 采用单文件 `{path, edits:[{oldText,newText}]}`；每段唯一匹配、按顺序应用、整批原子替换；不支持统一 diff 或多文件 | 通过单次原子替换获得真实全有或全无语义；多文件留给未来独立设计 |
| WP06-OPEN-002 | D-49 测试命令模型输入 | `run_test` 只接受 `{"command_id":"acceptance_test"}`；不接受测试文件子集、argv 或 timeout | 保持 D-32 的最强命令输入约束；未来节点选择需单独评审参数槽 |
| WP06-OPEN-003 | D-50 写入 no-op 语义 | `apply_patch` 拒绝所有 no-op；`write_file` 内容相同时返回 `changed=false` 且不进行物理替换 | 避免无意义物理写入和 mtime 变化，同时保留幂等观察结果 |
| WP06-OPEN-004 | D-51 副作用工具审批策略 | `write_file` 与 `apply_patch` 固定必审；`run_test` 仅由任务 `approval.required_tools` 提升 | 写入始终人工把关，测试命令仍可由任务配置决定是否审批 |
| WP06-OPEN-005 | D-52 测试命令效果分类 | `run_test` 固定为非幂等，任何超时或取消进入 `UNKNOWN_RESULT` | 不假设测试命令没有缓存、报告文件或其他副作用 |

## 15. 完成定义

WP-06 只有同时满足以下条件才可验收：

1. D-48 至 D-52 已确认并写入本文、`decisions.md` 和概要设计；
2. 三个真实工具由可信装配注册，所有调用经过 Gate；
3. 写入路径、任务写范围、审批和参数摘要均有拒绝与篡改测试；
4. 审批前文件不变，审批后写入至多一次，未知结果不重放；
5. `apply_patch` 的空、缺失、歧义和多段失败均证明整批不写；
6. `run_test` 的配置外命令、argv/env/cwd 注入和超时进程组终止有真实测试；
7. 写入、命令、State、Audit 和 Evidence 可通过关联 ID 重建；
8. 目标测试、全量 pytest、Ruff lint 和格式检查通过；
9. `acceptance-report.md` 使用实际证据标记 `accepted` 后，工作包才可进入 `DONE`；
10. 文档明确记录未实现项、剩余风险和下游责任。
