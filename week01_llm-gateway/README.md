# LLM Gateway

一个基于 Python / FastAPI 的统一 LLM 网关服务：对上提供 OpenAI Compatible API 与 Anthropic Messages API 兼容入口，对下统一接入多个 OpenAI-compatible 模型供应商，并在网关层处理模型路由、重试、故障转移、流式转发、结构化输出、Prompt 模板、用量与成本记录、鉴权和限流。

## 功能概览

- **多协议对外接口**
  - `POST /v1/chat/completions`：OpenAI Chat Completions 兼容入口
  - `POST /v1/responses`：OpenAI Responses 兼容入口
  - `POST /v1/messages`：Anthropic Messages 兼容入口
  - `GET /v1/models`：查看网关公开的模型别名
- **模型路由与韧性**
  - 多供应商、模型别名
  - `priority` 优先级路由
  - `weighted_round_robin` 加权轮询路由
  - 首选路由失败后的自动重试与 fallback
  - 轻量级进程内熔断器
- **协议适配器**
  - 统一封装 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages 三种协议
  - 将不同协议的请求统一为内部 `UnifiedRequest`，并把上游响应统一为内部数据模型
- **流式输出**
  - SSE 流式响应透明转发
  - 记录 TTFT
  - 客户端断开时取消上游请求
- **结构化输出**
  - 本地 JSON 提取与 JSON Schema 校验
  - 校验失败后自动携带修复提示重试
- **Prompt 模板管理**
  - `POST /v1/prompts`
  - `GET /v1/prompts`
  - `GET /v1/prompts/{id}`
  - `POST /v1/prompts/{id}/render`
  - 使用 Jinja2 Sandbox + `StrictUndefined` 渲染
- **可观测与管理**
  - `GET /admin/usage`：查询 Token、成本、延迟、TTFT、重试次数、fallback 次数
  - `GET /admin/routes`：查看模型路由与熔断器状态
  - `GET /healthz`、`GET /readyz`：健康检查与就绪检查
- **安全与限制**
  - Bearer API Key 或 `x-api-key` 鉴权
  - 使用不可逆短指纹记录调用方，不保存原始密钥
  - 进程内令牌桶限流
  - 不保存 Prompt 正文或用户消息正文

## 环境准备

推荐使用 Python 3.12。项目要求 Python 3.10+；当前本地 `.venv` 若为 Python 3.14，运行测试时可能出现 `pytest-asyncio` 的 deprecation warning，不影响功能。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

准备配置文件和上游密钥：

```bash
cp gateway.example.yaml gateway.yaml

# 按需配置真实环境变量；占位值仅用于示例
export GATEWAY_API_KEY="local-dev-key"
export OPENAI_API_KEY="sk-your-openai-key"
export ANTHROPIC_API_KEY="sk-ant-your-anthropic-key"
```

说明：

- `gateway.example.yaml` 支持 `${ENV_VAR}` 和 `${ENV_VAR:-default}` 环境变量展开。
- 仓库只应提交 `gateway.example.yaml`，不要提交真实的 `gateway.yaml`、`.env` 或任何真实密钥。
- 如果 `GATEWAY_API_KEY` 为空，则网关以开发模式运行，不校验调用方；生产环境应显式配置。

## 启动

在仓库根目录执行：

```bash
source .venv/bin/activate
GATEWAY_CONFIG=gateway.yaml python -m uvicorn app.main:app --reload --port 8000
```

启动后可访问：

- 健康检查：`GET /healthz`
- 就绪检查：`GET /readyz`
- 自动生成的 API 文档：`http://127.0.0.1:8000/docs`

## 测试与代码检查

```bash
python -m pytest -q
ruff check .
```

测试使用 `httpx.MockTransport` 模拟上游，不会消耗真实模型额度。

## 访问命令示例

以下示例默认网关运行在 `http://127.0.0.1:8000`，且已设置 `GATEWAY_API_KEY=local-dev-key`。

### 健康检查

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

### 查询模型别名

```bash
curl http://127.0.0.1:8000/v1/models \
  -H "Authorization: Bearer local-dev-key"
```

### Chat Completions

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "smart",
    "messages": [
      {"role": "user", "content": "用一句话解释什么是 LLM Gateway"}
    ],
    "stream": false
  }'
```

### Responses

```bash
curl http://127.0.0.1:8000/v1/responses \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "smart",
    "input": "用一句话介绍 FastAPI"
  }'
```

### Anthropic Messages

```bash
curl http://127.0.0.1:8000/v1/messages \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-fast",
    "max_tokens": 256,
    "messages": [
      {"role": "user", "content": "给我一个 Python 异步函数示例"}
    ]
  }'
```

### 流式输出

```bash
curl -N http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "smart",
    "messages": [
      {"role": "user", "content": "写一首关于网关的小诗"}
    ],
    "stream": true
  }'
```

### 结构化输出

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "smart",
    "messages": [
      {"role": "user", "content": "输出一份简单用户信息"}
    ],
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "user_info",
        "schema": {
          "type": "object",
          "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"}
          },
          "required": ["name", "age"]
        }
      }
    }
  }'
```

### Prompt 版本管理

创建 Prompt 并激活：

```bash
curl -X POST http://127.0.0.1:8000/v1/prompts \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "greeting",
    "name": "Greeting Prompt",
    "role": "user",
    "content": "你好，{{ name }}，请简要介绍 {{ topic }}。",
    "activate": true
  }'
```

