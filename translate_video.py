#!/usr/bin/env python3
"""YouTube 视频翻译工具 - 将英文 YouTube 视频翻译为中文字幕/配音版本。

用法:
    python translate_video.py <youtube_url> [选项]

示例:
    # 仅生成中文字幕版
    python translate_video.py https://www.youtube.com/watch?v=xxx --mode subtitle

    # 仅生成中文配音版
    python translate_video.py https://www.youtube.com/watch?v=xxx --mode dub

    # 两个版本都生成（默认）
    python translate_video.py https://www.youtube.com/watch?v=xxx --mode both

    # 使用 CosyVoice 声音克隆配音
    python translate_video.py https://www.youtube.com/watch?v=xxx --mode dub --tts-engine cosyvoice

    # 指定输出目录
    python translate_video.py https://www.youtube.com/watch?v=xxx -o ./output

    # 使用指定配置文件
    python translate_video.py https://www.youtube.com/watch?v=xxx -c my_config.yaml
"""

# Suppress httpx proxy warnings before any imports
import warnings
warnings.filterwarnings("ignore", message=".*HTTPS proxies.*")
warnings.filterwarnings("ignore", message=".*proxy.*not supported.*")

import argparse
import os
import sys
import shutil
import logging
from pathlib import Path

# Suppress httpx/httpcore logging
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from lib.config import load_config, build_restructure_config
from lib.downloader import download_video, extract_audio
from lib.transcriber import transcribe
from lib.subtitle_processor import fix_overlapping_subtitles, split_long_subtitles
from lib.restructurer import restructure_subtitles
from lib.translator import translate_subtitles, identify_speakers
from lib.estimator import estimate_cost_and_time, print_estimate
from lib.audio_gender import detect_gender_from_audio
from lib.tts_engine import generate_dubbed_audio
from lib.composer import compose_subtitle_version, compose_dubbed_version
from lib.platform_utils import find_ffmpeg, find_ytdlp, get_install_instructions, get_platform_name


