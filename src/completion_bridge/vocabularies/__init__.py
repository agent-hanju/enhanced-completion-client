"""기본 제공 어휘.

``cite``가 본문 태그에서 인용을 들어올린다. 네이티브 인용 채널이 있는 벤더(Anthropic
Messages)는 이 어휘 없이 어댑터가 바로 허브 블록을 만든다.
"""

from .cite import CITE_PATH, CiteVocabulary, cite_schema

__all__ = ["CITE_PATH", "CiteVocabulary", "cite_schema"]
