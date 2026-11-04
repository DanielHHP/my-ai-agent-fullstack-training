# WP-00 工程与契约内核验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-00 工程与契约内核 |
| 报告状态 | accepted |
| 报告版本 | 1.1.0 |
| 验收日期 | 2026-11-03 |
| 验收结论 | WP-00 v1.1.0 compatibility amendment 通过验收；项目整体仍在后续工作包中 |
| 对应规格 | [spec.md](spec.md) v1.1.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.1.0 / done |
| 代码基线 | `17933bbfd849a6a3aa65db69a32b35d1103f788e` |
| 基线工作区 | 代码基线提交时干净，无未提交修改 |
| 验收环境 | macOS Darwin x86_64；Python 3.13.5；pip 25.1.1 |

本报告是 WP-00 的程序证据汇总，不把模型自述或“文件存在”作为验收依据。验收结论只覆盖
工程基线、公共领域契约、严格配置与 Fixture；不覆盖 Gate、Sandbox、持久化、审批、恢复、
Completion 或 Agent 端到端运行。

> 兼容修订说明：本报告的规格、计划和实测证据对应 v1.1.0 已验收基线。`spec.md` 随后按
> D-23 至 D-28 提升到 v1.2.0，新增 Approval v2、审批角色/TTL 和 StopReason；该修订已由
> WP-02 T01 实现并通过其目标/全量测试，验收证据见
> [WP-02 acceptance-report.md](../wp-02-approval-lifecycle/acceptance-report.md)。

## 2. 验收范围

### 2.1 已验收能力

- 可复现的 src-layout Python 工程、editable 安装和统一开发依赖入口；
- 严格、可版本化、可稳定序列化的公共领域模型；
- 稳定枚举、错误码、ID、UTC 时间和 JSON 序列化规则；
- 严格 YAML 加载、跨字段语义校验、路径归一化、`state_dir` 工作区隔离和配置指纹；
- 代码理解与测试修复两类任务的 Fixture 配置；
- 临时 Git 工作区、SQLite 路径、固定 Clock/ID 和 FaultInjector 测试设施；
- 默认离线、确定性测试以及 Ruff lint/格式检查。

### 2.2 不在本报告验收范围

- WP-01 的 SQLite 业务表、状态机、预算持久化、审计链和租约；
- WP-02 的审批绑定、过期和一次性消费；
- WP-03/WP-06 的真实 Sandbox、文件/命令 handler、进程终止和输出截断；
- WP-04 的 Registry、Policy 和 Gate 固定检查链；
- WP-07 的 Checkpoint 和崩溃恢复；
- WP-08/WP-09 的代码理解与测试修复 Completion；
- WP-10/WP-11 的 `ToolCallingAgent` 适配、Harness 循环和 CLI；
- WP-12 的全局故障矩阵、正式样例和项目最终交付。

## 3. 交付物核对

| 交付物 | 位置 | 结果 |
| --- | --- | --- |
| 工程元数据与依赖 | `pyproject.toml`、`.python-version`、`.gitignore` | PASS |
| 包入口和版本 | `src/guarded_harness/__init__.py`、`_version.py` | PASS |
| 公共领域模型 | `src/guarded_harness/domain.py` | PASS |
| 配置加载与指纹 | `src/guarded_harness/config.py` | PASS |
| 领域契约测试 | `tests/test_domain.py` | PASS |
| 配置与路径测试 | `tests/test_config.py` | PASS；新增 `state_dir` 必填、工作区外和软链接真实落点测试 |
| 测试 Fixture | `tests/conftest.py`、`tests/fixtures/config/**` | PASS |
| 包内规格和计划 | `spec.md`、`plan.md` | PASS |
| 文档索引 | `docs/README.md`、`docs/work-packages/index.md` | PASS |

`pyproject.toml` 是运行时依赖和 `dev` 依赖的唯一来源。`requirements.txt` 已删除，打包元数据、
开发工具和安装入口不存在双份声明。

## 4. 执行证据