渲染 Prompt：

```bash
curl -X POST http://127.0.0.1:8000/v1/prompts/greeting/render \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "variables": {
      "name": "小明",
      "topic": "LLM Gateway"
    }
  }'
```

查询 Prompt：

```bash
curl http://127.0.0.1:8000/v1/prompts \
  -H "Authorization: Bearer local-dev-key"

curl http://127.0.0.1:8000/v1/prompts/greeting \
  -H "Authorization: Bearer local-dev-key"
```

### 管理接口

```bash
curl "http://127.0.0.1:8000/admin/usage?limit=20" \
  -H "Authorization: Bearer local-dev-key"

curl http://127.0.0.1:8000/admin/routes \
  -H "Authorization: Bearer local-dev-key"
```

## 核心模块处理链路

### 非流式调用

```text
Client
  |
  v
FastAPI routes
  |-- 1. 鉴权：Bearer Token / x-api-key
  |-- 2. 限流：进程内令牌桶
  |-- 3. 解析并归一化为 UnifiedRequest
  |-- 4. 若带 prompt_ref，则 PromptRepository.render 渲染模板
  v
GatewayService.complete
  |-- 选择协议适配器
  |-- ModelRouter.candidates 计算可用路由
  |     |-- priority / weighted_round_robin
  |     +-- 熔断器过滤不健康 provider
  v
ProtocolAdapter.build_request
  |-- OpenAI Chat Completions
  |-- OpenAI Responses
  |-- Anthropic Messages
  v
UpstreamClient.request_json
  |-- httpx.AsyncClient 发起上游 POST
  |-- 网络错误/超时/可重试状态码 -> 退避重试
  |-- 当前路由失败 -> 切换下一 fallback 路由
  v
Upstream Provider
  |
  v
ProtocolAdapter.parse_response
  |
  +--> 无结构化输出：直接返回 raw 响应
  |
  +--> 有结构化输出：
         validate_structured_content 本地校验
         |-- 校验通过 -> 返回
         +-- 校验失败 -> 携带修复提示重试
  |
  v
UsageRepository.record
  |-- Token、成本、延迟、TTFT、重试次数、fallback 次数
  +-- 只保存 API Key 指纹，不保存原密钥和正文
```

### 流式调用

```text
Client
  |
  v
FastAPI routes
  |-- 鉴权、限流、UnifiedRequest 归一化
  v
GatewayService.stream
  |-- ModelRouter 选择路由
  |-- ProtocolAdapter.build_request(stream=true)
  v
UpstreamClient.open_stream
  |-- httpx 流式打开上游 SSE
  |-- 在首字节发出前完成重试/fallback
  v
ProtocolAdapter.parse_stream_event
  |-- 归一化 text_delta / usage / done 事件
  v
StreamingResponse
  |-- 以 text/event-stream 向客户端透传
  |-- 记录 TTFT
  |-- request.is_disconnected -> 取消上游请求并结束
  v
UsageRepository.record
  +-- 记录流式调用用量、延迟与断开状态
```

## 项目结构

```text
app/
├── api/routes.py              # 兼容 API、Prompt 与管理接口
├── core/
│   ├── errors.py              # OpenAI 风格错误对象
│   ├── security.py            # Bearer Key 鉴权与密钥指纹
│   └── rate_limit.py          # 进程内令牌桶限流
├── services/
│   ├── adapters/              # OpenAI Chat / Responses / Anthropic Messages 适配器
│   ├── gateway.py             # 调用编排、流式、重试、结构化纠错
│   ├── upstream.py            # OpenAI-compatible 上游 HTTP 客户端
│   ├── router.py              # 路由、加权、fallback、熔断
│   ├── prompts.py             # Prompt 版本和安全渲染
│   ├── structured.py          # JSON 提取与 Schema 校验
│   └── usage.py               # SQLite 用量与成本账本
├── config.py                  # YAML + 环境变量配置
├── schemas.py                 # Pydantic 入参/出参模型
└── main.py                    # FastAPI 生命周期与依赖组装

docs/                          # 需求、设计、决策、拆解与验收文档
tests/                         # 使用 MockTransport 的单元/集成测试
gateway.example.yaml           # 配置示例，不包含真实密钥
requirements.txt
pytest.ini
```

## 配置说明

核心配置位于 `gateway.yaml`，主要包含：

- `providers`：上游供应商的 `base_url`、`api_key`、超时时间
- `models`：对外模型别名、路由策略、协议能力
- `retry`：每个路由的最大重试次数、退避时间、可重试状态码
- `circuit_breaker`：失败阈值与冷却时间
- `rate_limit`：是否启用、每分钟请求数与突发容量
- `structured_output_retries`：结构化输出本地校验失败后的修复重试次数
- `pricing`：不同模型的每百万 Token 价格

路由配置中的 `api` 支持 `chat`、`responses`、`messages`；`all` 是保留字，只能单独出现，表示三种协议都支持。

## 文档索引

更详细的需求、设计和验收资料请从 [docs/README.md](docs/README.md) 开始阅读，推荐顺序为：

1. [requirements.md](docs/requirements.md)
2. [design.md](docs/design.md)
3. [decisions.md](docs/decisions.md)
4. [task-breakdown.md](docs/task-breakdown.md)
5. [test-plan.md](docs/test-plan.md)
