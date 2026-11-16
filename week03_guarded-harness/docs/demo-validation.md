# Demo 验证操作指引

本文用于在真实模型下验证两个仓库任务：

| 场景 | 工作区 | 任务类型 | 审批模式 |
| --- | --- | --- | --- |
| `1_login_demo` | `workspace/1_login_demo` | 代码理解 | `run` 非阻塞退出，人工审批后 `resume` |
| `2_bug_fix` | `workspace/2_bug_fix/demo-app` | 测试修复 | `run --wait-for-approval 600`，另一终端人工审批 |

以下路径都相对于 `demo/`。两份配置分别是 `config/1_login_demo.yaml` 和
`config/2_bug_fix.yaml`。任务需求必须通过
`run --instruction` 传入；YAML 只声明权限、路径、预算、审批、测试命令和程序验收。
下面的指令刻意使用日常表达，不重复罗列 YAML 已经约束的路径、审批和预算；Harness 会把当前
任务类型的受信任验收格式写入 system prompt，程序仍负责权限和最终验收。

| 场景 | `max_steps` | `max_tool_calls` | `max_token_cost` |
| --- | ---: | ---: | ---: |
| Demo 1 | 32 | 80 | 4 |
| Demo 2 | 48 | 120 | 6 |

## 1. Demo 分析

### 1.1 `1_login_demo`

这是 Express + TypeScript 示例，没有测试套件。

- 当前配置允许单次模型响应最多返回 12 个工具调用，整个 Run 的总工具调用上限为 80。
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

代码理解任务的唯一允许写入是 `artifacts/login-flow.md`，源码和测试路径禁止写入。规范格式使用
`## 事实`、`## 推断`、`## 代码依据`，事实必须引用 `[E<n>]`。解析器也兼容 `## 一、事实` 这类
单个常见序号前缀，但规范标题仍由 system prompt 明确提供。system prompt 还要求通过
`write_file` 写入完整文档、不要把正文直接作为普通回答返回，并在写入成功后调用
`final_answer`。代码依据使用以下形式：

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

`deepseek-flash` 的 thinking 模式不接受 `tool_choice: required`，因此两份 demo 配置显式使用
`tool_choice: auto`。该值属于可信 YAML 和配置指纹，不能通过 instruction 或工具参数覆盖。

若模型提交的 `write_file` 参数包含会被持久化脱敏改变的 token/password 键值形式，Gate 会在
创建审批前返回 `TOOL_ARGUMENTS_NOT_RECOVERABLE` 的结构化 observation。模型应在同一 Run 内用
新的 `tool_call_id` 改写文档，例如只引用文件和行号或使用 `[REDACTED]`。不要继续 resume 一个
已经产生参数绑定冲突的旧 Run；该 Run 会转为 `BLOCKED/GATE_BLOCKED`。

先做只读配置检查：

```bash
guarded-harness --config config/1_login_demo.yaml approvals list --limit 1 --json
guarded-harness --config config/2_bug_fix.yaml approvals list --limit 1 --json
```

这两个查询不会创建模型、读取凭据或访问网络。

### 2.1 模型协议失败调试

需要检查模型原文时，可以临时开启模型协议诊断：

```bash
export GUARDED_HARNESS_DEBUG_MODEL_PROTOCOL=1
```

该开关默认关闭；开启后，仅当模型响应缺少结构化 tool calls、最终错误为
`MODEL_PROTOCOL_UNSUPPORTED` / `TOOL_CALLS_MISSING` 时，向 stderr 输出一行 JSON 调试记录。
stdout 中 `--json` 的正常结果不受影响。建议分别保存 stdout 和 stderr：

```bash
DEBUG_INSTRUCTION='帮我看下这个登录 demo 的登录流程，整理成 artifacts/login-flow.md。按 ## 事实、## 推断、## 代码依据 分段，事实要有 [E1] 这种引用，代码依据带文件行号，别改源码。'

demo1_run_exit=0
guarded-harness --config config/1_login_demo.yaml \
  run --instruction "$DEBUG_INSTRUCTION" --json \
  >/tmp/governed-demo1-run.json \
  2>/tmp/governed-demo1-model-debug.log \
  || demo1_run_exit=$?
printf 'exit=%s\n' "$demo1_run_exit"
cat /tmp/governed-demo1-run.json
cat /tmp/governed-demo1-model-debug.log
```

