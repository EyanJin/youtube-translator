"""Shared audio composition utilities for TTS engines (Edge TTS, CosyVoice, etc.)."""

import os
import subprocess
import tempfile
from pathlib import Path


# No artificial gap — keeps audio synced with subtitles
SPEAKER_CHANGE_GAP_MS = 0

# Max segments per ffmpeg filter_complex call to avoid command line limits
COMPOSE_BATCH_SIZE = 40


def compose_audio_track(segments, total_duration_s, output_path, max_speed_ratio=1.8):
    """Compose all TTS segments into a single audio track with timing alignment.

    For long videos (many segments), uses batched composition to avoid ffmpeg
    filter_complex command line limits.

    Strategy:
    - If TTS audio is LONGER than the time window: speed it up (max 1.8x)
    - If TTS audio is SHORTER: keep original speed (natural pause)
    - Never slow down TTS audio

    Args:
        segments: List of dicts with keys: audio_path, start_ms, end_ms,
                  target_duration_ms, text, voice (optional).
        total_duration_s: Total duration of the output track in seconds.
        output_path: Path to write the output WAV file.
    """
    if not segments:
        raise ValueError("没有可用的配音片段")

    # Filter out invalid segments upfront
    valid_segments = []
    for seg in segments:
        dur = get_audio_duration_ms(seg["audio_path"])
        if dur > 0:
            valid_segments.append(seg)

    if not valid_segments:
        raise ValueError("没有有效的配音片段可以合成")

    if len(valid_segments) <= COMPOSE_BATCH_SIZE:
        # Small enough for single-pass composition
        _compose_single_pass(valid_segments, total_duration_s, output_path, max_speed_ratio)
    else:
        # Large segment count — use batched composition
        _compose_batched(valid_segments, total_duration_s, output_path, max_speed_ratio)


def _compose_single_pass(segments, total_duration_s, output_path, max_speed_ratio=1.8):
    """Compose segments in a single ffmpeg call."""
    inputs = []
    filter_parts = []
    overlay_parts = []

    # Start with a silent base track
    inputs.extend(["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={total_duration_s}"])

    prev_voice = None
    valid_count = 0

    overflow_count = 0

    for i, seg in enumerate(segments):
        input_idx = i + 1  # 0 is the silent base
        inputs.extend(["-i", seg["audio_path"]])

        actual_duration_ms = get_audio_duration_ms(seg["audio_path"])
        if actual_duration_ms <= 0:
            continue

        target_ms = seg["target_duration_ms"]
        delay_ms = seg["start_ms"]

        # Add gap when speaker changes
        current_voice = seg.get("voice", "")
        if prev_voice is not None and current_voice != prev_voice:
            delay_ms += SPEAKER_CHANGE_GAP_MS
        prev_voice = current_voice

        # Build filter chain for this segment
        filters = []

        # Only speed up if TTS is longer than the target window
        if actual_duration_ms > target_ms * 1.1:
            speed_ratio = actual_duration_ms / target_ms
            speed_ratio = min(max_speed_ratio, speed_ratio)  # Cap speed
            filters.append(f"atempo={speed_ratio:.3f}")

            # After speeding up, check if audio still overflows
            adjusted_duration_ms = actual_duration_ms / speed_ratio
            if adjusted_duration_ms > target_ms * 1.05:
                # Truncate to target duration to prevent overlap with next segment
                trim_s = target_ms / 1000.0
                filters.append(f"atrim=0:{trim_s:.3f}")
                overflow_count += 1

        # Always add delay for timing alignment
        filters.append(f"adelay={delay_ms}|{delay_ms}")

        filter_chain = ",".join(filters)
        filter_parts.append(f"[{input_idx}:a]{filter_chain}[s{i}]")
        overlay_parts.append(f"[s{i}]")
        valid_count += 1

    if overflow_count > 0:
        print(f"[配音] 注意: {overflow_count} 段音频加速后仍超出时间窗口，已截断处理")

    if not overlay_parts:
        raise ValueError("没有有效的配音片段可以合成")

    # Mix all segments with the silent base
    mix_input = "[0:a]" + "".join(overlay_parts)
    filter_parts.append(
        f"{mix_input}amix=inputs={valid_count + 1}:duration=first:dropout_transition=0:normalize=0[out]"
    )

    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ac", "1",
        "-ar", "44100",
        str(output_path),
    ]

    print("[配音] 正在合成音频轨道...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[配音] 复杂合成失败，尝试简化方案...")
        _compose_simple(segments, total_duration_s, output_path)


