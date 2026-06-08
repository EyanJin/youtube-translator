# YouTube Video Translator

English YouTube videos → Chinese subtitles + AI dubbing. One command. Near-zero cost.

[English](#features) | [中文](#中文文档)

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.9+-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)

---

## Two Ways to Use

### As an AI Skill (Recommended)

If you use Claude Code, Codex, Cursor, Kiro, or other AI coding assistants — just say:

> "翻译这个视频 https://www.youtube.com/watch?v=..."

> "translate this YouTube video https://www.youtube.com/watch?v=..."

The AI handles everything: proxy, config, execution, error recovery. No commands to memorize.

**Install the skill:**
```bash
git clone https://github.com/EyanJin/youtube-translator.git
cd youtube-translator
pip install -r requirements.txt
cp config.example.yaml config.yaml  # add your API key
```

Then just talk to your AI assistant. It reads [SKILL.md](SKILL.md) and knows what to do.

Supported platforms: Claude Code, Codex, Cursor, Kiro, OpenCode, Gemini CLI, and any tool that supports Agent Skills.

---

### As a CLI Tool

```bash
python translate_video.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

Output: two video files ready to watch.
- `video_中文字幕.mp4` — original audio + Chinese subtitles
- `video_中文配音.mp4` — Chinese AI dubbing + subtitles

---

## Why This Tool

Most translation tools give you **raw machine translation of fragmented auto-captions**. The result is unreadable.

This tool does more:

| Step | What it does | Why it matters |
|------|--------------|----------------|
| Restructure | Merges fragmented captions into complete sentences | YouTube auto-subs break mid-word. We fix that first. |
| Speaker detection | Identifies who is speaking and their gender | Different speakers get different voices in dubbing. |
| Translation | LLM translates complete sentences, not fragments | Complete context = better translation quality. |
| Dubbing | Multi-voice TTS with timing alignment | Each speaker has their own voice. Pauses feel natural. |
| Checkpoint | Caches every step | Interrupted? Re-run and it picks up where it left off. |

**Cost: ~$0.01 per video.** Edge TTS is free. Groq Whisper is free. Only LLM translation costs money (pennies with DeepSeek).

---

## Features

- Restructure fragmented auto-captions into natural sentences via LLM
- Multi-speaker detection with gender-aware voice assignment (male/female/child)
- LLM translation (DeepSeek, Qwen, GPT, or any OpenAI-compatible API)
- Free Chinese TTS via Edge TTS — or CosyVoice for voice cloning
- Hard-burned or soft subtitles
- Checkpoint system — resume from where you left off
- Cross-platform (Windows / macOS / Linux)
- Usable as an AI coding assistant skill

## Processing Pipeline

```
YouTube URL
  → Download video + English subtitles (yt-dlp)
  → Fix overlapping timestamps
  → Restructure fragments into sentences + identify speakers (LLM)
  → Translate to Chinese (LLM)
  → Generate multi-voice Chinese dubbing (Edge TTS)
  → Compose final video with subtitles (ffmpeg)
```

---

## Quick Start

### Requirements

- Python 3.9+
- ffmpeg (video processing)
- yt-dlp (YouTube download)
- Node.js (required by yt-dlp)

### Installation

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

**Manual (all platforms):**
```bash
git clone https://github.com/EyanJin/youtube-translator.git
cd youtube-translator
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

### Get API Keys

