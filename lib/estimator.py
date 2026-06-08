"""Cost and time estimation for video translation pipeline."""

import math


# Approximate token counts per subtitle entry (input + output)
TOKENS_PER_ENTRY_RESTRUCTURE = 80   # ~40 input + ~40 output
TOKENS_PER_ENTRY_TRANSLATE = 60     # ~30 input + ~30 output

# Approximate API pricing per 1M tokens (input + output blended)
MODEL_PRICING = {
    # DeepSeek models
    "deepseek-chat": 0.5,
    "deepseek-reasoner": 2.0,
    # OpenAI models (USD)
    "gpt-4o": 7.5,
    "gpt-4o-mini": 0.3,
    "gpt-4-turbo": 20.0,
    # Claude models (USD)
    "claude-sonnet-4-6": 9.0,
    "claude-haiku-4-5": 2.0,
    "claude-opus-4-6": 45.0,
}

# Average API latency per call (seconds)
AVG_API_LATENCY = 5


def estimate_cost_and_time(subtitle_count, config, duration_s=0, needs_whisper=False):
    """Estimate API cost and processing time for a video translation job.

    Args:
        subtitle_count: Number of subtitle entries to process.
        config: Full config dict.
        duration_s: Video duration in seconds (for whisper estimation).
        needs_whisper: Whether Whisper transcription is needed.

    Returns:
        Dict with keys: restructure_calls, translate_calls, whisper_calls,
        estimated_cost_usd, estimated_time_min, details (list of strings).
    """
    restructure_batch = 50
    translate_batch = 25

    restructure_calls = math.ceil(subtitle_count / restructure_batch)
    translate_calls = math.ceil(subtitle_count / translate_batch)
    whisper_calls = math.ceil(duration_s / 600) if needs_whisper else 0  # 10min chunks

    # Get model names
    restructure_model = config.get("restructure", {}).get("model", "gpt-4o")
    translate_model = config.get("translate", {}).get("model", "deepseek-chat")

    # Estimate tokens
    restructure_tokens = subtitle_count * TOKENS_PER_ENTRY_RESTRUCTURE
    translate_tokens = subtitle_count * TOKENS_PER_ENTRY_TRANSLATE

    # Calculate costs
    restructure_price = _get_price_per_m(restructure_model)
    translate_price = _get_price_per_m(translate_model)

    restructure_cost = (restructure_tokens / 1_000_000) * restructure_price
    translate_cost = (translate_tokens / 1_000_000) * translate_price
    whisper_cost = (duration_s / 60) * 0.006 if needs_whisper else 0  # $0.006/min

    total_cost = restructure_cost + translate_cost + whisper_cost

    # Estimate time
    api_time = (restructure_calls + translate_calls) * AVG_API_LATENCY
    tts_time = subtitle_count * 1.5  # ~1.5s per TTS segment
    ffmpeg_time = duration_s * 0.1   # ~10% of video duration for encoding
    total_time_s = api_time + tts_time + ffmpeg_time
    total_time_min = total_time_s / 60

    details = [
        f"字幕条数: {subtitle_count}",
        f"重组: {restructure_calls} 次 API 调用 (模型: {restructure_model})",
        f"翻译: {translate_calls} 次 API 调用 (模型: {translate_model})",
    ]
    if needs_whisper:
        details.append(f"语音识别: {whisper_calls} 次 Whisper 调用")

    details.extend([
        f"预估费用: ${total_cost:.3f} USD",
        f"预估耗时: {total_time_min:.0f} 分钟",
    ])

    return {
        "restructure_calls": restructure_calls,
        "translate_calls": translate_calls,
        "whisper_calls": whisper_calls,
        "estimated_cost_usd": total_cost,
        "estimated_time_min": total_time_min,
        "details": details,
    }


def _get_price_per_m(model_name):
    """Get approximate price per 1M tokens for a model."""
    # Exact match
    if model_name in MODEL_PRICING:
        return MODEL_PRICING[model_name]

    # Partial match
    model_lower = model_name.lower()
    for key, price in MODEL_PRICING.items():
        if key in model_lower:
            return price

    # Default: assume mid-range
    return 2.0


def print_estimate(estimate):
    """Print a formatted cost/time estimate."""
    print("\n" + "─" * 40)
    print("  📊 预估信息")
    print("─" * 40)
    for line in estimate["details"]:
        print(f"  {line}")
    print("─" * 40)
