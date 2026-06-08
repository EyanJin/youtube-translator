"""Audio-based gender detection using neural model (wav2vec2) with pitch fallback.

Primary: wav2vec2-large-xlsr-53-gender-recognition model (300M params, trained for gender)
Fallback: librosa pyin F0 analysis (pitch-based heuristic)
"""

import subprocess
import tempfile
import os
from pathlib import Path

import numpy as np
import librosa


# Gender classification thresholds for pitch fallback (Hz)
MALE_MAX_F0 = 165
FEMALE_MAX_F0 = 250

# wav2vec2 model for neural gender detection
_WAV2VEC2_MODEL_NAME = "alefiury/wav2vec2-large-xlsr-53-gender-recognition-librispeech"
_wav2vec2_model = None
_wav2vec2_extractor = None
_wav2vec2_available = None  # None = not yet checked


def _load_wav2vec2():
    """Lazy-load wav2vec2 gender classification model.

    Returns True if model loaded successfully, False otherwise.
    """
    global _wav2vec2_model, _wav2vec2_extractor, _wav2vec2_available

    if _wav2vec2_available is not None:
        return _wav2vec2_available

    try:
        import torch
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

        print(f"[音频分析] 加载 wav2vec2 性别识别模型...")
        _wav2vec2_extractor = AutoFeatureExtractor.from_pretrained(_WAV2VEC2_MODEL_NAME)
        _wav2vec2_model = AutoModelForAudioClassification.from_pretrained(
            _WAV2VEC2_MODEL_NAME, num_labels=2
        )
        _wav2vec2_model.eval()

        # Move to GPU if available
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _wav2vec2_model = _wav2vec2_model.to(device)
        print(f"[音频分析] wav2vec2 模型已加载 (device={device})")
        _wav2vec2_available = True
    except Exception as e:
        print(f"[音频分析] wav2vec2 模型加载失败，将使用音高检测: {e}")
        _wav2vec2_available = False

    return _wav2vec2_available


def _classify_segment_wav2vec2(segment, sr):
    """Classify gender using wav2vec2 neural model.

    Args:
        segment: Audio numpy array.
        sr: Sample rate of the segment.

    Returns:
        ("male"/"female", confidence) or (None, 0) if classification fails.
    """
    import torch

    if not _load_wav2vec2():
        return None, 0.0

    try:
        # Resample to 16kHz if needed (wav2vec2 expects 16kHz)
        if sr != 16000:
            segment_16k = librosa.resample(segment, orig_sr=sr, target_sr=16000)
        else:
            segment_16k = segment

        # Need at least 0.5s of audio
        if len(segment_16k) < 8000:
            return None, 0.0

        # Truncate to 30s max to avoid memory issues
        max_samples = 16000 * 30
        if len(segment_16k) > max_samples:
            segment_16k = segment_16k[:max_samples]

        inputs = _wav2vec2_extractor(
            segment_16k, sampling_rate=16000, return_tensors="pt", padding=True
        )

        device = next(_wav2vec2_model.parameters()).device
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            logits = _wav2vec2_model(**inputs).logits

        probs = torch.softmax(logits, dim=-1).cpu().numpy()[0]

        # Model labels: 0=female, 1=male
        female_prob = float(probs[0])
        male_prob = float(probs[1])

        if male_prob > female_prob:
            return "male", male_prob
        else:
            return "female", female_prob

    except Exception as e:
        print(f"[音频分析] wav2vec2 推理失败: {e}")
        return None, 0.0


