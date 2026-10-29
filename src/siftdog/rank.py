"""Lexical relevance scoring (BM25) used to pick the best chunks of a page and to score results."""

import math
import re
from collections import Counter

_TOKEN = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have how in is it its of on or that the this to "
    "was were what when where which who why will with "
    "של על את עם אל זה זו זאת הוא היא הם הן אני כי גם אם או "
    "מה מי איך למה מתי איפה כל יש אין אבל".split()
)
# Hebrew vowel and cantillation marks (category Mn only; maqaf and other punctuation stay).
_HEBREW_MARKS = re.compile(r"[\u0591-\u05BD\u05BF\u05C1\u05C2\u05C4\u05C5\u05C7]")
# Quote-like marks inside a Hebrew word mark abbreviations (ת"א, צה"ל) or loan sounds (ג'ינס);
# dropping them keeps the word as one token on both the query and the document side.
_HEBREW_QUOTES = re.compile(r"(?<=[\u05D0-\u05EA])[\"'\u05F3\u05F4](?=[\u05D0-\u05EA])")


def tokenize(text: str) -> list[str]:
    text = _HEBREW_QUOTES.sub("", _HEBREW_MARKS.sub("", text.lower()))
    return [t for t in _TOKEN.findall(text) if t not in _STOPWORDS]


def bm25_scores(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    q_terms = set(tokenize(query))
    tokenized = [tokenize(d) for d in docs]
    if not q_terms or not tokenized:
        return [0.0] * len(docs)
    n = len(tokenized)
    avgdl = sum(len(t) for t in tokenized) / n or 1.0
    df = Counter(term for toks in tokenized for term in set(toks) if term in q_terms)
    scores = []
    for toks in tokenized:
        tf = Counter(toks)
        dl = len(toks)
        s = 0.0
        for term in q_terms:
            if not tf[term]:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores


def chunk(text: str, max_chars: int = 500) -> list[str]:
    """Split on paragraph boundaries, packing paragraphs into chunks of up to max_chars."""
    chunks: list[str] = []
    current = ""
    for para in (p.strip() for p in re.split(r"\n\s*\n", text)):
        if not para:
            continue
        while len(para) > max_chars:  # hard-wrap oversized paragraphs at a word boundary
            cut = para.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
        if current and len(current) + len(para) + 2 > max_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


def score_groups(query: str, groups: list[list[str]]) -> list[list[float]]:
    """BM25 scores for each group's texts, computed over one corpus of all groups so the
    IDF is shared (scoring a page's chunks alone makes IDF depend on page length)."""
    scores = iter(bm25_scores(query, [text for group in groups for text in group]))
    return [[next(scores) for _ in group] for group in groups]


def select_chunks(chunks: list[str], scores: list[float], n: int) -> list[str]:
    """The n highest-scoring chunks, in document order."""
    top = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)[:n]
    return [chunks[i] for i in sorted(top)]


def combine_scores(relevance: list[float], weight: float = 0.6) -> list[float]:
    """Blend normalized lexical relevance with the search engine's own rank order.

    The upstream engine's ordering already carries strong signals (links, freshness), so
    position acts as a prior; BM25 re-sorts within it. Output is in [0, 1].
    """
    top = max(relevance, default=0.0) or 1.0
    return [
        round(weight * (r / top) + (1 - weight) / (1 + 0.2 * i), 4) for i, r in enumerate(relevance)
    ]
