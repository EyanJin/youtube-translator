"""Subtitle translation using OpenAI-compatible LLM API."""

import json
import re
from collections import Counter
from pathlib import Path
from openai import OpenAI
import pysubs2

from lib.config import create_llm_client
from lib.api_utils import call_llm_with_retry
from lib.progress import ProgressTracker


# Translate subtitles in batches for better context
BATCH_SIZE = 25

SYSTEM_PROMPT = """你是一位专业的字幕翻译，擅长将英文口语翻译为自然流畅的中文。将英文字幕逐条翻译为中文。

规则（必须严格遵守）：
1. 输入有 N 条，输出必须恰好 N 条，不多不少
2. 每行格式：编号|中文翻译（编号必须与输入一致）
3. 即使原文很短（如单个词），也必须单独翻译为一行
4. 不要合并、拆分、跳过任何条目
5. 不要添加解释或注释

翻译风格（最重要）：
- **像中国人说话一样翻译**，不是逐词对照翻译。想象一个中国人在表达同样的意思，他会怎么说？
- 避免翻译腔：不要出现"关乎"、"促进"、"从而"、"无论...如何"等书面翻译词汇，用口语化的表达替代
- 用短句，像说话一样。长定语从句要拆成多个短句
- 主语可以省略（中文习惯省略主语），不要每句都以"我们"、"它"开头
- 被动句改为主动句（"被教会" → "学会了"）
- 英文的抽象名词要具体化（"the holistic development" → "全面成长"，不是"全面发展"）

翻译对照示例：
- "SEL is really about the holistic development of young people" → "社会情感学习说白了，就是帮助年轻人全面成长"（不是"社会情感学习关乎年轻人的全面发展"）
- "It reduces anxiety and distress" → "能减少孩子的焦虑和痛苦"（不是"它还能减少焦虑和痛苦"）
- "regardless of race, socioeconomic status, or gender" → "不管什么种族、家庭条件还是性别"（不是"无论种族、社会经济地位或性别如何"）
- "We really can create schools that inspire" → "我们真的能打造出让人受到鼓舞的学校"（不是"我们真的可以创建激励人心的学校"）

其他要求：
- 如果原文是一个从句（非完整句），翻译也保持从句形式，不要补全为完整句
- **关键规则**：如果原文在句中被截断（如以 "we"、"the"、"to" 等词结尾），翻译也必须在对应位置截断，绝不补全后续内容
- 每条翻译控制在 25 个中文字以内
- 专有名词翻译：SEL=社会情感学习, STEM=科学技术工程数学
- 当原文用代词指代前文提到的专有名词时，翻译中应还原为具体名词

示例输入：
1|School should be where everyone feels comfortable,
2|SEL is really about the holistic development of young people.
3|It teaches students to manage their emotions.
4|into the dialogue about the kind of social and emotional development we
5|want to promote for all of our children in the future.

示例输出：
1|学校应该让每个人都觉得自在，
2|社会情感学习说白了，就是帮助年轻人全面成长。
3|教会学生管理自己的情绪。
4|加入到关于我们未来想为所有孩子推动的
5|那种社会和情感成长的讨论中。"""


MAX_RETRIES = 2


def _build_translate_prompt(glossary=None):
    """Build translation system prompt with optional glossary injection."""
    prompt = SYSTEM_PROMPT

    if glossary and isinstance(glossary, dict) and len(glossary) > 0:
        # Replace the hardcoded glossary line in the prompt with the full glossary
        glossary_lines = "\n".join(f"  {en}={zh}" for en, zh in glossary.items())
        glossary_section = f"\n专有名词翻译（必须严格遵守，全片保持一致）：\n{glossary_lines}"

        # Replace the existing hardcoded glossary line
        if "专有名词翻译：" in prompt:
            prompt = re.sub(
                r"- 专有名词翻译：.*$",
                f"专有名词翻译对照表（必须严格遵守）：\n{glossary_lines}",
                prompt,
                flags=re.MULTILINE,
            )
        else:
            prompt += glossary_section

    return prompt


