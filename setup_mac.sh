#!/bin/bash
# YouTube 视频翻译工具 - macOS 安装脚本
set -e

echo "=============================="
echo "  YouTube Translator - macOS 安装"
echo "=============================="

# 检查 Homebrew
if ! command -v brew &> /dev/null; then
    echo "❌ 需要 Homebrew，请先安装: https://brew.sh"
    exit 1
fi

echo ""
echo "📦 安装系统依赖..."
brew install ffmpeg yt-dlp 2>/dev/null || echo "  (已安装)"

echo ""
echo "🐍 安装 Python 依赖..."
pip3 install pysubs2 edge-tts openai pydub librosa pyyaml requests httpx 2>/dev/null

echo ""
echo "📋 可选: 安装 GPU 加速依赖（说话人性别检测）"
read -p "是否安装 torch + transformers? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    pip3 install torch transformers
fi

echo ""
echo "⚙️  配置文件..."
if [ ! -f config.yaml ]; then
    cp config.example.yaml config.yaml
    echo "  已创建 config.yaml（从 config.example.yaml 复制）"
    echo ""
    echo "  ⚠️  请编辑 config.yaml 填入你的 API 密钥:"
    echo "     - translate.api_key  (翻译，必需)"
    echo "     - whisper.api_key    (语音识别，如视频无字幕则必需)"
    echo ""
    echo "  macOS 用户通常不需要代理配置，可以将 proxy 段留空或删除。"
else
    echo "  config.yaml 已存在，跳过"
fi

echo ""
echo "=============================="
echo "  ✅ 安装完成!"
echo "=============================="
echo ""
echo "使用方法:"
echo "  python3 translate_video.py 'https://www.youtube.com/watch?v=xxx'"
echo ""
echo "更多选项:"
echo "  python3 translate_video.py --help"
