"""Configuration loader for youtube-translator."""

import os
import shutil
import yaml
from pathlib import Path
from openai import OpenAI

from lib.platform_utils import find_ffmpeg


def create_llm_client(config):
    """Create an OpenAI client with optional proxy support.

    Args:
        config: Translate config dict with base_url, api_key, and optional proxy.

    Returns:
        OpenAI client instance.
    """
    kwargs = {
        "base_url": config["base_url"],
        "api_key": config["api_key"],
    }

    proxy = config.get("proxy")
    if proxy:
        import httpx
        import warnings
        # Suppress httpx HTTPS proxy warnings (proxy works correctly despite the warning)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*HTTPS proxies.*")
            kwargs["http_client"] = httpx.Client(proxy=proxy)

    return OpenAI(**kwargs)


def build_restructure_config(config):
    """Build restructure config by merging restructure overrides onto translate config.

    If restructure.model is set, use it; otherwise fall back to translate config entirely.
    base_url and api_key always fall back to translate if not specified in restructure.
    """
    translate = config.get("translate", {})
    restructure = config.get("restructure", {})

    result = dict(translate)  # Start with translate as base

    # Override with any non-empty restructure values
    if restructure.get("model"):
        result["model"] = restructure["model"]
    if restructure.get("base_url"):
        result["base_url"] = restructure["base_url"]
    if restructure.get("api_key"):
        result["api_key"] = restructure["api_key"]

    return result

DEFAULT_CONFIG = {
    "proxy": {"http": "", "https": ""},
    "whisper": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "",
        "model": "whisper-large-v3",
    },
    "translate": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
    },
    "restructure": {
        # Only model is required; base_url and api_key fall back to translate
        "model": "",
        # Max English words per subtitle entry (default 15, increase for fast speech)
        "max_words_per_entry": 15,
    },
    "glossary": {
        # Custom terminology mapping injected into translation/restructure prompts
        "terms": {},
    },
    "tts": {
        "engine": "edge",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+0%",
        "volume": "+0%",
        "keep_original_audio": True,
        "original_audio_volume": 0.3,
        "max_speed_ratio": 1.8,
        "cosyvoice": {
            "provider": "siliconflow",
            "api_key": "",
            "api_url": "",
            "model": "FunAudioLLM/CosyVoice2-0.5B",
        },
    },
    "output": {
        "directory": "",
        "subtitle_format": "srt",
        "burn_subtitles": True,
    },
}


def load_config(config_path=None):
    """Load config from YAML file, falling back to defaults.

    Priority: config file > environment variables > defaults.
    """
    config = dict(DEFAULT_CONFIG)

    if config_path is None:
        config_path = Path(__file__).parent.parent / "config.yaml"

    if Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        config = _deep_merge(config, user_config)

    # Environment variable overrides
    env_map = {
        "YT_WHISPER_API_KEY": ("whisper", "api_key"),
        "YT_WHISPER_BASE_URL": ("whisper", "base_url"),
        "YT_WHISPER_MODEL": ("whisper", "model"),
        "YT_TRANSLATE_API_KEY": ("translate", "api_key"),
        "YT_TRANSLATE_BASE_URL": ("translate", "base_url"),
        "YT_TRANSLATE_MODEL": ("translate", "model"),
        "YT_TTS_VOICE": ("tts", "voice"),
        "YT_PROXY": ("proxy", "http"),
    }
    for env_key, (section, key) in env_map.items():
        val = os.environ.get(env_key)
        if val:
            config[section][key] = val
            if env_key == "YT_PROXY":
                config["proxy"]["https"] = val

    # Ensure ffmpeg is discoverable (cross-platform)
    find_ffmpeg()

    return config


def _deep_merge(base, override):
    """Recursively merge override into base, returning a new dict."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
