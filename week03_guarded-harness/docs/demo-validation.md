# Demo 验证操作指引

本文用于在真实模型下验证两个仓库任务：

| 场景 | 工作区 | 任务类型 | 审批模式 |
| --- | --- | --- | --- |
| `1_login_demo` | `workspace/1_login_demo` | 代码理解 | `run` 非阻塞退出，人工审批后 `resume` |
| `2_bug_fix` | `workspace/2_bug_fix/demo-app` | 测试修复 | `run --wait-for-approval 600`，另一终端人工审批 |

以下路径都相对于 `demo/`。两份配置分别是 `config/1_login_demo.yaml` 和
`config/2_bug_fix.yaml`。任务需求必须通过
`run --instruction` 传入；YAML 只声明权限、路径、预算、审批、测试命令和程序验收。

## 1. Demo 分析

### 1.1 `1_login_demo`

这是 Express + TypeScript 示例，没有测试套件。

- `src/server.ts` 监听 3001，`src/app.ts` 将登录和 Profile 路由挂载到 `/api`。
- `POST /api/login` 在 `src/routes/login.ts` 校验 email/password 后调用
  `AuthService.login`。
- `src/services/auth-service.ts` 通过 `UserRepository.findByEmail` 查用户，调用
  `verifyPassword` 校验密码，再由 `issueAccessToken` 生成 token。
- `src/repositories/user-repository.ts` 查询 `users` 表；
  `src/db/database.ts` 是内存假表，预置 member 和 admin 两个用户。
- `src/middleware/auth-middleware.ts` 要求 `Authorization: Bearer <token>`，并调用
  `parseAccessToken` 保护 Profile 路由。
- 两个不能用于生产的事实：`password.ts` 直接比较 `"hash:" + 明文`；`token.ts` 只做
  base64url 编码和前缀检查，没有签名防伪。

代码理解任务的唯一允许写入是 `artifacts/login-flow.md`，源码和测试路径禁止写入。验收要求文档
严格包含 `## 事实`、`## 推断`、`## 代码依据`，事实必须引用 `[E<n>]`。代码依据使用以下形式：

```text
- E1: `src/routes/login.ts:7-25`
```

### 1.2 `2_bug_fix`

这是 Node + TypeScript 示例。已确认当前基线为：

```text
tests 7 / pass 4 / fail 3
```

三个失败用例都要求按秒级时刻判断失效；`src/auth/session-policy.ts` 当前只截取
`YYYY-MM-DD` 比较日期，因此同一到期日内的过期状态判断错误。

测试修复配置允许修改 `src/**`，以及新增 `tests/generated/**`；既有 `tests/**` 保持只读。
验收使用未经模型控制的基线/最终 `node --test`，要求：

- 基线至少有一个失败节点；
- 基线节点在最终报告中全部保留；
- 原失败节点修复，新增节点也全部通过；
- 报告、工作区变化和 Audit 链全部有效。

原始 `package.json` 没有锁文件，验收前需要在 demo 工作区安装本地 `tsx`：

```bash
cd workspace/2_bug_fix/demo-app
npm install --no-package-lock --ignore-scripts
npm test
cd -
```

`npm test` 当前应以退出码 1 结束，并显示 4 pass / 3 fail。`node_modules/` 已被 Git 忽略。

## 2. 环境准备

在 `demo/` 目录执行：

```bash
cd /Users/app_dev/code/ai_agent/ai_agent_training/my-ai-agent-fullstack-training/week03_guarded-harness/demo
source ../.venv/bin/activate
(cd .. && python -m pip install -e '.[dev]')
```

两份配置沿用现有真实模型 profile：OpenAI Chat Completions 兼容 Provider、模型
`deepseek-flash`、端点 `https://api.deepseek.com`。只在当前 shell 中提供凭据，不要把值写入
配置文件、命令行参数或文档：

```bash
export OPENAI_API_KEY='<your-key>'
```

先做只读配置检查：

```bash
guarded-harness --config config/1_login_demo.yaml approvals list --limit 1 --json
guarded-harness --config config/2_bug_fix.yaml approvals list --limit 1 --json
```

这两个查询不会创建模型、读取凭据或访问网络。

## 3. 场景一：非阻塞审批

### 3.1 启动

