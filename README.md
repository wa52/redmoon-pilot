# AI Anime Studio — 《从红月开始》制作 Agent

本仓库包含首个项目的制作 Bible、90 秒开场分镜，以及可在 OpenCode 中直接调用的项目级 Agent 定义。**这些 Agent 是具备职责边界的 OpenCode Agent，不是已部署的后台服务或无人值守渲染系统。** TTS、SFX 生成、音乐制作和 ComfyUI 自动渲染仍需接入/审核。

## 快速开始

1. 安装 OpenCode，并在本机完成可用模型 Provider 的登录/配置。
2. 在本目录启动 OpenCode；更改 Agent 文件后，重启已有 OpenCode 会话。
3. 检查 Agent 已载入：

   ```powershell
   opencode agent list
   ```

4. 将 `showrunner` 选作主 Agent：

   ```powershell
   opencode --agent showrunner
   ```

5. 在 Showrunner 会话中按需调用领域 Agent，例如 `novel-analyst`、`animation-director`、`visual-director`、`audio-director`、`production-coordinator`、`continuity-qa` 和 `audio-qa`。Audio Director 可再调度 `dialogue-agent` / `sfx-agent` / `music-director` / `mix-agent`；Subagent 也可用 OpenCode 的 `@agent-name` 方式单独调用。

当前配置不固定模型 Provider 或模型 ID；Subagent 默认继承调用它的主 Agent 模型。项目启动要求只有 OpenCode 与其已配置的模型 Provider。渲染时才需要用户的 ComfyUI/MCP、工作流、模型和本地资源；生成音频时还需另行选择 TTS/录音、音效和音乐工具。本仓库没有安装这些运行时，也没有写入 API Key。

## Agent 目录

| Agent | 模式 | 工作 |
|---|---|---|
| `showrunner` | Primary | 编排来源核对、编剧、导演、视听制作与审片流程 |
| `novel-analyst` | Subagent | 提取章节事实、视点、设定差异与来源锚点 |
| `screenwriter` | Subagent | 将核准情节整理为场次/改编台词，不把提案当原著 |
| `animation-director` | Subagent | 场次拆镜、时长、镜头调度和连续性 |
| `visual-director` | Subagent | 视觉体系、人物/场景资产、关键帧与画面审片 |
| `audio-director` | Subagent | 统筹对白/配音、SFX、主题动机、混音和音频 QA |
| `dialogue-agent` | Subagent | 已批准台词、角色声音指导、发音词典与对齐资料 |
| `sfx-agent` | Subagent | 环境声、Foley、音效 ID、来源与空间视点 |
| `music-director` | Subagent | Leitmotif、连续配乐 cue、变奏和对白留白 |
| `mix-agent` | Subagent | 总线、自动化、stem、母带规格和混音记录 |
| `production-coordinator` | Subagent | ComfyUI 工作流前置检查、任务状态和资产版本 |
| `continuity-qa` | Subagent | 故事、角色、场景、视点、画面/声音连续性只读审核 |
| `audio-qa` | Subagent | 发音、音频连续性、同步、技术与主观试听只读审核 |

详见 `.opencode/agents/`。QA Agent 只报告证据与问题，不自行改制作稿或给未试听/未观看的内容盖 PASS。Showrunner 对任务默认限制为上表中的项目 Agent。

## 制作资料

- `docs/WORLD_BIBLE.md`
- `docs/CHARACTER_BIBLE.md`
- `docs/VISUAL_BIBLE.md`
- `docs/AUDIO_BIBLE.md`
- `docs/PILOT_90S_STORYBOARD.md`

当前配套分镜是第一章《回家》的 25 镜开场提案，时间码合计 90 秒。时间/Agent 定义有静态检查，但尚无成片或最终审片。

## 原文来源与仓库边界

小说 TXT 的用户副本不纳入此仓库：文件较大、来源/授权未认证。制作文档记录了副本 SHA-256 和章节锚点；完整核对需要用户在本地工作区提供该文件。副本内容不得因为 GitHub 上传而自动加入版本控制。现有来源状态 `TEXT_CONFIRMED_IN_COPY` 不等于 `VERIFIED`。

公开发行、商业化或系统性改编前，需解决改编授权与音频/图像素材权利。当前 Visual Bible、Voice Bible 方向、分镜和音频 cue 均是制作提案，不是授权声明。

## 验证

- `opencode agent list`：OpenCode CLI 能否发现项目 Agent。
- 时间码检查：`docs/PILOT_90S_STORYBOARD.md` 的镜头从 00:00 连续到 01:30。
- Audio Cue ID 检查：分镜中每个 cue ID 都在 `docs/AUDIO_BIBLE.md` 登记。
- 生成与试听：本仓库未提供自动运行测试；TTS/ComfyUI/母带 QA 在各渲染运行时接入后单独执行。
