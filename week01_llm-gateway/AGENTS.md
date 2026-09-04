# AGENTS.md

本文件用于指导后续 AI 编码代理在本仓库中实现和维护 **LLM 统一模型调用服务**。

## 项目目标

打造一个基于 Python 的统一 LLM 网关服务：

- 对上层提供 OpenAI Compatible API。
- 对下层接入多个 OpenAI-compatible 模型供应商。
- 在网关层统一处理模型路由、重试、故障转移、流式转发、结构化输出、Prompt 模板、用量与成本记录、鉴权和限流。

## 参考项目

实现时优先参考以下路径中的现有服务，但不要盲目整目录复制；应保留其核心架构和交互语义，并按当前仓库目标逐步重建：

```text
/Users/app_dev/code/ai_agent/ai_agent_training/ai-agent-fullstack-training/course_code/week01/1-7/llm-gateway
```

## 技术栈

- Python 3.10+；推荐本地/CI 使用 3.12。当前开发 `.venv` 为 Python 3.14，运行测试时会看到 `pytest-asyncio` 的 deprecation warning，不影响功能。
- FastAPI + Uvicorn
- Pydantic v2
- httpx
- PyYAML
- Jinja2 Sandbox
- jsonschema
- aiosqlite
- pytest、pytest-asyncio、ruff
- pip + `requirements.txt` 管理依赖

## 核心能力

必须提供以下接口与能力：

- `POST /v1/chat/completions`：Chat Completions 兼容入口。
- `POST /v1/responses`：Responses API 兼容入口。
- `POST /v1/messages`：Anthropic Messages API 兼容入口。
- `GET /v1/models`：返回网关公开的模型别名。
- `POST /v1/prompts`、`GET /v1/prompts`、`GET /v1/prompts/{id}`、`POST /v1/prompts/{id}/render`：Prompt 模板版本管理。
- `GET /admin/usage`：查询调用用量。
- `GET /admin/routes`：查看模型路由与熔断器状态。
- `GET /healthz`、`GET /readyz`：健康检查与就绪检查。

网关层需实现：

- 多供应商、模型别名、优先级路由与加权轮询。
- 通过适配器统一封装 OpenAI Chat Completions、OpenAI Responses 和 Anthropic Messages。
- 首选供应商失败后的自动重试与 fallback。
- 轻量级进程内熔断器。
- SSE 流式响应透明转发；客户端断开时取消上游请求。
- 非流式结构化输出校验失败后的本地修复重试。
- Prompt 模板版本管理与 Jinja2 Sandbox 渲染。
- Token、成本、延迟、TTFT、重试与 fallback 用量记录。
- OpenAI 风格错误对象、Bearer API Key 鉴权和进程内令牌桶限流。

## 项目结构

建议按以下结构组织代码：

```text
app/
├── api/routes.py          # 兼容 API、Prompt 与管理接口
├── core/                  # 错误、鉴权、限流
│   ├── errors.py
│   ├── security.py        # 已实现：Bearer Key 鉴权与密钥指纹
│   └── rate_limit.py      # 已实现：进程内令牌桶限流
├── services/
│   ├── adapters/
│   │   ├── base.py
│   │   ├── openai_chat.py
│   │   ├── openai_responses.py
│   │   └── anthropic_messages.py
│   ├── gateway.py         # 调用编排、流式、重试、结构化纠错
│   ├── upstream.py        # OpenAI-compatible 上游 HTTP 客户端
│   ├── router.py          # 路由、加权、fallback、熔断
│   ├── prompts.py         # 已实现：Prompt 版本和安全渲染
│   ├── structured.py      # 已实现：JSON 提取与 Schema 校验
│   └── usage.py           # 已实现：SQLite 用量与成本账本
├── config.py              # YAML + 环境变量配置
├── schemas.py             # Pydantic 入参/出参模型
└── main.py                # FastAPI 生命周期与依赖组装

docs/                      # 需求、设计、决策、拆解与验收文档；定位和使用规范见下方专节

tests/
├── test_adapters.py
├── test_admin_routes.py
├── test_config.py
├── test_gateway.py
├── test_prompts.py
├── test_router_upstream.py
├── test_security_rate_limit.py
├── test_structured.py
└── test_usage.py

gateway.example.yaml
requirements.txt
Dockerfile
docker-compose.yml
Makefile
README.md
```

