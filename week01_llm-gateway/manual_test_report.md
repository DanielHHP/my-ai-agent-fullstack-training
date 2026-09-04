# llm gateway手动联调验证（真实供应商）

## 1. 环境变量

| 变量             | 是否必须 | 说明                                                                |
| ---------------- | -------- | ------------------------------------------------------------------------------------------------ |
| DEEPSEEK_API_KEY | 是       | DeepSeek 平台 API Key。两个 provider 默认复用同一个；如果你有两个不同 key，再拆成两个变量并改 YAML。 |
| GATEWAY_API_KEY  | 建议     | 网关调用方鉴权 key。留空时进入开发模式，不校验调用方；验收鉴权时建议设置。                         |
| GATEWAY_CONFIG   | 可选     | 指定配置文件路径，启动命令里会直接使用。                                                      |



## 2. 服务启动命令

source .venv/bin/activate
  python -m pip install -r requirements.txt

  export DEEPSEEK_API_KEY='你的 DeepSeek API Key'
  export GATEWAY_API_KEY='你的网关调用 Key'
  export GATEWAY_CONFIG=gateway.yaml

  uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

  启动后先做基础探测：

  curl -s http://127.0.0.1:8000/healthz
  curl -s http://127.0.0.1:8000/readyz

  curl -s \
    -H "Authorization: Bearer $GATEWAY_API_KEY" \
    http://127.0.0.1:8000/v1/models

  如果 GATEWAY_API_KEY 为空，开发模式下这些鉴权 header 不是必须的；如果设置了，后续 curl 都需要带同
  一个 header。

```json
{
  "object": "list",
  "data": [
    {
      "id": "deepseek-v4-flash",
      "object": "model",
      "created": 1788534329,
      "owned_by": "llm-gateway",
      "supported_protocols": [
        "messages"
      ]
    },
    {
      "id": "deepseek-v4-pro",
      "object": "model",
      "created": 1788534329,
      "owned_by": "llm-gateway",
      "supported_protocols": [
        "chat",
        "responses"
      ]
    }
  ]
}
```

## 3. 模型协议与流式/非流式 curl

统一变量方便复制：

```shell
  BASE=http://127.0.0.1:8000
  AUTH="Authorization: Bearer $GATEWAY_API_KEY"
```

### Chat Completions，非流式，deepseek-v4-pro

```shell
  curl -sS "$BASE/v1/chat/completions" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "deepseek-v4-pro",
      "messages": [
        {"role": "user", "content": "用一句话介绍你自己"}
      ],
      "stream": false
    }'
```

```json
{
  "id": "2b2894b9-b9c9-4c0e-b80f-a60a9761aff4",
  "object": "chat.completion",
  "created": 1788534396,
  "model": "deepseek-v4-pro",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "我是DeepSeek，由深度求索公司开发的AI助手，致力于用智能与知识为你提供帮助。",
        "reasoning_content": "我们需要回答用户中文请求：“用一句话介绍我自己”。需要理解：用户说“用一句话介绍你自己”，这里的“你自己”指的是助手自己？中文里“你自己”通常是对对方说“介绍你自己”，即让助手介绍自己。用户要求用一句话介绍你自己（我）。需要一句话介绍助手。需要简洁。可能我是DeepSeek，由深度求索公司开发的AI助手。要用一句话。注意“用一句话介绍你自己”中的“你自己”可能有点歧义，但作为AI助手应介绍自己。回答应中文。可以：“我是DeepSeek，由深度求索公司开发的AI助手，乐于用我的知识和能力为你提供帮助。”这是一句话。需要确保不冗长。可能需包含名称、开发者、功能。一句话。最终回答。"
      },
      "logprobs": null,
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 87,
    "completion_tokens": 184,
    "total_tokens": 271,
    "prompt_tokens_details": {
      "cached_tokens": 0
    },
    "completion_tokens_details": {
      "reasoning_tokens": 160
    },
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 87
  },
  "system_fingerprint": "a307abda487cd1b463329ccb945ce396"
}
```

  ### Chat Completions，流式，deepseek-v4-pro

