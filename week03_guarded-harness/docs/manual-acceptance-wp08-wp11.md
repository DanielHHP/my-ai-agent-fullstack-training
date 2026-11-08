# WP-08 至 WP-11 手动验收操作说明

## 1. 目的与边界

本文用于人工复核 WP-08 至 WP-11 中无法完全由机械规则证明的质量项：

- 代码理解文档的事实、推断和业务语义是否合理；
- 测试修复是否针对根因，是否存在断言弱化；
- CLI 审批、inspect、跨进程 resume 和错误提示是否可操作；
- 各类失败状态是否清楚、是否仍保留禁止操作边界。

手动验收不能替代冻结规格要求的 P0 自动化 AC。自动化测试失败、缺失或存在未解释 skip 时，
即使人工操作看起来正常，也不能把工作包标为 accepted。

2026-11-08 需求方已按本指南完成实际操作，所有观察项均符合预期；各工作包验收报告已据此升级为
`accepted`。

## 2. 环境准备

建议在 Linux 或 macOS 上执行。Windows 不属于 P0 进程组支持范围。

### 2.1 激活项目环境

```bash
cd /Users/app_dev/code/ai_agent/ai_agent_training/my-ai-agent-fullstack-training/week03_guarded-harness
source .venv/bin/activate
python --version
python -m pip install -e '.[dev]'
```

预期：

- Python 为 3.13.x（最低语法目标仍为 Python 3.10）；
- editable package 安装成功；
- `guarded-harness`、`pytest` 和 `ruff` 来自当前 `.venv`。

### 2.2 先执行自动化门禁

```bash
python -m pytest tests/test_completion.py tests/test_harness_loop.py tests/test_cli.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
git diff --check
```

预期：

- 全部测试通过；
- 失败、错误、未解释 skip 均为 0；
- Ruff 和格式检查通过；
- 任一命令失败时停止手动验收，先按对应工作包修复。

### 2.3 准备独立验收目录

不要在项目仓库内直接运行任务，避免生成状态目录和交付物：

```bash
export WP_ACCEPT_DIR="$(mktemp -d /tmp/guarded-harness-manual.XXXXXX)"
python tests/manual_acceptance_runner.py prepare "$WP_ACCEPT_DIR"
export WP_EXAMPLES="$WP_ACCEPT_DIR/examples"
export CODE_CONFIG="$WP_EXAMPLES/config/code-understanding.yaml"
export REPAIR_CONFIG="$WP_EXAMPLES/config/test-repair.yaml"
```

预期输出包含：

```text
prepared=<临时目录>
code_config=<临时目录>/examples/config/code-understanding.yaml
repair_config=<临时目录>/examples/config/test-repair.yaml
```

该 runner 只存在于 `tests/`，使用脚本化 Fake Model 调用与生产相同的 CLI `main`。它不是生产
Provider，也不能作为真实模型能力证据。

## 3. 代码理解人工验收

### 3.1 启动到审批等待

先记录源代码摘要和目标交付物状态：

```bash
git -C "$WP_EXAMPLES" status --short
test ! -e "$WP_EXAMPLES/workspaces/code-understanding/artifacts/code-understanding.md"
```

执行：

```bash
python tests/manual_acceptance_runner.py start-code "$WP_ACCEPT_DIR"
```

预期：

- 退出码为 `3`；
- JSON 中 `status=WAITING_APPROVAL`、`acceptance_status=NOT_EVALUATED`；
- 返回 `run_id` 和 `approval_id`；
- 交付物仍不存在；
- `git status --short` 不显示任务源码或交付物修改。

把输出中的值记录下来：

```bash
export CODE_RUN_ID="<粘贴 run_id>"
export CODE_APPROVAL_ID="<粘贴 approval_id>"
```

### 3.2 只读查看

```bash
guarded-harness --config "$CODE_CONFIG" approvals list \
  --run-id "$CODE_RUN_ID" --json

guarded-harness --config "$CODE_CONFIG" inspect "$CODE_RUN_ID" --json
```

人工观察：

- Approval 为 `PENDING`，绑定的工具是 `write_file`；
- Run 为 `WAITING_APPROVAL`；
- 能看到 STEP、ToolCall、Checkpoint 和 Audit head；
- 不显示完整参数正文、文件内容或敏感值；
- 命令执行前后目标文件仍不存在。