## docs/ 目录定位与使用规范

`docs/` 是本仓库的需求、设计、决策、任务拆解和验收文档沉淀目录，是后续编码代理理解“要做什么、为什么这样做、按什么顺序做、如何验收”的第一入口。它不承载运行时代码，也不复述源码细节；当前阶段保持轻量，等接口和实现稳定后，再按实际需要补充架构、API、配置、部署和开发文档。

### 定位

- **设计基线**：`requirements.md`、`design.md`、`decisions.md` 共同锁定网关的对外协议面、适配器边界、路由/流式/结构化/用量等核心契约。
- **实施依据**：`task-breakdown.md` 将需求拆为可验证阶段，并说明参考项目模块的复用或调整方式。
- **验收依据**：`test-plan.md` 定义 mock 上游方案、测试分层和功能场景，是阶段完成与整模块验收的判据。
- **交接入口**：`docs/README.md` 维护文档索引和推荐阅读顺序，新成员或新代理应先从这里进入。

### 使用场景

- **开工前**：按“需求 → 设计 → 决策 → 任务拆解 → 验收测试”顺序阅读，先理解范围和契约，再动代码。
- **实现中**：遇到协议、路由、重试、结构化输出、限流、鉴权等取舍时，优先查 `decisions.md` 的确认结论；阶段产出和验收标准以 `task-breakdown.md` 为准。
- **验收或复盘时**：以 `test-plan.md` 的验收目标、范围和场景为准；如果实现与设计出现偏差，应回到 `design.md` 或 `decisions.md` 更新基线，而不是只在代码里“另起炉灶”。
- **交接或扩展时**：从 `docs/README.md` 快速定位需要阅读和更新的文档，避免设计与实现长期脱节。

### 目录结构

```text
docs/
├── README.md          # 目录索引：文档清单、定位与推荐阅读顺序
├── requirements.md    # 整体需求与验收标准
├── design.md          # 阶段 0 设计基线：API 面、协议适配器、统一数据模型与配置模型
├── decisions.md       # 设计决策记录：已确认决策、理由、影响和细化结论
├── task-breakdown.md  # 需求拆解、参考项目复用判断与阶段实施计划
└── test-plan.md       # 验收测试计划：mock 上游方案、测试分层、功能场景
```

### 维护要求

- `docs/README.md` 的文档清单必须与目录实际文件保持一致；新增文档时先更新索引。
- `design.md` 和 `decisions.md` 属于“已确认基线”。若实现或后续决策发生变更，应更新对应文档并保留变更背景，不要静默覆盖历史结论。
- 每个阶段完成后，及时把实现状态回写到 `task-breakdown.md`、`design.md` 或 `test-plan.md` 的相关小节。
- 保持轻量，避免把 `docs/` 变成源码镜像、配置副本或秘密信息存放地；新文档只在接口或实现稳定且确有复用价值时增加。

当前阶段 1-8 的主要代码路径均已落地：模型路由、三种协议适配器、流式与结构化输出、Prompt 版本管理、SQLite 用量账本、鉴权、限流以及管理接口已经实现。

## 配置约定

- 使用 `gateway.yaml` 配置供应商、模型路由、重试、熔断、限流和定价。
- 配置文件中支持 `${ENV_VAR}` 和 `${ENV_VAR:-default}` 环境变量展开。
- 仓库只提交 `gateway.example.yaml`，不提交真实 `gateway.yaml`、`.env` 或任何真实密钥。
- 模型路由引用的 `provider` 必须已经在 `providers` 中声明，启动或加载配置时应校验。
- 每个路由需声明协议能力：`chat`、`responses`、`messages`；`all` 是保留字，只能单独出现，表示三种协议都支持，不再使用 `both`。