```shell
  curl -N -sS "$BASE/v1/chat/completions" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "deepseek-v4-pro",
      "messages": [
        {"role": "user", "content": "从 1 数到 10"}
      ],
      "stream": true
    }'
```

```text
data: {"id":"237a4abb-5467-4b7b-92ad-706998efe47a","object":"chat.completion.chunk","created":1788534650,"model":"deepseek-v4-pro","system_fingerprint":"a307abda487cd1b463329ccb945ce396","choices":[{"index":0,"delta":{"role":"assistant","content":null,"reasoning_content":""},"logprobs":null,"finish_reason":null}]}

data: {"id":"237a4abb-5467-4b7b-92ad-706998efe47a","object":"chat.completion.chunk","created":1788534650,"model":"deepseek-v4-pro","system_fingerprint":"a307abda487cd1b463329ccb945ce396","choices":[{"index":0,"delta":{"content":null,"reasoning_content":"我们需要"},"logprobs":null,"finish_reason":null}]}

...

data: {"id":"237a4abb-5467-4b7b-92ad-706998efe47a","object":"chat.completion.chunk","created":1788534650,"model":"deepseek-v4-pro","system_fingerprint":"a307abda487cd1b463329ccb945ce396","choices":[{"index":0,"delta":{"content":"10","reasoning_content":null},"logprobs":null,"finish_reason":null}]}

data: {"id":"237a4abb-5467-4b7b-92ad-706998efe47a","object":"chat.completion.chunk","created":1788534650,"model":"deepseek-v4-pro","system_fingerprint":"a307abda487cd1b463329ccb945ce396","choices":[{"index":0,"delta":{"content":"","reasoning_content":null},"logprobs":null,"finish_reason":"stop"}],"usage":{"prompt_tokens":91,"completion_tokens":121,"total_tokens":212,"prompt_tokens_details":{"cached_tokens":0},"completion_tokens_details":{"reasoning_tokens":101},"prompt_cache_hit_tokens":0,"prompt_cache_miss_tokens":91}}

data: [DONE]

```


  ### Responses API，非流式，deepseek-v4-pro

```shell
  curl -sS "$BASE/v1/responses" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "deepseek-v4-pro",
      "input": "用一句话介绍你自己",
      "stream": false
    }'
```

```json
{
  "id": "15dc39d2-b610-4f4b-83e8-c34d8aca9d77",
  "object": "response",
  "created_at": 1788534907,
  "status": "completed",
  "background": false,
  "completed_at": 1788534910,
  "content_filters": null,
  "error": null,
  "frequency_penalty": 0,
  "incomplete_details": null,
  "instructions": null,
  "max_output_tokens": null,
  "max_tool_calls": null,
  "model": "deepseek-v4-pro",
  "moderation": null,
  "output": [
    {
      "type": "reasoning",
      "id": "b554d1bf-e5e0-4d96-8852-9bcc1cbc1bb2",
      "status": "completed",
      "content": [
        {
          "type": "reasoning_text",
          "text": "我们需要回答用户中文“用一句话介绍我自己”。需要理解用户要求：用一句话介绍你自己。指的是我作为助手介绍自己。需要用一句话。简洁。可以说明身份、能力、服务态度。注意不要用分析。直接输出一句话。需要中文。例如：“我是DeepSeek，一个由深度求索公司开发的AI助手，乐于用我的知识和推理能力帮你解答问题、提供建议。” 一句话。确保不超过一句。可能用户问“你自己”，即AI。需要避免说“我不能”等。直接输出。"
        }
      ],
      "summary": [],
      "encrypted_content": "15dc39d2-b610-4f4b-83e8-c34d8aca9d77-0"
    },
    {
      "type": "message",
      "id": "f2bcd4bb-417b-4952-a528-2bf72ac2ba66",
      "status": "completed",
      "content": [
        {
          "type": "output_text",
          "annotations": [],
          "logprobs": [],
          "text": "我是DeepSeek，由深度求索公司开发的AI助手，致力于用准确、清晰的回答帮助你解决问题。"
        }
      ],
      "phase": "final_answer",
      "role": "assistant"
    }
  ],
  "parallel_tool_calls": true,
  "presence_penalty": 0,
  "previous_response_id": null,
  "prompt_cache_key": null,
  "prompt_cache_retention": null,
  "reasoning": {
    "effort": null,
    "summary": null
  },
  "safety_identifier": null,
  "service_tier": "default",
  "store": false,
  "temperature": 1,
  "text": {
    "format": {
      "type": "text"
    },
    "verbosity": null
  },
  "tool_choice": "auto",
  "tools": [],
  "top_logprobs": 0,
  "top_p": 1,
  "truncation": "disabled",
  "usage": {
    "input_tokens": 87,
    "input_tokens_details": {
      "cached_tokens": 0
    },
    "output_tokens": 134,
    "output_tokens_details": {
      "reasoning_tokens": 110
    },
    "total_tokens": 221
  },
  "user": null,
  "metadata": {}
}
```


  ### Responses API，流式，deepseek-v4-pro