### 3.3 审批并跨进程恢复

```bash
guarded-harness --config "$CODE_CONFIG" approvals approve "$CODE_APPROVAL_ID" \
  --actor manual-operator --role local_operator --json
```

预期：

- 退出码 0；
- Approval 变为 `APPROVED`；
- Run 仍等待执行；
- 交付物仍不存在。

另起命令恢复：

```bash
python tests/manual_acceptance_runner.py resume-code \
  "$WP_ACCEPT_DIR" "$CODE_RUN_ID"
```

预期：

- 退出码 0；
- `status=COMPLETED`、`acceptance_status=ACCEPTED`；
- 交付物存在；
- 源码、测试和配置没有额外修改。

### 3.4 人工审查生成文档

打开：

```text
$WP_EXAMPLES/workspaces/code-understanding/artifacts/code-understanding.md
```

对照：

```text
$WP_EXAMPLES/workspaces/code-understanding/src/app.py
```

逐项检查：

- 每个“事实”是否与引用行一致；
- 主观解释是否位于“推断”，没有被写成确定事实；
- `E1` 等引用是否指向真实文件和有效行号；
- 文档是否说明了实际行为，而不是模板化复述；
- 是否泄露密钥、令牌或无关用户数据；
- Git diff 是否只有交付物变化。

执行：

```bash
git -C "$WP_EXAMPLES" status --short
git -C "$WP_EXAMPLES" diff -- \
  workspaces/code-understanding/src \
  workspaces/code-understanding/tests
```

预期源码和测试 diff 为空。记录：

```text
代码理解语义审查：PASS / FAIL
问题：
证据路径：
审查人/时间：
```

### 3.5 重复 resume

再次执行：

```bash
python tests/manual_acceptance_runner.py resume-code \
  "$WP_ACCEPT_DIR" "$CODE_RUN_ID"
```

预期：

- 仍返回已接受终态；
- 文件内容不变；
- 不产生第二次写入；
- inspect 中 `write_file` 调用仍只有一次。

## 4. 测试修复人工验收

### 4.1 确认失败基线

```bash
git -C "$WP_EXAMPLES" status --short
git -C "$WP_EXAMPLES" diff -- workspaces/test-repair/src
```

执行：

```bash
python tests/manual_acceptance_runner.py start-repair "$WP_ACCEPT_DIR"
```

预期：

- 退出码 3；
- Run 为 `WAITING_APPROVAL`；
- 源码仍为 `return left - right`；
- 已有测试文件未修改；
- inspect 中可见基线测试执行和至少一个失败节点。

记录：

```bash
export REPAIR_RUN_ID="<粘贴 run_id>"
export REPAIR_APPROVAL_ID="<粘贴 approval_id>"
```

查看：

```bash
guarded-harness --config "$REPAIR_CONFIG" inspect "$REPAIR_RUN_ID" --json
```

人工观察：

- 基线命令是任务配置声明的 pytest 命令；
- 基线有失败/错误节点；
- 报告与当前工作区绑定；
- handler 尚未修改源码。

### 4.2 审批并恢复修复

```bash
guarded-harness --config "$REPAIR_CONFIG" approvals approve "$REPAIR_APPROVAL_ID" \
  --actor manual-operator --role local_operator --json

python tests/manual_acceptance_runner.py resume-repair \
  "$WP_ACCEPT_DIR" "$REPAIR_RUN_ID"
```

预期：

- approve 不修改源码；
- resume 退出码 0；
- 最终 `ACCEPTED`；
- `src/calculator.py` 的减号被修复为加号；
- `tests/test_calculator.py` 未修改；
- inspect 中有一次基线测试和一次最终测试。

### 4.3 人工审查修复质量

```bash
git -C "$WP_EXAMPLES" diff -- workspaces/test-repair
```

逐项检查：

- 修复是否针对源码根因；
- 是否修改、删除、跳过或弱化已有测试；
- 是否新增了未声明文件；
- 最终 JUnit 是否覆盖原基线节点；
- stdout/stderr 是否只显示摘要，不泄露敏感信息；
- 模型最终回答是否没有被当作成功证据。

记录：

```text
测试修复质量审查：PASS / FAIL
是否只修源码：
既有测试是否修改：
是否发现断言弱化：
证据路径：
审查人/时间：
```

## 5. 拒绝与错误路径人工验收

### 5.1 人工 rejection

