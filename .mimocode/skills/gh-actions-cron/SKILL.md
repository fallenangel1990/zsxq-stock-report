---
name: gh-actions-cron
description: "管理 GitHub Actions 定时任务：添加/移除/调整 schedule cron、切换手动/自动触发、处理时区换算"
---

# GitHub Actions 定时任务管理

管理本项目 `.github/workflows/` 中的定时触发配置。

## 工作流文件

| 文件 | 功能 |
|------|------|
| `daily-report.yml` | 每日股票报告 |
| `market-review.yml` | 盘后复盘报告 |
| `consecutive-limit-up.yml` | 连板股票扫描 |
| `intraday-monitor.yml` | 盘中动态预警 |
| `stock-dashboard.yml` | 股票仪表盘 |

## 关键规则（来自 cerebrum Do-Not-Repeat）

1. **UTC 提前 4 小时补偿**：用户确认 Actions 推送比目标北京时间晚 4 小时。配置 cron 时必须按目标北京时间提前 4 小时。
   - 示例：目标 08:30 北京 → UTC `30 20 * * 0-4`（即北京时间 04:30）
   - 示例：目标 12:00 北京 → UTC `0 0 * * 1-5`（即北京时间 08:00）

2. **YAML `on:` 键**：顶层事件键必须写成 `"on":`（带引号），不要用裸 `on:`。裸键在 YAML 1.1 中会被解析为布尔值 `true`。

3. **禁用定时不要用空 schedule**：`schedule: []` 无效，GitHub 要求 schedule 至少包含一个 cron map。正确做法是直接移除 `schedule` 键，只保留 `workflow_dispatch`。

4. **交易日过滤**：cron 只覆盖工作日（`1-5` 或 `0-4`），实际交易日判断需在 workflow 步骤中使用 `chinese_calendar` 排除节假日。

5. **同花顺分组日期时区**：分组名中的日期必须用北京时间 `ZoneInfo("Asia/Shanghai")`，避免 UTC runner 在凌晨生成错误日期。

## 操作模板

### 添加定时触发

在 workflow 文件的 `on:` 下添加 `schedule:` 块：

```yaml
on:
  schedule:
    - cron: '<UTC cron 表达式>'
  workflow_dispatch:
```

### 移除定时触发（仅保留手动）

删除 `schedule:` 块及其子项，只保留 `workflow_dispatch:`。

### 调整 cron 时间

1. 确认目标北京时间
2. 减去 4 小时得到 UTC 时间
3. 验证 cron 语法：`分 时 日 月 周`

### 验证步骤

修改完成后：
1. `python -m py_compile` 检查相关 Python 文件（如有代码变更）
2. 检查 YAML 语法是否正确
3. 确认 `on:` 键带引号
4. git commit 并 push