```shell
  curl -N -sS "$BASE/v1/responses" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "deepseek-v4-pro",
      "input": "从 1 数到 10",
      "stream": true
    }'
```

```text
event: response.created
data: {"type":"response.created","response":{"id":"b351c3d8-5a41-422d-bc3d-4d80c39d34c8","object":"response","created_at":1788534995,"status":"in_progress","background":false,"completed_at":null,"content_filters":null,"error":null,"frequency_penalty":0.0,"incomplete_details":null,"instructions":null,"max_output_tokens":null,"max_tool_calls":null,"model":"deepseek-v4-pro","moderation":null,"output":[],"parallel_tool_calls":true,"presence_penalty":0.0,"previous_response_id":null,"prompt_cache_key":null,"prompt_cache_retention":null,"reasoning":{"effort":null,"summary":null},"safety_identifier":null,"service_tier":"default","store":false,"temperature":1.0,"text":{"format":{"type":"text"},"verbosity":null},"tool_choice":"auto","tools":[],"top_logprobs":0,"top_p":1.0,"truncation":"disabled","usage":null,"user":null,"metadata":{}},"sequence_number":0}

event: response.in_progress
data: {"type":"response.in_progress","response":{"id":"b351c3d8-5a41-422d-bc3d-4d80c39d34c8","object":"response","created_at":1788534995,"status":"in_progress","background":false,"completed_at":null,"content_filters":null,"error":null,"frequency_penalty":0.0,"incomplete_details":null,"instructions":null,"max_output_tokens":null,"max_tool_calls":null,"model":"deepseek-v4-pro","moderation":null,"output":[],"parallel_tool_calls":true,"presence_penalty":0.0,"previous_response_id":null,"prompt_cache_key":null,"prompt_cache_retention":null,"reasoning":{"effort":null,"summary":null},"safety_identifier":null,"service_tier":"default","store":false,"temperature":1.0,"text":{"format":{"type":"text"},"verbosity":null},"tool_choice":"auto","tools":[],"top_logprobs":0,"top_p":1.0,"truncation":"disabled","usage":null,"user":null,"metadata":{}},"sequence_number":1}

event: response.output_item.added
data: {"type":"response.output_item.added","item":{"type":"reasoning","id":"b7b8f52a-f189-47d2-ad7f-491e1ea683c5","status":"in_progress","content":[],"summary":[],"encrypted_content":"b351c3d8-5a41-422d-bc3d-4d80c39d34c8-0"},"output_index":0,"sequence_number":2}

event: response.content_part.added
data: {"type":"response.content_part.added","content_index":0,"item_id":"b7b8f52a-f189-47d2-ad7f-491e1ea683c5","output_index":0,"part":{"type":"reasoning_text","text":""},"sequence_number":3}

event: response.reasoning_text.delta
data: {"type":"response.reasoning_text.delta","content_index":0,"delta":"我们需要","item_id":"b7b8f52a-f189-47d2-ad7f-491e1ea683c5","output_index":0,"sequence_number":4}

event: response.reasoning_text.delta
data: {"type":"response.reasoning_text.delta","content_index":0,"delta":"回答","item_id":"b7b8f52a-f189-47d2-ad7f-491e1ea683c5","output_index":0,"sequence_number":5}

...

event: response.output_text.delta
data: {"type":"response.output_text.delta","content_index":0,"delta":"1","item_id":"e93e5afa-d8be-434a-ab49-35dbbe3f22fe","logprobs":[],"output_index":1,"sequence_number":77}

event: response.output_text.delta
data: {"type":"response.output_text.delta","content_index":0,"delta":"，","item_id":"e93e5afa-d8be-434a-ab49-35dbbe3f22fe","logprobs":[],"output_index":1,"sequence_number":78}

event: response.output_text.delta
data: {"type":"response.output_text.delta","content_index":0,"delta":"2","item_id":"e93e5afa-d8be-434a-ab49-35dbbe3f22fe","logprobs":[],"output_index":1,"sequence_number":79}

...

```


  ### Anthropic Messages API，非流式，deepseek-v4-flash

  注意：Messages 协议必须带 max_tokens。

