"""Speech-to-text transcription using Whisper API (OpenAI-compatible)."""

import os
import tempfile
from pathlib import Path
from openai import OpenAI


# Whisper API has a 25MB file size limit
MAX_FILE_SIZE_MB = 25


def transcribe(audio_path, config):
    """Transcribe audio to SRT subtitles using Whisper API.

    Args:
        audio_path: Path to audio file (wav/mp3/m4a).
        config: Whisper config dict with base_url, api_key, model.

    Returns:
        Path to the generated SRT file.
    """
    audio_path = Path(audio_path)
    srt_path = audio_path.with_suffix(".srt")

    client = OpenAI(
        base_url=config["base_url"],
        api_key=config["api_key"],
    )

    file_size_mb = audio_path.stat().st_size / (1024 * 1024)

    if file_size_mb > MAX_FILE_SIZE_MB:
        print(f"[识别] 音频文件 {file_size_mb:.1f}MB 超过 {MAX_FILE_SIZE_MB}MB 限制，正在分段处理...")
        srt_content = _transcribe_chunked(audio_path, client, config["model"])
    else:
        print(f"[识别] 正在识别语音 ({file_size_mb:.1f}MB)...")
        srt_content = _transcribe_single(audio_path, client, config["model"])

    srt_path.write_text(srt_content, encoding="utf-8")
    print(f"[识别] 字幕已生成: {srt_path.name}")
    return srt_path


def _transcribe_single(audio_path, client, model):
    """Transcribe a single audio file."""
    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model=model,
            file=f,
            response_format="srt",
            language="en",
        )
    return response


def _transcribe_chunked(audio_path, client, model):
    """Split audio into chunks and transcribe each, then merge SRT."""
    from pydub import AudioSegment

    audio = AudioSegment.from_file(str(audio_path))
    chunk_duration_ms = 10 * 60 * 1000  # 10 minutes per chunk
    chunks = []

    for i in range(0, len(audio), chunk_duration_ms):
        chunk = audio[i:i + chunk_duration_ms]
        chunks.append((i, chunk))

    all_srt_lines = []
    subtitle_index = 1

    for chunk_offset_ms, chunk in chunks:
        # Export chunk to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            chunk.export(tmp.name, format="wav")
            tmp_path = tmp.name

        try:
            print(f"[识别] 处理分段 {chunk_offset_ms // 60000}-{(chunk_offset_ms + len(chunk)) // 60000} 分钟...")
            with open(tmp_path, "rb") as f:
                srt_text = client.audio.transcriptions.create(
                    model=model,
                    file=f,
                    response_format="srt",
                    language="en",
                )

            # Adjust timestamps and re-index
            adjusted = _adjust_srt_timestamps(srt_text, chunk_offset_ms, subtitle_index)
            all_srt_lines.append(adjusted["text"])
            subtitle_index = adjusted["next_index"]
        finally:
            os.unlink(tmp_path)

    return "\n".join(all_srt_lines)


def _adjust_srt_timestamps(srt_text, offset_ms, start_index):
    """Adjust SRT timestamps by adding an offset and re-index entries."""
    import re

    lines = srt_text.strip().split("\n")
    result_lines = []
    current_index = start_index
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines
        if not line:
            result_lines.append("")
            i += 1
            continue

        # Check if this is an index line (number only)
        if line.isdigit():
            result_lines.append(str(current_index))
            current_index += 1
            i += 1
            continue

        # Check if this is a timestamp line
        ts_match = re.match(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", line
        )
        if ts_match:
            start = _add_ms_to_timestamp(ts_match.group(1), offset_ms)
            end = _add_ms_to_timestamp(ts_match.group(2), offset_ms)
            result_lines.append(f"{start} --> {end}")
            i += 1
            continue

        # Text line
        result_lines.append(line)
        i += 1

    return {"text": "\n".join(result_lines), "next_index": current_index}


def _add_ms_to_timestamp(ts_str, offset_ms):
    """Add milliseconds to an SRT timestamp string."""
    h, m, rest = ts_str.split(":")
    s, ms = rest.split(",")
    total_ms = int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms) + offset_ms

    new_h = total_ms // 3600000
    total_ms %= 3600000
    new_m = total_ms // 60000
    total_ms %= 60000
    new_s = total_ms // 1000
    new_ms = total_ms % 1000

    return f"{new_h:02d}:{new_m:02d}:{new_s:02d},{new_ms:03d}"