def main():
    args = parse_args()
    config = load_config(args.config)

    # Override output directory if specified
    if args.output:
        config["output"]["directory"] = args.output

    # Validate config
    validate_config(config, args.mode)

    # Determine TTS engine (CLI flag overrides config)
    tts_engine = args.tts_engine or config.get("tts", {}).get("engine", "edge")

    # Check required tools
    check_tools()

    output_dir = config["output"]["directory"] or str(Path.cwd() / "yt_output")
    proxy = config["proxy"].get("http") or config["proxy"].get("https") or None

    # Inject proxy into LLM configs
    if proxy:
        config["translate"]["proxy"] = proxy

    # Build restructure config (uses stronger model if configured, falls back to translate)
    restructure_config = build_restructure_config(config)
    if proxy:
        restructure_config["proxy"] = proxy

    print("=" * 60)
    print("  YouTube 视频翻译工具")
    print("=" * 60)
    print(f"  视频: {args.url}")
    print(f"  模式: {args.mode}")
    if args.mode in ("dub", "both"):
        print(f"  TTS引擎: {tts_engine}")
    print(f"  输出: {output_dir}")
    print("=" * 60)

    try:
        # Step 1: Download video
        print("\n📥 第一步: 下载视频")
        dl_result = download_video(args.url, output_dir, proxy=proxy)
        video_path = dl_result["video_path"]
        en_srt_path = dl_result["subtitle_path"]
        duration = dl_result["duration"]
        title = dl_result["title"]

        # Long video advisory
        if duration > 3600:
            hours = duration / 3600
            print(f"\n⚠️  长视频提示: 时长 {hours:.1f} 小时，处理时间较长，"
                  f"如中途中断可重新运行从断点继续")
        elif duration > 1800:
            mins = duration / 60
            print(f"\n⚠️  视频时长 {mins:.0f} 分钟，预计处理需要一些时间")

        # Organize: move files into a title-based subdirectory
        video_dir = Path(output_dir) / title
        if video_dir != video_path.parent:
            video_dir.mkdir(parents=True, exist_ok=True)
            new_video = video_dir / video_path.name
            if not new_video.exists():
                video_path.rename(new_video)
            video_path = new_video
            if en_srt_path:
                new_srt = video_dir / en_srt_path.name
                if not new_srt.exists():
                    en_srt_path.rename(new_srt)
                en_srt_path = new_srt

        # Step 2: Get English subtitles (download or transcribe)
        if en_srt_path is None:
            print("\n🎙 第二步: 语音识别")
            audio_path = extract_audio(video_path)
            en_srt_path = transcribe(audio_path, config["whisper"])
            audio_path.unlink(missing_ok=True)
        else:
            print("\n✅ 第二步: 已有英文字幕，跳过语音识别")

        # Step 2.5: Fix overlapping timestamps (YouTube auto-subs)
        fix_overlapping_subtitles(en_srt_path)

        # Show cost/time estimate (unless --yes flag)
        if not args.yes:
            import pysubs2
            _subs_for_estimate = pysubs2.load(str(en_srt_path))
            est = estimate_cost_and_time(
                len(_subs_for_estimate), config, duration_s=duration,
                needs_whisper=False,
            )
            print_estimate(est)
            del _subs_for_estimate

        # Determine intermediate file paths
        restructured_en_path = Path(str(en_srt_path).replace(".en.srt", ".en_restructured.srt"))
        if str(restructured_en_path) == str(en_srt_path):
            restructured_en_path = en_srt_path.with_name(en_srt_path.stem + "_restructured.srt")

        # Step 3: Restructure (skip if cached)
        speaker_map_path = restructured_en_path.with_suffix(".speakers.json")
        if restructured_en_path.exists() and speaker_map_path.exists():
            print("\n✅ 第三步: 使用已有重组字幕（如需重新生成，请删除 *_restructured.srt）")
            import json
            with open(speaker_map_path, "r", encoding="utf-8") as f:
                speaker_map = {int(k): v for k, v in json.load(f).items()}
        else:
            print("\n📝 第三步: 重组字幕")
            glossary = config.get("glossary", {}).get("terms", {})
            restructured_subs, speaker_map = restructure_subtitles(
                en_srt_path, restructure_config, glossary=glossary
            )
            restructured_subs.save(str(restructured_en_path))
            # Cache speaker map
            import json
            with open(speaker_map_path, "w", encoding="utf-8") as f:
                json.dump(speaker_map, f)

        # Step 3.5: Identify speaker traits (gender/age) for TTS voice matching
        # Only needed for Edge TTS dubbing — CosyVoice uses voice cloning instead
        speaker_traits = None
        if args.mode in ("dub", "both") and tts_engine == "edge":
            speaker_traits_path = restructured_en_path.with_name(
                restructured_en_path.stem + ".speaker_traits.json"
            )
            if speaker_traits_path.exists():
                print("\n✅ 第三步半: 使用已有说话人特征（如需重新识别，请删除 *_speaker_traits.json）")
                import json
                with open(speaker_traits_path, "r", encoding="utf-8") as f:
                    speaker_traits = {int(k): v for k, v in json.load(f).items()}
            else:
                print("\n🎭 第三步半: 识别说话人特征")

                # Audio-based gender detection (accurate, based on pitch)
                # Pass speaker_map so audio segments are grouped per speaker
                audio_genders = detect_gender_from_audio(
                    video_path, restructured_en_path, speaker_map=speaker_map
                )

                # LLM-based trait analysis (speaker ID + age)
                speaker_traits = identify_speakers(
                    restructured_en_path, config["translate"]
                )

                # Merge: audio gender overrides LLM gender when confidence is high
                # Aggregate audio detections per speaker ID for consistency
                from collections import defaultdict, Counter
                speaker_audio_votes = defaultdict(list)
                for idx, trait in speaker_traits.items():
                    audio = audio_genders.get(idx)
                    if audio and isinstance(audio, dict) and audio.get("confidence", 0) > 0.3:
                        speaker_audio_votes[trait["id"]].append(audio["gender"])

                # Apply majority vote per speaker
                for speaker_id, votes in speaker_audio_votes.items():
                    if votes:
                        majority_gender = Counter(votes).most_common(1)[0][0]
                        for idx, trait in speaker_traits.items():
                            if trait["id"] == speaker_id:
                                trait["gender"] = majority_gender

                import json
                with open(speaker_traits_path, "w", encoding="utf-8") as f:
                    json.dump(speaker_traits, f, ensure_ascii=False)

        # Step 4: Translate (skip if cached)
        cn_srt_path = restructured_en_path.with_name(
            restructured_en_path.stem + ".zh.srt"
        )
        if cn_srt_path.exists():
            print("\n✅ 第四步: 使用已有翻译（如需重新翻译，请删除 *.zh.srt）")
        else:
            print("\n🌐 第四步: 翻译字幕")
            glossary = config.get("glossary", {}).get("terms", {})
            cn_srt_path = translate_subtitles(restructured_en_path, config["translate"],
                                               glossary=glossary)

        # Split long subtitles for display (always regenerate)
        import shutil as _shutil
        cn_display_path = cn_srt_path.with_name(
            cn_srt_path.stem.replace(".zh", "") + "_display.zh.srt"
        )
        _shutil.copy2(str(cn_srt_path), str(cn_display_path))
        split_long_subtitles(cn_display_path)

        # Step 5: Generate outputs
        burn = not args.soft_sub  # 默认硬字幕，--soft-sub 时软字幕
        results = []

        if args.mode in ("subtitle", "both"):
            print("\n🎬 第五步: 生成字幕版视频")
            # Use display version (split) for subtitle video
            sub_video = compose_subtitle_version(video_path, cn_display_path, burn=burn)
            results.append(("字幕版", sub_video))

        if args.mode in ("dub", "both"):
            step = "第五步" if args.mode == "dub" else "第六步"
            print(f"\n🔊 {step}: 生成配音版视频 (引擎: {tts_engine})")

            if tts_engine == "cosyvoice":
                from lib.tts_cosyvoice import generate_dubbed_audio_cosyvoice
                dubbed_audio = generate_dubbed_audio_cosyvoice(
                    cn_srt_path, duration, config["tts"],
                    video_path=video_path,
                    en_srt_path=restructured_en_path,
                    speaker_map=speaker_map,
                )
            else:
                dubbed_audio = generate_dubbed_audio(
                    cn_srt_path, duration, config["tts"],
                    speaker_map=speaker_map, speaker_traits=speaker_traits,
                )

            # Background music config
            keep_original = config.get("tts", {}).get("keep_original_audio", True)
            original_volume = config.get("tts", {}).get("original_audio_volume", 0.3)

            dub_video = compose_dubbed_version(
                video_path, dubbed_audio, cn_display_path, burn=burn,
                keep_original_audio=keep_original,
                original_audio_volume=original_volume,
            )
            results.append(("配音版", dub_video))
            Path(dubbed_audio).unlink(missing_ok=True)

        # Summary
        print("\n" + "=" * 60)
        print("  ✅ 处理完成!")
        print("=" * 60)
        print(f"  重组英文字幕: {restructured_en_path}")
        print(f"  中文字幕: {cn_srt_path}")
        for label, path in results:
            print(f"  {label}: {path}")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\n\n已取消。")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="YouTube 视频翻译工具 - 英文视频 → 中文字幕/配音",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="YouTube 视频链接")
    parser.add_argument(
        "-m", "--mode",
        choices=["subtitle", "dub", "both"],
        default="both",
        help="输出模式: subtitle=仅字幕, dub=仅配音, both=两者都要 (默认: both)",
    )
    parser.add_argument(
        "-o", "--output",
        help="输出目录 (默认: ./yt_output)",
    )
    parser.add_argument(
        "-c", "--config",
        help="配置文件路径 (默认: config.yaml)",
    )
    parser.add_argument(
        "--soft-sub",
        action="store_true",
        help="使用软字幕（需要播放器手动开启），默认为硬字幕（烧录进画面）",
    )
    parser.add_argument(
        "--tts-engine",
        choices=["edge", "cosyvoice"],
        default=None,
        help="TTS 引擎: edge=Edge TTS (默认), cosyvoice=CosyVoice 声音克隆",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="跳过预估确认，直接开始处理",
    )
    return parser.parse_args()


