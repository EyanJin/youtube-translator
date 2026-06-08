"""Video composition using ffmpeg - combine video, audio, and subtitles."""

import subprocess
import shutil
import sys
from pathlib import Path


def _escape_srt_path(srt_path):
    """Escape SRT path for ffmpeg subtitles filter (cross-platform).

    ffmpeg's subtitles filter needs:
    - Forward slashes (not backslashes) on all platforms
    - Escaped colons (for Windows drive letters like C:)
    - Single quotes around the path (for spaces and special chars)
    """
    escaped = str(srt_path).replace("\\", "/")
    # Only escape colons on Windows (drive letters like C:)
    if sys.platform == "win32":
        escaped = escaped.replace(":", "\\:")
    return f"'{escaped}'"


def _extract_background_audio(video_path):
    """Extract background audio (no vocals) from video using Demucs.

    Uses Facebook's Demucs model to separate vocals from background music/ambient.
    Results are cached — if the separated file already exists, it's reused.

    Returns:
        Path to the non-vocal audio file, or None if separation fails.
    """
    video_path = Path(video_path)
    output_dir = video_path.parent / "demucs_separated"
    # Demucs uses the input filename (without extension) as subdirectory name
    # Since we feed it <video_stem>.full_audio.wav, the subdir is <video_stem>.full_audio
    audio_stem = video_path.stem + ".full_audio"
    no_vocals_path = output_dir / "htdemucs" / audio_stem / "no_vocals.wav"

    # Check cache
    if no_vocals_path.exists():
        print(f"[音频分离] 使用已缓存的背景音频: {no_vocals_path.name}")
        return no_vocals_path

    # Check if demucs is available
    if not shutil.which("demucs"):
        print("[音频分离] Demucs 未安装，跳过音频分离（pip install demucs）")
        return None

    # First extract audio from video as WAV (Demucs needs audio file)
    audio_path = video_path.with_suffix(".full_audio.wav")
    if not audio_path.exists():
        print("[音频分离] 正在从视频提取音频...")
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "44100", "-ac", "2",
            str(audio_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[音频分离] 音频提取失败: {result.stderr[:200]}")
            return None

    # Run Demucs to separate vocals from background
    print("[音频分离] 正在使用 Demucs 分离人声和背景音乐（首次运行需下载模型）...")
    cmd = [
        "python", "-m", "demucs",
        "--two-stems=vocals",
        "-n", "htdemucs",
        "-o", str(output_dir),
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"[音频分离] Demucs 分离失败: {result.stderr[:300]}")
        # Clean up
        audio_path.unlink(missing_ok=True)
        return None

    # Clean up the full audio file (we have the separated tracks now)
    audio_path.unlink(missing_ok=True)

    if no_vocals_path.exists():
        print(f"[音频分离] 背景音频分离完成: {no_vocals_path.name}")
        return no_vocals_path
    else:
        print("[音频分离] Demucs 输出文件未找到")
        return None


def compose_subtitle_version(video_path, cn_srt_path, output_path=None, burn=True):
    """Create video with Chinese subtitles (original audio preserved).

    Args:
        video_path: Path to original video.
        cn_srt_path: Path to Chinese SRT file.
        output_path: Output video path. Auto-generated if None.
        burn: If True (default), burn subtitles into video (always visible).

    Returns:
        Path to output video.
    """
    video_path = Path(video_path)
    cn_srt_path = Path(cn_srt_path)

    if output_path is None:
        output_path = video_path.with_name(video_path.stem + "_中文字幕.mp4")
    output_path = Path(output_path)

    if burn:
        # Hard subtitles: re-encode video with subtitles burned in
        srt_escaped = _escape_srt_path(cn_srt_path)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vf", f"subtitles={srt_escaped}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy",
            str(output_path),
        ]
    else:
        # Soft subtitles: embed SRT as a subtitle stream (no re-encoding)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(cn_srt_path),
            "-c:v", "copy",
            "-c:a", "copy",
            "-c:s", "mov_text",
            "-metadata:s:s:0", "language=chi",
            str(output_path),
        ]

    print(f"[合成] 正在生成字幕版视频{'（硬字幕）' if burn else '（软字幕）'}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"字幕合成失败:\n{result.stderr}")

    print(f"[合成] 字幕版完成: {output_path.name}")
    return output_path


def compose_dubbed_version(video_path, dubbed_audio_path, cn_srt_path,
                           output_path=None, burn=True, keep_original_audio=False,
                           original_audio_volume=0.3):
    """Create video with Chinese dubbed audio and subtitles.

    Args:
        video_path: Path to original video.
        dubbed_audio_path: Path to Chinese audio track.
        cn_srt_path: Path to Chinese SRT file.
        output_path: Output video path. Auto-generated if None.
        burn: If True (default), burn subtitles into video.
        keep_original_audio: If True, mix original audio at lower volume.
        original_audio_volume: Volume level for original audio when mixing (0.0-1.0).

    Returns:
        Path to output video.
    """
    video_path = Path(video_path)
    dubbed_audio_path = Path(dubbed_audio_path)
    cn_srt_path = Path(cn_srt_path)

    if output_path is None:
        output_path = video_path.with_name(video_path.stem + "_中文配音.mp4")
    output_path = Path(output_path)

    if keep_original_audio:
        # Try to use Demucs-separated background audio (no vocals)
        bg_audio_path = _extract_background_audio(video_path)

        if bg_audio_path:
            # Use separated background audio (music + ambient, no speech)
            vol = max(0.0, min(1.0, original_audio_volume))
            filter_complex = (
                f"[2:a]volume={vol}[bg];"
                f"[1:a]volume=1.0[dub];"
                f"[bg][dub]amix=inputs=2:duration=first[aout]"
            )
            audio_inputs = ["-i", str(bg_audio_path)]
            audio_map = ["-filter_complex", filter_complex, "-map", "0:v", "-map", "[aout]"]
        else:
            # Fallback: mix full original audio (includes speech)
            print("[合成] 注意: 无法分离背景音乐，将混入完整原始音频（含英文对话）")
            vol = max(0.0, min(1.0, original_audio_volume))
            filter_complex = f"[0:a]volume={vol}[orig];[1:a]volume=1.0[dub];[orig][dub]amix=inputs=2:duration=first[aout]"
            audio_inputs = []
            audio_map = ["-filter_complex", filter_complex, "-map", "0:v", "-map", "[aout]"]
    else:
        # Replace audio entirely
        audio_inputs = []
        audio_map = ["-map", "0:v", "-map", "1:a"]

    if burn:
        srt_escaped = _escape_srt_path(cn_srt_path)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(dubbed_audio_path),
        ] + audio_inputs + audio_map + [
            "-vf", f"subtitles={srt_escaped}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(dubbed_audio_path),
        ] + audio_inputs + audio_map + [
            "-i", str(cn_srt_path),
            "-map", f"{2 + len(audio_inputs) // 2}:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-c:s", "mov_text",
            "-metadata:s:s:0", "language=chi",
            "-shortest",
            str(output_path),
        ]

    print(f"[合成] 正在生成配音版视频{'（硬字幕）' if burn else '（软字幕）'}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"配音合成失败:\n{result.stderr}")

    print(f"[合成] 配音版完成: {output_path.name}")
    return output_path
