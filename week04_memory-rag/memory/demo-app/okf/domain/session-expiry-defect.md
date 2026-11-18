---
type: Defect
title: 会话过期判断按「日」比较
description: isSessionExpired 用日期字符串前缀比较，导致同一天内过期不被判定为过期。
tags: [session, auth, defect, expiry]
generated: { by: pi-coding-agent/deepseek-flash, at: 2026-11-18T01:37:45Z }
sources:
  - id: session-policy-src
    resource: ../../src/auth/session-policy.ts
    title: isSessionExpired 实现
  - id: readme
    resource: ../../README.md
    title: README 对缺陷的描述（第 5-6 行）
---

# 缺陷描述

`src/auth/session-policy.ts` 中 `isSessionExpired` 取 `expiresAt` 与 `now` 的
前 10 个字符（`YYYY-MM-DD`）做字符串比较，因此只精确到「日」：

```ts
const expiresOn = session.expiresAt.slice(0, 10);
const currentDay = now.toISOString().slice(0, 10);
return expiresOn < currentDay;
```

同一天内的过期不会被判为已过期——例如 `expiresAt = 2026-09-11T00:30:00Z`
而 `now = 2026-09-11T00:45:00Z` 时，两者日期前缀相同，返回 `false`。[^session-policy-src] [^readme]

# 与测试的冲突

`tests/session-boundary.test.ts` 的 3 个用例按「秒级时刻」要求语义，因此在修复前失败；
详见 [测试套件](/testing/test-suite.md)。

# 期望语义

根据测试用例，边界应为：

| `now` 相对 `expiresAt` | `isSessionExpired` |
| --- | --- |
| 到期前（`00:29:59.999Z`） | `false` |
| 恰好到期（`00:30:00.000Z`） | `true` |
| 到期后（`00:30:00.001Z`） | `true` |

即 `now >= expiresAt` 视为过期。

[^session-policy-src]: src/auth/session-policy.ts
[^readme]: README.md
