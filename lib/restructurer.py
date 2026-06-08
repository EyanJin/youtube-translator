"""Restructure fragmented YouTube subtitles into natural sentences with speaker identification.

YouTube auto-generated subtitles split text at arbitrary time boundaries, not at
sentence boundaries. This module uses an LLM to:
1. Merge fragments into complete, natural sentences
2. Identify speaker changes
3. Preserve timestamp alignment
"""

import json
import re
from pathlib import Path
from openai import OpenAI
import pysubs2

from lib.config import create_llm_client
from lib.api_utils import call_llm_with_retry
from lib.progress import ProgressTracker


RESTRUCTURE_PROMPT = """你是一位专业的字幕编辑。下面是从视频自动生成的英文字幕片段，每个片段都有编号。
这些片段在任意位置断开，不是按自然语句边界切分的。

请完成两个任务：

任务1：将片段合并为**字幕单元**。
任务2：识别说话人切换，用编号标记（S1, S2, S3...）。

## 字幕单元的定义（最重要的规则）

字幕不是文章，每条字幕必须是观众能一眼读完的**语义单元**：
- 一个短句或一个从句，而非一整个复合长句
- **严格限制：每条字幕 6-15 个英文单词**。超过 15 词必须拆分，少于 6 词必须与相邻内容合并。
- 长复合句必须在从句边界处拆分为多条字幕
- 拆分点：逗号、分号、连接词（and, but, so, because, where, that, which）、冒号
- 列举多个项目时（A, B, C, and D），必须拆分为多条字幕
- 两个独立句子绝不合并为一条字幕
- 同一说话人的连续字幕保持相同说话人编号
- **绝不产生少于 5 个词的字幕条目**（如 "and their relationships" 不能单独成条，必须与前一条合并）
- 每条字幕必须语义完整，不能以介词、连词或冠词结尾

## 说话人切换判断

- 人称变化（"I think..." vs "We believe..."）
- 视角/角色变化（学生 vs 老师 vs 家长 vs 研究者）
- 语气/风格明显变化
- 当不确定时，倾向于标记为新说话人（宁多勿少）

## 输出格式（每行一条字幕单元）

起始编号-结束编号|说话人编号|字幕文本

## 规则

- 每个片段只能属于一个字幕单元，不能重复使用
- 编号必须连续，覆盖所有输入片段
- 同一说话人的连续字幕，说话人编号相同
- 不要翻译，保持英文原文，但可以修正明显的语音识别错误（如拼写错误、专有名词识别错误）
- 如果说话人在句中停顿（如 "are they showing her to..."），后续内容仍属于同一句话，必须合并或保持连贯，不要因为停顿而断开语义

## 示例

输入：
1|school should be
2|where everyone feels comfortable and
3|everyone should know that everyone's
4|unique and you don't have to be perfect
5|sel is really about the holistic
6|development of young people
7|with social emotional learning gives the
8|space where people can actually
9|look at students as more than just
10|numbers on a standardized test
11|we give people the space to explore
12|who they are as people

输出：
1-2|S1|School should be where everyone feels comfortable,
3-4|S1|and everyone should know that everyone's unique and you don't have to be perfect.
5-6|S2|SEL is really about the holistic development of young people.
7-10|S3|With social emotional learning, we give the space where people can look at students as more than just numbers on a standardized test;
11-12|S3|we give people the space to explore who they are as people."""


BATCH_SIZE = 50  # Process subtitles in batches to stay within context limits
OVERLAP_SIZE = 8  # Overlap between batches to avoid cross-batch truncation