使用新的临时根，避免前一个验收留下的交付物使工作区不干净：

```bash
export REJECT_ROOT="$(mktemp -d /tmp/guarded-harness-reject.XXXXXX)"
python tests/manual_acceptance_runner.py prepare "$REJECT_ROOT"
export REJECT_CODE_CONFIG="$REJECT_ROOT/examples/config/code-understanding.yaml"
```

启动一个代码理解 Run：

```bash
python tests/manual_acceptance_runner.py start-code "$REJECT_ROOT"
```

记录新的 `run_id`、`approval_id`：

```bash
export REJECT_RUN_ID="<粘贴 run_id>"
export REJECT_APPROVAL_ID="<粘贴 approval_id>"
```

然后执行：

```bash
guarded-harness --config "$REJECT_CODE_CONFIG" approvals reject "$REJECT_APPROVAL_ID" \
  --actor manual-operator --role local_operator --json

guarded-harness --config "$REJECT_CODE_CONFIG" inspect "$REJECT_RUN_ID" --json
```

预期：

- Approval 为 `REJECTED`；
- Run 为 `STOPPED/APPROVAL_REJECTED/NOT_EVALUATED`；
- handler 未执行；
- 目标文件不存在或保持原摘要；
- CLI 不允许通过 inspect 修改状态。

### 5.2 非授权角色与自批

再次准备独立临时根：

```bash
export ROLE_ROOT="$(mktemp -d /tmp/guarded-harness-role.XXXXXX)"
python tests/manual_acceptance_runner.py prepare "$ROLE_ROOT"
export ROLE_CODE_CONFIG="$ROLE_ROOT/examples/config/code-understanding.yaml"
python tests/manual_acceptance_runner.py start-code "$ROLE_ROOT"
```

记录：

```bash
export ROLE_RUN_ID="<粘贴 run_id>"
export ROLE_APPROVAL_ID="<粘贴 approval_id>"
```

先用错误角色执行：

```bash
guarded-harness --config "$ROLE_CODE_CONFIG" approvals approve "$ROLE_APPROVAL_ID" \
  --actor manual-operator --role viewer --json
```

预期：拒绝，错误码为 `APPROVAL_APPROVER_DENIED`，审批仍为 `PENDING`。

再尝试：

```bash
guarded-harness --config "$ROLE_CODE_CONFIG" approvals approve "$ROLE_APPROVAL_ID" \
  --actor agent --role local_operator --json
```

预期：拒绝，错误码为 `APPROVAL_SELF_APPROVAL_DENIED`。

完成观察后可使用正确 role 拒绝该 Run，避免留下待审批任务。

### 5.3 未批准时 resume

再次准备独立临时根：

```bash
export RESUME_ROOT="$(mktemp -d /tmp/guarded-harness-resume.XXXXXX)"
python tests/manual_acceptance_runner.py prepare "$RESUME_ROOT"
python tests/manual_acceptance_runner.py start-code "$RESUME_ROOT"
```

记录 `RESUME_RUN_ID`，不执行 approve，直接：

```bash
export RESUME_RUN_ID="<粘贴 run_id>"
python tests/manual_acceptance_runner.py resume-code \
  "$RESUME_ROOT" "$RESUME_RUN_ID"
```

预期：

- 退出码 3；
- Run 仍为 `WAITING_APPROVAL`；
- 模型不继续决策；
- 文件不变化。

## 6. Blocked / Unknown / 审计异常复核

这些故障依赖精确事务边界，手工制造容易产生不可重复结果。建议以自动化故障测试为主要证据，
人工执行并观察：

```bash
python -m pytest \
  tests/test_fault_recovery.py::test_hard_exit_after_side_effect_recovers_unknown_without_replay \
  tests/test_fault_recovery.py::test_result_committed_before_checkpoint_resumes_without_replay \
  tests/test_fault_recovery.py::test_database_write_lock_fails_closed_without_partial_state \
  tests/test_fault_recovery.py::test_checkpoint_tampering_is_detected_after_successful_run \
  tests/test_fault_recovery.py::test_lease_conflict_fences_old_token \
  tests/test_cli.py::test_inspect_corrupt_audit_chain_returns_internal_failure \
  tests/test_cli.py::test_cli_unverifiable_result_returns_exit_8 \
  -q
```

人工观察测试断言是否证明：