def translate_subtitles(srt_path, config, glossary=None):
    """Translate English SRT subtitles to Chinese.

    Supports checkpoint/resume: if a previous run was interrupted, partial
    translations are loaded from a .partial.json file and processing continues
    from where it left off.

    Args:
        srt_path: Path to English SRT file.
        config: Translate config dict with base_url, api_key, model.
        glossary: Optional dict of term→translation for consistent terminology.

    Returns:
        Path to the Chinese SRT file.
    """
    srt_path = Path(srt_path)
    cn_srt_path = srt_path.with_name(srt_path.stem.replace(".en", "") + ".zh.srt")
    partial_path = srt_path.with_name(srt_path.stem.replace(".en", "") + "_translated.partial.json")

    subs = pysubs2.load(str(srt_path))
    total_batches = (len(subs) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"[翻译] 共 {len(subs)} 条字幕，分 {total_batches} 批翻译...")

    # Build system prompt with glossary injection
    system_prompt = _build_translate_prompt(glossary)

    client = create_llm_client(config)

    translated_texts = _translate_all_batches(subs, client, config["model"],
                                               partial_path, system_prompt)

    # Post-process: fix cases where LLM completed a truncated sentence,
    # causing the next entry to be a duplicate of the tail
    original_texts = [e.text.replace("\\N", " ").replace("\n", " ").strip() for e in subs]
    translated_texts = _fix_trailing_duplicates(translated_texts, original_texts)

    # Apply translations to subtitle entries
    for i, event in enumerate(subs):
        if i < len(translated_texts):
            event.text = translated_texts[i]

    subs.save(str(cn_srt_path))

    # Clean up checkpoint on success
    partial_path.unlink(missing_ok=True)

    print(f"[翻译] 中文字幕已保存: {cn_srt_path.name}")
    return cn_srt_path


