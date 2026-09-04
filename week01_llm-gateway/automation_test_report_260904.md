# LLM 统一模型调用服务自动化验收测试报告

- 报告文件：`automation_test_report_260904.md`
- 执行日期：2026-09-04
- 仓库：`week01_llm-gateway`
- 分支：`main`
- 提交：`b6b7600`

## 1. 结论

本次验收测试通过。

- 静态检查：通过
- 白盒自动化测试：98/98 通过
- HTTP 黑盒冒烟测试：8/8 通过
- 全链路未访问真实模型服务商

## 2. 执行依据

本次测试依据以下文档执行：

- `docs/README.md`
- `docs/requirements.md`
- `docs/design.md`
- `docs/decisions.md`
- `docs/task-breakdown.md`
- `docs/test-plan.md`

自动化流水线入口：

- `scripts/run_acceptance_tests.sh`
- `scripts/run_http_acceptance_tests.sh`

## 3. 测试环境

| 项目 | 值 |
| --- | --- |
| Python | 3.14.0 |
| pytest | 8.4.2 |
| ruff | 0.16.5 |
| pytest-asyncio | 0.26.0 |
| anyio | 4.14.2 |
| 执行目录 | `/Users/app_dev/code/ai_agent/ai_agent_training/my-ai-agent-fullstack-training/week01_llm-gateway` |
| Git 分支 | `main` |
| Git 提交 | `b6b7600` |
| 白盒开始时间 | 2026-09-04T00:42:46Z |
| 黑盒开始时间 | 2026-09-04T00:42:56Z |

## 4. 自动化流水线执行结果

### 4.1 白盒流水线

执行命令：

```bash
bash scripts/run_acceptance_tests.sh
```

执行内容：

1. 输出环境信息
2. `ruff check .`
3. `pytest tests`，生成 JUnit XML 和 Markdown 摘要

结果：

| 检查项 | 结果 |
| --- | --- |
| ruff 静态检查 | 通过，`All checks passed!` |
| pytest 测试总数 | 98 |
| 通过 | 98 |
| 失败 | 0 |
| 错误 | 0 |
| 跳过 | 0 |
| 总耗时 | 2.03s |

测试文件分布：

| 测试文件 | 用例数 | 结果 |
| --- | ---: | --- |
| `tests/test_acceptance_pipeline.py` | 4 | 通过 |
| `tests/test_adapters.py` | 8 | 通过 |
| `tests/test_admin_routes.py` | 10 | 通过 |
| `tests/test_config.py` | 6 | 通过 |
| `tests/test_gateway.py` | 19 | 通过 |
| `tests/test_prompts.py` | 8 | 通过 |
| `tests/test_router_upstream.py` | 6 | 通过 |
| `tests/test_routes.py` | 10 | 通过 |
| `tests/test_security_rate_limit.py` | 6 | 通过 |
| `tests/test_structured.py` | 9 | 通过 |
| `tests/test_usage.py` | 12 | 通过 |

### 4.2 黑盒流水线

执行命令：

```bash
bash scripts/run_http_acceptance_tests.sh
```

结果：

| 用例 | 场景 | 结果 |
| --- | --- | --- |
| BH-01 | `GET /healthz` 无鉴权存活检查 | 通过 |
| BH-02 | `GET /readyz` 无鉴权就绪检查 | 通过 |
| BH-03 | 无鉴权请求 `/v1/models` 返回 401 | 通过 |
| BH-04 | 携带密钥查询模型列表 | 通过 |
| BH-05 | Chat Completions 非流式调用 | 通过 |
| BH-06 | Chat Completions SSE 流式调用 | 通过 |
| BH-07 | 限流连续三次请求，第三次返回 429 | 通过 |
| BH-08 | SIGTERM 优雅退出 | 通过 |

## 5. 验收场景覆盖与执行结果

### A. 配置加载与协议能力声明

- ACC-CFG-01 环境变量展开：通过
- ACC-CFG-02 路由 provider 未声明：通过
- ACC-CFG-03 协议能力解析：通过
- ACC-CFG-04 `all` 保留字组合约束：通过
- ACC-CFG-05 未知协议值拒绝：通过
- ACC-CFG-06 默认配置值：通过

### B. 协议适配器与请求构造

- ACC-ADP-01 适配器注册：通过
- ACC-ADP-02 OpenAI Chat 请求构造：通过
- ACC-ADP-03 OpenAI Responses 请求构造：通过
- ACC-ADP-04 Anthropic 鉴权与 `max_tokens` 必填：通过
- ACC-ADP-05 Anthropic usage 归一化：通过
- ACC-ADP-06 Anthropic 结构化 Schema 注入：通过

### C. 模型路由与 `/v1/models`

- ACC-RTR-01 未知模型别名：通过
- ACC-RTR-02 入口协议不支持：通过
- ACC-RTR-03 不允许跨协议 fallback：通过
- ACC-RTR-04 priority 路由顺序：通过
- ACC-RTR-05 weighted round robin：通过
- ACC-RTR-06 provider disabled：通过
- ACC-RTR-07 熔断器打开与恢复：通过
- ACC-RTR-08 `/v1/models` 协议并集：通过

