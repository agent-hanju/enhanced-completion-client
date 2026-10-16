# 개발 환경과 정적 검사

저장소 루트에서 `uv sync --group dev`로 개발 의존성을 설치한다.
프로젝트는 `src/completion_bridge`를 editable 패키지로 설치하며 Python 3.12 이상을 사용한다.
시스템 Python을 선택하면 해당 환경의 Pydantic·httpx·pytest를 IDE가 찾지 못할 수 있다.

## VS Code

[설정 예제](../examples/vscode.settings.json)를 `.vscode/settings.json`으로 복사한다.
이 디렉터리는 개인 IDE 설정이므로 Git에서 제외한다. 기존 설정이 있으면 항목을 병합한다.
`Python: Select Interpreter`에서도 이 저장소의 `.venv/Scripts/python.exe`를 선택한다.
이미 선택한 인터프리터는 `python.defaultInterpreterPath`보다 우선할 수 있다.

Pylance는 `pyproject.toml`의 Pyright 설정으로 src·tests·examples를 검사한다.
Mypy 확장은 선택한 인터프리터를 사용하고 src만 strict 검사한다. 테스트는 Pyright로
함께 검사하므로 Mypy의 대상 축소가 테스트 타입 검증 전체를 비활성화하는 것은 아니다.
패키지 이름은 `completion-bridge`, import 이름은 `completion_bridge`다.

```powershell
uv run ruff check src tests examples
uv run ruff format --check src tests examples
uv run mypy src
npx --yes pyright --pythonpath .venv/Scripts/python.exe
uv run pytest --cov=completion_bridge
uv build
```

Pyright CLI는 Node 도구이며 Python 모듈로 설치되어 있지 않다. CLI 실행 시
`--pythonpath`를 생략하면 시스템 Python을 찾아 의존성 누락을 보고할 수 있다.
Pyright와 Mypy는 서로 다른 검사이므로 두 결과를 별도로 확인한다.

## 외부 서비스 테스트

기본 pytest는 외부 호출을 하지 않는다. `.env.example`의 항목을 참고해 `.env`를 설정한 후
`uv run pytest -m 'live and not slow'`로 외부 테스트를 실행한다.
키·엔드포인트·모델 설정이 있어도 실제 권한이나 서비스 가용성을 보장하지 않는다.
HTTP 401·404는 실제 실패로 보고하며, 건너뛴 테스트를 통과로 계산하지 않는다.