def detect_gender_from_audio(video_path, srt_path, speaker_map=None):
    """Detect speaker gender based on audio, aggregated per speaker.

    Uses wav2vec2 neural model as primary method, falls back to pitch-based
    detection if the model is unavailable.

    Args:
        video_path: Path to the video file.
        srt_path: Path to the restructured SRT file (for timing info).
        speaker_map: Optional dict {subtitle_index: speaker_id}.

    Returns:
        Dict mapping subtitle index (0-based) to {"gender": str, "confidence": float}.
    """
    import pysubs2
    from collections import defaultdict

    video_path = Path(video_path)
    subs = pysubs2.load(str(srt_path))

    if not subs:
        return {}

    print(f"[音频分析] 正在分析 {len(subs)} 段音频的说话人性别...")

    # Extract full audio track once (mono, 22050Hz for librosa)
    audio_path = _extract_full_audio(video_path)
    if audio_path is None:
        print("[音频分析] 音频提取失败，跳过性别检测")
        return {}

    try:
        y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    except Exception as e:
        print(f"[音频分析] 音频加载失败: {e}")
        _cleanup(audio_path)
        return {}

    total_duration = len(y) / sr

    # Group subtitle indices by speaker
    speaker_segments = defaultdict(list)
    for i, event in enumerate(subs):
        text = event.text.replace("\\N", "").replace("\n", "").strip()
        if not text or text.startswith("[") or text.startswith("("):
            continue

        speaker_id = "default"
        if speaker_map and i in speaker_map:
            sid = speaker_map[i]
            speaker_id = sid if isinstance(sid, str) else str(sid)

        start_s = max(0, min(event.start / 1000.0, total_duration - 0.1))
        end_s = max(start_s + 0.1, min(event.end / 1000.0, total_duration))

        speaker_segments[speaker_id].append((i, start_s, end_s))

    # Try loading wav2vec2 model
    use_neural = _load_wav2vec2()
    method_name = "wav2vec2" if use_neural else "音高分析"
    print(f"[音频分析] 使用 {method_name} 进行性别检测")

    # For each speaker, concatenate all their audio segments and classify
    speaker_gender = {}
    for speaker_id, segments in speaker_segments.items():
        combined = []
        for _, start_s, end_s in segments:
            start_sample = int(start_s * sr)
            end_sample = int(end_s * sr)
            seg = y[start_sample:end_sample]
            if len(seg) > sr * 0.05:
                combined.append(seg)

        if not combined:
            continue

        combined_audio = np.concatenate(combined)
        if len(combined_audio) < sr * 0.5:
            continue

        # Try wav2vec2 first, fall back to pitch
        if use_neural:
            gender, confidence = _classify_segment_wav2vec2(combined_audio, sr)
            if gender is None or confidence < 0.55:
                # Low confidence from neural model — try pitch as tiebreaker
                pitch_gender, pitch_conf = _classify_segment_pitch(combined_audio, sr)
                if gender is None:
                    gender, confidence = pitch_gender, pitch_conf
                elif pitch_gender and pitch_gender != gender and pitch_conf > 0.5:
                    # Disagreement — log it, trust neural model but lower confidence
                    print(f"[音频分析] {speaker_id}: wav2vec2={gender}({confidence:.2f}) vs 音高={pitch_gender}({pitch_conf:.2f})")
                    confidence *= 0.8
        else:
            gender, confidence = _classify_segment_pitch(combined_audio, sr)

        if gender and confidence > 0.15:
            speaker_gender[speaker_id] = {"gender": gender, "confidence": confidence}

    _cleanup(audio_path)

    # Map speaker-level results back to subtitle indices
    results = {}
    for speaker_id, segments in speaker_segments.items():
        if speaker_id in speaker_gender:
            for sub_idx, _, _ in segments:
                results[sub_idx] = dict(speaker_gender[speaker_id])

    # Summary
    speaker_summary = ", ".join(
        f"{sid}={info['gender']}({info['confidence']:.2f})"
        for sid, info in sorted(speaker_gender.items())
    )
    print(f"[音频分析] 性别检测完成 ({method_name}): {speaker_summary}")

    return results


def _classify_segment_pitch(segment, sr):
    """Classify a single audio segment by pitch (F0 analysis).

    Fallback method when wav2vec2 is unavailable.

    Returns ("male"/"female"/"child", confidence) or (None, 0).
    """
    try:
        f0, voiced_flag, voiced_probs = librosa.pyin(
            segment,
            fmin=librosa.note_to_hz('C2'),
            fmax=librosa.note_to_hz('C6'),
            sr=sr,
        )

        voiced_f0 = f0[voiced_flag]
        if len(voiced_f0) < 5:
            return None, 0.0

        voiced_ratio = len(voiced_f0) / max(len(f0), 1)
        pitch_std = float(np.std(voiced_f0))
        median_f0 = float(np.median(voiced_f0))

        pitch_consistency = max(0, 1.0 - (pitch_std / max(median_f0, 1) / 0.3))
        confidence = min(1.0, voiced_ratio * pitch_consistency)

        if confidence < 0.15:
            return None, 0.0

        if median_f0 < MALE_MAX_F0:
            return "male", confidence
        elif median_f0 < FEMALE_MAX_F0:
            return "female", confidence
        else:
            return "child", confidence

    except Exception:
        return None, 0.0


def _extract_full_audio(video_path):
    """Extract full audio track from video as WAV for librosa."""
    tmp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp_file.name
    tmp_file.close()
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "22050",
        "-ac", "1",
        tmp_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return tmp_path


def _cleanup(path):
    """Remove temporary file."""
    if path and os.path.exists(path):
        os.unlink(path)
