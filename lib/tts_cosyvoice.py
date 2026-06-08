"""CosyVoice cloud API TTS engine for cross-lingual voice cloning.

Supports:
- SiliconFlow (硅基流动): CosyVoice2-0.5B with reference audio cloning
- Fish Audio: (placeholder for future integration)

Usage:
    dubbed_audio = generate_dubbed_audio_cosyvoice(
        cn_srt_path, duration, tts_config,
        video_path=video_path,
        en_srt_path=en_srt_path,
        speaker_map=speaker_map,
    )
"""

import base64
import subprocess
import tempfile
import time
from pathlib import Path

import pysubs2
import requests

from lib.subtitle_processor import merge_for_tts
from lib.tts_common import compose_audio_track, get_audio_duration_ms, cleanup_temp_files


# Default API endpoints per provider
PROVIDER_DEFAULTS = {
    "siliconflow": {
        "api_url": "https://api.siliconflow.cn/v1/audio/speech",
        "model": "FunAudioLLM/CosyVoice2-0.5B",
    },
}

# Max reference audio duration (seconds)
MAX_REF_DURATION_S = 10
# Min reference audio duration (seconds)
MIN_REF_DURATION_S = 3


def generate_dubbed_audio_cosyvoice(cn_srt_path, original_duration_s, tts_config,
                                     video_path=None, en_srt_path=None,
                                     speaker_map=None):
    """Generate Chinese dubbed audio using CosyVoice voice cloning.

    Args:
        cn_srt_path: Path to Chinese SRT file.
        original_duration_s: Original video duration in seconds.
        tts_config: TTS config dict from config.yaml.
        video_path: Path to original video (for extracting reference audio).
        en_srt_path: Path to restructured English SRT (for reference timing).
        speaker_map: Dict {subtitle_index: speaker_id}.

    Returns:
        Path to the generated dubbed audio WAV file.
    """
    cn_srt_path = Path(cn_srt_path)
    output_path = cn_srt_path.with_suffix(".dubbed.wav")

    # Parse config
    cosyvoice_config = tts_config.get("cosyvoice", {})
    provider = cosyvoice_config.get("provider", "siliconflow")
    api_key = cosyvoice_config.get("api_key", "")
    api_url = cosyvoice_config.get("api_url", "") or PROVIDER_DEFAULTS.get(provider, {}).get("api_url", "")
    model = cosyvoice_config.get("model", "") or PROVIDER_DEFAULTS.get(provider, {}).get("model", "")

    if not api_key:
        raise ValueError(f"CosyVoice API 密钥未配置 (tts.cosyvoice.api_key)")

    # Extract reference audio per speaker
    speaker_refs = {}
    if video_path and en_srt_path:
        speaker_refs = _extract_speaker_references(
            video_path, en_srt_path, speaker_map
        )
        print(f"[CosyVoice] 提取了 {len(speaker_refs)} 个说话人的参考音频")

    # Load Chinese subtitles and merge short segments for natural TTS
    subs = pysubs2.load(str(cn_srt_path))
    merged = merge_for_tts(subs, max_gap_ms=300, max_merge_duration_ms=10000,
                           speaker_map=speaker_map)
    print(f"[CosyVoice] {len(subs)} 条字幕合并为 {len(merged)} 个语音段")

    # Generate TTS for each segment
    segments = []
    for i, seg in enumerate(merged):
        text = seg["text"].strip()
        if not text:
            continue

        # Determine which speaker's reference to use
        ref_audio_b64 = None
        ref_text = None
        sub_idx = seg.get("sub_indices", [i])[0]
        speaker_id = seg.get("speaker", "default")

        if speaker_id in speaker_refs:
            ref_audio_b64 = speaker_refs[speaker_id]["audio_b64"]
            ref_text = speaker_refs[speaker_id]["text"]

        # Generate audio via API
        audio_path = _call_cosyvoice_api(
            text=text,
            api_url=api_url,
            api_key=api_key,
            model=model,
            ref_audio_b64=ref_audio_b64,
            ref_text=ref_text,
            output_dir=cn_srt_path.parent,
            segment_idx=i,
        )

        if audio_path is None:
            print(f"[CosyVoice] 第 {i+1} 段生成失败，跳过: {text[:30]}...")
            continue

        segments.append({
            "audio_path": str(audio_path),
            "start_ms": seg["start_ms"],
            "end_ms": seg["end_ms"],
            "target_duration_ms": seg["end_ms"] - seg["start_ms"],
            "text": text,
            "voice": speaker_id,
        })

        if (i + 1) % 5 == 0 or i == len(merged) - 1:
            print(f"[CosyVoice] 已生成 {i + 1}/{len(merged)} 段...")

    if not segments:
        raise ValueError("CosyVoice 没有生成任何有效的配音片段")

    print(f"[CosyVoice] TTS 生成完成，共 {len(segments)} 段有效音频")

    # Compose into timed audio track
    max_speed = tts_config.get("max_speed_ratio", 1.8)
    compose_audio_track(segments, original_duration_s, output_path, max_speed_ratio=max_speed)
    cleanup_temp_files(segments)

    # Clean up reference audio temp files
    for ref in speaker_refs.values():
        ref_path = ref.get("audio_path")
        if ref_path and Path(ref_path).exists():
            Path(ref_path).unlink(missing_ok=True)

    return str(output_path)