def validate_config(config, mode):
    """Check that required API keys are configured and print config summary."""
    errors = []

    translate_key = config["translate"].get("api_key", "")
    if not translate_key or translate_key == "your-translate-api-key":
        errors.append("翻译 API 密钥未配置 (translate.api_key)")

    whisper_key = config["whisper"].get("api_key", "")
    if not whisper_key or whisper_key == "your-whisper-api-key":
        print("[提示] Whisper API 密钥未配置，如果视频没有现成字幕将无法进行语音识别")

    # Check CosyVoice API key if that engine is selected
    tts_engine = config.get("tts", {}).get("engine", "edge")
    if tts_engine == "cosyvoice" and mode in ("dub", "both"):
        cosyvoice_key = config.get("tts", {}).get("cosyvoice", {}).get("api_key", "")
        if not cosyvoice_key:
            errors.append("CosyVoice API 密钥未配置 (tts.cosyvoice.api_key)")

    if errors:
        print("❌ 配置错误:")
        for e in errors:
            print(f"   - {e}")
        print(f"\n请编辑配置文件或设置环境变量。参考: config.example.yaml")
        sys.exit(1)

    # Print config summary (with masked API keys)
    def _mask(key):
        if not key or key.startswith("your-"):
            return "(未配置)"
        return key[:4] + "****" + key[-4:] if len(key) > 8 else "****"

    print(f"  翻译模型: {config['translate'].get('model', '?')}")
    print(f"  翻译API: {_mask(translate_key)}")
    restructure_model = config.get('restructure', {}).get('model', '')
    if restructure_model:
        print(f"  重组模型: {restructure_model}")
    glossary_count = len(config.get('glossary', {}).get('terms', {}))
    if glossary_count > 0:
        print(f"  术语表: {glossary_count} 个词条")
    print(f"  平台: {get_platform_name()}")


def check_tools():
    """Verify required external tools are available (cross-platform)."""
    missing = []

    if not find_ffmpeg():
        missing.append("ffmpeg")
    elif not shutil.which("ffprobe"):
        missing.append("ffprobe (通常与 ffmpeg 一起安装)")

    if not find_ytdlp():
        missing.append("yt-dlp")

    if missing:
        platform = get_platform_name()
        print(f"❌ 缺少必要工具 ({platform}): {', '.join(missing)}")
        print("请安装后重试:")
        for tool in missing:
            print(f"  {get_install_instructions(tool)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
