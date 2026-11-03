# WP-00 工程与契约内核实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-00 工程与契约内核 |
| 文档状态 | done |
| 计划版本 | 1.0.2 |
| 对应规格 | `spec.md` v1.0.2 / frozen |
| 依赖 | 无 |
| 当前准入结论 | accepted：T01 至 T08 已完成并通过 WP-00 验收 |
| 下游影响 | WP-01 至 WP-12 的公共模型、配置和测试 Fixture |

本计划对应已冻结的 `spec.md` v1.0.2。当前已完成施工；若后续发现规格冲突，
必须先停止并更新规格，不能在计划中临时改变行为。

## 2. 计划目标与完成定义

本计划把 WP-00 拆成可按顺序验证、审查和回滚的增量：

1. 建立可复现的 Python 工程和统一命令入口；
2. 以严格模型冻结跨模块对象、枚举、错误、ID 和时间；
3. 以严格加载器冻结 YAML 结构、路径归一化和配置指纹；
4. 提供两类任务的隔离 Fixture 和确定性测试基础设施；
5. 用契约测试证明正常、拒绝和安全边界行为；
6. 向 WP-01、WP-03、WP-04 等下游交付冻结的公共 API 和风险清单。

工作包完成不等于项目完成。WP-00 不执行仓库副作用，不能代替 Gate、Sandbox、恢复和 Completion
的下游验收。

## 3. 设计与文件边界

### 3.1 计划文件布局

```text
.gitignore
.python-version
pyproject.toml
src/guarded_harness/
├── __init__.py
├── domain.py
└── config.py
tests/
├── conftest.py
├── fixtures/
│   └── config/
│       ├── code-understanding.yaml
│       ├── test-repair.yaml
│       └── invalid/
│           ├── duplicate-deliverable.yaml
│           ├── path-escape.yaml
│           ├── unknown-field.yaml
│           └── invalid-budget.yaml
├── test_domain.py
└── test_config.py
```

测试 Fixture 不是 WP-12 的最终 `examples/config/`。后者需要由正式配置加载器校验，并用于
Fake Model 端到端任务。

### 3.2 依赖方向

```text
guarded_harness.__init__
        │
        ├── domain.py
        └── config.py ───────> domain.py

tests/test_domain.py ──────> domain.py
tests/test_config.py ──────> config.py + domain.py
tests/conftest.py ─────────> 标准库领域无关测试工具
```

禁止：

- `domain.py` 导入 `config.py`、Sandbox、State Store、Audit 或 `smolagents`；
- `config.py` 执行 Gate、handler、数据库 migration 或网络调用；
- 测试通过私有属性修改安全行为；
- 复制或 monkey patch `smolagents` 核心；
- 让 Fixture 依赖真实用户仓库或本机绝对路径。

### 3.3 公共契约封装

`domain.py` 内部可以使用少量共享基类和 helper，但公共 API 由 `__init__.py` 的显式 `__all__`
导出。所有公共模型使用 Pydantic 2，严格配置集中在基类中，避免每个模型重复遗漏：

```text
StrictDomainModel
  - extra = "forbid"
  - validate_assignment = true
  - use_enum_values = false
  - arbitrary_types_allowed = false
  - str_strip_whitespace = false
```

需要固定事实的 `ToolSpec`、`ToolObservation`、`CompletionCheck` 等模型可以在各自层改为
immutable；可变 Run 状态不得为避免更新而依赖 `object.__setattr__`。

## 4. 详细设计

### 4.1 工程与打包

1. `.python-version` 使用现有 `3.13`，`requires-python` 为 `>=3.10,<3.14`；
2. `pyproject.toml` 使用 PEP 621 项目元数据，并声明：
   - 包名 `guarded-harness`，导入名 `guarded_harness`；
   - `requires-python`；
   - setuptools 或 hatchling 中择一的构建后端；
   - `src/` 布局包发现；
   - console script 留到 WP-11，不在 WP-00 提前添加空命令；
   - pytest、ruff 配置；
3. `pyproject.toml` 同时作为运行时依赖和开发依赖的唯一来源，固定 `smolagents==1.26.0`；
   开发与验收统一执行 `python -m pip install -e '.[dev]'`，不再维护 `requirements.txt`；
