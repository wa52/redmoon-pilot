# AI Anime Studio — 《从红月开始》制作 Agent

本仓库包含首个项目的制作 Bible、90 秒开场分镜、可在 OpenCode 中直接调用的项目级 Agent，以及 RM-009～RM-018 的静帧/Animatic 原型工具。**这些 Agent 是 OpenCode 项目级 Agent，不是常驻后台服务。** 已生成十张 ComfyUI 静帧和 36 秒 CPU 摄像机运动 Animatic，并带程序化 ambience/Foley/音乐粗混；没有本地 I2V 视频扩散，也没有正式 TTS/演员对白。

## 快速开始

1. 安装 OpenCode，并在本机完成可用模型 Provider 的登录/配置。
2. **先把当前目录切到本仓库根目录**；从工作区父目录进入时，在 PowerShell 执行：

   ```powershell
   Set-Location .\redmoon-pilot
   ```

   若仓库克隆到了其他位置，则进入那个克隆目录。OpenCode 只会读取当前 worktree 的 `.opencode/agents/`；在父目录启动时，`showrunner` 等项目 Agent 不会出现。更改 Agent 文件后，重启已有 OpenCode 会话。
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

当前配套分镜是第一章《回家》的 25 镜开场提案，时间码合计 90 秒。生成用暂存文件在 `assets/generated/`（已忽略）；审核后可用打包脚本复制到版本化的 shot/animatic/audio/review 目录。生成图是 `CREATIVE_PROPOSAL`，不代表原著人物外形定稿。

## Phase 2：RM-009～RM-018 生产原型

先为十个镜头渲染 1024×576 静帧，再用 CPU Ken Burns 相机运动合成 **36 秒 Animatic** 和原创程序化环境声/简短主题动机。当前 GPU VRAM 在 8 GB 档以下，不运行本地视频扩散。角色五官/发型/部分服装仍是 `CREATIVE_PROPOSAL`，关键帧在人工确认前不能当作 approved model sheet。

### 依赖

- Python 3.10+。
- `Pillow`（生成 contact sheet/相机运动帧）；`imageio-ffmpeg` 用于在 Windows 上取得 FFmpeg 可执行文件路径。安装命令：`python -m pip install Pillow imageio-ffmpeg`。
- 已启动、可从本机回环地址访问的 ComfyUI API。
- ComfyUI 核心节点及 ComfyUI-GGUF 提供的 `CLIPLoaderGGUF`。
- ComfyUI 模型库中的 `z_image_turbo_bf16.safetensors`、`Qwen3-4B-UD-Q6_K_XL.gguf` 和 `z-image-ae.safetensors`。脚本会在提交前检查 node/model 选项；缺项时停止，不会擅自下载。
- 环境变量 `COMFYUI_API_URL` 指向该 ComfyUI 的 loopback API；本机当前原型实例在 8190 端口。脚本拒绝非 loopback API。无需 API Key 或 Comfy credits。
- 环境变量 `FFMPEG_EXE` 指向 FFmpeg 可执行文件。可从已安装的 `imageio-ffmpeg` 查询路径：`$env:FFMPEG_EXE = (python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")`。

### 运行

在仓库根目录的 PowerShell 中：

```powershell
$env:COMFYUI_API_URL = 'http://127.0.0.1:8190'
python scripts/render_styleframes.py --dry-run --shot RM-009
python scripts/render_styleframes.py --shot RM-009
```

单镜确认后批量生成还缺的静帧；默认不覆盖已有图像：

```powershell
python scripts/render_styleframes.py
```

图像和哈希/提示词/seed/Comfy job ID 写入 `assets/generated/styleframes/`。完成十张后，检查联系表再构建 Animatic 与初版声音：

```powershell
$env:FFMPEG_EXE = (python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
python scripts/build_animatic.py --contact-sheet-only
python scripts/build_animatic.py
```

当前实际运行已生成 10 张 1024×576 style frame、36 秒/24fps（864 帧）H.264 Animatic、WAV 环境/音乐/Foley/mix stems、联系表和来源 manifest，暂存在 `assets/generated/`。程序化声音没有人声，也不是最终配乐/混音；画面运动是静帧推拉，不是 I2V。

本地审看后，将当前 draft 按镜头打包成生产目录：

```powershell
python scripts/package_prototype.py --dry-run
python scripts/package_prototype.py
```

打包结果位于 `shots/RM-009..RM-018/`、`animatic/`、`audio/RM-009-RM-018-v01/` 和 `reviews/`，包括关键帧、提示词/模型/Comfy job provenance、36 秒动画草稿、WAV stems 与审查状态。当前只生成逐镜静帧，没有单镜 I2V 视频；角色跨镜身份一致性为 `NEEDS_REVIEW`。音频经过完整解码，程序化粗混测得 `-23.8 LUFS`、`-6.9 dBFS true peak`；它没有可辨对白，仍需人耳连续审听，审美状态是 `UNVERIFIED`。正式成片不能沿用这些状态。

## 原文来源与仓库边界

小说 TXT 的用户副本不纳入此仓库：文件较大、来源/授权未认证。制作文档记录了副本 SHA-256 和章节锚点；完整核对需要用户在本地工作区提供该文件。副本内容不得因为 GitHub 上传而自动加入版本控制。现有来源状态 `TEXT_CONFIRMED_IN_COPY` 不等于 `VERIFIED`。

公开发行、商业化或系统性改编前，需解决改编授权与音频/图像素材权利。当前 Visual Bible、Voice Bible 方向、分镜和音频 cue 均是制作提案，不是授权声明。

## 验证

- `opencode agent list`：OpenCode CLI 能否发现项目 Agent。
- 时间码检查：`docs/PILOT_90S_STORYBOARD.md` 的镜头从 00:00 连续到 01:30。
- Audio Cue ID 检查：分镜中每个 cue ID 都在 `docs/AUDIO_BIBLE.md` 登记。
- 生成与试听：本仓库未提供自动运行测试；TTS/ComfyUI/母带 QA 在各渲染运行时接入后单独执行。