```bash
INSTRUCTION_1='分析 1_login_demo 的登录调用链，说明 HTTP 入口、路由挂载、认证服务、用户仓储、数据来源、密码校验、token 签发与鉴权中间件；生成 artifacts/login-flow.md。文档必须严格按“## 事实、## 推断、## 代码依据”的顺序组织。事实必须使用带 [E1] 引用的 Markdown 列表；代码依据必须使用形如 “- E1: `src/routes/login.ts:7-25`” 的列表，并确保路径和行号真实可读。只读取实际源码，不修改源码。'

set +e
guarded-harness --config config/1_login_demo.yaml \
  run --instruction "$INSTRUCTION_1" --json | tee /tmp/governed-demo1-run.json
demo1_run_exit=${PIPESTATUS[0]}
set -e
printf 'exit=%s\n' "$demo1_run_exit"
```

预期退出码是 `3`，状态为 `WAITING_APPROVAL`；此时 `artifacts/login-flow.md` 尚未创建或修改。
退出码 `0` 表示本次直接通过程序验收；其他退出码应结合 JSON 错误码和 `inspect` 判定。

提取 ID：

```bash
json_file_get() {
  python - "$1" "$2" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
for part in sys.argv[2].split("."):
    value = value[int(part)] if isinstance(value, list) else value[part]
print(value)
PY
}

RUN1_ID="$(json_file_get /tmp/governed-demo1-run.json data.run_id)"
APPROVAL1_ID="$(json_file_get /tmp/governed-demo1-run.json data.approval_id)"
printf 'run_id=%s\napproval_id=%s\n' "$RUN1_ID" "$APPROVAL1_ID"
```

### 3.2 查看、审批和恢复

```bash
guarded-harness --config config/1_login_demo.yaml \
  approvals list --run-id "$RUN1_ID" --status pending --json
```

确认工具名、参数摘要、角色和工作区后，由另一个操作者批准：

```bash
guarded-harness --config config/1_login_demo.yaml \
  approvals approve "$APPROVAL1_ID" \
  --actor demo-reviewer --role local_operator --json
```

然后恢复 Run：

```bash
guarded-harness --config config/1_login_demo.yaml \
  resume "$RUN1_ID" --json | tee /tmp/governed-demo1-resume.json
```

每个写工具调用使用一次性审批。如果模型再次写文档，`resume` 会以退出码 `3` 返回新的
`approval_id`；重复执行 `approvals list/approve/resume`，直到 JSON 中的
`status=COMPLETED` 且 `acceptance_status=ACCEPTED`。最终成功还应表现为命令退出码 `0`。

## 4. 场景二：阻塞等待十分钟

### 4.1 终端 A 启动

```bash
INSTRUCTION_2='先使用程序提供的失败基线定位问题，再修复 src/auth/session-policy.ts 的会话过期判断，使到期时刻及之后立即失效、到期前仍然有效。如需补充测试，只能新增到 tests/generated/ 下；不得修改、删除、跳过或弱化 tests/ 下已有测试。每次修复后调用 run_test 获取反馈，完成后让全部 Node 测试通过，并说明源码改动。'

set +e
guarded-harness --config config/2_bug_fix.yaml \
  run --instruction "$INSTRUCTION_2" \
  --wait-for-approval 600 --json | tee /tmp/governed-demo2-run.json
demo2_run_exit=${PIPESTATUS[0]}
set -e
printf 'exit=%s\n' "$demo2_run_exit"
```

终端 A 会等待审批最多 600 秒。等待超时只让命令以退出码 `3` 返回，Run 仍保持
`WAITING_APPROVAL`，不会自动审批或过期。

Agent 首次调用模型前，程序已经独立执行失败基线；有界、脱敏的失败节点摘要会作为
`test_baseline_context` 注入模型上下文。模型修复后可调用受控 `run_test` 获取新的退出码和
输出，`run_test` 的 observation 会回到 ReAct 循环。模型自查结束后，程序仍会用同一可信命令
独立执行最终验收，模型调用或“测试通过”自述都不能替代该结果。

### 4.2 终端 B 审批

```bash
guarded-harness --config config/2_bug_fix.yaml \
  approvals list --status pending --json
```

从输出中确认 demo 2 的 Run 和审批绑定，然后批准：

```bash
APPROVAL2_ID='<从 approvals list 输出复制>'
guarded-harness --config config/2_bug_fix.yaml \
  approvals approve "$APPROVAL2_ID" \
  --actor demo-reviewer --role local_operator --json
```

终端 A 检测到批准后会原地恢复执行。配置把 `write_file`、`apply_patch` 和 `run_test` 都设为
必审；若出现下一次 `PENDING` 审批，继续在终端 B 的剩余等待窗口内执行 `list/approve`。
若终端 A 已因 600 秒超时退出，则审批后显式恢复：