def _call_cosyvoice_api(text, api_url, api_key, model,
                         ref_audio_b64=None, ref_text=None,
                         output_dir=None, segment_idx=0):
    """Call CosyVoice API to generate speech for a single segment.

    Returns path to generated audio file, or None on failure.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "input": text,
        "response_format": "wav",
        "sample_rate": 44100,
    }

    # Use reference audio for voice cloning if available
    if ref_audio_b64 and ref_text:
        payload["references"] = [{
            "audio": f"data:audio/wav;base64,{ref_audio_b64}",
            "text": ref_text,
        }]
    else:
        # Fall back to a preset voice
        payload["voice"] = f"{model}:alex"

    output_path = Path(output_dir or ".") / f"cosyvoice_seg_{segment_idx:04d}.wav"

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Bypass system proxy for Chinese domestic APIs (SiliconFlow, etc.)
            response = requests.post(
                api_url, json=payload, headers=headers,
                timeout=60, stream=True,
                proxies={"http": None, "https": None},
            )

            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=4096):
                        if chunk:
                            f.write(chunk)
                return str(output_path)

            elif response.status_code == 429:
                # Rate limited — wait and retry
                wait = min(2 ** attempt * 2, 10)
                print(f"[CosyVoice] 速率限制，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue

            else:
                error_msg = response.text[:200] if response.text else "unknown"
                print(f"[CosyVoice] API 错误 {response.status_code}: {error_msg}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return None

        except requests.exceptions.Timeout:
            print(f"[CosyVoice] 请求超时 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            return None

        except Exception as e:
            print(f"[CosyVoice] 请求异常: {e}")
            return None

    return None


def _extract_speaker_references(video_path, en_srt_path, speaker_map):
    """Extract reference audio clips per speaker from the original video.

    For each speaker, finds their longest clean segments and extracts
    audio to use as voice cloning reference.

    Returns:
        Dict {speaker_id: {"audio_b64": str, "text": str, "audio_path": str}}
    """
    from collections import defaultdict

    video_path = Path(video_path)
    subs = pysubs2.load(str(en_srt_path))

    if not subs or not speaker_map:
        return {}

    # Group segments by speaker, sorted by duration (longest first)
    speaker_segments = defaultdict(list)
    for i, event in enumerate(subs):
        text = event.text.replace("\\N", "").replace("\n", "").strip()
        if not text or text.startswith("[") or text.startswith("("):
            continue

        speaker_id = "default"
        if i in speaker_map:
            sid = speaker_map[i]
            speaker_id = sid if isinstance(sid, str) else str(sid)

        duration_s = (event.end - event.start) / 1000.0
        speaker_segments[speaker_id].append({
            "start_ms": event.start,
            "end_ms": event.end,
            "duration_s": duration_s,
            "text": text,
        })

    # For each speaker, pick segments totaling 5-10s of audio
    speaker_refs = {}
    for speaker_id, segments in speaker_segments.items():
        # Sort by duration descending — prefer longer, cleaner segments
        segments.sort(key=lambda s: s["duration_s"], reverse=True)

        selected = []
        total_s = 0.0
        for seg in segments:
            if total_s >= MAX_REF_DURATION_S:
                break
            if seg["duration_s"] < 1.0:
                continue
            selected.append(seg)
            total_s += seg["duration_s"]

        if total_s < MIN_REF_DURATION_S or not selected:
            continue

        # Extract and concatenate selected audio segments
        ref_path = _extract_audio_segments(video_path, selected, speaker_id)
        if ref_path is None:
            continue

        # Read as base64
        with open(ref_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("utf-8")

        # Combine reference texts
        ref_text = " ".join(s["text"] for s in selected)

        speaker_refs[speaker_id] = {
            "audio_b64": audio_b64,
            "text": ref_text,
            "audio_path": ref_path,
        }

    return speaker_refs


def _extract_audio_segments(video_path, segments, speaker_id):
    """Extract and concatenate audio segments from video for a speaker.

    Returns path to the concatenated WAV file, or None on failure.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="cosyvoice_ref_"))
    segment_files = []

    for i, seg in enumerate(segments):
        start_s = seg["start_ms"] / 1000.0
        duration_s = seg["duration_s"]
        out_path = tmp_dir / f"ref_{speaker_id}_{i}.wav"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-ss", f"{start_s:.3f}",
            "-t", f"{duration_s:.3f}",
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "1",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and out_path.exists():
            segment_files.append(out_path)

    if not segment_files:
        return None

    # If only one segment, use it directly
    if len(segment_files) == 1:
        return str(segment_files[0])

    # Concatenate multiple segments
    concat_list = tmp_dir / "concat.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for sf in segment_files:
            f.write(f"file '{sf}'\n")

    output_path = tmp_dir / f"ref_{speaker_id}_combined.wav"
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "1",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0 and output_path.exists():
        # Clean up individual segments
        for sf in segment_files:
            sf.unlink(missing_ok=True)
        concat_list.unlink(missing_ok=True)
        return str(output_path)

    return str(segment_files[0]) if segment_files else None