def restructure_subtitles(srt_path, config, glossary=None):
    """Restructure fragmented subtitles into subtitle units with speaker info.

    Supports checkpoint/resume: if a previous run was interrupted, partial results
    are loaded from a .partial.json file and processing continues from where it
    left off.

    Args:
        srt_path: Path to the (overlap-fixed) English SRT file.
        config: Translate config dict with base_url, api_key, model.
        glossary: Optional dict of term→translation for terminology preservation.

    Returns:
        Tuple of (restructured_subs: pysubs2.SSAFile, speaker_map: dict).
        speaker_map maps subtitle index (0-based) to speaker type.
    """
    subs = pysubs2.load(str(srt_path))
    if len(subs) == 0:
        return subs, {}

    # Pre-filter: remove trailing noise entries (e.g., isolated "you", "yeah")
    # These are often video outro artifacts from auto-generated subtitles
    subs = _strip_trailing_noise(subs)

    # Short video fast path: skip LLM restructuring for very few subtitles
    SHORT_VIDEO_THRESHOLD = 8
    if len(subs) <= SHORT_VIDEO_THRESHOLD:
        print(f"[重组] 短视频模式: 仅 {len(subs)} 条字幕，使用简化处理")
        return _restructure_short_video(subs)

    # Build restructure prompt with glossary and word limit from config
    max_words = config.get("max_words_per_entry", 15)
    prompt = RESTRUCTURE_PROMPT
    if max_words != 15:
        prompt = prompt.replace(
            "每条字幕 6-15 个英文单词**。超过 15 词必须拆分",
            f"每条字幕 6-{max_words} 个英文单词**。超过 {max_words} 词必须拆分",
        )
    if glossary and isinstance(glossary, dict) and len(glossary) > 0:
        terms_list = ", ".join(glossary.keys())
        prompt += f"\n\n## 专有名词（请保持原样，不要修正这些词）\n{terms_list}"

    # Checkpoint file for resume support
    partial_path = Path(str(srt_path).replace(".srt", "_restructured.partial.json"))

    # Try to resume from checkpoint
    all_sentences, covered_up_to, resume_batch = _load_checkpoint(partial_path, len(subs))
    if resume_batch > 0:
        print(f"[重组] 发现断点文件，从第 {covered_up_to + 1} 个片段处继续 "
              f"(已完成 {len(all_sentences)} 个字幕单元)")

    client = create_llm_client(config)

    total_batches = (len(subs) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"[重组] 正在将 {len(subs)} 个字幕片段重组为字幕单元 (共 {total_batches} 批)...")

    # Track speaker assignments from previous batches for cross-batch consistency
    speaker_summary_for_context = ""
    progress = ProgressTracker(total_batches, "重组")

    for batch_start in range(resume_batch, len(subs), BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, len(subs))
        batch_num = batch_start // BATCH_SIZE + 1

        # Add overlap context from previous batch end
        context_start = batch_start
        if batch_start > 0:
            context_start = max(0, batch_start - OVERLAP_SIZE)

        lines = []
        for i in range(context_start, batch_end):
            idx = i + 1  # 1-based
            text = subs[i].text.replace("\\N", " ").replace("\n", " ").strip()
            prefix = ""
            if i < batch_start:
                prefix = "[上文参考] "
            lines.append(f"{prefix}{idx}|{text}")

        user_msg = "\n".join(lines)
        if context_start < batch_start:
            # Build context header with speaker continuity info
            context_header = (
                f"注意：编号 {context_start + 1}-{batch_start} 是上一批的上下文参考，"
                f"帮助你理解衔接。只需输出编号 {batch_start + 1} 及之后的内容。\n"
            )
            if speaker_summary_for_context:
                context_header += (
                    f"\n上一批的说话人分配如下，请保持一致：\n"
                    f"{speaker_summary_for_context}\n"
                )
            user_msg = context_header + "\n" + user_msg

        progress.update(batch_num, f"片段 {batch_start + 1}-{batch_end}")

        reply = call_llm_with_retry(
            client, config["model"],
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_msg},
            ],
            on_retry=lambda a, e, d: print(f"[重组] API 错误，{d}s 后重试 ({a}/3)..."),
        )
        sentences = _parse_restructured(reply, subs)

        # Deduplicate: only keep sentences whose fragment range starts after
        # what we've already committed
        for sent in sentences:
            if sent["frag_start"] >= covered_up_to:
                all_sentences.append(sent)
                covered_up_to = sent["frag_end"] + 1

        # Build speaker summary for next batch's context
        speaker_summary_for_context = _build_speaker_summary(all_sentences)

        # Save checkpoint after each batch
        _save_checkpoint(partial_path, all_sentences, covered_up_to, batch_start + BATCH_SIZE)

    if not all_sentences:
        print("[重组] 重组失败，使用原始字幕")
        _remove_checkpoint(partial_path)
        return subs, {}

    # Post-processing: realign speaker IDs across batches, then fix truncated entries
    all_sentences = _realign_speaker_ids(all_sentences)
    all_sentences = _postprocess_sentences(all_sentences)

    # Build new subtitle file and speaker map
    new_subs = pysubs2.SSAFile()
    speaker_map = {}

    for i, sent in enumerate(all_sentences):
        event = pysubs2.SSAEvent(
            start=sent["start_ms"],
            end=sent["end_ms"],
            text=sent["text"],
        )
        new_subs.append(event)
        speaker_map[i] = sent["speaker"]

    # Count speakers
    speakers = {}
    for s in speaker_map.values():
        speakers[s] = speakers.get(s, 0) + 1
    speaker_summary = ", ".join(f"{k}:{v}句" for k, v in sorted(speakers.items()))

    print(f"[重组] 完成: {len(subs)} 个片段 → {len(new_subs)} 个字幕单元")
    print(f"[重组] 说话人分布: {speaker_summary}")

    # Clean up checkpoint file on success
    _remove_checkpoint(partial_path)

    return new_subs, speaker_map


