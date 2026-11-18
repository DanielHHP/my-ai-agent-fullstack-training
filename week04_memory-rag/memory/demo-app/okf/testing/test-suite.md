---
type: Test Suite
title: demo-app 测试套件（7 个用例）
description: bug-fix-demo 的全部测试文件路径、用例数与各自覆盖对象。
tags: [testing, session, auth, node-test]
generated: { by: pi-coding-agent/deepseek-flash, at: 2026-11-18T01:37:45Z }
sources:
  - id: readme
    resource: ../../README.md
    title: bug-fix-demo README（项目说明与预期测试结果）
  - id: session-boundary-test
    resource: ../../tests/session-boundary.test.ts
    title: 会话过期秒级边界测试（3 个用例）
  - id: session-policy-test
    resource: ../../tests/session-policy.test.ts
    title: 会话过期常规测试（2 个用例）
  - id: login-flow-test
    resource: ../../tests/login-flow.test.ts
    title: 登录流程测试（2 个用例）
---

# 测试文件

测试全部位于项目根目录的 `tests/` 下，共 7 个用例（3 + 2 + 2）。

| 测试文件 | 用例数 | 覆盖对象 |
| --- | --- | --- |
| `tests/session-boundary.test.ts` | 3 | `src/auth/session-policy.ts` 的 `isSessionExpired`，秒级边界：到期前 1ms 有效、到期时刻即过期、到期后 1ms 失效 |
| `tests/session-policy.test.ts` | 2 | `isSessionExpired` 的常规过期与未过期判定 |
| `tests/login-flow.test.ts` | 2 | `src/routes/login.ts` 的 `loginRoute`：成功登录返回令牌、密码错误抛 `INVALID_PASSWORD` |

## 会话相关测试细节

`session-boundary` 的 3 个用例使用同一 fixture `{ expiresAt: "2026-09-11T00:30:00.000Z" }`，
分别以 `00:29:59.999Z`、`00:30:00.000Z`、`00:30:00.001Z` 作为 `now`，断言「按秒级时刻」的过期语义。[^session-boundary-test]

该语义与当前实现不符，详见 [会话过期缺陷](/domain/session-expiry-defect.md)。

## 断言风格

所有用例使用 `node:assert/strict` 的 `assert.equal` / `assert.rejects`，基于 `node:test` 的 `test()`。[^session-policy-test] [^login-flow-test]

[^readme]: README.md，项目性质与预期结果
[^session-boundary-test]: tests/session-boundary.test.ts
[^session-policy-test]: tests/session-policy.test.ts
[^login-flow-test]: tests/login-flow.test.ts