所有命令均在仓库根目录执行，Python 环境由 `source .venv/bin/activate` 激活。
本轮记录的命令退出码均为 0；测试失败数为 0，skip 数为 0。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| Editable 安装 | `python -m pip install -e '.[dev]'` | PASS；成功构建并安装 `guarded-harness==0.1.0` |
| WP-00 目标测试 | `python -m pytest tests/test_domain.py tests/test_config.py -q` | PASS；`31 passed in 0.62s` |
| 全量测试 | `python -m pytest -q` | PASS；`31 passed in 0.58s` |
| Lint | `python -m ruff check .` | PASS；`All checks passed!` |
| 格式 | `python -m ruff format --check .` | PASS；`20 files already formatted` |
| 依赖一致性 | `python -m pip check` | PASS；`No broken requirements found.` |
| 安装后导入 | 导入 `guarded_harness` 并读取版本、导出数 | PASS；版本 `0.1.0`，包根导出 50 个名称 |

当前直接依赖版本核验：

```text
smolagents==1.26.0
pydantic==2.14.1
PyYAML==6.0.3
pytest==9.1.1
ruff==0.17.3
```

## 5. 规格场景验收

| 规格场景 | 验收结果 | 主要证据 |
| --- | --- | --- |
| WP00-AC-001 新环境安装并导入 | PASS | editable 安装、包根导入、包元数据测试 |
| WP00-AC-002 缺字段、多字段和错类型拒绝 | PASS | `test_models_forbid_unknown_and_missing` |
| WP00-AC-003 固定领域模型 round-trip | PASS | `test_domain_models_round_trip`、ToolSpec 规范 JSON round-trip |
| WP00-AC-004 枚举和时间边界 | PASS | `test_enum_id_and_time_boundaries` |
| WP00-AC-005 两份任务 Fixture | PASS | `test_load_task_fixtures` |
| WP00-AC-006 YAML 严格解析 | PASS | `test_yaml_strict_errors`、`test_missing_config_and_non_file` |
| WP00-AC-007 预算和任务语义 | PASS | `test_cross_field_semantics`、`test_invalid_fixtures_fail_closed` |
| WP00-AC-008 路径穿越和符号链接 | PASS | `test_symlink_escape_is_rejected`、Windows/敏感路径测试 |
| WP00-AC-009 重复交付物 | PASS | `test_invalid_fixtures_fail_closed[duplicate-deliverable]` |
| WP00-AC-010 指纹稳定与敏感 | PASS | 等价配置、默认值、顺序变化和语义变化测试 |
| WP00-AC-011 测试确定性 | PASS | 固定 Clock/ID、FaultInjector 测试；默认测试离线 |
| WP00-AC-012 失败不产生副作用 | PASS | `test_rejection_has_no_side_effects` |
| WP00-AC-013 公共导出与契约冻结 | PASS | `test_public_api_importable`、`test_pyproject_is_the_single_dependency_source` |
| WP00-AC-014 状态目录隔离 | PASS | `test_state_dir_is_required_and_outside_workspace`、`test_state_dir_symlink_into_workspace_is_rejected` |

## 6. 任务卡验收

| 任务 | 状态 | 验收结论 |
| --- | --- | --- |
| WP00-T01 工程与依赖基线 | DONE | editable 安装、导入、版本、依赖元数据和 `pip check` 均通过 |
| WP00-T02 领域标量、枚举和错误 | DONE | 严格枚举、ID、UTC 时间、schema version 和稳定错误码测试通过 |
| WP00-T03 公共领域模型 | DONE | 必填/未知字段、模型 round-trip、权限集合稳定序列化通过 |
| WP00-T04 严格配置 schema | DONE | 两类 Acceptance、预算、审批、工具和策略匹配拒绝路径通过 |
| WP00-T05 YAML、路径和指纹 | DONE | 重复 key、多文档、复杂度、穿越、软链接、敏感路径、state_dir 隔离及指纹通过 |
| WP00-T06 Fixture 与任务样例 | DONE | 临时 Git/SQLite、Clock、ID、FaultInjector 和两份配置可复用 |
| WP00-T07 契约与安全测试 | DONE | 31 项测试全部通过，无失败；当前支持的平台未触发 symlink skip |
| WP00-T08 文档与封版 | DONE | spec/plan、决策、索引、安装入口和本报告已同步 |