4. `.gitignore` 覆盖 `.venv/`、`__pycache__/`、`.pytest_cache/`、`.ruff_cache/`、构建产物、
   `.guarded-harness/`、本地 SQLite/WAL/SHM 和临时测试目录；
5. 包版本 `__version__` 从单一可信来源生成或读取，不在多个文件中手写不一致版本。

依赖安装不应触发测试、网络运行或真实模型初始化。WP-00 不新增未在 docstring/注释中说明用途的
运行时依赖。

### 4.2 严格模型基类与序列化

建议实现以下内部协议：

```text
canonical_json_dump(model) -> str
validate_model(type, mapping) -> model
stable_validation_issues(exc) -> tuple[ValidationIssue, ...]
```

处理顺序：

1. Pydantic 构造或 `model_validate_json`；
2. 把异常转换成 `ValidationIssue`，使用类型与字段位置排序，最多保留固定条数；
3. 对时间、ID、schema version 做跨字段校验；
4. 通过 `model_dump(mode="json")` 生成纯 JSON 可序列化值；
5. `json.dumps` 使用固定参数生成规范字节；
6. 固定模型重新 `model_validate_json`，完成 round-trip 断言。

`Decimal` 进入 JSON 时使用明确版本化格式（建议字符串），避免浮点尾差。枚举始终保存枚举成员，
序列化时输出稳定 value；加载时只接受精确枚举值。

### 4.3 时间和 ID

`Clock`、`IdFactory` 在 `domain.py` 定义为 `typing.Protocol` 或等价协议，不把测试实现带入生产
默认路径。建议默认生产实现放在后续装配层；WP-00 只提供可靠最小实现供模型默认值和测试使用。

ID 设计采用静态正则约束：

- 生成 ID：固定前缀 + 32 位小写十六进制，便于日志关联；
- `trace_id`：32 位小写十六进制；
- `tool_call_id`：非空、长度上限 256、禁止控制字符，其他字节保持原样；
- 所有 ID 在序列化前后字符串完全一致。

默认时钟返回 `datetime.now(timezone.utc)`；序列化 helper 固定 UTC 并输出 `Z`。测试发现 naive
datetime 时抛出 `DOMAIN_TIME_INVALID`。

### 4.4 领域模型与错误码

实现顺序：

1. 标量类型、枚举、`StopReason`、`ErrorCode`；
2. 预算、工作区、时间和 ID 值对象；
3. ToolSpec、ToolInvocation、ToolObservation；
4. Step、Run、Approval、Evidence；
5. CompletionItem、CompletionCheck；
6. `Clock`、`IdFactory`、`FaultInjector` 协议。

每个公共模型至少有以下契约测试：

- 完整合法实例可构造；
- 缺失必填字段失败；
- 多字段注入失败；
- 枚举大小写或未知值失败；
- 模型 JSON round-trip 稳定；
- 数值/时间边界失败；
- 错误码和字段路径稳定。

`Run` 和 `ToolInvocation` 在 WP-00 只定义结构，不添加状态转移方法；状态机由 WP-01 实现。
`Approval` 在 WP-00 只定义绑定字段，不添加批准/消费方法；生命周期由 WP-02 实现。

### 4.5 严格 YAML 加载

建议配置加载流水线：

```python
path = Path(path_input)
raw = read_bounded_bytes(path, max_bytes=1_048_576)
documents = load_single_mapping_with_duplicate_detection(raw)
validated = HarnessConfig.model_validate(documents)
resolved = resolve_and_validate(validated, base_dir=path.parent)
fingerprint = compute_config_fingerprint(resolved)
return ResolvedHarnessConfig(config=resolved, fingerprint=fingerprint)
```

实现要求：

- 先做文件类型、存在性和大小检查，避免读取超大文件；
- 自定义 `SafeLoader` 记录重复 key，不直接使用默认覆盖行为；
- 只接受一个 document；
- YAML anchor/alias 经过大小和深度上限保护，避免别名展开导致内存放大；
- 错误信息不输出完整 YAML 文本或可能敏感的值；
- `load_config` 不调用 `mkdir`、不初始化数据库、不执行 Git；
- Pydantic 校验和跨字段校验分开，以便返回稳定的语义错误。

### 4.6 配置模型

建议使用 discriminated union 表达任务验收：

