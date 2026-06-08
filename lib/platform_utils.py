"""Platform detection and cross-platform utility functions."""

import os
import sys
import glob
import shutil


def is_windows():
    return sys.platform == "win32"


def is_mac():
    return sys.platform == "darwin"


def is_linux():
    return sys.platform.startswith("linux")


def get_platform_name():
    if is_windows():
        return "Windows"
    elif is_mac():
        return "macOS"
    elif is_linux():
        return "Linux"
    return sys.platform


def find_ffmpeg():
    """Find ffmpeg executable and add to PATH if needed.

    Returns True if ffmpeg is available, False otherwise.
    """
    if shutil.which("ffmpeg"):
        return True

    search_paths = []

    if is_windows():
        search_paths = [
            os.path.expandvars(r"%LOCALAPPDATA%/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin"),
            r"C:/ffmpeg*/bin",
            r"C:/Program Files/ffmpeg*/bin",
            r"C:/Program Files (x86)/ffmpeg*/bin",
        ]
    elif is_mac():
        search_paths = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/opt/local/bin",  # MacPorts
        ]
    elif is_linux():
        search_paths = [
            "/usr/bin",
            "/usr/local/bin",
            "/snap/bin",
        ]

    for pattern in search_paths:
        matches = glob.glob(pattern) if "*" in pattern else [pattern]
        for match in matches:
            ffmpeg_name = "ffmpeg.exe" if is_windows() else "ffmpeg"
            ffmpeg_path = os.path.join(match, ffmpeg_name)
            if os.path.isfile(ffmpeg_path):
                os.environ["PATH"] = match + os.pathsep + os.environ.get("PATH", "")
                return True

    return False


def find_ytdlp():
    """Find yt-dlp executable.

    On Windows, checks for a bundled yt-dlp.exe in the project directory first.
    On all platforms, falls back to system PATH.

    Returns:
        Path string to yt-dlp executable, or None if not found.
    """
    from pathlib import Path

    if is_windows():
        # Check for bundled exe in project root
        project_root = Path(__file__).parent.parent
        local_exe = project_root / "yt-dlp.exe"
        if local_exe.exists():
            return str(local_exe)

    # System PATH
    ytdlp = shutil.which("yt-dlp")
    if ytdlp:
        return ytdlp

    return None


def get_install_instructions(tool):
    """Get platform-specific install instructions for a tool."""
    instructions = {
        "ffmpeg": {
            "Windows": "winget install Gyan.FFmpeg",
            "macOS": "brew install ffmpeg",
            "Linux": "sudo apt install ffmpeg  # or: sudo dnf install ffmpeg",
        },
        "yt-dlp": {
            "Windows": "pip install yt-dlp  # 或下载 yt-dlp.exe 放在项目目录",
            "macOS": "brew install yt-dlp  # 或: pip3 install yt-dlp",
            "Linux": "pip3 install yt-dlp  # 或: sudo apt install yt-dlp",
        },
    }

    platform = get_platform_name()
    tool_instructions = instructions.get(tool, {})
    return tool_instructions.get(platform, f"请安装 {tool}")