def _translate_all_batches(subs, client, model, partial_path=None, system_prompt=None):
    """Translate all subtitle entries in batches with checkpoint support."""
    total = len(subs)
    if system_prompt is None:
        system_prompt = SYSTEM_PROMPT

    # Load checkpoint if available
    all_translations, resume_from = _load_translate_checkpoint(partial_path, total)
    if resume_from > 0:
        print(f"[翻译] 发现断点文件，从第 {resume_from + 1} 条继续 (已完成 {resume_from} 条)")

    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    progress = ProgressTracker(total_batches, "翻译")

    for batch_start in range(resume_from, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch = subs[batch_start:batch_end]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        # Build numbered text for this batch
        lines = []
        for i, event in enumerate(batch):
            idx = batch_start + i + 1
            text = event.text.replace("\\N", " ").replace("\n", " ").strip()
            lines.append(f"{idx}|{text}")

        user_msg = "\n".join(lines)
        expected_ids = list(range(batch_start + 1, batch_end + 1))

        # Inject context from previous batch for terminology/reference consistency
        if batch_start > 0 and len(all_translations) >= 3:
            context_count = min(3, batch_start)
            context_lines = []
            for ci in range(context_count):
                orig_idx = batch_start - context_count + ci
                orig_text = subs[orig_idx].text.replace("\\N", " ").replace("\n", " ").strip()
                trans_text = all_translations[orig_idx]
                context_lines.append(f"  {orig_text} → {trans_text}")
            context_block = "\n".join(context_lines)
            user_msg = (
                f"上文参考（仅供理解上下文，不需要翻译）：\n{context_block}\n\n"
                f"请翻译以下内容：\n{user_msg}"
            )

        progress.update(batch_num, f"第 {batch_start + 1}-{batch_end} 条")

        # Try translating with retries
        batch_translations = None
        for attempt in range(MAX_RETRIES + 1):
            reply = call_llm_with_retry(
                client, model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                on_retry=lambda a, e, d: print(f"[翻译] API 错误，{d}s 后重试 ({a}/3)..."),
            )
            parsed = _parse_by_index(reply, expected_ids)

            if len(parsed) == len(batch):
                batch_translations = parsed
                break

            if attempt < MAX_RETRIES:
                missing = [eid for eid in expected_ids if eid not in _parse_index_map(reply)]
                print(f"[翻译] 缺少 {len(missing)} 条 (编号: {missing[:5]}...)，重试中...")

        # Final fallback: fill missing with retranslation or original text
        if batch_translations is None:
            index_map = _parse_index_map(reply)
            batch_translations = []
            for i, eid in enumerate(expected_ids):
                if eid in index_map:
                    batch_translations.append(index_map[eid])
                else:
                    # Try single-line translation for missing entries
                    original = batch[i].text.replace("\\N", " ").replace("\n", " ").strip()
                    single = _translate_single(client, model, original)
                    batch_translations.append(single)

        all_translations.extend(batch_translations)

        # Save checkpoint after each batch
        if partial_path:
            _save_translate_checkpoint(partial_path, all_translations)

    return all_translations


def _save_translate_checkpoint(partial_path, translations):
    """Save translation progress to checkpoint file."""
    data = {"translations": translations, "count": len(translations)}
    Path(partial_path).write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_translate_checkpoint(partial_path, total_subs):
    """Load translation progress from checkpoint file.

    Returns:
        Tuple of (translations_list, resume_from_index).
        If no checkpoint exists, returns ([], 0).
    """
    if not partial_path or not Path(partial_path).exists():
        return [], 0

    try:
        data = json.loads(Path(partial_path).read_text(encoding="utf-8"))
        translations = data.get("translations", [])
        count = data.get("count", len(translations))

        # Sanity: checkpoint count should not exceed total
        if count > total_subs:
            print("[翻译] 断点文件与当前字幕不匹配，重新开始")
            return [], 0

        # Resume from the next batch boundary after the last completed entry
        resume_from = (count // BATCH_SIZE) * BATCH_SIZE
        # Trim translations to the batch boundary for clean resume
        translations = translations[:resume_from]

        return translations, resume_from

    except (json.JSONDecodeError, KeyError) as e:
        print(f"[翻译] 断点文件损坏，重新开始: {e}")
        return [], 0


def _fix_trailing_duplicates(translated_texts, original_texts):
    """Fix cases where the LLM completed a truncated sentence, duplicating content.

    When English entry N ends mid-sentence and entry N+1 continues it,
    the LLM sometimes translates N as a complete sentence, then N+1 repeats
    the tail. Detect and fix this overlap.
    """
    if len(translated_texts) < 2:
        return translated_texts

    result = list(translated_texts)

    for i in range(len(result) - 1):
        curr = result[i].strip()
        next_t = result[i + 1].strip()

        if not curr or not next_t or len(next_t) < 4:
            continue

        # Check if next entry is a substring of current (full duplication)
        if next_t in curr:
            overlap_start = curr.rfind(next_t)
            if overlap_start > 0:
                trimmed = curr[:overlap_start].rstrip("，、；：。！？ ")
                if len(trimmed) >= 2:
                    result[i] = trimmed
                    continue

        # Check for partial overlap: find the longest common substring
        # between the tail of curr and the beginning of next_t
        overlap = _find_longest_common_substring(curr, next_t)
        if overlap and len(overlap) >= 6:
            # Trim current entry at the overlap point
            overlap_pos = curr.find(overlap)
            if overlap_pos > 0:
                trimmed = curr[:overlap_pos].rstrip("，、；：。！？ ")
                if len(trimmed) >= 2:
                    result[i] = trimmed

    return result


def _find_longest_common_substring(text_a, text_b):
    """Find the longest substring that appears in both text_a and text_b.

    Only considers substrings that start within the last 60% of text_a
    and within the first 60% of text_b (to focus on tail-head overlaps).
    """
    min_len = 6
    best = ""

    # Only search in the tail portion of text_a
    search_start_a = max(0, len(text_a) * 2 // 5)
    # Only match against the head portion of text_b
    search_end_b = min(len(text_b), len(text_b) * 3 // 5)

    for length in range(min(len(text_a) - search_start_a, search_end_b), min_len - 1, -1):
        for start in range(search_start_a, len(text_a) - length + 1):
            substr = text_a[start:start + length]
            pos_in_b = text_b.find(substr)
            if pos_in_b >= 0 and pos_in_b < search_end_b:
                return substr

    return best


def _translate_single(client, model, text):
    """Translate a single line as fallback."""
    try:
        result = call_llm_with_retry(
            client, model,
            messages=[
                {"role": "system", "content": "将以下英文翻译为简洁的中文，只输出翻译结果："},
                {"role": "user", "content": text},
            ],
            max_retries=2,
        )
        return result
    except Exception:
        return f"[未翻译] {text}"


def _parse_by_index(text, expected_ids):
    """Parse translation response using index matching. Returns list in order of expected_ids."""
    index_map = _parse_index_map(text)
    result = []
    for eid in expected_ids:
        if eid in index_map:
            result.append(index_map[eid])
        else:
            return result  # Stop at first gap to signal incomplete
    return result


def _parse_index_map(text):
    """Parse response into {index: translation} dict."""
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        match = re.match(r"(\d+)\s*[|｜]\s*(.+)", line)
        if match:
            idx = int(match.group(1))
            result[idx] = match.group(2).strip()
    return result


SPEAKER_PROMPT = """分析以下英文字幕，识别每条字幕的说话人特征。

每条字幕已标注说话人编号（如 S1, S2）。请根据以下线索判断每个说话人的性别和年龄段：

判断线索：
- 说话内容和用词（"my daughter" → 家长, "in my classroom" → 教师, "school should be" → 可能是学生）
- 人称和视角（第一人称经历 vs 第三人称分析）
- 语气和表达风格（正式学术 vs 口语化 vs 稚嫩）
- 角色推断（教育类视频中常有：学生、教师、家长、研究者、管理者）

性别分类：male / female
年龄段分类：child（儿童/青少年）/ young（青年）/ adult（成年）/ elder（年长）

输出格式：每行一条
编号|说话人编号|性别|年龄段

规则：
- 同一说话人编号的所有条目，性别和年龄段必须一致
- 如果无法判断性别，默认 female
- 如果无法判断年龄，默认 adult
- 只输出编号和特征，不要解释

示例输出：
1|S1|female|child
2|S2|male|adult
3|S3|female|adult
4|S3|female|adult"""


SPEAKER_BATCH_SIZE = 80


def identify_speakers(srt_path, config):
    """Identify speaker traits (gender, age) in subtitles using LLM analysis.

    Args:
        srt_path: Path to restructured English SRT file.
        config: Translate config dict with base_url, api_key, model.

    Returns:
        Dict mapping subtitle index (0-based) to trait dict:
        {0: {"id": "S1", "gender": "female", "age": "child"}, ...}
    """
    subs = pysubs2.load(str(srt_path))
    if len(subs) == 0:
        return {}

    client = create_llm_client(config)

    # We need the speaker map to include speaker IDs in the prompt.
    # Read from the companion speakers.json if available.
    speaker_json_path = Path(srt_path).with_suffix(".speakers.json")
    speaker_id_map = {}
    if speaker_json_path.exists():
        with open(speaker_json_path, "r", encoding="utf-8") as f:
            speaker_id_map = {int(k): v for k, v in json.load(f).items()}

    print(f"[说话人] 正在识别说话人特征（性别/年龄）...")

    all_traits = {}

    for batch_start in range(0, len(subs), SPEAKER_BATCH_SIZE):
        batch_end = min(batch_start + SPEAKER_BATCH_SIZE, len(subs))

        lines = []
        for i in range(batch_start, batch_end):
            text = subs[i].text.replace("\\N", " ").replace("\n", " ").strip()
            speaker_id = speaker_id_map.get(i, "S?")
            lines.append(f"{i + 1}|{speaker_id}|{text}")

        try:
            reply = call_llm_with_retry(
                client, config["model"],
                messages=[
                    {"role": "system", "content": SPEAKER_PROMPT},
                    {"role": "user", "content": "\n".join(lines)},
                ],
                on_retry=lambda a, e, d: print(f"[说话人] API 错误，{d}s 后重试 ({a}/3)..."),
            )
            for line in reply.split("\n"):
                line = line.strip()
                match = re.match(
                    r"(\d+)\s*[|｜]\s*(\S+)\s*[|｜]\s*(\w+)\s*[|｜]\s*(\w+)", line
                )
                if match:
                    idx = int(match.group(1)) - 1  # Convert to 0-based
                    speaker_id = match.group(2).strip().upper()
                    gender = match.group(3).strip().lower()
                    age = match.group(4).strip().lower()

                    if gender not in ("male", "female"):
                        gender = "female"
                    if age not in ("child", "young", "adult", "elder"):
                        age = "adult"

                    all_traits[idx] = {
                        "id": speaker_id,
                        "gender": gender,
                        "age": age,
                    }

        except Exception as e:
            print(f"[说话人] 批次 {batch_start + 1}-{batch_end} 识别失败: {e}")

    # Ensure consistency: same speaker ID → same gender/age
    # Use majority vote per speaker ID
    speaker_votes = {}
    for idx, trait in all_traits.items():
        sid = trait["id"]
        if sid not in speaker_votes:
            speaker_votes[sid] = []
        speaker_votes[sid].append((trait["gender"], trait["age"]))

    speaker_consensus = {}
    for sid, votes in speaker_votes.items():
        # Pick the most common (gender, age) pair
        most_common = Counter(votes).most_common(1)[0][0]
        speaker_consensus[sid] = {"gender": most_common[0], "age": most_common[1]}

    # Apply consensus back to all entries
    for idx, trait in all_traits.items():
        consensus = speaker_consensus.get(trait["id"])
        if consensus:
            trait["gender"] = consensus["gender"]
            trait["age"] = consensus["age"]

    # Fill in any missing entries with defaults
    for i in range(len(subs)):
        if i not in all_traits:
            sid = speaker_id_map.get(i, "S?")
            consensus = speaker_consensus.get(sid, {"gender": "female", "age": "adult"})
            all_traits[i] = {"id": sid, **consensus}

    # Summary
    unique_speakers = {}
    for trait in all_traits.values():
        sid = trait["id"]
        if sid not in unique_speakers:
            unique_speakers[sid] = f"{trait['gender']}/{trait['age']}"
    summary = ", ".join(f"{k}({v})" for k, v in sorted(unique_speakers.items()))
    print(f"[说话人] 识别完成: {summary}")

    return all_traits
