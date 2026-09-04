"""기본 제공 어휘.

``cite``로 시작한다. 문서 첨부 어휘는 같은 틀로 뒤에 붙인다.
"""

from .cite import CITE_PATH, CitationBlock, CiteVocabulary, cite_schema

__all__ = ["CITE_PATH", "CitationBlock", "CiteVocabulary", "cite_schema"]
