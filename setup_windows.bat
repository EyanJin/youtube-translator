@echo off
REM YouTube 视频翻译工具 - Windows 安装脚本
echo ==============================
echo   YouTube Translator - Windows Setup
echo ==============================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [X] Python not found. Please install Python 3.9+ from https://python.org
    echo     Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)
echo [OK] Python found

REM Check ffmpeg
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [!] ffmpeg not found. Installing via winget...
    winget install Gyan.FFmpeg >nul 2>&1
    if %errorlevel% neq 0 (
        echo     winget failed. Please install ffmpeg manually:
        echo     1. Download from https://www.gyan.dev/ffmpeg/builds/
        echo     2. Extract and add the bin/ folder to your system PATH
        echo     Or run: choco install ffmpeg
    ) else (
        echo [OK] ffmpeg installed. You may need to restart terminal for PATH to update.
    )
) else (
    echo [OK] ffmpeg found
)

REM Check yt-dlp
where yt-dlp >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [!] yt-dlp not found. Installing via pip...
    pip install yt-dlp
) else (
    echo [OK] yt-dlp found
)

REM Install Python dependencies
echo.
echo Installing Python dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [!] pip install failed. If you have network issues, try:
    echo     set https_proxy=http://your-proxy:port
    echo     pip install -r requirements.txt
    pause
    exit /b 1
)
echo [OK] Dependencies installed

REM Create config.yaml if not exists
echo.
if not exist config.yaml (
    copy config.example.yaml config.yaml >nul
    echo [OK] Created config.yaml from template
    echo.
    echo ============================================
    echo   NEXT STEP: Edit config.yaml
    echo ============================================
    echo.
    echo   Required: translate.api_key
    echo     - DeepSeek (recommended, cheap): https://platform.deepseek.com
    echo     - OpenAI: https://platform.openai.com
    echo.
    echo   Optional: whisper.api_key (only if video has no subtitles)
    echo     - Groq (free): https://console.groq.com
    echo.
    echo   Optional: proxy (if you need VPN to access YouTube)
    echo     - Set http/https to your proxy address
    echo ============================================
) else (
    echo [OK] config.yaml already exists
)

echo.
echo ==============================
echo   Setup complete!
echo ==============================
echo.
echo Usage:
echo   python translate_video.py "https://www.youtube.com/watch?v=xxx"
echo.
echo More options:
echo   python translate_video.py --help
echo.
pause
