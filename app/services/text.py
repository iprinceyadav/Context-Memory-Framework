from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple

STOP_WORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "is", "it", "by", "with", "as", "was", "that", "this", "are",
    "be", "has", "had", "have", "not", "from", "will", "can", "do", "does",
    "did", "been", "if", "so", "no", "what", "when", "where", "which",
    "who", "how", "all", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "than", "too", "very", "just", "about", "show",
    "me", "my", "get", "give", "list",
}

_WORD = re.compile(r"[a-z0-9_]+")
_TERM_SATURATION = 1.2
_SPARSE_SPACE = 2 ** 31 - 1
_index_cache: Dict[str, int] = {}


def words(text: str) -> List[str]:
    found: List[str] = []
    for word in _WORD.findall((text or "").lower()):
        if word in STOP_WORDS:
            continue
        found.append(word)
        if "_" in word:
            found.extend(part for part in word.split("_") if part and part not in STOP_WORDS)
    return found


def tokenize(text: str) -> Set[str]:
    return set(words(text))


_FOLLOW_UP_OPENERS: Set[str] = {"now", "and", "also", "only", "instead", "same", "then", "but", "just"}
_FOLLOW_UP_PAIRS: Set[Tuple[str, str]] = {("what", "about"), ("how", "about")}
_FOLLOW_UP_REFERENCES: Set[str] = {"those", "these", "them", "same", "above", "previous"}
_FOLLOW_UP_MAX_TERMS = 2


def is_follow_up(question: str) -> bool:
    raw = _WORD.findall((question or "").lower())
    if not raw:
        return False
    if raw[0] in _FOLLOW_UP_OPENERS or tuple(raw[:2]) in _FOLLOW_UP_PAIRS:
        return True
    if _FOLLOW_UP_REFERENCES & set(raw):
        return True
    return len(words(question)) <= _FOLLOW_UP_MAX_TERMS


def follow_up_query(question: str, previous_question: Optional[str]) -> str:
    if previous_question and is_follow_up(question):
        return f"{previous_question.strip()} {question.strip()}"
    return question


def _term_index(term: str) -> int:
    index = _index_cache.get(term)
    if index is None:
        index = int(hashlib.md5(term.encode("utf-8")).hexdigest()[:8], 16) % _SPARSE_SPACE
        _index_cache[term] = index
    return index


def sparse_vector(text: str, query: bool = False) -> Tuple[List[int], List[float]]:
    counts = Counter(words(text))
    weights: Dict[int, float] = {}
    for term, count in counts.items():
        weight = 1.0 if query else count * (_TERM_SATURATION + 1) / (count + _TERM_SATURATION)
        index = _term_index(term)
        weights[index] = weights.get(index, 0.0) + weight
    indices = sorted(weights)
    return indices, [weights[index] for index in indices]
