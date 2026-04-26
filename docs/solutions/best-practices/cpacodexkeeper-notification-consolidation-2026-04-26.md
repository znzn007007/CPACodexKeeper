---
title: CPACodexKeeper 通知整合与降噪模式
date: 2026-04-26
category: best-practices
module: CPACodexKeeper notification consolidation
problem_type: best_practice
component: service_object
severity: medium
applies_when:
  - notification delivery mixes realtime alerts and scheduled broadcasts
  - multiple notification summaries overlap or duplicate each other
  - one daemon is deployed on multiple machines and notifications need source identity
  - operators need safe notification-format tests before production rollout
tags:
  - notifications
  - feishu
  - multi-machine
  - scheduled-broadcast
  - quota-alerts
  - operational-noise
---

# CPACodexKeeper 通知整合与降噪模式

## Context

CPACodexKeeper 原来的飞书通知同时承担“实时告警”“轮次流水账”和“两套日报”三种职责：普通轮次变更通知会包含删除、禁用、启用、刷新等动作，生产上可能每个 daemon 间隔都推一次；账号状态日报和 CPA quota 日报又分别汇总账号数量与额度状态，内容重复。多机部署时，通知标题也缺少服务器身份，飞书里很难区分来源。

这次改造的目标不是减少真实风险信号，而是把通知分层：高风险事件尽快可见，低风险维护动作进入定时播报，所有消息都能看出是哪台机器发出的。

## Guidance

### 1. 先按“是否需要立刻打扰人”划分通知

建议把通知分成两类：

- **实时通知**：删除 token、禁用 token、Plus 额度告警/恢复、CPA API 异常、usage 大面积失败、连续网络失败/恢复、巡检异常、进程异常退出。
- **定时播报**：启用 token、刷新 token、普通轮次统计、账号数量、quota 概况。

删除和禁用仍然是高风险动作，但不要逐 token 发送；同一轮发生多个删除或禁用时，轮次结束后按类型合并成一条通知。

### 2. 标题身份放在 notifier transport 层

服务器名称应作为通知基础设施能力，而不是散落在每个业务调用点。这样新增告警、测试通知、异常通知时都会自动带上来源。

示例模式：

```python
# FeishuNotifier 内部统一处理
text = self._format_title(title) + "\n" + "\n".join(lines)
# => [sub2api-prod] CPA Codex 定时播报
```

配置使用业务无关名称：

```env
CPA_SERVER_NAME=sub2api-prod
```

不要把它命名成 `FEISHU_*`，因为服务器身份是跨通知渠道的概念。

### 3. 不新增第二套 scheduler

定时播报继续挂在 maintainer 每轮结束后的 post-run 链路里，不额外加 cron、sidecar 或第二个 daemon。推荐顺序：

```text
maintainer run finishes
  -> 异常 / 网络失败类通知
  -> quota aggregation + Plus alert/recovery
  -> 删除 / 禁用合并通知
  -> 定时播报槽位检查
  -> 统一定时播报
```

这样可以复用现有 daemon 生命周期，也避免两个调度源同时读写同一批 token 或状态文件。

### 4. 业务播报状态不要写进 notifier cooldown state

通知 transport 的状态只适合保存冷却、连续网络失败等“发送层”信息。quota alert 状态和定时播报槽位属于业务领域状态，应该放在 quota/domain state 里。

本次模式：

```text
notify_state.json
  - cooldowns
  - consecutive_failure_rounds

quota_healthcheck_state.json
  - alert_state
  - broadcast_state
```

如果从旧 summary state 迁移，要做最小兼容读取，避免线上状态文件升级后重复播报。

### 5. quota job 返回聚合结果，不自己发送日报

quota job 应负责：

- 聚合本轮 snapshots
- 评估 Plus alert / recovery
- 发送实时 quota alert / recovery
- 返回 aggregate 给 maintainer

它不应该再单独发送“CPA Codex 额度日报”。统一定时播报由 maintainer 拿账号 stats、round events 和 quota aggregate 一起生成。

### 6. 保留受控测试通知

上线前需要验证 webhook、关键词/签名、安全模式、标题前缀和消息格式，但不能进入真实 maintainer 主流程。CLI 应提供 fake data 的测试模式：

```powershell
python main.py --quota-test broadcast --quota-test-state-file .\runtime\quota_healthcheck_state.test.json
python main.py --quota-test deleted --quota-test-state-file .\runtime\quota_healthcheck_state.test.json
python main.py --quota-test disabled --quota-test-state-file .\runtime\quota_healthcheck_state.test.json
python main.py --quota-test alert --quota-test-state-file .\runtime\quota_healthcheck_state.test.json
python main.py --quota-test recovery --quota-test-state-file .\runtime\quota_healthcheck_state.test.json
```

