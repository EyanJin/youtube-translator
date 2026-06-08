"""Chinese TTS using Edge TTS with timing alignment and speaker support."""

import asyncio
import os
import re
import sys
import tempfile
import subprocess
from pathlib import Path
import edge_tts
import pysubs2

from lib.subtitle_processor import merge_for_tts
from lib.tts_common import compose_audio_track, get_audio_duration_ms, cleanup_temp_files


# Voice mapping by speaker traits (gender, age)
# Uses all 6 standard zh-CN voices for maximum differentiation
VOICE_MAP = {
    ("male", "child"):    "zh-CN-YunxiaNeural",       # 男童 → 可爱童声
    ("male", "young"):    "zh-CN-YunxiNeural",        # 青年男性 → 阳光活泼
    ("male", "adult"):    "zh-CN-YunjianNeural",      # 成年男性 → 热情沉稳
    ("male", "elder"):    "zh-CN-YunyangNeural",      # 年长男性 → 专业可靠
    ("female", "child"):  "zh-CN-XiaoyiNeural",       # 女童 → 活泼
    ("female", "young"):  "zh-CN-XiaoyiNeural",       # 青年女性 → 活泼
    ("female", "adult"):  "zh-CN-XiaoxiaoNeural",     # 成年女性 → 温柔
    ("female", "elder"):  "zh-CN-XiaoxiaoNeural",     # 年长女性 → 温柔
}

# Rate adjustments by age (applied on top of config rate)
AGE_RATE_ADJUST = {
    "child": "+8%",
    "young": "+0%",
    "adult": "+0%",
    "elder": "-5%",
}

# Extended voice pool for multi-speaker differentiation
# When multiple speakers share the same gender+age, cycle through these
VOICE_POOL_MALE = [
    "zh-CN-YunjianNeural",     # 男声，热情沉稳
    "zh-CN-YunxiNeural",       # 男声，阳光活泼
    "zh-CN-YunyangNeural",     # 男声，专业可靠
    "zh-CN-YunxiaNeural",      # 男声，可爱（偏年轻）
]

VOICE_POOL_FEMALE = [
    "zh-CN-XiaoxiaoNeural",    # 女声，温柔
    "zh-CN-XiaoyiNeural",      # 女声，活泼
]

# Legacy fallback pool (for when no speaker traits available)
VOICE_POOL = [
    "zh-CN-YunxiNeural",      # 男声，年轻活泼
    "zh-CN-XiaoxiaoNeural",   # 女声，温柔
    "zh-CN-YunjianNeural",    # 男声，沉稳
    "zh-CN-XiaoyiNeural",     # 女声，活泼
    "zh-CN-YunyangNeural",    # 男声，专业
    "zh-CN-YunxiaNeural",     # 男声，可爱
]

# No artificial gap — keeps audio synced with subtitles
SPEAKER_CHANGE_GAP_MS = 0