配置示例：

```yaml
providers:
  openai:
    base_url: https://api.openai.com
    api_key: ${OPENAI_API_KEY}
  anthropic:
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}

models:
  smart:
    strategy: priority
    routes:
      - provider: openai
        model: gpt-5.2
        api: "chat,responses"
  claude-fast:
    strategy: priority
    routes:
      - provider: anthropic
        model: claude-sonnet-4-5
        api: "messages"
```

## 编码与设计约定

- 对外错误统一为 OpenAI 风格 JSON：

```json
{
  "error": {
    "message": "Unknown model alias: demo",
    "type": "invalid_request_error",
    "param": "model",
    "code": "model_not_found"
  }
}
```

- I/O 操作使用 `async`/`await`，不阻塞事件循环。
- Pydantic 模型用于请求、响应和配置校验。
- 上游 HTTP 调用统一收口在 `UpstreamClient`，不要在路由函数中直接创建 httpx 请求。
- 路由、重试、fallback 和熔断逻辑放在 `ModelRouter` 与 `GatewayService`，保持接口边界清晰。
- 重试只覆盖网络错误、超时和配置中的可重试状态码；普通 4xx 不重试。
- 流式响应一旦已向下游发送内容，不切换到另一模型续写；失败时发送 SSE 错误事件并结束。
- 流式输出无法在已经发送内容后进行无损结构化纠错；严格业务协议建议使用非流式接口。
- 用量记录只保存 API Key 的不可逆短指纹，不保存原密钥、Prompt 正文或用户消息正文。
- 进程内熔断器和限流器需保持独立接口；多副本部署时应替换为 Redis 等共享存储。
- SQLite 路径由配置决定，启动时自动创建父目录和表结构。`PromptRepository.initialize()` 创建 `prompts` 表；`UsageRepository.initialize()` 创建 `usage_events` 表。

## 安全要求

- API Key 使用 `SecretStr` 或等效方式处理，不打印到日志。
- 使用 `hmac.compare_digest` 比较 API Key。
- Prompt 渲染必须使用 Jinja2 `SandboxedEnvironment` 与 `StrictUndefined`。
- 不要信任模型输出；结构化输出仍需本地 `jsonschema` 二次校验。
- 不要把 `.env`、`gateway.yaml`、`data/` 中的敏感数据提交到仓库。

## 验证与开发命令

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp gateway.example.yaml gateway.yaml
set -a; source .env; set +a
GATEWAY_CONFIG=gateway.yaml uvicorn app.main:app --reload --port 8000
python -m pytest -q
ruff check .
docker compose up --build
```

测试应使用 httpx 的 `MockTransport` 模拟上游，不消耗真实模型额度，并覆盖以下场景：

- 模型别名路由、重试和 fallback。
- 用量记录中的 Token、成本、重试次数和 fallback 次数。
- 结构化输出本地校验与自动修复。
- Prompt 版本创建、激活版本、变量渲染和渲染错误。
- SSE 流式转发、TTFT、断开取消。
- Chat Completions、Responses 和 Messages 三种协议路由。
- API Key 鉴权与限流错误语义。

## 建议实现顺序

1. 初始化 Python 项目脚手架、`requirements.txt`、配置加载和 FastAPI 生命周期。
2. 实现健康检查、鉴权、OpenAI 风格错误和基础路由。
3. 实现上游 HTTP 客户端、模型路由、重试、fallback 和熔断。
4. 打通非流式 Chat Completions、Responses 与 Messages API。
5. 实现三种协议的 SSE 流式转发与客户端断开处理。
6. 实现结构化输出校验与修复重试。
7. 实现 Prompt 模板版本管理与安全渲染。
8. 实现 SQLite 用量与成本账本（已完成）。
9. 补齐限流、管理接口、Docker 和完整测试（限流与管理接口已完成，Docker 和部署验证按需继续完善）。