测试状态文件要可隔离，避免污染生产 `quota_healthcheck_state.json`。

## Why This Matters

通知系统一旦变成流水账，真正需要处理的风险会被淹没。CPACodexKeeper 又是会执行真实删除、禁用、启用、刷新动作的 daemon，所以通知设计要同时满足两点：

- 风险动作和异常要尽快暴露。
- 正常维护动作不能持续刷屏。

把“实时告警”和“定时播报”拆清楚后，飞书的信噪比更高，也更容易上线多台机器：每条消息都有来源，每个状态文件都有清晰职责，恢复和回滚时不会因为错误恢复 `notify_state.json` 或 quota state 而重复通知。

## When to Apply

- daemon 每轮都会产生状态变化，但不是每个变化都需要打扰人。
- 同一套服务同时有实时通知、日报、quota 报告等多条汇总链路。
- 多台机器部署同一服务，飞书消息需要区分来源。
- 通知发送状态、告警状态、业务播报槽位混在一起，导致恢复或迁移风险变高。
- 需要上线前验证消息格式，但不能触发真实业务写操作。

不适合的场景：每个事件都必须秒级响应，或者通知只是本地调试日志而不是运维信号。

## Examples

### 配置示例

```env
CPA_SERVER_NAME=sub2api-prod
CPA_STATUS_BROADCAST_ENABLED=true
CPA_STATUS_BROADCAST_HOURS_LOCAL=8,12,18,23
CPA_STATUS_BROADCAST_TIMEZONE=Asia/Hong_Kong

CPA_QUOTA_REPORT_ENABLED=true
CPA_QUOTA_ALERT_ENABLED=true
CPA_QUOTA_STATE_FILE=./runtime/quota_healthcheck_state.json
FEISHU_NOTIFY_STATE_FILE=./runtime/notify_state.json
```

旧的 `FEISHU_NOTIFY_SEND_DAILY_SUMMARY` 和 `FEISHU_NOTIFY_DAILY_SUMMARY_HOURS_UTC` 可以作为兼容输入保留，但新文档和新部署应优先使用 `CPA_STATUS_BROADCAST_*`。

### 代码边界示例

```text
Settings
  - 只解析配置和兼容旧变量

FeishuNotifier
  - 统一标题前缀
  - 发送删除/禁用合并通知
  - 发送统一定时播报
  - 管理发送层 cooldown / failure state

CPACodexKeeper maintainer
  - 记录 round_events
  - 记录 quota_snapshots
  - 决定 post-run 通知顺序

QuotaHealthcheckJob
  - 返回 quota aggregate
  - 保留 Plus alert/recovery 状态机
  - 不发送独立 quota 日报

QuotaHealthcheckState
  - 保存 alert_state
  - 保存 broadcast_state
```

### 测试覆盖清单

至少覆盖：

- `CPA_SERVER_NAME` 解析和标题前缀。
- `CPA_STATUS_BROADCAST_HOURS_LOCAL=8,12,18,23` 去重排序和非法小时拒绝。
- Docker compose 透传新变量。
- 删除/禁用同轮合并，启用/刷新不触发实时账号变更通知。
- quota alert/recovery 发送失败时不推进状态。
- quota job 不再发送独立额度日报，但返回 aggregate。
- 同一时区同一小时槽定时播报只发送一次。
- `--quota-test broadcast/deleted/disabled/alert/recovery` 不进入 maintainer 主流程。

本次验证结果：

```text
python -m unittest discover -s tests  # 64 tests OK
python -m compileall src              # passed
git diff --check                      # no whitespace errors, only CRLF warnings
python main.py --dry-run --quota-test broadcast ...  # exit 0
```

## Related

- `docs/brainstorms/cpacodexkeeper-notification-consolidation-requirements.md` — 需求澄清来源。
- `docs/plans/2026-04-26-001-feat-cpacodexkeeper-notification-consolidation-plan.md` — 实现计划和需求追踪。
- `src/notifier.py` — Feishu transport、标题前缀、合并通知和定时播报格式。
- `src/maintainer.py` — post-run 通知编排、round events、quota snapshots。
- `src/quota_job.py` — quota 聚合和 Plus alert/recovery。
- `src/quota_state.py` — alert/broadcast 领域状态。
- `src/settings.py` — 新配置和旧配置兼容。
- `src/cli.py` — 受控测试通知入口。