def _compose_batched(segments, total_duration_s, output_path, max_speed_ratio=1.8):
    """Compose segments in batches, then merge intermediate files.

    Splits segments into groups of COMPOSE_BATCH_SIZE, composes each group
    into an intermediate WAV with correct timing, then overlays all intermediate
    files onto a single silent base track.
    """
    intermediate_files = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="tts_compose_"))

    try:
        # Split into batches and compose each
        total_batches = (len(segments) + COMPOSE_BATCH_SIZE - 1) // COMPOSE_BATCH_SIZE
        print(f"[配音] 分 {total_batches} 批合成 {len(segments)} 段音频...")

        for batch_idx in range(0, len(segments), COMPOSE_BATCH_SIZE):
            batch = segments[batch_idx:batch_idx + COMPOSE_BATCH_SIZE]
            batch_num = batch_idx // COMPOSE_BATCH_SIZE + 1
            intermediate_path = tmp_dir / f"batch_{batch_num:03d}.wav"

            print(f"[配音] 合成批次 {batch_num}/{total_batches} ({len(batch)} 段)...")
            _compose_single_pass(batch, total_duration_s, intermediate_path, max_speed_ratio)

            if intermediate_path.exists():
                intermediate_files.append(str(intermediate_path))

        if not intermediate_files:
            raise ValueError("所有批次合成均失败")

        if len(intermediate_files) == 1:
            # Only one batch — just move the file
            import shutil
            shutil.move(intermediate_files[0], str(output_path))
        else:
            # Merge all intermediate files by overlaying onto a silent base
            _merge_intermediate_files(intermediate_files, total_duration_s, output_path)

    finally:
        # Clean up intermediate files
        for f in intermediate_files:
            if os.path.exists(f):
                os.unlink(f)
        # Clean up temp directory
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


def _merge_intermediate_files(intermediate_files, total_duration_s, output_path):
    """Merge intermediate batch WAV files by overlaying them onto a silent base."""
    inputs = ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={total_duration_s}"]
    overlay_parts = []

    for i, fpath in enumerate(intermediate_files):
        input_idx = i + 1
        inputs.extend(["-i", fpath])
        overlay_parts.append(f"[{input_idx}:a]")

    mix_input = "[0:a]" + "".join(overlay_parts)
    n_inputs = len(intermediate_files) + 1
    filter_complex = (
        f"{mix_input}amix=inputs={n_inputs}:duration=first"
        f":dropout_transition=0:normalize=0[out]"
    )

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ac", "1",
        "-ar", "44100",
        str(output_path),
    ]

    print(f"[配音] 正在合并 {len(intermediate_files)} 个批次...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"批次合并失败:\n{result.stderr[:500]}")


def _compose_simple(segments, total_duration_s, output_path):
    """Simplified composition: concat segments with silence gaps."""
    concat_path = str(output_path) + ".concat.txt"

    with open(concat_path, "w", encoding="utf-8") as f:
        prev_end_ms = 0
        for seg in segments:
            gap_s = max(0, (seg["start_ms"] - prev_end_ms)) / 1000.0
            if gap_s > 0.05:
                gap_path = str(output_path) + f".gap_{seg['start_ms']}.wav"
                subprocess.run([
                    "ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"anullsrc=r=44100:cl=mono:d={gap_s}",
                    "-t", str(gap_s), gap_path,
                ], capture_output=True)
                f.write(f"file '{gap_path}'\n")

            f.write(f"file '{seg['audio_path']}'\n")
            actual_dur = get_audio_duration_ms(seg["audio_path"])
            prev_end_ms = seg["start_ms"] + actual_dur

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_path,
        "-ac", "1", "-ar", "44100",
        str(output_path),
    ], capture_output=True)

    os.unlink(concat_path)


def get_audio_duration_ms(audio_path):
    """Get audio file duration in milliseconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return int(float(result.stdout.strip()) * 1000)
    except (ValueError, AttributeError):
        return 0


def cleanup_temp_files(segments):
    """Remove temporary TTS audio files."""
    for seg in segments:
        path = seg.get("audio_path")
        if path and os.path.exists(path):
            os.unlink(path)
