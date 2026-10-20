from collections import Counter
from dataclasses import dataclass
import heapq
import math
from typing import Iterable, Iterator
import unicodedata

from ...Abstract.History.D_ChatId import D_ChatId
from ...Abstract.History.I_SearchConfig import I_SearchConfig
from .C_SearchConfig import C_SearchConfig


@dataclass(frozen=True, slots=True)
class _SearchDocument:
    terms: frozenset[str]
    title_length: int
    body_length: int
    title: str


class C_RoughMatchIndex:
    """Incremental in-memory inverted index with field-weighted BM25 and character n-grams."""

    def __init__(self, config: I_SearchConfig | None = None) -> None:
        self._config = config if config is not None else C_SearchConfig()
        self._postings: dict[str, dict[str, tuple[int, int]]] = {}
        self._documents: dict[str, _SearchDocument] = {}
        self._title_length = 0
        self._body_length = 0

    def validateQuery(self, content: str, maxCnt: int) -> bool:
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        if isinstance(maxCnt, bool) or not isinstance(maxCnt, int):
            raise TypeError("maxCnt must be an integer")
        if maxCnt < 0:
            raise ValueError("maxCnt must be greater than or equal to zero")
        return maxCnt > 0 and self._config.getTokenPattern().search(unicodedata.normalize("NFKC", content)) is not None

    @staticmethod
    def _normalize(text: str) -> str:
        return unicodedata.normalize("NFKC", text).casefold()

    def _features(self, text: str) -> Iterator[str]:
        for matched in self._config.getTokenPattern().finditer(self._normalize(text)):
            token = matched.group()
            if self._config.getCjkPattern().match(token):
                for character in token:
                    yield "u:" + character
                for offset in range(len(token) - 1):
                    yield "b:" + token[offset:offset + 2]
            else:
                yield "w:" + token
                for offset in range(len(token) - 2):
                    yield "g:" + token[offset:offset + 3]

    def getChatList(self) -> list[str]:
        return list(self._documents)

    def _getPostings(self, term: str) -> dict[str, tuple[int, int]]:
        return self._postings.get(term, {})

    def removeDocument(self, chat_id: str) -> bool:
        document = self._documents.pop(chat_id, None)
        if document is None:
            return False
        self._title_length -= document.title_length
        self._body_length -= document.body_length
        for term in document.terms:
            postings = self._postings[term]
            del postings[chat_id]
            if not postings:
                del self._postings[term]
        return True

    def updateDocument(self, chat_id: str, title: str, contents: Iterable[str] | str | None = None) -> None:
        """Replace a document; contents=None retains its existing indexed body without raw text."""
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise ValueError("chat_id must be a non-empty string")
        if not isinstance(title, str):
            raise TypeError("title must be a string")
        title_counts = Counter(self._features(title))
        body_counts: Counter[str] = Counter()
        previous = self._documents.get(chat_id)
        if contents is None and previous is not None:
            body_counts.update({term: self._postings[term][chat_id][1] for term in previous.terms if self._postings[term][chat_id][1]})
        elif contents is not None:
            for text in (contents,) if isinstance(contents, str) else contents:
                if not isinstance(text, str):
                    raise TypeError("contents must contain strings")
                body_counts.update(self._features(text))
        terms = frozenset(title_counts.keys() | body_counts.keys())
        document = _SearchDocument(terms, sum(title_counts.values()), sum(body_counts.values()), " ".join(self._normalize(title).split()))
        self.removeDocument(chat_id)
        self._documents[chat_id] = document
        self._title_length += document.title_length
        self._body_length += document.body_length
        for term in terms:
            self._postings.setdefault(term, {})[chat_id] = title_counts[term], body_counts[term]

    def roughMatch(self, content: str, maxCnt: int) -> list[D_ChatId]:
        """Return up to maxCnt positive-scoring D_ChatId values, descending by relevance, ties by ID."""
        if not self.validateQuery(content, maxCnt) or not self._documents:
            return []
        features = dict.fromkeys(self._features(content), 1.0)
        has_bigrams = any(term.startswith("b:") for term in features)
        for term in features:
            if term.startswith("g:"):
                features[term] = 0.25
            elif term.startswith("u:") and has_bigrams:
                features[term] = 0.1
        count = len(self._documents)
        average_title = self._title_length / count or 1.0
        average_body = self._body_length / count or 1.0
        norms: dict[str, tuple[float, float]] = {}
        scores: dict[str, float] = {}
        for term in sorted(features):
            postings = self._getPostings(term)
            if not postings:
                continue
            inverse_frequency = math.log1p((count - len(postings) + 0.5) / (len(postings) + 0.5))
            for chat_id, (title_frequency, body_frequency) in postings.items():
                if chat_id not in norms:
                    document = self._documents[chat_id]
                    norms[chat_id] = (
                        1.2 * (0.7 + 0.3 * document.title_length / average_title),
                        1.2 * (0.25 + 0.75 * document.body_length / average_body),
                    )
                title_norm, body_norm = norms[chat_id]
                title_score = 4.0 * title_frequency * 2.2 / (title_frequency + title_norm)
                body_score = body_frequency * 2.2 / (body_frequency + body_norm)
                scores[chat_id] = scores.get(chat_id, 0.0) + features[term] * inverse_frequency * (title_score + body_score)
        normalized_query = " ".join(self._normalize(content).split())
        for chat_id in scores:
            if normalized_query in self._documents[chat_id].title:
                scores[chat_id] *= 1.5
        return [D_ChatId(chat_id) for chat_id in heapq.nsmallest(maxCnt, scores, key=lambda chat_id: (-scores[chat_id], chat_id))]