def _save_checkpoint(partial_path, sentences, covered_up_to, next_batch_start):
    """Save restructuring progress to checkpoint file."""
    data = {
        "covered_up_to": covered_up_to,
        "next_batch_start": next_batch_start,
        "sentences": sentences,
    }
    Path(partial_path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_checkpoint(partial_path, total_subs):
    """Load restructuring progress from checkpoint file.

    Returns:
        Tuple of (sentences_list, covered_up_to, resume_batch_start).
        If no checkpoint exists, returns ([], 0, 0).
    """
    if not Path(partial_path).exists():
        return [], 0, 0

    try:
        data = json.loads(Path(partial_path).read_text(encoding="utf-8"))
        sentences = data.get("sentences", [])
        covered_up_to = data.get("covered_up_to", 0)
        next_batch_start = data.get("next_batch_start", 0)

        # Sanity check: if checkpoint is for a different subtitle count, discard
        if next_batch_start > total_subs:
            print("[重组] 断点文件与当前字幕不匹配，重新开始")
            return [], 0, 0

        return sentences, covered_up_to, next_batch_start

    except (json.JSONDecodeError, KeyError) as e:
        print(f"[重组] 断点文件损坏，重新开始: {e}")
        return [], 0, 0


def _remove_checkpoint(partial_path):
    """Remove checkpoint file."""
    Path(partial_path).unlink(missing_ok=True)


def _restructure_short_video(subs):
    """Simple restructuring for short videos (≤8 subtitles).

    Merges adjacent short entries (< 6 words) without LLM,
    assigns all to speaker S1.
    """
    sentences = []
    current_text = ""
    current_start = None
    current_end = None

    for i, event in enumerate(subs):
        text = event.text.replace("\\N", " ").replace("\n", " ").strip()
        if not text:
            continue

        if current_text == "":
            current_text = text
            current_start = event.start
            current_end = event.end
        elif len(current_text.split()) < 6:
            # Merge short entries
            current_text += " " + text
            current_end = event.end
        else:
            sentences.append({
                "start_ms": current_start,
                "end_ms": current_end,
                "text": current_text,
                "speaker": "S1",
            })
            current_text = text
            current_start = event.start
            current_end = event.end

    if current_text:
        sentences.append({
            "start_ms": current_start,
            "end_ms": current_end,
            "text": current_text,
            "speaker": "S1",
        })

    new_subs = pysubs2.SSAFile()
    speaker_map = {}
    for i, sent in enumerate(sentences):
        new_subs.append(pysubs2.SSAEvent(
            start=sent["start_ms"], end=sent["end_ms"], text=sent["text"],
        ))
        speaker_map[i] = "S1"

    print(f"[重组] 短视频完成: {len(subs)} 个片段 → {len(new_subs)} 个字幕单元")
    return new_subs, speaker_map


def _build_speaker_summary(sentences):
    """Build a speaker summary string for cross-batch context injection.

    Extracts the last few sentences per speaker to give the LLM context about
    who has been speaking and what they sound like.
    """
    if not sentences:
        return ""

    # Collect unique speakers and their last seen text
    speakers = {}
    for sent in sentences:
        sid = _normalize_speaker_id(sent["speaker"])
        speakers[sid] = sent["text"][:60]

    lines = []
    for sid, sample in sorted(speakers.items()):
        lines.append(f"  {sid}: \"{sample}...\"")

    return "\n".join(lines)


def _realign_speaker_ids(sentences):
    """Realign speaker IDs to ensure consistency and remove gaps.

    1. Normalize all speaker IDs (S1F → S1, etc.)
    2. Renumber to be contiguous (S1, S3, S5 → S1, S2, S3)
    """
    if not sentences:
        return sentences

    # Normalize all IDs first
    for sent in sentences:
        sent["speaker"] = _normalize_speaker_id(sent["speaker"])

    # Collect unique speakers in order of first appearance
    seen = []
    for sent in sentences:
        sid = sent["speaker"]
        if sid not in seen:
            seen.append(sid)

    # Build renumbering map: preserve order of appearance
    remap = {}
    for i, old_sid in enumerate(seen):
        remap[old_sid] = f"S{i + 1}"

    # Apply renumbering
    for sent in sentences:
        sent["speaker"] = remap.get(sent["speaker"], sent["speaker"])

    if len(remap) > 1:
        mapping_str = ", ".join(f"{k}→{v}" for k, v in remap.items() if k != v)
        if mapping_str:
            print(f"[重组] 说话人编号重映射: {mapping_str}")

    return sentences


def _postprocess_sentences(sentences):
    """Fix truncated entries, merge ultra-short entries, and clean up artifacts.

    - Merge entries ending with '...' into the next entry if same speaker and close in time
    - Merge entries that end mid-sentence (no terminal punctuation, ends with function words)
    - Merge ultra-short entries (<5 words) with adjacent same-speaker entries
    - Skip isolated ultra-short noise entries at the end (e.g., 'You.' as video outro)
    """
    if not sentences:
        return sentences

    # Words that signal a sentence is incomplete when they appear at the end
    INCOMPLETE_ENDINGS = {
        "the", "a", "an", "to", "of", "in", "on", "at", "for", "with",
        "and", "but", "or", "so", "that", "which", "who", "more", "most",
        "likely", "their", "they're", "is", "are", "was", "were", "be",
        "this", "these", "those", "its", "her", "his", "my", "our", "your",
        "economic", "social", "despite", "including", "such",
    }

    # Pass 1: Merge truncated entries (ending with ... or incomplete words or trailing comma)
    result = []
    i = 0
    while i < len(sentences):
        sent = dict(sentences[i])  # shallow copy
        text = sent["text"].strip()

        is_truncated = (
            text.endswith("...") or text.endswith("…")
            or text.endswith(",")  # Trailing comma = mid-list/mid-sentence
            or _ends_incomplete(text, INCOMPLETE_ENDINGS)
        )

        if is_truncated and i + 1 < len(sentences):
            next_sent = sentences[i + 1]
            same_speaker = (
                _normalize_speaker_id(sent["speaker"])
                == _normalize_speaker_id(next_sent["speaker"])
            )
            close_in_time = (next_sent["start_ms"] - sent["end_ms"]) < 2000

            if same_speaker or close_in_time:
                clean_text = text.rstrip(".…").rstrip() if (text.endswith("...") or text.endswith("…")) else text
                merged_text = clean_text + " " + next_sent["text"]
                sent["text"] = merged_text
                sent["end_ms"] = next_sent["end_ms"]
                sent["frag_end"] = next_sent["frag_end"]
                if not same_speaker:
                    sent["speaker"] = next_sent["speaker"]
                result.append(sent)
                i += 2
                continue

        # Skip ultra-short noise at the very end of the video
        is_last = (i == len(sentences) - 1)
        is_noise = len(text.replace(".", "").replace("。", "").strip()) <= 2
        if is_last and is_noise:
            i += 1
            continue

        result.append(sent)
        i += 1

    # Pass 2: Merge ultra-short entries (<5 words) with adjacent same-speaker entries
    merged = []
    i = 0
    while i < len(result):
        sent = dict(result[i])
        word_count = len(sent["text"].split())

        if word_count < 5 and i > 0:
            # Try to merge with previous entry (same speaker)
            prev = merged[-1]
            same_speaker = (
                _normalize_speaker_id(sent["speaker"])
                == _normalize_speaker_id(prev["speaker"])
            )
            if same_speaker:
                prev["text"] = prev["text"].rstrip(",;") + " " + sent["text"]
                prev["end_ms"] = sent["end_ms"]
                prev["frag_end"] = sent["frag_end"]
                i += 1
                continue

        if word_count < 5 and i + 1 < len(result):
            # Try to merge with next entry (same speaker)
            next_sent = result[i + 1]
            same_speaker = (
                _normalize_speaker_id(sent["speaker"])
                == _normalize_speaker_id(next_sent["speaker"])
            )
            if same_speaker:
                sent["text"] = sent["text"].rstrip(",;") + " " + next_sent["text"]
                sent["end_ms"] = next_sent["end_ms"]
                sent["frag_end"] = next_sent["frag_end"]
                merged.append(sent)
                i += 2
                continue

        merged.append(sent)
        i += 1

    # Pass 3: Fix garbled text from mid-sentence pauses
    # Detect patterns like "word to When next" (unexpected capitalization mid-sentence)
    cleaned = []
    for sent in merged:
        text = sent["text"]
        text = _fix_garbled_joins(text)
        sent["text"] = text
        cleaned.append(sent)

    # Pass 4: Split entries that are too long (>12s duration)
    MAX_DURATION_MS = 12000
    final = []
    for sent in cleaned:
        duration_ms = sent["end_ms"] - sent["start_ms"]
        if duration_ms > MAX_DURATION_MS:
            splits = _split_long_entry(sent, MAX_DURATION_MS)
            final.extend(splits)
        else:
            final.append(sent)

    # Pass 5: Strip trailing noise from the last entry (e.g., "... future. You.")
    if final:
        last = final[-1]
        text = last["text"].strip()
        # Remove trailing isolated short words that are video outro noise
        noise_patterns = [" You.", " you.", " You", " you"]
        for pattern in noise_patterns:
            if text.endswith(pattern) and len(text) > len(pattern) + 10:
                last["text"] = text[:-len(pattern)].strip()
                break

    return final


def _ends_incomplete(text, incomplete_words):
    """Check if text ends with a word that signals an incomplete sentence."""
    # Strip trailing punctuation for check
    clean = text.rstrip(".,;:!?").strip()
    if not clean:
        return False
    last_word = clean.split()[-1].lower()
    return last_word in incomplete_words


def _fix_garbled_joins(text):
    """Fix garbled text caused by mid-sentence pauses in auto-generated subtitles.

    Detects patterns like "showing her to When teachers" where an unexpected
    capital letter appears mid-sentence (indicating a bad join from two fragments).
    """
    # Pattern: lowercase word followed by space and unexpected Capitalized word
    # that isn't a proper noun (I, SEL, etc.)
    PROPER_NOUNS = {"I", "SEL", "STEM", "AI", "US", "USA", "UK", "CASEL"}

    words = text.split()
    if len(words) < 3:
        return text

    fixed_words = [words[0]]
    for i in range(1, len(words)):
        word = words[i]
        prev_word = words[i - 1]

        # Check if this word is unexpectedly capitalized mid-sentence
        if (word[0].isupper()
                and word not in PROPER_NOUNS
                and not prev_word.endswith((".", "!", "?", ":", ";"))
                and not prev_word[0].isupper()):
            # Lowercase it — it was likely a fragment boundary artifact
            fixed_words.append(word[0].lower() + word[1:])
        else:
            fixed_words.append(word)

    return " ".join(fixed_words)


def _split_long_entry(sent, max_duration_ms):
    """Split an entry that exceeds max duration into smaller pieces at sentence boundaries.

    Tries to split at: period, semicolon, comma, then falls back to word-count midpoint.
    """
    text = sent["text"]
    duration_ms = sent["end_ms"] - sent["start_ms"]

    # Try to find a split point in the text
    split_chars = [". ", "; ", ", "]
    best_pos = None

    for sep in split_chars:
        # Find split point closest to the middle
        mid = len(text) // 2
        positions = []
        idx = 0
        while True:
            pos = text.find(sep, idx)
            if pos == -1:
                break
            positions.append(pos + len(sep))
            idx = pos + 1

        if positions:
            best_pos = min(positions, key=lambda p: abs(p - mid))
            break

    if best_pos is None or best_pos < 5 or best_pos > len(text) - 5:
        # No good split point — split at word boundary near middle
        words = text.split()
        if len(words) >= 4:
            mid_word = len(words) // 2
            part1 = " ".join(words[:mid_word])
            part2 = " ".join(words[mid_word:])
        else:
            return [sent]  # Too short to split
    else:
        part1 = text[:best_pos].strip()
        part2 = text[best_pos:].strip()

    if not part1 or not part2:
        return [sent]

    # Distribute time proportionally
    ratio = len(part1) / max(len(text), 1)
    split_ms = sent["start_ms"] + int(duration_ms * ratio)

    sent1 = dict(sent)
    sent1["text"] = part1
    sent1["end_ms"] = split_ms

    sent2 = dict(sent)
    sent2["text"] = part2
    sent2["start_ms"] = split_ms

    # Recursively split if still too long
    results = []
    for s in [sent1, sent2]:
        if s["end_ms"] - s["start_ms"] > max_duration_ms:
            results.extend(_split_long_entry(s, max_duration_ms))
        else:
            results.append(s)

    return results


def _normalize_speaker_id(speaker):
    """Normalize speaker ID for comparison: 'S1F' → 'S1'."""
    match = re.match(r"(S\d+)", str(speaker).upper().strip())
    return match.group(1) if match else speaker


def _strip_trailing_noise(subs):
    """Remove trailing noise entries from auto-generated subtitles.

    YouTube auto-captions often end with isolated short words like "you",
    "yeah", "thanks" that are video outro artifacts or misrecognized audio.
    These cause timing issues when merged into the last real sentence.
    """
    NOISE_WORDS = {"you", "yeah", "yes", "no", "okay", "ok", "thanks", "bye",
                   "um", "uh", "hmm", "ah", "oh"}

    while len(subs) > 1:
        last = subs[-1]
        text = last.text.replace("\\N", " ").replace("\n", " ").strip().lower()
        text_clean = text.rstrip(".,!?;: ")

        # Check if it's a short noise entry
        if len(text_clean.split()) <= 2 and text_clean in NOISE_WORDS:
            # Also check for a time gap from the previous entry (>2s gap = likely noise)
            prev = subs[-2]
            gap_ms = last.start - prev.end
            if gap_ms > 1500 or len(text_clean.split()) == 1:
                print(f"[重组] 过滤尾部噪音: '{text_clean}' ({last.start}ms-{last.end}ms)")
                subs.events.pop()
                continue
        break

    return subs


def _parse_restructured(text, original_subs):
    """Parse LLM restructuring output into sentence list."""
    sentences = []

    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # Match: 1-4|S1|sentence text  or  1-4|F|sentence text
        match = re.match(r"(\d+)\s*-\s*(\d+)\s*[|｜]\s*(\S+)\s*[|｜]\s*(.+)", line)
        if not match:
            # Try single number: 5|S1|sentence
            match = re.match(r"(\d+)\s*[|｜]\s*(\S+)\s*[|｜]\s*(.+)", line)
            if match:
                start_idx = int(match.group(1)) - 1
                end_idx = start_idx
                speaker_raw = match.group(2).strip()
                sent_text = match.group(3).strip()
            else:
                continue
        else:
            start_idx = int(match.group(1)) - 1
            end_idx = int(match.group(2)) - 1
            speaker_raw = match.group(3).strip()
            sent_text = match.group(4).strip()

        # Clamp indices to valid range
        start_idx = max(0, min(start_idx, len(original_subs) - 1))
        end_idx = max(start_idx, min(end_idx, len(original_subs) - 1))

        # Normalize speaker ID: S1/S2/S3 or legacy F/M/C
        speaker = speaker_raw.upper()

        sentences.append({
            "start_ms": original_subs[start_idx].start,
            "end_ms": original_subs[end_idx].end,
            "text": sent_text,
            "speaker": speaker,
            "frag_start": start_idx,
            "frag_end": end_idx,
        })

    return sentences