```bash
RUN2_ID='<从 /tmp/governed-demo2-run.json 提取 data.run_id>'
guarded-harness --config config/2_bug_fix.yaml \
  resume "$RUN2_ID" --wait-for-approval 600 --json
```

预期最终退出码 `0`，`status=COMPLETED`，`acceptance_status=ACCEPTED`。程序级验收会独立复跑
Node 测试并检查 7 个基线节点、原失败修复、新增节点状态、工作区路径和 Audit 链。

## 5. Audit 与 Checkpoint 检查

以下命令对任一 Run 使用；把 `CONFIG`、`RUN_ID` 和 `STATE_DB` 换成对应场景：

```bash
CONFIG=config/1_login_demo.yaml
RUN_ID="$RUN1_ID"
STATE_DB=.guarded-harness/1_login_demo/harness.sqlite3

guarded-harness --config "$CONFIG" \
  inspect "$RUN_ID" --audit-limit 1000 --json | tee /tmp/governed-inspect.json
```

`inspect` 先验证完整 Audit 哈希链，再返回有界投影。重点字段：

- `data.run.status`、`stop_reason`、`acceptance_status`；
- `data.run.last_checkpoint_id`；
- `data.audit.valid=true`、`head_sequence`、`truncated` 和稳定排序事件；
- `data.recoveries`：只包含可见 `RECOVERY_RECORDED` 审计事实；
- `data.invocations`：工具开始/结束、审批、结果摘要和 Evidence ID；
- `data.completion`：程序化验收项和最终结论。

读取整个 Run 的 Audit、Checkpoint 和恢复记录：

```bash
python - "$STATE_DB" "$RUN_ID" <<'PY'
import json
import sqlite3
import sys
from pathlib import Path

database = Path(sys.argv[1]).resolve()
run_id = sys.argv[2]
connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
connection.row_factory = sqlite3.Row
connection.execute("PRAGMA query_only = ON")

queries = {
    "checkpoints": """
        SELECT checkpoint_id, sequence, boundary_kind, boundary_ref,
               run_revision, last_audit_sequence, created_at
        FROM checkpoints WHERE run_id = ? ORDER BY sequence
    """,
    "recoveries": """
        SELECT recovery_id, checkpoint_id, action, status, reason_code,
               invocation_id, approval_id, created_at
        FROM recovery_records WHERE run_id = ? ORDER BY created_at, recovery_id
    """,
    "audit": """
        SELECT sequence, event_type, status, decision, code, rule_id,
               tool_name, tool_call_id, approval_id, model_call_id
        FROM audit_events WHERE run_id = ? ORDER BY sequence
    """,
}

for name, sql in queries.items():
    rows = [dict(row) for row in connection.execute(sql, (run_id,))]
    print(json.dumps({"name": name, "count": len(rows), "rows": rows}, ensure_ascii=False))
PY
```

判读规则：

- 已开始运行的 Run 至少应有一个 Checkpoint；`last_checkpoint_id` 应指向当前最后安全点；
- 审批后正常 `resume` 会继续产生安全点，但不应伪造 recovery record；
- `recovery_records=[]` 在无崩溃、无 W6 补建、无恢复计划时是正常结果；
- 只有实际执行恢复计划时才应出现 recovery record，并应关联原 Checkpoint、动作、状态和原因码；
- Audit 链无效、Checkpoint 哈希/关联损坏或恢复记录与实际 App/Invocation 不一致时，保持阻塞，
  不手工改库，也不把部分产物判为成功。

查看单个审批或工具调用的关联审计：

```bash
guarded-harness --config "$CONFIG" inspect "$RUN_ID" \
  --approval-id "$APPROVAL1_ID" --audit-limit 1000 --json
```

先从上一步的 `invocations` 选择一个 `tool_call_id`，再执行同样的 `inspect --tool-call-id ...`。
这样可以确认门禁决定、审批生命周期、工具执行、错误码和 Evidence 的稳定顺序。

## 6. 已确认运行口径

1. 真实模型继续使用 `OPENAI_API_KEY` 环境变量；两份配置使用 `deepseek-flash` 和
   `https://api.deepseek.com`。密钥只由受控运行时读取，不写入配置、命令行参数、State 或 Audit。
2. 场景二保留 `run_test` 人工必审。模型每次执行受控测试反馈都需要一次性审批，因此一次 Run
   可能产生多次阻塞审批；程序自己的基线和最终验收仍由 Completion 内部控制面执行，不要求模型
   审批。