| Service | Purpose | Where to sign up | Cost |
|---------|---------|------------------|------|
| [DeepSeek](https://platform.deepseek.com) | Translation | platform.deepseek.com | ~$0.001/video |
| [Groq](https://console.groq.com) | Whisper (speech recognition) | console.groq.com | Free |
| [SiliconFlow](https://cloud.siliconflow.cn) | CosyVoice (voice cloning) | cloud.siliconflow.cn | Optional |

Edit `config.yaml` and add your `translate.api_key` — that's the only required key.

### Run

```bash
# Both subtitle + dubbing (default)
python translate_video.py "https://www.youtube.com/watch?v=xxx"

# Subtitle only (faster)
python translate_video.py "https://www.youtube.com/watch?v=xxx" -m subtitle

# Dubbing only
python translate_video.py "https://www.youtube.com/watch?v=xxx" -m dub

# Voice cloning (more natural, needs SiliconFlow API)
python translate_video.py "https://www.youtube.com/watch?v=xxx" --tts-engine cosyvoice

# Soft subtitles (toggleable in player)
python translate_video.py "https://www.youtube.com/watch?v=xxx" --soft-sub
```

---

## Configuration

Copy `config.example.yaml` to `config.yaml`. Key sections:

| Section | Required | Notes |
|---------|----------|-------|
| `translate.api_key` | Yes | Any OpenAI-compatible API |
| `whisper.api_key` | Only if video has no subtitles | Groq is free |
| `proxy` | If needed | For YouTube access in restricted regions |
| `glossary.terms` | Optional | Custom terminology (ensures consistency) |
| `tts.engine` | Optional | `edge` (free, default) or `cosyvoice` (cloning) |

Environment variables also work: `YT_TRANSLATE_API_KEY`, `YT_PROXY`, etc.

### Custom Voices

```yaml
tts:
  voice: "zh-CN-XiaoxiaoNeural"  # Default female
```

Available: XiaoxiaoNeural (female, gentle), XiaoyiNeural (female, lively), YunxiNeural (male, young), YunjianNeural (male, deep), YunyangNeural (male, news-anchor), YunxiaNeural (male, teen)

Multi-speaker videos auto-assign different voices — no manual config needed.

---

## Checkpoint & Resume

If interrupted, just re-run. The tool skips completed steps automatically.

To **force regeneration** of a specific step:
- Re-translate with different model: delete `*.zh.srt`
- Re-restructure subtitles: delete `*_restructured.srt`
- Re-detect speakers: delete `*.speaker_traits.json`

To **edit translations** before generating video: edit the `*.zh.srt` file, then re-run.

---

## Limitations

- Source: English videos only
- Target: Chinese only (prompts and TTS are Chinese-specific)
- Input: YouTube URLs only (no local files yet)
- Single video per command (no playlist support)

---

## FAQ

<details>
<summary><b>yt-dlp returns 403 / "not available"</b></summary>

Make sure your proxy is running and yt-dlp is up to date: `pip install -U yt-dlp`
</details>

<details>
<summary><b>Translation quality is poor</b></summary>

Try a better model. Recommended: DeepSeek-V3 or Qwen2.5-72B for Chinese. You can also add a glossary in `config.yaml` for domain-specific terms.
</details>

<details>
<summary><b>Video has no subtitles</b></summary>

The tool auto-detects this and uses Whisper for speech recognition. Configure `whisper.api_key` in config.yaml — [Groq](https://console.groq.com) offers it free.
</details>

<details>
<summary><b>Speaker voices don't match</b></summary>

Gender is detected via audio analysis + LLM inference. To override: edit `.speaker_traits.json` and re-run.
</details>

<details>
<summary><b>ffmpeg not found on Windows</b></summary>

Run `winget install Gyan.FFmpeg`, or download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add the `bin/` folder to your system PATH. Restart your terminal.
</details>

---

## License

MIT

---

---

## 中文文档

将英文 YouTube 视频自动翻译为中文字幕版和中文配音版。

### 两种使用方式

**方式一：作为 AI 助手的 Skill（推荐）**

如果你使用 Claude Code、Codex、Cursor、Kiro 等 AI 编程助手，直接说：

> "翻译这个视频 https://www.youtube.com/watch?v=..."

AI 自动处理所有细节。无需记命令。详见 [SKILL.md](SKILL.md)。

**方式二：命令行直接运行**

```bash
python translate_video.py "https://www.youtube.com/watch?v=视频ID"
```

### 为什么用这个工具

大多数翻译工具直接翻译 YouTube 自动生成的碎片字幕，结果不通顺。

本工具先重组字幕为完整句子，再翻译完整语义，质量远超直接翻译碎片。同时自动识别多个说话人，分配不同音色配音。

**费用：约 ￥0.01-0.1/视频。** Edge TTS 配音免费，Groq 语音识别免费，只有 LLM 翻译花几分钱。

### 快速开始

**Windows：**
```bash
git clone https://github.com/EyanJin/youtube-translator.git
cd youtube-translator
setup_windows.bat
```

**macOS：**
```bash
git clone https://github.com/EyanJin/youtube-translator.git
cd youtube-translator
chmod +x setup_mac.sh && ./setup_mac.sh
```

### 获取 API 密钥

| 服务 | 用途 | 注册地址 | 费用 |
|------|------|----------|------|
| [DeepSeek](https://platform.deepseek.com) | 翻译（推荐） | platform.deepseek.com | ~￥0.01/视频 |
| [Groq](https://console.groq.com) | 语音识别 | console.groq.com | 免费 |
| [SiliconFlow](https://cloud.siliconflow.cn) | 声音克隆（可选） | cloud.siliconflow.cn | 可选 |

编辑 `config.yaml`，填入 `translate.api_key` 即可开始使用。

### 使用示例

```bash
# 设置代理（如需要）
export https_proxy=http://127.0.0.1:7890
export http_proxy=http://127.0.0.1:7890

# 字幕 + 配音（默认）
python translate_video.py "https://www.youtube.com/watch?v=视频ID"

# 仅字幕（更快）
python translate_video.py "https://www.youtube.com/watch?v=视频ID" -m subtitle

# 仅配音
python translate_video.py "https://www.youtube.com/watch?v=视频ID" -m dub

# 声音克隆配音（更自然）
python translate_video.py "https://www.youtube.com/watch?v=视频ID" --tts-engine cosyvoice
```

### 输出文件

| 文件 | 说明 |
|------|------|
| `视频名_中文字幕.mp4` | **看这个** — 原音频 + 中文字幕 |
| `视频名_中文配音.mp4` | **看这个** — 中文AI配音 + 字幕 |
| `*_restructured.zh.srt` | 中文翻译（可编辑后重新生成视频） |

### 断点续传

中断后重新运行同一命令即可，自动跳过已完成步骤。

想换翻译模型？删除 `*.zh.srt` 后重新运行。
想手动润色？编辑 `*.zh.srt` 后重新运行。

### 自定义音色

```yaml
tts:
  voice: "zh-CN-XiaoxiaoNeural"  # 默认女声
```

可选：XiaoxiaoNeural（温柔女声）、XiaoyiNeural（活泼女声）、YunxiNeural（年轻男声）、YunjianNeural（沉稳男声）、YunyangNeural（播报男声）、YunxiaNeural（少年）

多说话人视频自动分配不同音色。

### 已知限制

- 仅支持英文视频 → 中文
- 仅支持 YouTube 链接（暂不支持本地文件）
- 每次处理一个视频（不支持播放列表）

### 常见问题

**yt-dlp 报 403：** 确保代理已开启，运行 `pip install -U yt-dlp` 更新。

**翻译质量不好：** 换用 DeepSeek-V3 或 Qwen2.5-72B。也可在 `config.yaml` 的 `glossary.terms` 添加术语表。

**视频无字幕：** 配置 `whisper.api_key`（[Groq](https://console.groq.com) 免费）。

**Windows 找不到 ffmpeg：** 运行 `winget install Gyan.FFmpeg`，重启终端。

### 许可证

MIT