def _run_async(coro):
    """Run async coroutine, avoiding Windows event loop close warnings on Python 3.9."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)


def _extract_speaker_number(speaker_id):
    """Extract speaker number from ID like 'S1F' → 'S1', 'S2' → 'S2'."""
    match = re.match(r"(S\d+)", speaker_id.upper().strip())
    return match.group(1) if match else speaker_id


def generate_dubbed_audio(cn_srt_path, original_duration_s, config,
                          speaker_map=None, speaker_traits=None):
    """Generate Chinese audio track aligned to subtitle timings.

    Args:
        cn_srt_path: Path to Chinese SRT file.
        original_duration_s: Duration of original video in seconds.
        config: TTS config dict with voice, rate, volume.
        speaker_map: Optional dict {subtitle_index: speaker_id} from restructuring.
        speaker_traits: Optional dict {subtitle_index: {"id": "S1", "gender": "male", "age": "adult"}}
                        from speaker identification. Takes priority over speaker_map for voice selection.

    Returns:
        Path to the generated audio file (WAV).
    """
    cn_srt_path = Path(cn_srt_path)
    output_path = cn_srt_path.with_name(cn_srt_path.stem + "_dubbed.wav")

    subs = pysubs2.load(str(cn_srt_path))

    # Merge consecutive subtitles into speech segments for natural flow
    merged = merge_for_tts(
        subs, max_gap_ms=300, max_merge_duration_ms=10000,
        speaker_map=speaker_map,
    )
    print(f"[配音] {len(subs)} 条字幕合并为 {len(merged)} 个语音段...")

    # Resolve voice and rate for each segment based on speaker traits
    speaker_voice_cache = {}  # speaker_id → (voice, rate_adjust)
    voices = []
    rates = []
    base_rate = config.get("rate", "+0%")

    for seg in merged:
        raw_speaker = seg.get("speaker", "default")
        speaker_num = _extract_speaker_number(raw_speaker)

        if speaker_num not in speaker_voice_cache:
            voice, rate_adj = _resolve_voice_for_speaker(
                speaker_num, seg.get("sub_indices", []),
                speaker_traits, speaker_map, base_rate,
            )
            speaker_voice_cache[speaker_num] = (voice, rate_adj)

        voice, rate_adj = speaker_voice_cache[speaker_num]
        voices.append(voice)
        rates.append(rate_adj)

    if len(speaker_voice_cache) > 1:
        voice_summary = ", ".join(
            f"{k}→{v[0].replace('zh-CN-', '').replace('Neural', '')}"
            for k, v in speaker_voice_cache.items()
        )
        print(f"[配音] 音色分配: {voice_summary}")

    segments = _run_async(
        _generate_merged_segments(merged, voices, rates, config)
    )

    max_speed = config.get("max_speed_ratio", 1.8)
    compose_audio_track(segments, original_duration_s, output_path, max_speed_ratio=max_speed)
    cleanup_temp_files(segments)

    print(f"[配音] 中文音频已生成: {output_path.name}")
    return output_path


def _resolve_voice_for_speaker(speaker_num, sub_indices, speaker_traits,
                                speaker_map, base_rate):
    """Resolve TTS voice and rate for a speaker based on traits.

    When multiple speakers share the same gender, uses the speaker number
    to cycle through the extended voice pool for differentiation.

    Returns:
        Tuple of (voice_name, rate_string).
    """
    gender = None
    age = None

    # Try to get traits from speaker_traits (new format)
    if speaker_traits:
        for idx in sub_indices:
            trait = speaker_traits.get(idx)
            if trait and isinstance(trait, dict):
                gender = trait.get("gender")
                age = trait.get("age")
                break
        # If not found by sub_indices, search by speaker ID
        if gender is None:
            for idx, trait in speaker_traits.items():
                if isinstance(trait, dict) and _extract_speaker_number(trait.get("id", "")) == speaker_num:
                    gender = trait.get("gender")
                    age = trait.get("age")
                    break

    if gender and age:
        # Use speaker number to differentiate voices within the same gender
        num_match = re.search(r"\d+", speaker_num)
        speaker_idx = (int(num_match.group()) - 1) if num_match else 0

        # Pick from the gender-specific extended pool, cycling by speaker index
        if gender == "male":
            pool = VOICE_POOL_MALE
        else:
            pool = VOICE_POOL_FEMALE

        voice = pool[speaker_idx % len(pool)]
        rate_adj = _combine_rates(base_rate, AGE_RATE_ADJUST.get(age, "+0%"))
        return voice, rate_adj

    # Fallback: round-robin from VOICE_POOL (legacy behavior)
    # Extract numeric part of speaker_num for index
    num_match = re.search(r"\d+", speaker_num)
    idx = (int(num_match.group()) - 1) if num_match else 0
    voice = VOICE_POOL[idx % len(VOICE_POOL)]
    return voice, base_rate


def _combine_rates(base_rate, adjustment):
    """Combine base rate with age adjustment.

    E.g., base "+0%" + adjustment "+8%" → "+8%"
         base "+10%" + adjustment "-5%" → "+5%"
    """
    def parse_rate(r):
        r = r.strip().replace("%", "")
        return int(r) if r else 0

    combined = parse_rate(base_rate) + parse_rate(adjustment)
    sign = "+" if combined >= 0 else ""
    return f"{sign}{combined}%"


async def _generate_merged_segments(merged, voices, rates, config):
    """Generate TTS audio for merged speech segments."""
    segments = []
    volume = config.get("volume", "+0%")

    for i, seg in enumerate(merged):
        text = seg["text"]
        if not text or text.startswith("["):
            continue

        start_ms = seg["start_ms"]
        end_ms = seg["end_ms"]
        duration_ms = end_ms - start_ms

        if duration_ms <= 0:
            continue

        voice = voices[i] if i < len(voices) else voices[0]
        rate = rates[i] if i < len(rates) else config.get("rate", "+0%")

        # Generate TTS to temp file
        tmp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = tmp_file.name
        tmp_file.close()
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
            await communicate.save(tmp_path)
        except Exception as e:
            print(f"[配音] 警告: 第 {i+1} 段 TTS 失败: {e}")
            continue

        segments.append({
            "audio_path": tmp_path,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "target_duration_ms": duration_ms,
            "text": text,
            "voice": voice,
        })

        if (i + 1) % 10 == 0 or i == len(merged) - 1:
            print(f"[配音] 已生成 {i + 1}/{len(merged)} 段...")

    print(f"[配音] TTS 生成完成，共 {len(segments)} 段有效音频")
    return segments
