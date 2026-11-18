---
type: Playbook
title: 测试环境阻塞：node_modules 缺失
description: 2026-11-18 实测 npm test 因缺少 tsx 而全部加载失败，以及两种恢复方式。
tags: [testing, environment, blocker, tsx]
status: stable
generated: { by: pi-coding-agent/deepseek-flash, at: 2026-11-18T01:37:45Z }
sources:
  - id: readme
    resource: ../../README.md
    title: node_modules 软链说明（第 13 行）
  - id: package-json
    resource: ../../package.json
    title: test 脚本使用 tsx
---

# 现象

2026-11-18 在本工作区执行 `npm test`，3 个测试文件全部在**加载阶段**失败：

```text
Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'tsx' imported from
  .../week04_memory-rag/memory/demo-app/
```

汇总输出为 `tests 3 / pass 0 / fail 3`，每个文件报 `'test failed'`。

# 原因

- 工作区不存在 `node_modules`。
- README 所述的软链目标 `../../../harness_agent/node_modules` 也不存在
  （`ls` 返回 `No such file or directory`），因此 `tsx` 无法解析。[^readme]
- `package.json` 的测试脚本依赖 `tsx` 作为 `--import` 加载器。[^package-json]

# 影响

此种失败**不是**业务断言失败，不能据此判断用例真实通过率，也不能用来对比
[运行手册](/testing/runbook.md) 中的 `7 tests / 4 pass / 3 fail` 预期。

# 恢复方式

二选一，随后重新执行 `npm test`：

```bash
# 方式一：重建软链（需目标目录真实存在）
ln -s ../../../harness_agent/node_modules node_modules

# 方式二：本地安装依赖
npm install
```

# 验证

恢复后应能进入用例执行阶段，并得到 README 第 8 行描述的 7/4/3（修复前）或 7 pass（修复后）结果。[^readme]

[^readme]: README.md
[^package-json]: package.json
