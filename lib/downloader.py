"""YouTube video downloader using yt-dlp."""

import subprocess
import shutil
import json
import re
from pathlib import Path

from lib.platform_utils import find_ytdlp


def _get_ytdlp_cmd():
    """Find yt-dlp executable using cross-platform detection."""
    cmd = find_ytdlp()
    if cmd:
        return cmd
    raise FileNotFoundError("找不到 yt-dlp，请安装: pip install yt-dlp (或 brew install yt-dlp)")


def _ytdlp_base_args():
    """Common yt-dlp arguments for all commands."""
    return ["--js-runtimes", "node"]


def download_video(url, output_dir, proxy=None):
    """Download video and attempt to fetch existing subtitles.

    Returns dict with keys: video_path, subtitle_path (or None), title, duration.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # First, get video info to determine title and available subtitles
    info = _get_video_info(url, proxy)
    title = _sanitize_filename(info.get("title", "video"))
    duration = info.get("duration", 0)

    video_path = output_dir / f"{title}.mp4"
    subtitle_path = None

    # Build yt-dlp command
    ytdlp = _get_ytdlp_cmd()
    cmd = [
        ytdlp,
    ] + _ytdlp_base_args() + [
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", str(video_path),
        "--no-playlist",
    ]

    # Try to download English subtitles (auto-generated or manual)
    has_en_subs = _has_english_subtitles(info)
    if has_en_subs:
        cmd.extend([
            "--write-sub",
            "--write-auto-sub",
            "--sub-lang", "en",
            "--sub-format", "srt",
            "--convert-subs", "srt",
        ])

    if proxy:
        cmd.extend(["--proxy", proxy])

    cmd.append(url)

    print(f"[下载] 正在下载: {title}")
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        # Filter out proxy warnings from error output
        stderr = "\n".join(
            line for line in result.stderr.splitlines()
            if "HTTPS proxies" not in line
        ).strip()
        if stderr:
            raise RuntimeError(f"yt-dlp 下载失败:\n{stderr}")
        # If only proxy warnings, the download may have actually succeeded
        # Check if video file exists before raising

    # Find the actual video file (yt-dlp may adjust the filename)
    video_path = _find_output_file(output_dir, title, ".mp4")
    if video_path is None:
        raise FileNotFoundError(f"下载完成但找不到视频文件: {output_dir}/{title}.mp4")

    # Check if subtitles were downloaded
    if has_en_subs:
        subtitle_path = _find_output_file(output_dir, title, ".en.srt")
        if subtitle_path is None:
            subtitle_path = _find_output_file(output_dir, title, ".srt")

    if subtitle_path:
        print(f"[下载] 已获取英文字幕: {subtitle_path.name}")
    else:
        print("[下载] 未找到现有字幕，将使用语音识别")

    return {
        "video_path": video_path,
        "subtitle_path": subtitle_path,
        "title": title,
        "duration": duration,
    }


def extract_audio(video_path, output_path=None):
    """Extract audio from video for Whisper transcription.

    Returns path to the extracted audio file.
    """
    video_path = Path(video_path)
    if output_path is None:
        output_path = video_path.with_suffix(".wav")
    else:
        output_path = Path(output_path)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(output_path),
    ]

    print(f"[音频] 正在提取音频...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 音频提取失败:\n{result.stderr}")

    print(f"[音频] 音频已提取: {output_path.name}")
    return output_path


def _get_video_info(url, proxy=None):
    """Get video metadata without downloading."""
    ytdlp = _get_ytdlp_cmd()
    cmd = [ytdlp] + _ytdlp_base_args() + ["--dump-json", "--no-playlist", url]
    if proxy:
        cmd.extend(["--proxy", proxy])

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        stderr = "\n".join(
            line for line in result.stderr.splitlines()
            if "HTTPS proxies" not in line
        ).strip()
        raise RuntimeError(f"获取视频信息失败:\n{stderr}")

    return json.loads(result.stdout)


def _has_english_subtitles(info):
    """Check if video has English subtitles (manual or auto-generated)."""
    subs = info.get("subtitles", {})
    auto_subs = info.get("automatic_captions", {})
    return "en" in subs or "en" in auto_subs


def _sanitize_filename(name):
    """Remove characters that are invalid in filenames."""
    name = re.sub(r'''[<>:"/\\|?*']''', '', name)
    name = name.strip('. ')
    return name[:200] if len(name) > 200 else name


def _find_output_file(directory, title_prefix, suffix):
    """Find a file matching the title prefix and suffix in directory."""
    directory = Path(directory)
    # Exact match first
    exact = directory / f"{title_prefix}{suffix}"
    if exact.exists():
        return exact

    # Fuzzy match: yt-dlp sometimes modifies filenames
    for f in directory.iterdir():
        if f.name.endswith(suffix) and f.name.startswith(title_prefix[:30]):
            return f

    return None