- 子进程真实退出，不是普通异常；
- `UNKNOWN_RESULT` 不调用模型重放；
- 已确定结果不重复写；
- DB 锁失败关闭；
- Checkpoint 篡改被哈希/关联校验拒绝；
- 旧租约 token 被 fencing；
- 审计链损坏时 inspect 返回退出码 9；
- `UNVERIFIABLE` 返回退出码 8，不显示成功。

记录：

```text
故障恢复人工复核：PASS / FAIL
异常点：
观察结果：
证据：
```

## 7. 运维文档与 CLI 可用性检查

执行：

```bash
guarded-harness --help
python -m guarded_harness --help
guarded-harness --config "$CODE_CONFIG" approvals list --help
```

人工检查：

- console script 与 module 入口 usage 一致；
- 必填参数和错误提示清楚；
- 运行、审批、resume、inspect 的下一步明确；
- 退出码说明与 [operations.md](operations.md) 一致；
- 失败时不会暗示“强制成功”或“直接修改数据库”；
- 日志、JSON 和文档没有密钥或完整敏感参数。

## 8. 已实现能力与剩余风险核对

对照 [README.md](../README.md)、[requirements.md](requirements.md)、[design.md](design.md) 和
各工作包验收报告，人工确认：

- 没有把 WP-10/WP-11 的计划能力写成生产已验收；
- 没有把 Fake Model 写成真实模型能力；
- 没有把自然语言语义检查写成程序化证明；
- 没有声称测试断言弱化能完全自动检测；
- Windows、多仓库、分布式、Web UI 和隐藏验收仍明确为非目标；
- P1 真实模型 T2 仍为 `NOT_RUN`。

## 9. 手动验收结果记录模板

```text
验收日期：
代码基线：
操作系统 / Python / smolagents：
执行人：

自动化门禁：
- 目标测试：PASS / FAIL（数量）
- 全量测试：PASS / FAIL（数量）
- Ruff lint：PASS / FAIL
- Ruff format：PASS / FAIL
- git diff --check：PASS / FAIL

WP-08：
- 合法流程：PASS / FAIL
- 审批前无写入：PASS / FAIL
- 跨进程 resume：PASS / FAIL
- 文档语义与引用：PASS / FAIL
- 备注：

WP-09：
- 基线失败：PASS / FAIL
- 审批后修复：PASS / FAIL
- 最终测试与节点：PASS / FAIL
- 测试 diff 质量：PASS / FAIL
- 备注：

WP-10：
- Fake Model/原生 Agent：PASS / FAIL
- 预算/未知/Checkpoint：PASS / FAIL
- 并发 resume：PASS / FAIL
- 备注：

WP-11：
- CLI 入口：PASS / FAIL
- approve/reject/inspect：PASS / FAIL
- 退出码与脱敏：PASS / FAIL
- 运维说明：PASS / FAIL
- 备注：

最终结论：PASS / FAIL
阻塞问题：
剩余风险：
```

## 10. 2026-11-08 验收记录

| 工作包 | 人工观察结果 |
| --- | --- |
| WP-08 | 审批前无写入、跨进程 resume 一次、文档事实/推断/引用和源码 diff 符合预期；PASS |
| WP-09 | 失败基线、审批后源码修复、最终 JUnit、节点守恒、既有测试只读和 diff 质量符合预期；PASS |
| WP-10 | Fake Model 驱动原生 Agent、预算/未知/Checkpoint 和并发 resume 行为符合预期；PASS |
| WP-11 | CLI 入口、approve/reject/inspect、退出码、脱敏和跨进程恢复符合预期；PASS |
| WP-12 | 两类 E2E、故障矩阵、正式样例、文档和最终交付证据通过；PASS |

阻塞问题：无。

剩余风险：真实模型 P1、Windows 进程组、容器级隔离和自然语言语义全自动证明仍不属于 P0。

## 11. 清理

验收结束后删除临时目录。删除前先确认路径来自本次 `mktemp`：

```bash
printf '%s\n' \
  "$WP_ACCEPT_DIR" \
  "$REJECT_ROOT" \
  "$ROLE_ROOT" \
  "$RESUME_ROOT"
rm -r -- "$WP_ACCEPT_DIR" "$REJECT_ROOT" "$ROLE_ROOT" "$RESUME_ROOT"
```

不要删除项目仓库中的实际状态目录或用户工作区。
