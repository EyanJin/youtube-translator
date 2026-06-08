"""Subtitle preprocessing - fix overlapping timestamps, merge for TTS, split for display."""

import re
import pysubs2


def _normalize_speaker(speaker_id):
    """Normalize speaker ID: 'S1F' → 'S1', 'S2M' → 'S2'. For same-speaker detection."""
    match = re.match(r"(S\d+)", str(speaker_id).upper().strip())
    return match.group(1) if match else speaker_id


def fix_overlapping_subtitles(srt_path):
    """Fix YouTube auto-generated subtitle overlap.

    YouTube auto-subs use a rolling two-line format where each entry overlaps
    with the previous one. This causes double-display when burning hard subtitles.

    Fix: set each entry's end time to the next entry's start time.
    """
    subs = pysubs2.load(str(srt_path))

    if not _has_overlapping_timestamps(subs):
        return srt_path

    print(f"[字幕] 检测到时间重叠，正在修复...")

    # Reload and fix
    original = pysubs2.load(str(srt_path))
    subs = pysubs2.SSAFile()
    for i in range(len(original) - 1):
        next_start = original[i + 1].start
        if original[i].end > next_start:
            original[i].end = next_start
        if original[i].end > original[i].start:
            subs.append(original[i])
    if original:
        subs.append(original[-1])

    subs.save(str(srt_path))
    print(f"[字幕] 重叠修复完成，共 {len(subs)} 条字幕")
    return srt_path


def merge_for_tts(subs, max_gap_ms=500, max_merge_duration_ms=8000, speaker_map=None):
    """Merge consecutive subtitle entries into speech segments for TTS.

    Never merges across speaker boundaries.
    """
    if not subs:
        return []

    def _get_speaker(idx):
        if speaker_map:
            return speaker_map.get(idx, "default")
        return "default"

    segments = []
    current = {
        "text": _clean_text(subs[0].text),
        "start_ms": subs[0].start,
        "end_ms": subs[0].end,
        "sub_indices": [0],
        "speaker": _get_speaker(0),
    }

    for i in range(1, len(subs)):
        gap = subs[i].start - current["end_ms"]
        merged_duration = subs[i].end - current["start_ms"]
        text = _clean_text(subs[i].text)
        speaker = _get_speaker(i)

        same_speaker = _normalize_speaker(speaker) == _normalize_speaker(current["speaker"])
        if same_speaker and gap <= max_gap_ms and merged_duration <= max_merge_duration_ms:
            current["text"] += text
            current["end_ms"] = subs[i].end
            current["sub_indices"].append(i)
        else:
            segments.append(current)
            current = {
                "text": text,
                "start_ms": subs[i].start,
                "end_ms": subs[i].end,
                "sub_indices": [i],
                "speaker": speaker,
            }

    segments.append(current)
    return segments


def _has_overlapping_timestamps(subs):
    overlap_count = 0
    for i in range(len(subs) - 1):
        if subs[i].end > subs[i + 1].start:
            overlap_count += 1
    return overlap_count > len(subs) * 0.3


def _clean_text(text):
    """Clean subtitle text for TTS input."""
    return text.replace("\\N", "").replace("\n", "").strip()


# ============================================================
# Subtitle display splitting
# ============================================================

# Chinese punctuation that can serve as break points
CLAUSE_BREAKS = set("，、；：）》")
SENTENCE_ENDS = set("。！？")
ALL_PUNCT = CLAUSE_BREAKS | SENTENCE_ENDS

# Display limits
MAX_CHARS_PER_LINE = 18
MAX_CHARS_PER_ENTRY = MAX_CHARS_PER_LINE * 2  # 36 chars = 2 lines


def split_long_subtitles(srt_path):
    """Split long subtitle entries into display-friendly chunks.

    Key rules:
    - ONLY break at Chinese punctuation, NEVER mid-word
    - Max 2 lines per subtitle, ~16 chars per line
    - If no punctuation available, show as single line (player handles wrapping)
    - Time distributed proportionally
    """
    subs = pysubs2.load(str(srt_path))
    new_subs = pysubs2.SSAFile()

    for event in subs:
        text = event.text.replace("\\N", "").replace("\n", "").strip()

        if len(text) <= MAX_CHARS_PER_ENTRY:
            # Fits in one entry — add line break at punctuation if needed
            display = _format_for_display(text)
            new_subs.append(pysubs2.SSAEvent(
                start=event.start, end=event.end, text=display
            ))
        else:
            # Too long — split into clauses at punctuation, then group
            chunks = _split_into_display_chunks(text)
            _distribute_time(chunks, event.start, event.end, new_subs)

    count_before = len(subs)
    count_after = len(new_subs)
    new_subs.save(str(srt_path))

    if count_after > count_before:
        print(f"[字幕] 长字幕拆分: {count_before} → {count_after} 条")

    return srt_path


def _split_into_display_chunks(text):
    """Split long text into chunks, each ≤ MAX_CHARS_PER_ENTRY.

    Only splits at punctuation boundaries. Never breaks a word.
    """
    # First, split into clauses at punctuation
    clauses = _extract_clauses(text)

    # Then group clauses into display chunks
    chunks = []
    current = ""

    for clause in clauses:
        if not current:
            current = clause
        elif len(current) + len(clause) <= MAX_CHARS_PER_ENTRY:
            current += clause
        else:
            chunks.append(current)
            current = clause

    if current:
        chunks.append(current)

    return chunks


def _extract_clauses(text):
    """Split text into clauses, each ending at a punctuation mark.

    Each clause includes its trailing punctuation.
    """
    clauses = []
    current = ""

    for char in text:
        current += char
        if char in ALL_PUNCT:
            clauses.append(current)
            current = ""

    # Remaining text without trailing punctuation
    if current:
        clauses.append(current)

    return clauses


def _distribute_time(chunks, start_ms, end_ms, new_subs):
    """Create subtitle events for chunks with proportional time allocation."""
    total_chars = sum(len(c) for c in chunks)
    total_duration = end_ms - start_ms
    current_start = start_ms

    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1:
            chunk_end = end_ms
        else:
            proportion = len(chunk) / total_chars
            chunk_end = current_start + int(total_duration * proportion)

        display = _format_for_display(chunk.strip())
        new_subs.append(pysubs2.SSAEvent(
            start=current_start, end=chunk_end, text=display
        ))
        current_start = chunk_end


def _format_for_display(text):
    """Format text for subtitle display: add line break at punctuation if needed.

    Rules:
    - If ≤ 16 chars: single line
    - If > 16 chars: try to break at punctuation near the middle
    - If no punctuation: show as single line (never break mid-word)
    """
    if len(text) <= MAX_CHARS_PER_LINE:
        return text

    # Find punctuation break point closest to the middle
    mid = len(text) // 2
    best_pos = None
    best_dist = 999

    for i, char in enumerate(text):
        if char in ALL_PUNCT and 2 < i < len(text) - 2:
            dist = abs((i + 1) - mid)
            if dist < best_dist:
                best_dist = dist
                best_pos = i + 1  # Break AFTER punctuation

    if best_pos is not None:
        line1 = text[:best_pos].strip()
        line2 = text[best_pos:].strip()
        if line1 and line2:
            return f"{line1}\\N{line2}"

    # No punctuation — show as single line, never break mid-word
    return text
