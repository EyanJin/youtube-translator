# YouTube Video Translator

[English](#english) | [中文](#中文)

---

## English

Automatically translate English YouTube videos into Chinese — with subtitles and AI-powered dubbing.

### Features

- Download YouTube videos and subtitles via yt-dlp
- Restructure fragmented auto-captions into natural sentences using LLM
- Multi-speaker detection with gender-aware voice assignment
- LLM translation (DeepSeek, Qwen, GPT, or any OpenAI-compatible API)
- Free Chinese TTS via Microsoft Edge TTS (or CosyVoice for voice cloning)
- Hard-burned or soft subtitles
- Checkpoint system — resume from where you left off if interrupted
- Cross-platform (Windows / macOS / Linux)

### How It Works

```
YouTube URL
  → yt-dlp: download video + English subtitles
  → Fix overlapping subtitle timestamps
  → LLM: restructure fragments into complete sentences + identify speakers
  → LLM: translate to Chinese
  → Split long subtitles for display
  → Edge TTS: generate multi-voice Chinese dubbing
  → ffmpeg: compose final video (subtitles + dubbed audio)
```

### Requirements

- Python 3.9+
- ffmpeg
- yt-dlp
- Node.js (required by yt-dlp)
- Network proxy (for YouTube access, if needed in your region)

### Quick Start

**Windows:**
```bash
git clone https://github.com/EyanJin/youtube-translator.git
cd youtube-translator
setup_windows.bat
```

**macOS:**
```bash
git clone https://github.com/EyanJin/youtube-translator.git
cd youtube-translator
chmod +x setup_mac.sh && ./setup_mac.sh
```

**Manual setup (all platforms):**
```bash
git clone https://github.com/EyanJin/youtube-translator.git
cd youtube-translator
pip install -r requirements.txt
cp config.example.yaml config.yaml
# Edit config.yaml — add your API keys (see below)
```

### Where to Get API Keys

| Service | Purpose | Sign Up | Cost |
|---------|---------|---------|------|
| [DeepSeek](https://platform.deepseek.com) | Translation (recommended) | platform.deepseek.com | ~$0.001/video |
| [Groq](https://console.groq.com) | Speech recognition (Whisper) | console.groq.com | Free |
| [OpenAI](https://platform.openai.com) | Translation / Restructure | platform.openai.com | Pay-per-use |
| [SiliconFlow](https://cloud.siliconflow.cn) | CosyVoice (voice cloning) | cloud.siliconflow.cn | Optional |

Most videos cost less than $0.01 to process. Edge TTS (default dubbing engine) is completely free.

### Usage

```bash
# Subtitle + dubbing (default)
python translate_video.py "https://www.youtube.com/watch?v=xxx"

# Subtitle only (faster, no TTS)
python translate_video.py "https://www.youtube.com/watch?v=xxx" -m subtitle

# Dubbing only
python translate_video.py "https://www.youtube.com/watch?v=xxx" -m dub

# Use CosyVoice (voice cloning, more natural)
python translate_video.py "https://www.youtube.com/watch?v=xxx" --tts-engine cosyvoice

# Custom output directory
python translate_video.py "https://www.youtube.com/watch?v=xxx" -o ./my_output

# Soft subtitles (toggleable in player)
python translate_video.py "https://www.youtube.com/watch?v=xxx" --soft-sub

# Skip confirmation prompt
python translate_video.py "https://www.youtube.com/watch?v=xxx" -y
```

### Configuration

Copy `config.example.yaml` to `config.yaml` and fill in:

| Section | Required | Notes |
|---------|----------|-------|
| `translate.api_key` | Yes | Any OpenAI-compatible API (DeepSeek recommended) |
| `whisper.api_key` | Only if video has no subtitles | Groq offers free Whisper API |
| `tts.cosyvoice.api_key` | Only for voice cloning | SiliconFlow / Fish Audio |
| `proxy` | If needed | For YouTube access in restricted regions |

You can also use environment variables:
- `YT_TRANSLATE_API_KEY`, `YT_TRANSLATE_BASE_URL`, `YT_TRANSLATE_MODEL`
- `YT_WHISPER_API_KEY`, `YT_WHISPER_BASE_URL`
- `YT_TTS_VOICE`, `YT_PROXY`

### Output Files

After processing, you'll find these files in the output directory:

| File | What is it |
|------|------------|
| `video_中文字幕.mp4` | **Watch this** — original audio + Chinese subtitles |
| `video_中文配音.mp4` | **Watch this** — Chinese AI dubbing + subtitles |
| `video.mp4` | Original downloaded video |
| `video.en_restructured.srt` | Restructured English subtitles |
| `video_restructured.zh.srt` | Chinese translation (editable before re-run) |

Intermediate files (`.speakers.json`, `.speaker_traits.json`) are caches for the checkpoint system. They can be safely deleted to force regeneration.

### Checkpoint & Resume

If processing is interrupted (network error, Ctrl+C, etc.), simply re-run the same command. The tool automatically skips completed steps:

- Video download: cached
- Subtitle restructuring: cached (delete `*_restructured.srt` to regenerate)
- Translation: cached (delete `*.zh.srt` to regenerate with different model)
- Speaker detection: cached (delete `*.speaker_traits.json` to re-detect)

To change the translation model mid-process, delete the `.zh.srt` file and re-run.

### Limitations

- **Source language**: English only (other languages may produce poor results)
- **Target language**: Chinese only (hardcoded in prompts and TTS)
- **Input**: YouTube URLs only (local video files not supported yet)
- **Single video**: No playlist support (processes one video per command)
- **TTS quality**: AI-generated voices — natural-sounding but not human-perfect

### Use as an AI Coding Assistant Skill

This tool can also be used as a **skill** inside AI coding assistants (Claude Code, Codex, Cursor, Kiro, OpenCode, Gemini CLI, etc.). Instead of running commands manually, just tell your AI assistant:

> "translate this video https://www.youtube.com/watch?v=..."

**Installation as a skill:**

```bash
# Option 1: Copy SKILL.md to your project's skill directory
cp SKILL.md ~/.claude/commands/translate-video.md

# Option 2: Or just keep this repo cloned — the AI will find translate_video.py
```

Once installed, triggers like "翻译视频", "translate youtube", "视频配音" will activate the skill automatically.

See [SKILL.md](SKILL.md) for the full skill definition.

### License

MIT

---

## 中文

将英文 YouTube 视频自动翻译为中文字幕版和中文配音版。

### 功能特点

- 自动下载 YouTube 视频和字幕
- 智能重组碎片字幕为完整自然语句
- 自动识别多个说话人并分配不同音色（男声/女声/儿童）
- LLM 翻译（支持 DeepSeek、Qwen、GPT 等 OpenAI 兼容接口）
- Edge TTS 免费中文配音（或 CosyVoice 声音克隆）
- 硬字幕烧录或软字幕
- 断点续传——中断后重新运行自动从上次位置继续
- 跨平台支持（Windows / macOS / Linux）

### 处理流程

```
YouTube 链接
  → yt-dlp 下载视频 + 抓取英文字幕
  → 修复字幕时间重叠
  → LLM 重组碎片字幕为完整句子 + 识别说话人
  → LLM 翻译为中文
  → 长字幕拆分为适合显示的短行
  → Edge TTS 生成多音色中文配音
  → ffmpeg 合成最终视频
```

### 环境要求

- Python 3.9+
- ffmpeg
- yt-dlp
- Node.js（yt-dlp 需要）
- 网络代理（访问 YouTube）

### 快速开始

**Windows 用户：**
```bash
git clone https://github.com/EyanJin/youtube-translator.git
cd youtube-translator
setup_windows.bat
```

**macOS 用户：**
```bash
git clone https://github.com/EyanJin/youtube-translator.git
cd youtube-translator
chmod +x setup_mac.sh && ./setup_mac.sh
```

**手动安装：**
```bash
git clone https://github.com/EyanJin/youtube-translator.git
cd youtube-translator
pip install -r requirements.txt
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入 API 密钥
```

### 如何获取 API 密钥

| 服务 | 用途 | 注册地址 | 费用 |
|------|------|----------|------|
| [DeepSeek](https://platform.deepseek.com) | 翻译（推荐） | platform.deepseek.com | ~￥0.01/视频 |
| [Groq](https://console.groq.com) | 语音识别 Whisper | console.groq.com | 免费 |
| [OpenAI](https://platform.openai.com) | 翻译 / 字幕重组 | platform.openai.com | 按量付费 |
| [SiliconFlow](https://cloud.siliconflow.cn) | CosyVoice 声音克隆 | cloud.siliconflow.cn | 可选 |

大部分视频处理成本不到 ￥0.1。Edge TTS（默认配音引擎）完全免费。

### 使用方法

```bash
# 设置代理（如需要，改成你自己的代理地址）
export https_proxy=http://127.0.0.1:7890
export http_proxy=http://127.0.0.1:7890

# 生成字幕版 + 配音版（默认）
python translate_video.py "https://www.youtube.com/watch?v=视频ID"

# 仅字幕版（更快，不生成配音）
python translate_video.py "https://www.youtube.com/watch?v=视频ID" -m subtitle

# 仅配音版
python translate_video.py "https://www.youtube.com/watch?v=视频ID" -m dub

# 使用 CosyVoice 声音克隆（更自然）
python translate_video.py "https://www.youtube.com/watch?v=视频ID" --tts-engine cosyvoice

# 指定输出目录
python translate_video.py "https://www.youtube.com/watch?v=视频ID" -o ./我的输出

# 软字幕（播放器可开关）
python translate_video.py "https://www.youtube.com/watch?v=视频ID" --soft-sub
```

### 配置说明

将 `config.example.yaml` 复制为 `config.yaml` 并填入：

| 配置项 | 是否必需 | 说明 |
|--------|----------|------|
| `translate.api_key` | 是 | 任何 OpenAI 兼容 API（推荐 DeepSeek，便宜且中文好） |
| `whisper.api_key` | 仅视频无字幕时 | Groq 提供免费 Whisper API |
| `tts.cosyvoice.api_key` | 仅声音克隆时 | SiliconFlow / Fish Audio |
| `proxy` | 视情况 | 访问 YouTube 所需的代理地址 |

### 输出文件

处理完成后，输出目录中的文件：

| 文件 | 说明 |
|------|------|
| `视频名_中文字幕.mp4` | **看这个** — 原音频 + 中文硬字幕 |
| `视频名_中文配音.mp4` | **看这个** — 中文AI配音 + 字幕 |
| `视频名.mp4` | 下载的原始视频 |
| `视频名.en_restructured.srt` | 重组后的英文字幕 |
| `视频名_restructured.zh.srt` | 中文翻译（可手动编辑后重新合成） |

中间文件（`.speakers.json`、`.speaker_traits.json`）是断点续传的缓存，可安全删除以强制重新生成。

### 断点续传

如果处理中断（网络错误、Ctrl+C 等），直接重新运行同一命令即可。工具会自动跳过已完成的步骤：

- 视频下载：已缓存
- 字幕重组：已缓存（删除 `*_restructured.srt` 可强制重新生成）
- 翻译：已缓存（删除 `*.zh.srt` 可换模型重新翻译）
- 说话人识别：已缓存（删除 `*.speaker_traits.json` 可重新识别）

### 自定义配音音色

```yaml
tts:
  voice: "zh-CN-XiaoxiaoNeural"  # 默认女声
  rate: "+0%"    # 语速: "+10%" 加快, "-10%" 减慢
  volume: "+0%"  # 音量
```

可选音色：
- `zh-CN-XiaoxiaoNeural` — 女声，温柔（默认）
- `zh-CN-XiaoyiNeural` — 女声，活泼
- `zh-CN-YunxiNeural` — 男声，年轻
- `zh-CN-YunjianNeural` — 男声，沉稳
- `zh-CN-YunyangNeural` — 男声，新闻播报风格
- `zh-CN-YunxiaNeural` — 男声，少年

多说话人视频会自动分配不同音色，无需手动配置。

### 费用估算

| 环节 | 费用 |
|------|------|
| 视频下载 | 免费 |
| 字幕重组 + 翻译 | ~￥0.01-0.1/视频（DeepSeek） |
| 语音识别（如需要） | 免费（Groq） |
| 中文配音 | 免费（Edge TTS） |
| 视频合成 | 免费（ffmpeg） |

### 常见问题

**Q: yt-dlp 报错 403 / "not available"**
A: 确保代理已开启，且 yt-dlp 是最新版本。运行 `pip install -U yt-dlp` 更新。

**Q: 字幕翻译质量不好**
A: 尝试更换翻译模型。推荐 DeepSeek-V3 或 Qwen2.5-72B，中文质量最好。也可以在 `config.yaml` 的 `glossary.terms` 中添加专有名词对照表。

**Q: 配音音色不匹配**
A: 说话人性别由音频分析 + LLM 推断，可手动编辑 `.speaker_traits.json` 后删除配音缓存重新生成。

**Q: 视频没有自带字幕**
A: 工具会自动调用 Whisper API 进行语音识别。需要在 config.yaml 中配置 `whisper.api_key`（推荐 [Groq](https://console.groq.com) 免费）。

**Q: 中途中断了怎么办**
A: 直接重新运行同一命令，会自动跳过已完成的步骤。

**Q: 想修改翻译后重新生成视频**
A: 编辑 `*_restructured.zh.srt` 文件，然后重新运行命令，工具会使用修改后的翻译重新合成视频。

**Q: Windows 下 ffmpeg 找不到**
A: 运行 `winget install Gyan.FFmpeg` 安装，或从 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 下载后将 bin 目录加入系统 PATH。安装后重启终端。

### 已知限制

- **源语言**：仅支持英文视频（其他语言效果不佳）
- **目标语言**：仅支持翻译为中文（提示词和TTS均为中文定制）
- **输入源**：仅支持 YouTube 链接（暂不支持本地视频文件）
- **单视频处理**：不支持播放列表（每次处理一个视频）
- **配音质量**：AI 合成语音——流畅自然但非真人

### 作为 AI 编程助手的 Skill 使用

本工具也可以作为 **Skill** 安装到 AI 编程助手中（Claude Code、Codex、Cursor、Kiro、OpenCode、Gemini CLI 等）。安装后无需手动输入命令，直接对 AI 说：

> "翻译这个视频 https://www.youtube.com/watch?v=..."

**安装为 Skill：**

```bash
# 方式1：复制 SKILL.md 到你的 AI 助手的命令目录
cp SKILL.md ~/.claude/commands/translate-video.md

# 方式2：保持本仓库克隆在本地，AI 会自动找到 translate_video.py
```

安装后，"翻译视频"、"translate youtube"、"视频配音"等触发词会自动激活。

详见 [SKILL.md](SKILL.md)。

### 许可证

MIT
