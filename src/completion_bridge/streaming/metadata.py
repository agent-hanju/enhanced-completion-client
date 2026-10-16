"""스트림 병합 정책의 키·값과 필드 선언용 메타데이터를 정의한다.

모델 선언과 병합기는 이 정의를 공유한다. 메타데이터 딕셔너리는 선언 후 수정하지 않는다.
"""

from typing import Any, Final

__all__ = ["MERGE_META_KEY", "OVERWRITE", "INDEX", "STREAM_OVERWRITE", "STREAM_INDEX"]

MERGE_META_KEY: Final = "x-completion-bridge-merge"
OVERWRITE: Final = "overwrite"
INDEX: Final = "index"

STREAM_OVERWRITE: Final[dict[str, Any]] = {MERGE_META_KEY: OVERWRITE}
STREAM_INDEX: Final[dict[str, Any]] = {MERGE_META_KEY: INDEX}
