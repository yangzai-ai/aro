"""Conservative, deterministic Chinese fact extraction without extra LLM calls."""

import re

from capabilities.memory.store import MemoryFact


def extract_facts(text: str) -> list[MemoryFact]:
    """Recognize explicit first-person statements; ignore questions/quotations."""
    facts: list[MemoryFact] = []
    for sentence in re.split(r"[。！!；;\n]", text[:10000]):
        sentence = sentence.strip()
        if not sentence or any(mark in sentence for mark in ("?", "？", "“", "”", '"', "「", "」")):
            continue
        if re.search(r"哪个|什么|是否|假如|如果|例如|比如|不是|不再|不要|不负责|不喜欢", sentence):
            continue
        area = re.fullmatch(r"我(?:目前|现在)?负责([^，,。？?]{1,60}?)(?:的)?支持[，,]?", sentence)
        if area:
            facts.append(MemoryFact(f"负责范围：{area.group(1)}；工作：技术支持", "responsibility"))
            continue
        language = re.fullmatch(
            r"我(?:喜欢|偏好|希望)(?:使用|用)?(中文|英文|英语|汉语)(?:回复|回答|交流)?", sentence
        )
        if language:
            facts.append(MemoryFact(f"语言偏好：{language.group(1)}", "preference"))
            continue
        ticket = re.fullmatch(
            r"我(?:正在|上次|今天)?(?:关注|处理|查看)(?:工单)?\s*(TKT-[A-Za-z0-9-]+)",
            sentence,
            re.IGNORECASE,
        )
        if ticket:
            facts.append(MemoryFact(f"关注工单：{ticket.group(1).upper()}", "event"))
    return facts[:5]