## 7. 安全与失败关闭检查

| 检查项 | 证据 | 结果 |
| --- | --- | --- |
| 模型/配置注入未声明字段 | Pydantic `extra="forbid"` 测试 | PASS |
| 缺字段、非法枚举、naive 时间 | 领域契约测试 | PASS |
| 超大 YAML、多文档、重复键、危险 tag | YAML 严格测试 | PASS |
| 绝对交付物和 Windows 风格路径别名 | 配置路径测试 | PASS |
| `..` 路径逃逸 | 非法 Fixture | PASS |
| 符号链接逃逸 | 临时真实 symlink 测试 | PASS |
| `.env`、私钥、`.git`、状态根目录 | 敏感路径和受保护 state 测试 | PASS |
| `state_dir` 缺失或进入工作区 | 必填、`relative_to` 和符号链接落点测试 | PASS |
| 重复工具和重复交付物 | 跨字段/规范化重复测试 | PASS |
| 指纹环境依赖 | 环境变量与路径等价测试 | PASS |
| 拒绝产生文件或目录副作用 | 目录快照测试 | PASS |

WP-00 不包含 handler，因此本报告不宣称已经验证“Gate 拒绝时 handler 执行次数为 0”；
该证据必须由 WP-04 及真实工具工作包提供。

## 8. 飞书核心验收关联

指定飞书文档“核心验收”表 revision 11 包含 15 条 P0 和 4 条 P1。WP-00 只承接与公共契约、
严格配置、路径原文校验和测试确定性直接相关的部分，不能代表这些场景已端到端通过。

| 核心场景 | WP-00 验收内容 | 状态 | 后续责任 |
| --- | --- | --- | --- |
| G1 参数缺字段/错类型 | 严格模型与 ToolObservation 契约 | WP-00 部分 PASS | WP-04 验证 handler 未执行 |
| S1/dot-dot 路径逃逸 | 配置加载期拒绝交付物逃逸 | WP-00 部分 PASS | WP-03 验证真实 I/O 前拦截 |
| S2 软链接和敏感目录 | 配置路径真实落点与敏感路径拒绝 | 配置层 PASS；P1 | WP-03 验证执行与审计 |
| S3 配置外命令 | 受控 argv 配置字段 | 契约 PASS | WP-03/WP-06 验证不执行 |
| H3 预算停止 | StopReason、RunStatus、预算模型边界 | 模型层 PASS | WP-01/WP-10 验证终态行为 |
| T1 确定性测试 | WP-00 场景离线、可重复执行 | WP-00 部分 PASS | WP-12 验证全部 P0 场景 |

其余核心验收场景依赖 Gate、审批、恢复、Completion 或端到端 Agent 运行，本报告不将其标记为
通过。

## 9. 已知限制与剩余风险

1. 当前只在 Python 3.13.5 运行测试；Python 3.10–3.12 由 Ruff `py310` 静态目标约束，没有独立
   解释器运行证据。
2. 当前只在 macOS x86_64 验证；Windows 路径别名和文件系统大小写行为采用防御性拒绝逻辑，
   但未在 Windows 运行时验证。
3. WP-00 路径检查是配置加载期静态边界，不能替代 WP-03 Sandbox 在真实 I/O 前的再次检查。
4. 测试数量按规格场景和关键拒绝路径设计，未设置行覆盖率或分支覆盖率门禁；后续工作包仍需
   增加 Gate、Sandbox、恢复和 Completion 的专门测试。
5. WP-00 没有 SQLite 业务表、审计、审批消费、Checkpoint、真实工具或 `ToolCallingAgent`
   循环，因此不能用于宣称项目整体或两类任务端到端完成。

## 10. 验收结论

**WP-00 工程与契约内核 v1.1.0 compatibility amendment：ACCEPTED。**

冻结规格、计划任务、自动化测试、lint、格式检查和依赖一致性均已有可复现证据，代码基线已经
固定为上述提交。

项目整体仍处于 P0 开发阶段。WP-01 的规格和计划已经冻结/批准，现在可以按 WP01-T01 至 T08
的顺序进入实现。