```shell
  curl -sS "$BASE/v1/messages" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "deepseek-v4-flash",
      "max_tokens": 256,
      "messages": [
        {"role": "user", "content": "用一句话介绍你自己"}
      ],
      "stream": false
    }'
```

```json
{
  "id": "b25b144e-3b94-4ae0-8f53-b052f842219d",
  "type": "message",
  "role": "assistant",
  "model": "deepseek-v4-flash",
  "content": [
    {
      "type": "thinking",
      "thinking": "We need answer in Chinese. One sentence intro.",
      "signature": "b25b144e-3b94-4ae0-8f53-b052f842219d"
    },
    {
      "type": "text",
      "text": "我是DeepSeek，由深度求索公司创造的AI助手，乐于用中文为你解答问题、提供帮助。"
    }
  ],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 87,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "output_tokens": 35,
    "service_tier": "standard"
  }
}
```

  ### Anthropic Messages API，流式，deepseek-v4-flash

```shell
  curl -N -sS "$BASE/v1/messages" \
    -H "$AUTH" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "deepseek-v4-flash",
      "max_tokens": 256,
      "messages": [
        {"role": "user", "content": "从 1 数到 10"}
      ],
      "stream": true
    }'
```

```text
event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"1"}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"，"}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"2"}}

...

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"，"}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"10"}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"input_tokens":91,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":74,"service_tier":"standard"}}

event: message_stop
data: {"type":"message_stop"}

```

## 4. Prompt模版

### 创建

```shell
curl -sS -X POST "$BASE/v1/prompts" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${GATEWAY_API_KEY:-}" \
    -d '{
      "id": "code-reviewer",
      "name": "Code Reviewer",
      "description": "Review code in target language",
      "role": "system",
      "content": "你是一名严格的代码评审。请检查 {{language}} 代码，并重点评估 {{focus}}。",
      "activate": true
    }'
```

```json
{
  "id": "code-reviewer",
  "version": 2,
  "name": "Code Reviewer",
  "description": "Review code in target language",
  "role": "system",
  "content": "你是一名严格的代码评审。请检查 {{language}} 代码，并重点评估 {{focus}}。",
  "is_active": true,
  "created_at": "2026-09-04T15:26:53.264900+00:00"
}
```


### 查询

```shell

curl -sS "$BASE/v1/prompts" \
    -H "Authorization: Bearer ${GATEWAY_API_KEY:-}" 
```

```json
[
  {
    "id": "code-reviewer",
    "version": 2,
    "name": "Code Reviewer",
    "description": "Review code in target language",
    "role": "system",
    "content": "你是一名严格的代码评审。请检查 {{language}} 代码，并重点评估 {{focus}}。",
    "is_active": true,
    "created_at": "2026-09-04T15:26:53.264900+00:00"
  },
  {
    "id": "code-reviewer",
    "version": 1,
    "name": "Code Reviewer",
    "description": "Review code in target language",
    "role": "system",
    "content": "你是一名严格的代码评审。请检查 {{language}} 代码，并重点评估 {{focus}}。",
    "is_active": false,
    "created_at": "2026-09-04T15:26:44.457754+00:00"
  }
]
```

