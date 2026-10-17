"""병합 구현의 시간·메모리를 동일 입력으로 비교하는 수동 실행 도구.

실행 예:
    .venv/Scripts/python.exe tests/benchmark_merge.py --baseline ca14949
    --output PATH

입력 생성과 모듈 로딩은 측정에서 제외한다. 시간은 timeit의 GC 비활성화
조건에서 구현 순서를 번갈아 측정한 중앙값이며, 메모리는 별도 실행한다.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import subprocess
import sys
import timeit
import tracemalloc
import types
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from completion_bridge import HubResponse, TextBlock, ToolResultBlock
from completion_bridge.streaming.merge import StreamMerger


def load_merger(name: str, source: str) -> Any:
    """이전 구현을 별도 모듈로 로딩하여 현재 구현과 함께 실행한다."""
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module.StreamMerger


def workloads() -> dict[str, list[BaseModel]]:
    """짧은 스트림·중첩·모델 목록·일반 값 목록을 측정 전에 생성한다."""
    class Values(BaseModel):
        values: list[int]

    nested = []
    for _ in range(30):
        block: Any = TextBlock(seq=(0,), text="hello")
        for depth in range(20):
            block = ToolResultBlock(seq=(0,), tool_use_id=str(depth), blocks=[block])
        nested.append(HubResponse(content=[block]))
    cases = {
        "text_100_deltas_8_items": [
            HubResponse(content=[TextBlock(seq=(i % 8,), text="hello")])
            for i in range(100)
        ],
        "nested_depth20_30_deltas": nested,
        "wide_1000_items": [HubResponse(content=[
            TextBlock(seq=(i,), text="hello") for i in range(1000)
        ])],
        "primitive_10000_items": [Values(values=list(range(10000)))],
    }
    for count in (100, 300, 1000):
        cases[f"growing_{count}_deltas"] = [
            HubResponse(content=[TextBlock(seq=(i,), text="hello")]) for i in range(count)
        ]
    return cases


def run(merger_type: Any, deltas: list[BaseModel], build: bool) -> Any:
    """새 누적기에 모든 델타를 적용하고 지정한 경우 최종 모델도 생성한다."""
    merger = merger_type(type(deltas[0]))
    for delta in deltas:
        merger.apply(delta)
    return (merger.build() if hasattr(merger, "build") else merger.result) if build else merger


def main() -> None:
    """기존 yield·원자적 모델·best effort 구현의 결과 일치·시간·메모리를 측정한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="ca14949")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--number", type=int, default=10)
    parser.add_argument("--skip-atomic", action="store_true")
    args = parser.parse_args()
    source = subprocess.check_output([
        "git", "show", f"{args.baseline}:src/completion_bridge/streaming/merge.py",
    ]).decode("utf-8")
    variants = {"yield": load_merger("_merge_baseline", source)}
    atomic_source = subprocess.check_output([
        "git", "show", "c841584:src/completion_bridge/streaming/merge.py",
    ]).decode("utf-8")
    variants["atomic"] = load_merger("_merge_atomic", atomic_source)
    variants["best_effort"] = StreamMerger
    if args.skip_atomic:
        del variants["atomic"]
    report: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "baseline": args.baseline,
        "repeat": args.repeat,
        "number": args.number,
        "results": {},
    }
    for name, deltas in workloads().items():
        expected = run(variants["yield"], deltas, True).model_dump()
        for cls in variants.values():
            assert run(cls, deltas, True).model_dump() == expected, name
        number = 1 if name.startswith("growing_") else args.number
        results: dict[str, Any] = {label: {"number": number} for label in variants}
        for build in (False, True):
            samples: dict[str, list[float]] = {label: [] for label in variants}
            for repetition in range(args.repeat):
                ordered = list(variants.items())
                if repetition % 2:
                    ordered.reverse()
                for label, cls in ordered:
                    timer = timeit.Timer(
                        lambda cls=cls, deltas=deltas, build=build: run(cls, deltas, build)
                    )
                    elapsed = timer.timeit(number=number)
                    samples[label].append(elapsed * 1000 / number)
            metric = "apply_result_ms" if build else "apply_ms"
            for label, values in samples.items():
                results[label][metric] = round(statistics.median(values), 4)
        for label, cls in variants.items():
            for build in (False, True):
                gc.collect()
                tracemalloc.start()
                result = run(cls, deltas, build)
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                metric = "apply_result_peak_kib" if build else "apply_peak_kib"
                results[label][metric] = round(peak / 1024, 1)
                del result
        # 결과 조회 비용은 누적 완료 후 별도로 측정한다.
        for label, cls in variants.items():
            merger = run(cls, deltas, False)
            read = merger.build if label == "yield" else lambda merger=merger: merger.result
            values = timeit.repeat(read, number=number, repeat=args.repeat)
            results[label]["result_ms"] = round(statistics.median(values) * 1000 / number, 4)
        report["results"][name] = results
        print(name, json.dumps(results), flush=True)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