调试记录的 `response` 包含 `content`、`tool_calls`、`token_usage`、脱敏后的 provider `raw`
以及 `message_type` 和 `role`。输出受 32 KiB/256 行上限约束；已知模型凭据值、Bearer/JWT、
token/password 键值和常见密钥格式会在显示前脱敏。该记录不写入 State 或 Audit，也不改变
Gate、Sandbox、审批或预算行为。

调试结束后关闭开关：

```bash
unset GUARDED_HARNESS_DEBUG_MODEL_PROTOCOL
```

不要将未复核的 stderr 文件作为长期日志或验收材料保存。即使已经脱敏，模型正文仍可能包含仓库
内容；分享前应再次人工检查。

## 3. 场景一：非阻塞审批

### 3.1 启动

```bash
INSTRUCTION_1='帮我看下这个登录 demo 的登录流程，整理成 artifacts/login-flow.md。按 ## 事实、## 推断、## 代码依据 分段，事实要有 [E1] 这种引用，代码依据带文件行号，别改源码。'

demo1_run_exit=0
guarded-harness --config config/1_login_demo.yaml \
  run --instruction "$INSTRUCTION_1" --json \
  >/tmp/governed-demo1-run.json \
  || demo1_run_exit=$?
cat /tmp/governed-demo1-run.json
printf 'exit=%s\n' "$demo1_run_exit"
```

上述命令使用 `command || exit_code=$?` 捕获非零退出码，不再切换 `set -e`。即使调用者 shell
已经开启 `errexit`，`WAITING_APPROVAL` 的退出码 `3` 也不会让终端或外层脚本意外退出。需要把
`0` 以外的退出码视为允许值时，应由调用者在命令结束后显式判断。

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
demo1_resume_exit=0
guarded-harness --config config/1_login_demo.yaml \
  resume "$RUN1_ID" --json \
  >/tmp/governed-demo1-resume.json \
  || demo1_resume_exit=$?
cat /tmp/governed-demo1-resume.json
printf 'exit=%s\n' "$demo1_resume_exit"
```

每个写工具调用使用一次性审批。如果模型再次写文档，`resume` 会以退出码 `3` 返回新的
`approval_id`；重复执行 `approvals list/approve/resume`，直到 JSON 中的
`status=COMPLETED` 且 `acceptance_status=ACCEPTED`。最终成功还应表现为命令退出码 `0`。

Resume 提示会要求模型不要无依据重写已经成功的交付物；完成必要写入和检查后应调用
`final_answer` 结束 Agent 循环。该调用只结束模型执行，随后 Harness 自动进入 `VERIFYING`，
独立执行最终验收，并在 JSON 中记录最终 `status` 和 `acceptance_status`。

## 4. 场景二：阻塞等待十分钟

### 4.1 终端 A 启动

```bash
INSTRUCTION_2='这个登录会话 demo 有几个测试挂了，帮我查下原因并修好。要补测试就放 tests/generated，已有测试别动；改完跑测试确认一下。'

demo2_run_exit=0
guarded-harness --config config/2_bug_fix.yaml \
  run --instruction "$INSTRUCTION_2" \
  --wait-for-approval 600 --json \
  >/tmp/governed-demo2-run.json \
  || demo2_run_exit=$?
cat /tmp/governed-demo2-run.json
printf 'exit=%s\n' "$demo2_run_exit"
```

终端 A 会等待审批最多 600 秒。等待超时只让命令以退出码 `3` 返回，Run 仍保持
`WAITING_APPROVAL`，不会自动审批或过期。

Agent 首次调用模型前，程序已经独立执行失败基线；有界、脱敏的失败节点摘要会作为
`test_baseline_context` 注入模型上下文。模型修复后可调用受控 `run_test` 获取新的退出码和
输出，`run_test` 的 observation 会回到 ReAct 循环。模型自查结束后，程序仍会用同一可信命令
独立执行最终验收，模型调用或“测试通过”自述都不能替代该结果。

因此日常指令不需要再写“先检查基线”或指定具体缺陷文件；这些信息由程序基线和模型自己读取
源码获得。

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
demo2_resume_exit=0
guarded-harness --config config/2_bug_fix.yaml \
  resume "$RUN2_ID" --wait-for-approval 600 --json \
  >/tmp/governed-demo2-resume.json \
  || demo2_resume_exit=$?
cat /tmp/governed-demo2-resume.json
printf 'exit=%s\n' "$demo2_resume_exit"
```

预期最终退出码 `0`，`status=COMPLETED`，`acceptance_status=ACCEPTED`。程序级验收会独立复跑
Node 测试并检查 7 个基线节点、原失败修复、新增节点状态、工作区路径和 Audit 链。

## 5. Audit 与 Checkpoint 检查

