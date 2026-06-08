---
name: translate-video
description: >
  Translate English YouTube videos to Chinese with subtitles and AI dubbing.
  Downloads video, restructures fragmented captions into natural sentences,
  detects multiple speakers, translates via LLM, generates Chinese TTS dubbing,
  and composes the final video. Supports checkpoint/resume.
triggers:
  - translate video
  - translate youtube
  - youtube translate
  - 翻译视频
  - 翻译YouTube
  - youtube翻译
  - 视频翻译
  - dub video
  - 视频配音
  - /translate-video
platforms:
  - claude-code
  - codex
  - cursor
  - opencode
  - gemini-cli
  - kiro
---

# YouTube Video Translator Skill

Translate English YouTube videos to Chinese (subtitles + AI dubbing).

## Prerequisites

This skill requires the youtube-translator tool to be installed. If not installed:

```bash
pip install -r requirements.txt
```

Also requires: ffmpeg, yt-dlp, Node.js

## Parameters

Extract from user message:
- `URL` (required): YouTube video URL
- `MODE`: Output mode — `subtitle`, `dub`, or `both` (default: `both`)
- `OUTPUT_DIR`: Output directory (default: `./yt_output`)
- `TTS_ENGINE`: TTS engine — `edge` (default, free) or `cosyvoice` (voice cloning)

## Workflow

### Step 1: Locate the tool

Find `translate_video.py` in the project:

```bash
# Check common locations
TOOL_DIR=""
for dir in "." "./youtube-translator" "../youtube-translator" "$HOME/youtube-translator"; do
  if [ -f "$dir/translate_video.py" ]; then
    TOOL_DIR="$dir"
    break
  fi
done

if [ -z "$TOOL_DIR" ]; then
  echo "youtube-translator not found. Please install:"
  echo "  git clone https://github.com/EyanJin/youtube-translator.git"
  echo "  cd youtube-translator && pip install -r requirements.txt"
  exit 1
fi
```

### Step 2: Check configuration

Verify `config.yaml` exists in the tool directory:

```bash
if [ ! -f "$TOOL_DIR/config.yaml" ]; then
  echo "config.yaml not found. Creating from template..."
  cp "$TOOL_DIR/config.example.yaml" "$TOOL_DIR/config.yaml"
  echo ""
  echo "Please edit $TOOL_DIR/config.yaml and add your API keys:"
  echo "  - translate.api_key (required) — get one at https://platform.deepseek.com"
  echo "  - whisper.api_key (optional) — free at https://console.groq.com"
  exit 1
fi
```

### Step 3: Set up proxy (if needed)

On systems that need a proxy to access YouTube:

```bash
# Only set if not already configured and proxy is in config
if [ -z "$http_proxy" ]; then
  # Let the tool handle proxy from config.yaml
  :
fi
```

### Step 4: Run translation

```bash
cd "$TOOL_DIR"
python translate_video.py "{{URL}}" --mode {{MODE}} -o "{{OUTPUT_DIR}}" {{EXTRA_FLAGS}}
```

Where `EXTRA_FLAGS` can include:
- `--tts-engine cosyvoice` if user requests voice cloning
- `--soft-sub` if user requests toggleable subtitles
- `-y` to skip confirmation

### Step 5: Report results

After completion, report to the user:
1. List all generated output files with their full paths
2. Indicate which files to watch:
   - `*_中文字幕.mp4` — subtitle version (original audio + Chinese subtitles)
   - `*_中文配音.mp4` — dubbed version (Chinese AI voice + subtitles)
3. Remind the user:
   - The `.zh.srt` file can be manually edited and re-run to regenerate video
   - If interrupted, re-running the same command resumes from checkpoint
   - Glossary terms can be added in `config.yaml` under `glossary.terms`

## Error Handling

- If ffmpeg/yt-dlp not found: suggest installation commands for the user's platform
- If API key missing: point user to config.yaml and provide signup links
- If network error: suggest checking proxy settings
- If interrupted: reassure user that re-running will resume from last checkpoint

## Examples

User: "translate this video https://www.youtube.com/watch?v=abc123"
→ Run with mode=both, default output

User: "帮我把这个视频翻译成中文字幕版 https://youtube.com/watch?v=xyz"
→ Run with mode=subtitle

User: "translate and dub this https://youtube.com/watch?v=xyz to ./output"
→ Run with mode=both, output_dir=./output

User: "用声音克隆配音这个视频 https://youtube.com/watch?v=xyz"
→ Run with mode=dub, tts_engine=cosyvoice
