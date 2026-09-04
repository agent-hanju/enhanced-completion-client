"""테스트 공통 설정.

저장소 루트의 ``.env``를 환경변수로 올린다. 라이브 시험이 모듈 수준에서 환경변수를 읽으므로
수집보다 먼저 실행되어야 하는데, ``conftest.py``가 그 자리다.

``override=False``라 이미 설정된 환경변수는 덮지 않는다. 명령줄로 준 값이 파일보다 우선이어야
한다.

``python-dotenv``는 dev 의존이다. 런타임 의존은 ``httpx``와 ``pydantic`` 둘로 유지한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

from enhanced_completion import TransportError

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

# 벤더 용량 문제를 뜻하는 상태 코드.
CAPACITY_CODES = frozenset({429, 503})


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Iterator[pytest.TestReport]:
    """라이브 시험에서 벤더 용량 문제를 실패가 아니라 건너뜀으로 바꾼다.

    429와 503은 우리 계약이 아니라 그 서비스의 그때 사정이다. 요청이 400이나 401이 아니라
    거기까지 도달했다는 뜻이므로 요청 모양은 맞다. 이것을 실패로 두면 검증 결과가 외부
    가용성에 묶여 우리 코드의 문제와 구분되지 않는다.

    재시도로 넘기지 않는다. 이 라이브러리는 재시도 정책을 소유하지 않고 전송 계층과 소비 앱의
    몫으로 둔다. 시험도 그 경계를 지킨다.

    fixture로는 할 수 없다. pytest는 시험 본문의 예외를 fixture 제너레이터에 던지지 않으므로
    리포트 단계에서 갈아야 한다.

    ``live`` 마커가 붙은 시험에만 적용한다. 오프라인 시험에서 이 코드가 나오면 실제 결함이다.
    """
    report = yield
    if (
        report.when != "call"
        or not report.failed
        or item.get_closest_marker("live") is None
        or call.excinfo is None
    ):
        return report

    error = call.excinfo.value
    if isinstance(error, TransportError) and error.status_code in CAPACITY_CODES:
        report.outcome = "skipped"
        report.longrepr = (
            str(item.path),
            item.location[1] or 0,
            f"Skipped: upstream returned {error.status_code} ({error.detail[:100]})",
        )
    return report