以下命令对任一 Run 使用；把 `CONFIG`、`RUN_ID` 和 `STATE_DB` 换成对应场景：

```bash
CONFIG=config/1_login_demo.yaml
RUN_ID="$RUN1_ID"
STATE_DB=.guarded-harness/1_login_demo/harness.sqlite3
AUDIT_LIMIT=50
```

### 5.1 用 Turn 树检查运行事实

`inspect` 默认输出适合人工阅读的 Turn 树；`--pretty` 是同一格式的显式写法。每一轮使用一个
`Turn` 标题行，模型调用、候选工具调用、审批、Gate、工具执行、Checkpoint、预算和恢复等
子操作各占一条缩进行。无法安全关联到具体 Step 的运行级事实会放在 `Run operations`。

```bash
inspect_exit=0
guarded-harness --config "$CONFIG" \
  inspect "$RUN_ID" --audit-limit "$AUDIT_LIMIT" \
  >/tmp/governed-inspect.txt \
  || inspect_exit=$?
cat /tmp/governed-inspect.txt
printf 'exit=%s\n' "$inspect_exit"
```

需要显式声明阅读模式时，可以加上 `--pretty`：

```bash
guarded-harness --config "$CONFIG" \
  inspect "$RUN_ID" --audit-limit "$AUDIT_LIMIT" --pretty
```

`inspect` 参数口径：

| 参数 | 用途与约束 |
| --- | --- |
| 无格式参数或 `--pretty` | 输出 Turn 层级阅读视图，供人工检查；不是 JSON，不要用 `jq` 解析 |
| `--json` | 输出单个稳定 JSON envelope，供 `jq`、脚本和下面的字段断言使用 |
| `--json --pretty` | 无效组合，CLI 返回 `CLI_USAGE_ERROR`；两者必须二选一 |
| `--tool-call-id ID` | 只查询该工具调用的 Gate、审批、执行和 Evidence 关联事件 |
| `--approval-id ID` | 只查询该审批的生命周期和绑定关联事件 |
| `--model-call-id ID` | 只查询该模型 attempt 的调用、失败分类和用量关联事件 |
| `--audit-limit N` | 限制关联 Audit 事件数量，范围为 `1..1000`，默认 `100`；这是条数上限，不是输出字节上限 |

`--tool-call-id`、`--approval-id` 和 `--model-call-id` 互斥，一次只能选择一个目标。过滤只影响
Audit 关联事件；Run、Step、当前 Invocation、审批和 Completion 的 Snapshot 摘要仍可用于判读，
`data.recoveries` 则只反映当前可见的 `RECOVERY_RECORDED` 事件。

长 Run 建议从 `AUDIT_LIMIT=50` 开始。`inspect` 会把整个 Run Snapshot 和当前 Audit 窗口一起
交给 Redactor，完整投影的默认上限是 64 KiB；事件较多或单个事件摘要较大时，90、100 或 1000
条都可能超过该预算。

阅读视图应重点核对：

- 根节点显示 Run 状态、`acceptance`、`stop`、预算用量、最新 Checkpoint 和审批汇总；
- `Turn n/m [STATUS]` 应覆盖预期的 Agent 决策轮次，标题中的摘要是该轮已持久化的 Step 摘要；
- `current` 行表示 Snapshot 的当前事实，例如工具最终状态、审批有效期、模型 usage 和成本；
- `response ... TOOL_PREPARED` 表示模型提出候选工具调用，不代表 Gate 已允许或工具已执行；
- 同一 Turn 下应按稳定 sequence 看到 `gate`、`approval`、`tool`、`checkpoint`、`budget` 等子行；
- `Completion` 应展示程序化验收项；`Audit chain` 行应显示 `valid=yes`、事件总数、当前展示数和
  head sequence；
- `events` 大于 `shown` 时，Audit 历史只展示了一个窗口；应以 `current` 行判断当前状态，不能把
  不可见的历史子操作推断为未发生；
- 当前无筛选查询的 `truncated` 只表示查询额外取到了第 `N+1` 条事件，不保证能覆盖
  `shown < event_count` 的情况；长 Run 必须先按 `events/shown` 判读。

示例只保留关键行，实际 ID 和事件数量会不同：

