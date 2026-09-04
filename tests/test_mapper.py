"""합성. flush 순서가 계약이다."""

from __future__ import annotations

from enhanced_completion import compose, identity_mapper


class Doubler:
    """1:N 변환. 상태 없음."""

    def map(self, delta: int) -> list[int]:
        return [delta, delta]

    def flush(self) -> list[int]:
        return []


class Buffering:
    """개행이 올 때까지 모으는 상태 매퍼. 남은 것은 flush로 낸다."""

    def __init__(self) -> None:
        self.buffer = ""

    def map(self, delta: str) -> list[str]:
        self.buffer += delta
        out: list[str] = []
        while "\n" in self.buffer:
            line, _, self.buffer = self.buffer.partition("\n")
            out.append(line)
        return out

    def flush(self) -> list[str]:
        if not self.buffer:
            return []
        rest, self.buffer = self.buffer, ""
        return [rest]


class Upper:
    def map(self, delta: str) -> list[str]:
        return [delta.upper()]

    def flush(self) -> list[str]:
        return []


class TailMarker:
    """자신도 flush에서 값을 내는 매퍼. 순서 검증용."""

    def map(self, delta: str) -> list[str]:
        return [delta]

    def flush(self) -> list[str]:
        return ["<end>"]


class TestIdentity:
    def test_passes_through(self) -> None:
        mapper = identity_mapper()
        assert mapper.map(1) == [1]
        assert mapper.flush() == []

    def test_compose_with_no_args_is_identity(self) -> None:
        assert compose().map("x") == ["x"]


class TestComposition:
    def test_single_mapper_is_returned_as_is(self) -> None:
        assert compose(Doubler()).map(3) == [3, 3]

    def test_map_is_flattened_left_to_right(self) -> None:
        pipeline = compose(Buffering(), Upper())
        assert pipeline.map("ab\ncd\n") == ["AB", "CD"]

    def test_three_stages_chain(self) -> None:
        pipeline = compose(Buffering(), Upper(), Upper())
        assert pipeline.map("x\n") == ["X"]


class TestFlushOrder:
    def test_first_stage_leftovers_pass_through_second(self) -> None:
        """순진하게 f.flush() + g.flush()로 쓰면 f의 마지막 출력이 g를 건너뛴다.

        스트림 끝에서 닫히지 않은 태그가 정확히 이 경로를 탄다.
        """
        pipeline = compose(Buffering(), Upper())
        assert pipeline.map("ab\ncd") == ["AB"]
        assert pipeline.flush() == ["CD"]

    def test_second_stage_flush_comes_last(self) -> None:
        pipeline = compose(Buffering(), TailMarker())
        assert pipeline.map("a\nb") == ["a"]
        assert pipeline.flush() == ["b", "<end>"]

    def test_flush_is_empty_when_nothing_buffered(self) -> None:
        pipeline = compose(Buffering(), Upper())
        assert pipeline.map("a\n") == ["A"]
        assert pipeline.flush() == []