### D. 非流式调用、重试与 fallback

- ACC-GW-01 单路由成功：通过
- ACC-GW-02 每条路由最多 3 次重试：通过
- ACC-GW-03 fallback 后重试计数重置：通过
- ACC-GW-04 可重试状态码：通过
- ACC-GW-05 网络异常重试：通过
- ACC-GW-06 普通 4xx 不重试但 fallback：通过
- ACC-GW-07 所有候选路由失败：通过
- ACC-GW-08 三协议端到端非流式：通过

### E. SSE 流式输出

- ACC-STREAM-01 Chat SSE 透明转发：通过
- ACC-STREAM-02 Responses SSE 透明转发：通过
- ACC-STREAM-03 Anthropic SSE 透明转发：通过
- ACC-STREAM-04 TTFT 记录：通过
- ACC-STREAM-05 首字节前重试/fallback：通过
- ACC-STREAM-06 已发送内容后上游错误：通过
- ACC-STREAM-07 客户端断开取消上游：通过

### F. 结构化输出

- ACC-SCHEMA-01 合法 JSON 且符合 Schema：通过
- ACC-SCHEMA-02 Markdown code fence 提取：通过
- ACC-SCHEMA-03 非法 JSON 自动修复一次：通过
- ACC-SCHEMA-04 修复超过配置次数：通过
- ACC-SCHEMA-05 Anthropic 结构化注入与本地校验：通过
- ACC-SCHEMA-06 Chat 与 Responses 结构化参数解析：通过

### G. Prompt 版本管理

- ACC-PRM-01 创建并激活版本：通过
- ACC-PRM-02 查询激活版本或指定版本：通过
- ACC-PRM-03 模板渲染变量替换：通过
- ACC-PRM-04 缺失变量：通过
- ACC-PRM-05 非法 Jinja 模板：通过
- ACC-PRM-06 模型调用中 `prompt_ref` 注入：通过
- ACC-PRM-07 未知 Prompt ID：通过

### H. 可观测性与用量账本

- ACC-USAGE-01 成功调用记录：通过
- ACC-USAGE-02 成本计算：通过
- ACC-USAGE-03 TTFT 记录：通过
- ACC-USAGE-04 重试/fallback 记录：通过
- ACC-USAGE-05 结构化修复记录：通过
- ACC-USAGE-06 失败调用记录：通过
- ACC-USAGE-07 敏感信息脱敏：通过
- ACC-USAGE-08 `/admin/usage` 查询：通过

### I. 统一错误、鉴权与限流

- ACC-SEC-01 健康检查无需鉴权：通过
- ACC-SEC-02 业务接口需要鉴权：通过
- ACC-SEC-03 Bearer Key 鉴权：通过
- ACC-SEC-04 `x-api-key` 兼容：通过
- ACC-SEC-05 开发模式 IP 指纹：通过
- ACC-SEC-06 模型端点限流：通过
- ACC-SEC-07 非模型端点不限流：通过
- ACC-SEC-08 统一错误格式：通过

### J. 管理接口与健康检查

- ACC-ADMIN-01 `/admin/routes`：通过
- ACC-ADMIN-02 `/admin/usage`：通过
- ACC-ADMIN-03 `/v1/models`：通过
- ACC-ADMIN-04 未知字段策略：通过

## 6. 测试产物

白盒与黑盒流水线生成的主要产物：

- `reports/environment.txt`
- `reports/junit.xml`
- `reports/summary.md`
- `reports/blackbox-summary.md`
- `reports/blackbox/uvicorn.log`
- `reports/blackbox/*.body`
- `reports/blackbox/*.headers`

## 7. 观察项

本次执行无失败项。以下观察项不影响验收结果：

- 当前 `.venv` 为 Python 3.14，`pytest-asyncio` 产生大量 `asyncio.get_event_loop_policy` 相关 DeprecationWarning；按验收计划，不影响功能判断。
- `fastapi.testclient` 存在 Starlette `TestClient` 的弃用提示。
- `StructuredOutputSpec.schema` 字段名与 Pydantic `BaseModel` 属性同名，产生 UserWarning。
- 黑盒脚本停止 Uvicorn 时 shell 输出 `Terminated: 15`，这是脚本主动发送 SIGTERM 后的作业控制提示，BH-08 验证通过。

## 8. 验收通过标准核对

| 标准 | 结果 |
| --- | --- |
| `pytest` 全部通过 | 通过 |
| `ruff check .` 通过 | 通过 |
| 上游调用全部由 mock 覆盖 | 通过 |
| 三种协议非流式入口返回原生结构 | 通过 |
| 三种协议 SSE 透明转发并记录 TTFT | 通过 |
| 重试与 fallback 语义符合设计 | 通过 |
| 结构化输出修复与超限错误 | 通过 |
| Prompt 版本管理与渲染 | 通过 |
| 用量账本记录与脱敏 | 通过 |
| 鉴权限流语义 | 通过 |
| 管理接口与健康检查 | 通过 |
| HTTP 黑盒场景全部通过 | 通过 |
