# 二开基线（可验证版）

基底：上游 `garycaogan/ai-live-avatar`（MIT，见 LICENSE 与 README 顶部的署名）。
本仓库是二开分支，不是原作者的官方仓库；角色形象、人设、音色、素材库
仍需用自己的资产，原仓库同样不含这些。

本轮范围（用户选定）：文章能力跑通 + 安全解锁换背景/道具 + 打断接话。
不在本轮：唱歌、大幅度舞蹈、带货、投票、礼物 KPI、抖音官方弹幕接入。
这些要么依赖 Vidu 模型侧能力，要么需要平台资质，不能只靠改代码打包票。

## 改了什么

### 1. 安全解锁换背景/道具（`live/session.py`）

上游默认 `live_visual_edits=false`，原因是公开测试里连续换两次背景后，
Vidu 重画时衣服走样。本轮没有简单地把开关改成 true，而是加了四道 guard：

- **一次只改一项**：一条弹幕里同时有背景+道具时，只执行第一项，其余
  暂缓并在命令备注里写明（`visual_edit_single_change_only`）。
- **冷却**：两次换景之间至少隔 `visual_edit_cooldown_seconds` 秒
  （默认 0 即关闭，示例配置 20 秒），冷却内请求直接失败，不进队列、不花一次换图。
  默认关闭是为了不破坏上游“连续换装换景”的演示节奏；公开直播建议开启。
- **连续上限**：连续换景达到 `visual_edit_max_consecutive` 次（示例 2 次）
  后必须先发“换回来”复原，才能再换景。复原会清零计数。
- **衣服捆绑**：换景时若当前衣服不在参考图集合里，会把当前衣服
  （衣柜条目或底图）的参考图一起发进 switch（`visual_edit_bundle_outfit`），
  不再只发一张背景图/道具图。这是针对上游事故的直接修复。
- **快照复原**：每次换景前保存 `visual` 快照；换图回执失败时自动
  回滚内存中的画面集合；运营也可随时调 `POST /api/visual/revert` 手动复原。

换装仍走整套照片重开会话的老路，不受换景 guard 影响；换装成功后
换景计数清零。

配置见 `config.example.json` 的 `safety` 段；运行时也可用
`POST /api/settings` 改这些值（冷却 0–120 秒，上限 1–5 次）。
上游默认仍是 `live_visual_edits=false`，示例配置才开启——
开启不等于“公开直播已验证安全”，见下文“未实测”。

### 2. 打断接话（`live/session.py`、`server.py`）

- 新增 `schedule.interrupt_policy`：`off`（默认，保持上游“说完再发下一条”）、
  `gift`（只有带礼物的弹幕打断）、`all`（所有聊天/动作弹幕都可打断）。
- 打断时：保存当前正在说的那句（来自 Vidu 转写帧），发
  `AUDIO_INTERRUPTED`（真会话模式），清掉说话等待时间，把打断的弹幕
  放进优先队列，之后再放一条 `resume` 命令，让数字人自然地把被打断
  的话接完，而不是重新开头。优先队列跳过固定间隔等待，但仍会等
  打断那条的发言结束。
- 运营手动打断：`POST /api/interrupt`，body 可带 `text`（打断后先说的话）
  和 `user`；不带 `text` 时只打断并接回上一句。
- 已知限制（不要把它包装成“逐字精准剪切”）：Vidu 的转写帧是数字人
  “将要说的整句”，不是真实的语音结束事件；转写晚到时可能重新估算
  等待时间。打断是尽力而为，不是采样级切断。

### 3. 可观测性

`/api/state` 快照新增 `visual_safety`（开关、冷却、连续次数、上限、
快照情况）、`interrupt_policy`、`interrupted_line`、`priority_queue`。

## 怎么验证（不花钱的部分）

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest tests.test_logic tests.test_workbench tests.test_second_dev
# 端到端（含本地模拟 Vidu，不花钱，约 3 分钟）：
.venv/bin/python -m unittest tests.test_e2e
```

二开专属用例在 `tests/test_second_dev.py`，全部在空跑模式下跑：

- 一条弹幕多项画面变化只执行第一项；
- 冷却内、连续达上限时换景请求失败且不进队列；
- 换景时当前衣服被捆进参考图集合，复原后画面集合回到快照；
- 礼物弹幕在 `gift` 策略下打断当前发言、优先执行、随后接回被打断的句子；
- 非礼物弹幕在 `gift` 策略下不打断；`off` 策略下礼物也不打断。

空跑验证：`POST /api/session/start {"dry_run": true}` 后发模拟弹幕，
只演示过滤、排队和叠层，不调用 Vidu。

## 未实测（必须先说清楚）

- **真 Vidu 付费链路未实测**：本机没有你的 Vidu API Key，没有跑过
  真会话下的换背景/道具、打断信令回执、换图失败回滚。空跑和本地
  模拟 Vidu 通过，不等于真模型不走样。
- **付费小额实测清单**（拿到 Key 后按这个顺序跑，任何一步走样就停）：
  1. 单次换背景，确认衣服不变；
  2. 换装后再换背景，确认新衣服仍在；
  3. 连续换背景两次（中间不复原），确认第二张后衣服仍正常——
     若走样，立即复原并把 `visual_edit_max_consecutive` 降到 1；
  4. 拿道具、放下道具、再换背景；
  5. 礼物弹幕打断一次，确认打断后能接回上一句且不重复开头。
- **成本**：按上游 README 的 Vidu 价格，每小时约 169 元；
  上述实测建议单场预算先设小值（`safety.budget_credits`），并用
  `max_session_minutes` 兜底。
- **平台合规**：叠层里的“AI 实时生成画面”标识请保留；抖音等平台
  的数字人直播规则、弹幕官方接口申请不在本轮范围内。

## 启动（沿用上游）

```bash
cp config.example.json config.json   # 示例已开启安全换景 + gift 打断
.venv/bin/python server.py           # http://127.0.0.1:28900/workbench
```

`config.json`、密钥文件、个人角色资产不要提交（见 `.gitignore`）。