```shell
  #查询当前激活版本，预期是版本 2：

  curl -sS "$BASE/v1/prompts/code-reviewer" \
    -H "Authorization: Bearer ${GATEWAY_API_KEY:-}"
```

```json
{
  "id": "code-reviewer",
  "version": 2,
  "name": "Code Reviewer",
  "description": "Review code in target language",
  "role": "system",
  "content": "你是一名严格的代码评审。请检查 {{language}} 代码，并重点评估 {{focus}}。",
  "is_active": true,
  "created_at": "2026-09-04T15:26:53.264900+00:00"
}
```

```shell
  查询指定版本，预期返回版本 1：

  curl -sS "$BASE/v1/prompts/code-reviewer?version=1" \
    -H "Authorization: Bearer ${GATEWAY_API_KEY:-}"
```

```json
{
  "id": "code-reviewer",
  "version": 1,
  "name": "Code Reviewer",
  "description": "Review code in target language",
  "role": "system",
  "content": "你是一名严格的代码评审。请检查 {{language}} 代码，并重点评估 {{focus}}。",
  "is_active": false,
  "created_at": "2026-09-04T15:26:44.457754+00:00"
}
```

### 渲染

```shell
curl -sS -X POST "$BASE/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${GATEWAY_API_KEY:-}" \
    -d '{
      "model": "deepseek-v4-pro",
      "messages": [{"role": "user", "content": "都有哪些注意事项？"}],
      "max_tokens": 512,
      "prompt_ref": {
        "id": "code-reviewer",
        "variables": {"language": "Python", "focus": "异常处理"},
        "position": "prepend"
      }
    }'
```

max_tokens限制了一下输出长度，主要验证prompt模板内容是否已经传给模型供应商。

```json
{
  "id": "eb7b5abd-265e-49ab-8ebe-998be3d49e36",
  "object": "chat.completion",
  "created": 1788537505,
  "model": "deepseek-v4-pro",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "",
        "reasoning_content": "我们需要回答用户中文问题：“都有哪些注意事项？” 上下文是“你是一名严格的代码评审。请检查 Python 代码，并重点评估 异常处理。” 用户问“都有哪些注意事项？” 可能是在问代码评审中异常处理有哪些注意事项？需要给出具体清单。我们作为严格的代码评审，重点评估异常处理，需要列出注意事项。可能期望针对Python代码异常处理的注意事项。需要详尽、结构化。可以包括：捕获范围、异常类型、finally、上下文管理器、日志、重新抛出、自定义异常、避免吞异常、异常链、资源泄漏、并发、生成器、性能、安全、可观测性等。用中文回答。可能用户没有提供具体代码，所以给出通用注意事项。需要确保专业且严格。\n\n我们应输出一份评审清单。可以分点。\n\n需要思考：用户说“都有哪些注意事项？”可能是在延续对话，但无前文。我们需以代码评审者身份回答，重点评估异常处理。给出注意事项列表。可能包括：\n\n1. 不要捕获过于宽泛的 Exception/BaseException 而不处理。\n2. 捕获具体异常，避免隐藏bug。\n3. 不要吞异常（pass），至少记录日志。\n4. 使用 finally 释放资源，但注意 finally 中的异常会覆盖原异常。\n5. 优先使用上下文管理器（with）管理资源。\n6. 重新抛出异常时使用 bare raise 保留 traceback；避免 raise ex 丢失原始堆栈。\n7. 使用 `from` 进行异常链，明确原因。\n8. 避免在 except 块中捕获异常后仅打印，而应让程序失败或处理。\n9. 自定义异常继承 Exception 而非 BaseException。\n10. 异常消息应描述上下文、参数、操作。\n11. 在边界层（API、库）定义清晰异常契约。\n12. 避免在循环内部使用异常作为流程控制（性能/可读性）。\n13. 注意 Generator/迭代器中的异常传播，GeneratorExit、StopIteration。\n14. 并发：线程/进程池中的异常捕获与传播；Future.result() 抛出原始异常。\n15. 异步代码：asyncio.gather 异常收集；CancelledError 不应误捕获。\n16. 避免捕获 KeyboardInterrupt/SystemExit（BaseException 子类）除非必要。\n17. 异常安全：确保失败时对象状态一致，避免半初始化。\n18. 日志中不要"
      },
      "logprobs": null,
      "finish_reason": "length"
    }
  ],
  "usage": {
    "prompt_tokens": 106,
    "completion_tokens": 512,
    "total_tokens": 618,
    "prompt_tokens_details": {
      "cached_tokens": 0
    },
    "completion_tokens_details": {
      "reasoning_tokens": 512
    },
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 106
  },
  "system_fingerprint": "a307abda487cd1b463329ccb945ce396"
}
```