```text
HarnessConfig
  version
  state_dir
  workspace
  budget
  task

TaskConfig
  id
  kind
  description
  allowed_tools
  paths
  acceptance
  approval

CodeUnderstandingAcceptance
  strategy = "code_understanding"
  deliverables

TestRepairAcceptance
  strategy = "test_repair"
  test_command
  baseline_required = true
```

测试报告格式与节点证据留给 WP-09 冻结；WP-00 只实现 `test_command` 和基线要求，不提前把
未稳定字段写成承诺。

测试目录的写入权限只来自 `task.paths.deny_write` 和 `task.paths.allow_write`，acceptance 不再
保存第二份可写目录，避免 Gate、Sandbox 与 Completion 使用不一致的授权来源。

跨字段校验至少包括：

- `task.kind` 与 `acceptance.strategy` 匹配；
- `required_tools` 属于 `allowed_tools`；
- `allow_write` 与 `deny_write` 不产生隐式安全放宽；
- 交付物不能落在 `deny_write`，除非被任务文档规则显式允许；
- 测试命令 argv 不能为空，`kind=test_repair` 时必须有基线要求；
- `state_dir` 不得指向 `.git/` 或工作区内依赖目录；
- 首期 `channel` 为 `local_cli`。

### 4.7 路径归一化与逃逸判定

提供内部 `PathBoundary` 或等价 helper，供 config、后续 Sandbox 和测试复用，但 WP-00 不把它
宣称为完整 Sandbox。算法：

```text
resolve_existing_or_nearest_ancestor(input_path)
  -> 绝对化
  -> 找到最近已存在祖先
  -> ancestor.resolve(strict=True)
  -> 拼接尚不存在的尾部组件
  -> lexical normalise

assert_within(root, candidate)
  -> candidate == root 或 candidate.relative_to(root) 成功
  -> 失败返回 CONFIG_PATH_ESCAPE
```

`workspace.root` 必须真实存在并解析符号链接。`deliverables` 允许尚不存在，但其最近祖先必须在
工作区内；若最终组件已存在为指向工作区外的 symlink，必须拒绝。路径关系使用 `Path.relative_to`
或等价判断，不比较字符串前缀。

配置中的 glob 模式只做静态归一化和词法安全校验；真正的文件匹配与操作前后真实落点检查属于
WP-03/WP-06。

### 4.8 配置规范化

对集合语义字段：

- 拒绝重复项；
- 按规范化 POSIX 字符串排序；
- 记录在 `ResolvedHarnessConfig`，不改变原始语义。

对有顺序语义字段：

- `deliverables` 保留输入顺序并根据 D-03 进入指纹；
- `test_command` 严格保留 argv 顺序；
- 保留空列表/非空列表的系统差异并显式校验。

YAML 的显式默认值与省略默认值必须得到相同有效配置。路径等价写法通过真实路径归一化后得到相同
配置对象。

### 4.9 配置指纹

指纹实现建议：

```python
payload = {
    "fingerprint_schema": 1,
    "config": resolved_config.model_dump(mode="json"),
}
canonical = json.dumps(
    payload,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
)
digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
return f"sha256:{digest}"
```

需要测试：

- 相同语义的 YAML 注释、键顺序、缩进不同，指纹相同；
- 集合字段顺序不同，指纹相同；
- 交付物顺序改变，指纹改变；
- 预算、任务说明、路径、命令、审批或 state 目录改变，指纹改变；
- 路径等价写法得到同一指纹；
- 计算异常不会返回空字符串；
- 指纹输出始终匹配 `sha256:<64 lowercase hex>`。

### 4.10 测试基础设施

`tests/conftest.py` 建议提供：

| Fixture | 输出 | 关键约束 |
| --- | --- | --- |
| `temp_git_repo` | Git 根、工作区、外围脏映射 | 每个用例隔离，不访问真实仓库 |
| `temp_sqlite_path` | 不存在的 SQLite 路径 | 不创建 WP-01 表 |
| `fixed_clock` | 固定 UTC 时间序列 | 可断言，无真实 sleep |
| `fixed_ids` | 固定 ID 序列 | 可追溯，无随机断言 |
| `fault_injector` | 可注册故障的测试对象 | 调用计数确定 |
| `config_factory` | 生成合法/非法 YAML 的 helper | 默认值和错误样例集中 |

