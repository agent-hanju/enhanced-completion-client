"""테스트 공통 설정.

저장소 루트의 ``.env``를 환경변수로 올린다. 라이브 시험이 모듈 수준에서 환경변수를 읽으므로
수집보다 먼저 실행되어야 하는데, ``conftest.py``가 그 자리다.

``override=False``라 이미 설정된 환경변수는 덮지 않는다. 명령줄로 준 값이 파일보다 우선이어야
한다.

``python-dotenv``는 dev 의존이다. 런타임 의존은 ``httpx``와 ``pydantic`` 둘로 유지한다.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