## 5. 注意事项

  协议路由边界：deepseek-v4-pro 只声明了 chat,responses，deepseek-v4-flash 只声明了 messages。给
  pro 调 /v1/messages，或给 flash 调 /v1/chat/completions，会返回 422 protocol_not_supported，这是
  预期行为。

  鉴权范围：只有 /healthz 和 /readyz 不需要鉴权。/v1/models、Prompt 接口、/admin/* 都需要 Bearer
  Key。建议设置 GATEWAY_API_KEY 后，顺便验证缺 key、错 key 都返回统一 401 invalid_api_key。

  限流：当前 rate_limit 默认 enabled: true，每分钟 60 请求，突发容量 10。连续压测很容易触发 429
  rate_limit_exceeded。如果只是先做功能验收，可临时改成 enabled: false，或调大 requests_per_minute
  和 burst；限流本身也是核心验收项，建议最后再单独验证。

  流式测试必须用 curl -N，否则 SSE 会被缓冲，观感上像一次性返回。三种协议返回的是各自原生 SSE 事件
  格式，不是统一格式，这是当前设计决定，不要期望三种协议事件名一致。

  Messages 协议记得 max_tokens。缺少时会在进入上游前返回 422 missing_required_parameter。

  结构化输出建议用非流式测试，流式已经发出内容后无法无损修复。Chat/Responses 用 response_format:
  {"type": "json_schema", "json_schema": {"name": "...", "schema": {...}}}；Messages 当前通过系统提
  示注入 JSON Schema，再做本地校验修复。

  熔断器：同一 provider 连续失败 5 次会打开熔断，冷却 30 秒。验收时可以看：

  curl -sS -H "$AUTH" "$BASE/admin/routes"

  用量与成本：

  curl -sS -H "$AUTH" "$BASE/admin/usage?limit=20"

  会看到 protocol、provider、upstream_model、stream、input_tokens、output_tokens、cached_tokens、
  latency_ms、first_token_ms、retries、fallbacks、repair_retries、cost_usd 等字段。


## 附录

### Gateway API key生成

python3 -c 'import secrets; print("sk-gateway-" + secrets.token_urlsafe(32))'

  或者：

  openssl rand -hex 32

  也可以生成带前缀的版本：

  printf 'sk-gateway-%s\n' "$(openssl rand -hex 24)"

  然后把它写入本地 .env，例如：

  GATEWAY_API_KEY='sk-gateway-xxxxxx'
  DEEPSEEK_API_KEY='你的 DeepSeek Key'

  启动前加载：

  set -a
  source .env
  set +a

  GATEWAY_CONFIG=gateway.yaml uvicorn app.main:app --host 127.0.0.1 --port 8000

  或者直接 export：

  export GATEWAY_API_KEY='sk-gateway-xxxxxx'
  export DEEPSEEK_API_KEY='你的 DeepSeek Key'

  生成时建议注意几点：

  - 至少 32 字节随机值，也就是 256 位熵。
  - 加一个 sk- 或 sk-gateway- 前缀，方便识别和审计。
  - 不要用短密码、固定单词或可预测的递增字符串。
  - .env 已经被 .gitignore 忽略，不要提交到仓库。
  - 不要把这个 key 打印到日志里；当前服务的用量记录也只保存 key 的短指纹，不会保存原文。