Fixture 应优先使用 `pytest` 临时目录和标准库，不引入网络、Docker、真实数据库服务或外部模型。
Git Fixture 若依赖本机 Git，可在 WP-00 测试开始时检查 `git --version` 并在缺失时明确 skip；
WP-12 最终环境必须提供 Git，不能长期依赖 skip 通过验收。

## 5. 施工任务卡

### WP00-T01 工程与依赖基线

| 项目 | 内容 |
| --- | --- |
| 目标 | 建立可安装、可导入、可运行 pytest/Ruff 的最小 Python 工程 |
| 前置 | 规格冻结完成 |
| 涉及文件 | `.gitignore`、`.python-version`、`pyproject.toml`、`src/guarded_harness/__init__.py` |
| 对应规格 | WP00-AC-001、WP00-INV-009、OD-01 |
| 完成条件 | `pip install -e '.[dev]'` 成功；导入包和版本；pytest/Ruff 可发现配置 |
| 拒绝/故障 | Python 版本不符、依赖无法安装、包发现失败时明确失败 |

### WP00-T02 领域标量、枚举和错误契约

| 项目 | 内容 |
| --- | --- |
| 目标 | 冻结 TaskKind、状态枚举、Tool 风险/权限、ErrorCode、ID/时间 helper |
| 前置 | T01 |
| 涉及文件 | `domain.py`、`__init__.py`、`tests/test_domain.py` |
| 对应规格 | WP00-INV-002、WP00-INV-003、WP00-AC-004、WP00-AC-013，D-11 |
| 完成条件 | 错误码和枚举稳定；未知值/naive time 拒绝；公共导出契约通过 |
| 拒绝/故障 | 非法 ID、未知 schema version、本地时区时间失败关闭 |

### WP00-T03 公共领域模型

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 Run、Step、ToolSpec、ToolInvocation、ToolObservation、Approval、Evidence、Completion |
| 前置 | T02 |
| 涉及文件 | `domain.py`、`__init__.py`、`tests/test_domain.py` |
| 对应规格 | WP00-AC-002、WP00-AC-003、WP00-AC-013，及第 7 节全部模型 |
| 完成条件 | 合法模型 round-trip；未知/缺失字段拒绝；模型字段和导出集合可审查 |
| 拒绝/故障 | 类型、枚举、预算、时间和 schema version 错误产生稳定 issue |

### WP00-T04 严格配置 schema

| 项目 | 内容 |
| --- | --- |
| 目标 | 定义 HarnessConfig、Workspace/Budget/Path/Approval 和两类 task acceptance |
| 前置 | T03；测试修复配置遵循冻结决策 D-13 |
| 涉及文件 | `config.py`、`domain.py`、`tests/test_config.py` |
| 对应规格 | WP00-AC-005 至 WP00-AC-009，第 8.2 至 8.4 节 |
| 完成条件 | 两类配置合法；未知字段、策略不匹配、预算/路径/重复项拒绝 |
| 拒绝/故障 | 所有拒绝都在返回 ResolvedHarnessConfig 前发生，不创建文件 |

### WP00-T05 YAML 加载、路径归一化和指纹

| 项目 | 内容 |
| --- | --- |
| 目标 | 完成安全 YAML loader、路径边界 helper、规范化和 SHA-256 指纹 |
| 前置 | T04 |
| 涉及文件 | `config.py`、`tests/test_config.py` |
| 对应规格 | WP00-AC-006 至 WP00-AC-010、WP00-INV-004 至 WP00-INV-006 |
| 完成条件 | 重复 key/多文档拒绝；软链接逃逸拒绝；等价拼写同指纹；有效字段变化异指纹 |
| 拒绝/故障 | 读取/解析/规范化/指纹异常不返回部分配置 |

### WP00-T06 测试 Fixture 与任务样例

| 项目 | 内容 |
| --- | --- |
| 目标 | 建立隔离 Git/SQLite/Clock/ID/FaultInjector Fixture 和两份任务配置 |
| 前置 | T05 |
| 涉及文件 | `tests/conftest.py`、`tests/fixtures/config/**`、相关测试 |
| 对应规格 | WP00-AC-005、WP00-AC-011、WP00-AC-012，第 9 节 |
| 完成条件 | 两个任务配置可独立加载；测试重复运行结果一致；无本机绝对路径依赖 |
| 拒绝/故障 | 缺失 git/临时目录不可用时明确失败或在非最终环境明确 skip |

