---
type: Runbook
title: 运行 demo-app 测试
description: 执行测试套件的命令、前置条件与预期通过/失败数。
tags: [testing, runbook, commands]
generated: { by: pi-coding-agent/deepseek-flash, at: 2026-11-18T01:37:45Z }
sources:
  - id: package-json
    resource: ../../package.json
    title: npm scripts 与 devDependencies
  - id: readme
    resource: ../../README.md
    title: 运行说明与预期结果
---

# 运行命令

在项目根目录 `demo-app/` 下执行：

```bash
npm test
```

`npm test` 实际调用：`package.json` 的 `scripts.test` 为
`node --import tsx --test "tests/**/*.test.ts"`。[^package-json]

等价的直接调用方式：

```bash
node --import tsx --test "tests/**/*.test.ts"
```

# 预期结果

| 阶段 | 预期 |
| --- | --- |
| 修复前 | `7 tests / 4 pass / 3 fail`，失败集为 `tests/session-boundary.test.ts` 的 3 个用例 |
| 修复后 | `7 pass` |

预期值来源：README 第 8 行。[^readme]

# 前置条件

- Node.js `>= 22.19`。
- `node_modules` 需可用，提供 `tsx`。README 说明它是指向 `../../../harness_agent/node_modules` 的软链。[^readme]
- devDependencies：`tsx@4.20.6`、`typescript@5.9.3`。[^package-json]

> 当前环境的依赖缺失问题见 [测试环境阻塞](/testing/known-blockers.md)；执行前先确认该阻塞已解除，
> 否则看到的是模块加载失败而非上表的断言失败。

[^package-json]: package.json
[^readme]: README.md