```text
Run run_...  WAITING_APPROVAL
├─ acceptance NOT_EVALUATED  stop=-
├─ last checkpoint checkpoint_...
├─ approvals 1  PENDING:1
├─ Turns
│  └─ Turn 2/2  [COMPLETED]  Agent tool call: write_file
│     current tool write_file PREPARED call=write-1 risk=WRITE approval=approval_...
│     current approval approval_... PENDING tool=write_file call=write-1 roles=operator
│     response #20 TOOL_PREPARED PENDING actor=agent tool=write_file call=write-1
│     approval #21 APPROVAL_REQUESTED PENDING ... roles=operator ttl=86400
│     gate #23 GATE_DECISION PENDING ... rule=GATE_APPROVAL_V1 code=APPROVAL_REQUIRED
└─ Audit chain valid=yes  events=28  shown=28  truncated=no  head=#28
```

### 5.2 用 JSON 核对稳定字段

需要脚本断言或完整字段检查时使用 `--json`，不要从 Turn 树提取机器字段：

```bash
inspect_exit=0
guarded-harness --config "$CONFIG" \
  inspect "$RUN_ID" --audit-limit "$AUDIT_LIMIT" --json \
  >/tmp/governed-inspect.json \
  || inspect_exit=$?
cat /tmp/governed-inspect.json
printf 'exit=%s\n' "$inspect_exit"
```

`inspect` 先验证完整 Audit 哈希链，再返回有界投影。重点字段：

- `data.run.status`、`stop_reason`、`acceptance_status`；
- `data.run.last_checkpoint_id`；
- `data.audit.valid=true`、`head_sequence`、`truncated` 和稳定排序事件；
- 模型失败事件的 `output_summary.reason_code`、最多 16 个 `tool_names`、调用数量和允许上限；
- `data.recoveries`：只包含可见 `RECOVERY_RECORDED` 审计事实；
- `data.invocations`：工具开始/结束、审批、结果摘要和 Evidence ID；
- `data.completion`：程序化验收项和最终结论。

正常 JSON 投影应包含 `data.run`、`data.steps` 和 `data.audit`。若 `data` 只有 `value`、
`redaction_truncated` 和 `redaction_count`，说明完整投影超过 64 KiB 后被折叠为字符串预览；
该结果不是可解析的检查视图。此时不要继续用 `jq` 提取字段，应先把 `AUDIT_LIMIT` 降到 `50`，
再逐步尝试 `60`、`70`、`80`。完整历史必须使用下一节的只读 SQLite 查询，不能继续提高
`--audit-limit` 来绕过字节预算。

JSON 和 Turn 树来自同一只读 inspection projection。若两者状态不一致，应停止人工验收并检查
查询版本、Audit 链和状态库，不要手工修改 SQLite 或只保留其中一个视图。

### 5.3 读取 Checkpoint、Recovery 和完整 Audit

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
               tool_name, tool_call_id, approval_id, model_call_id,
               input_summary_json, output_summary_json
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

### 5.4 过滤单个审批、工具或模型调用

```bash
APPROVAL_ID='<从 inspect JSON data.approvals 或 approvals list 复制>'
guarded-harness --config "$CONFIG" inspect "$RUN_ID" \
  --approval-id "$APPROVAL_ID" --audit-limit "$AUDIT_LIMIT" --pretty
```

先从上一步的 `data.invocations` 选择一个 `tool_call_id`，再检查工具关联事实：

```bash
TOOL_CALL_ID='<从 inspect JSON data.invocations 复制>'
guarded-harness --config "$CONFIG" inspect "$RUN_ID" \
  --tool-call-id "$TOOL_CALL_ID" --audit-limit "$AUDIT_LIMIT" --pretty
```

需要检查真实模型 attempt、失败分类或 usage 时，从 `data.model_calls` 选择
`model_call_id`：

```bash
MODEL_CALL_ID='<从 inspect JSON data.model_calls 复制>'
guarded-harness --config "$CONFIG" inspect "$RUN_ID" \
  --model-call-id "$MODEL_CALL_ID" --audit-limit "$AUDIT_LIMIT" --pretty
```

三个过滤参数不能组合使用。默认输出适合依次查看审批生命周期、Gate 决定、工具执行、错误码和
Evidence；需要落盘或脚本断言时，将对应命令中的 `--pretty` 换成 `--json`，不要同时传入两者。

## 6. 已确认运行口径

1. 真实模型继续使用 `OPENAI_API_KEY` 环境变量；两份配置使用 `deepseek-flash` 和
   `https://api.deepseek.com`。密钥只由受控运行时读取，不写入配置、命令行参数、State 或 Audit。
2. 场景二保留 `run_test` 人工必审。模型每次执行受控测试反馈都需要一次性审批，因此一次 Run
   可能产生多次阻塞审批；程序自己的基线和最终验收仍由 Completion 内部控制面执行，不要求模型
   审批。