### WP00-T07 契约和安全边界测试

| 项目 | 内容 |
| --- | --- |
| 目标 | 覆盖所有 WP00-AC 场景及拒绝不产生副作用 |
| 前置 | T02 至 T06 |
| 涉及文件 | `tests/test_domain.py`、`tests/test_config.py`、必要 Fixture |
| 对应规格 | 第 12 节全部场景，第 13 节 G1/S1/S2/S3/H3 的 WP-00 部分 |
| 完成条件 | 目标测试、全量 pytest、Ruff lint、Ruff format check 全绿 |
| 拒绝/故障 | 失败测试不得被 skip、xfail 或弱断言掩盖 |

### WP00-T08 文档、索引与契约封版

| 项目 | 内容 |
| --- | --- |
| 目标 | 同步公共 API、冻结决策、规格状态和下游交接 |
| 前置 | T07 |
| 涉及文件 | `docs/README.md`、`docs/task-breakdown.md`、工作包文档、必要的设计/决策文档 |
| 对应规格 | 第 14、15、16 节 |
| 完成条件 | spec 标记 frozen；plan 标记 done；公共契约和错误码与测试一致；下游风险记录 |
| 拒绝/故障 | 文档与实现不一致时保持 blocked，不宣称 WP-00 完成 |

任务之间默认串行。T02/T03 与 T04 的测试准备可以在同一开发者内小步并行，但公共模型未稳定前
不能冻结配置文件格式。

## 6. 施工顺序与增量提交

推荐顺序：

```text
T01 工程
  -> T02 标量/枚举/错误
  -> T03 模型
  -> T04 配置 schema
  -> T05 loader/路径/指纹
  -> T06 Fixture
  -> T07 安全测试
  -> T08 文档和封版
```

建议提交粒度：

```text
chore(wp-00): add reproducible Python project baseline
feat(wp-00): define strict harness domain contracts
feat(wp-00): add typed task model contracts
feat(wp-00): load and fingerprint validated YAML config
test(wp-00): cover strict config and path boundaries
test(wp-00): add deterministic harness fixtures
docs(wp-00): freeze engineering contract baseline
```

每个提交只包含一个可测试行为；涉及公共字段时同步契约测试和文档。不得用“包能 import”代替
安全边界测试。

## 7. 测试计划

### 7.1 `tests/test_domain.py`

建议测试分组与场景：

| 测试组 | 覆盖 |
| --- | --- |
| strict_models | 缺字段、未知字段、错类型、schema version |
| enums_errors | 枚举边界、错误码稳定、issue 顺序 |
| ids_time | ID 格式/保留 tool_call_id、UTC 时间、naive/本地时区拒绝 |
| canonical_serialization | JSON round-trip、Decimal、map 顺序、列表顺序 |
| model_contracts | Run、Step、ToolSpec、Invocation、Observation、Approval、Evidence、Completion |
| exported_api | `__all__`/包根导入/版本 |

### 7.2 `tests/test_config.py`

建议测试分组与场景：

| 测试组 | 覆盖 |
| --- | --- |
| yaml_strictness | 空、多文档、重复 key、非 mapping、未知字段、超大文件 |
| task_fixtures | 代码理解、测试修复两份合法配置 |
| semantic_validation | kind/strategy、工具、预算、审批通道、命令 |
| paths | 相对基准、绝对交付物、`..`、symlink、最近存在祖先、重复别名 |
| fingerprint | 排版等价、集合等价、敏感字段变化、哈希格式 |
| side_effects | 拒绝后目标目录/state 目录无新增，不创建 SQLite |

### 7.3 测试数据

- 合法配置只使用工作区内相对路径和临时仓库；
- 错误配置按错误码分文件，避免一个样例同时触发多个无关错误；
- 软链接测试在支持 symlink 的平台创建并在不支持平台明确说明；
- 时间断言使用固定 Clock，不使用 `sleep`；
- ID 断言使用固定序列，不依赖真实随机 UUID；
- 所有临时文件由 pytest 管理，不在项目 `docs/` 或根目录保存运行产物。

## 8. 规格场景到测试与证据映射

| 规格场景 | 主要测试入口 | 验收证据 |
| --- | --- | --- |
| WP00-AC-001 | `test_domain.py::test_public_api_importable` | pip 安装日志、导入测试 |
| WP00-AC-002 | `test_domain.py::test_models_forbid_unknown_and_missing` | 错误码和字段路径断言 |
| WP00-AC-003 | `test_domain.py::test_domain_models_round_trip` | 固定 JSON 契约 fixture |
| WP00-AC-004 | `test_domain.py::test_enum_and_utc_time_boundaries` | 拒绝异常断言 |
| WP00-AC-005 | `test_config.py::test_load_task_fixtures` | 两份配置加载结果 |
| WP00-AC-006 | `test_config.py::test_yaml_strict_errors` | 每类 YAML 错误码 |
| WP00-AC-007 | `test_config.py::test_cross_field_semantics` | 预算/策略/工具/审批拒绝 |
| WP00-AC-008 | `test_config.py::test_path_escape_and_symlink` | 工作区外路径未创建/未读取 |
| WP00-AC-009 | `test_config.py::test_duplicate_deliverables` | 规范化重复错误 |
| WP00-AC-010 | `test_config.py::test_fingerprint_determinism` | 相同/不同指纹断言 |
| WP00-AC-011 | `test_config.py::test_fixtures_are_deterministic` | 固定时钟/ID 和离线命令 |
| WP00-AC-012 | `test_config.py::test_rejection_has_no_side_effect` | 目录快照前后一致 |
| WP00-AC-013 | `test_domain.py::test_public_contract_snapshot` | 导出集合/枚举/schema 快照 |

飞书 G1/S1/S2/S3 的 WP-00 部分分别由 schema 错误、路径归一化、软链接和配置 argv 测试覆盖；
真正的 handler、Sandbox 和进程测试由 WP-03/WP-04/WP-06 补齐。H3 的 WP-00 部分只验证模型边界，
预算停止行为由 WP-01/WP-10 验证。

## 9. 验收命令

施工完成后在声明的 Python 虚拟环境中执行：

```bash
python -m pip install -e '.[dev]'
python -m pytest tests/test_domain.py tests/test_config.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

最终证据记录 Python 版本、pip 安装结果、测试数量、失败/skip 数、Ruff 退出码和 Git diff 范围。

## 10. 风险、阻塞与处理

| 风险 | 影响 | 处理 |
| --- | --- | --- |
| Python 3.10 运行时未单独验证 | 最低版本兼容性只有静态检查证据 | 保留 Ruff `py310` 检查并记录环境限制，不宣称已完成 3.10 运行时验收 |
| 公共 ID/时间格式不一致 | 持久化和测试关联失败 | 严格按 D-11 实现格式与契约测试 |
| 指纹基准不一致 | 恢复/审批误判 | 按 D-12 固定配置目录基准和集合顺序 |
| 测试报告字段提前扩展 | 与 WP-09 契约冲突 | WP-00 只实现 D-13 允许的字段 |
| 下游改名/加字段 | 破坏性契约变更 | 按 D-14 使用 schema 升级和冻结导出 |
| 跨平台 symlink 行为不同 | Windows/macOS 测试结果差异 | 平台明确 skip 并记录最终验收环境 |
| Pydantic/依赖升级 | 严格序列化或 model 行为变化 | 固定主版本范围并增加 round-trip 契约测试 |
| `smolagents` 引入 import/依赖副作用 | 包导入变慢或触发额外依赖 | WP-00 不让 `domain.py` 导入 smolagents，后续单独适配测试 |
| 路径 helper 被误认为完整 Sandbox | 下游错误地跳过真实落点检查 | 文档和命名明确只做配置静态检查 |
| 配置 Fixture 被当成正式样例 | 交付证据与实际任务配置混淆 | 保持 `tests/fixtures/config/` 与 WP-12 examples 分离 |

## 11. 回滚策略

WP-00 在开始前没有任何运行时业务数据，因此回滚原则是：

1. 每个增量可独立 revert；
2. 领域模型变更时先回滚公共导出和测试，再回滚 config schema；
3. 尚未发布 migration，不涉及数据库回滚；
4. 若路径/指纹方案未达成一致，恢复到上一个已冻结 spec 和对应代码提交；
5. 不允许通过放宽测试、删除拒绝场景或把未知字段转为允许来“修复”失败；
6. 若已与其他工作包共享公共模型，回滚前必须通知下游并更新其依赖和测试。

## 12. 下游交接

WP-00 封版后必须交付：

- `domain.py` 公共模型和枚举的冻结版本；
- `config.py` 配置 schema、路径基准、指纹算法和错误码；
- 两份任务 Fixture 的可用加载结果；
- 隔离 Git/SQLite/Clock/ID/FaultInjector 的测试 Fixture；
- WP00-AC 到测试证据的映射；
- 未解决风险和 P1 未纳入项；
- 给 WP-01、WP-03、WP-04、WP-07、WP-08、WP-09、WP-10、WP-11、WP-12 的契约变更摘要。

下游不得在未提升 schema version 的情况下：

- 复制一份不同的 Run/ToolInvocation 模型；
- 改变 `tool_call_id` 的透传语义；
- 用裸字典替代审批或证据；
- 改变配置相对路径基准或指纹算法；
- 把 WP-00 的静态路径检查当作真实 Sandbox 操作。

## 13. 实际完成情况

当前状态：**已完成 WP-00 验收**。完成日期：2026-11-03。

| 项目 | 状态 |
| --- | --- |
| `spec.md` | frozen，v1.0.2 |
| `plan.md` | done，v1.0.2 |
| 工程文件 | `.gitignore`、`.python-version`、`pyproject.toml`、包入口和构建配置已完成 |
| 公共模型 | 严格枚举、错误码、ID/时间、Run/Step/Tool/Approval/Evidence/Completion 契约已完成 |
| 配置加载与指纹 | 严格 YAML、重复键/复杂度检查、路径归一化、绝对 `workspace.root`/`state_dir` 和 SHA-256 指纹已完成 |
| Fixture 与测试 | 临时 Git/SQLite、固定 Clock/ID、FaultInjector 和两类任务配置已完成 |
| 验收命令 | 安装、目标测试、全量测试、Ruff lint、格式检查和 `pip check` 全部通过 |
| 开放决策 | OD-01 至 OD-06 已关闭 |

### 13.1 任务完成记录

| 任务 | 状态 | 证据 |
| --- | --- | --- |
| WP00-T01 工程与依赖基线 | DONE | `.venv` editable 安装成功，`pip check` 无损坏依赖 |
| WP00-T02 领域标量、枚举和错误 | DONE | `tests/test_domain.py` 覆盖严格枚举、ID、时间和稳定错误码 |
| WP00-T03 公共领域模型 | DONE | 公共模型、schema version、稳定序列化和导出契约测试通过 |
| WP00-T04 严格配置 schema | DONE | 两类任务配置加载，跨字段语义和未知字段拒绝通过 |
| WP00-T05 YAML、路径和指纹 | DONE | 重复键、复杂度、穿越、软链接、敏感路径和指纹测试通过 |
| WP00-T06 Fixture 与任务样例 | DONE | `tests/conftest.py` 与 `tests/fixtures/config/` 已落地 |
| WP00-T07 契约与安全测试 | DONE | 目标测试和全量测试均为 29 passed |
| WP00-T08 文档与封版 | DONE | spec/plan、决策、索引和 WP-00 总计划入口已同步 |

### 13.2 验收证据

验收环境：`source .venv/bin/activate`，Python 3.13.5，pip 25.1.1。

| 命令 | 结果 |
| --- | --- |
| `python -m pip install -e '.[dev]'` | 成功安装项目和 dev 依赖 |
| `python -m pytest tests/test_domain.py tests/test_config.py -q` | `29 passed in 0.79s` |
| `python -m pytest -q` | `29 passed in 1.06s` |
| `python -m ruff check .` | `All checks passed!` |
| `python -m ruff format --check .` | `17 files already formatted` |
| `python -m pip check` | `No broken requirements found.` |

### 13.3 剩余风险

- 当前环境只实际运行 Python 3.13；最低版本仅由 Ruff `py310` 语法目标约束，Python 3.10–3.12
  未在独立解释器上执行测试。
- WP-00 的路径检查是配置加载期静态边界，不替代 WP-03 Sandbox 在真实 I/O 前的再次检查。
- WP-00 未实现 Gate、SQLite 业务表、审批消费、恢复、真实工具或 Agent 循环；这些仍由后续
  工作包负责。
