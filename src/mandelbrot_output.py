from __future__ import annotations

from collections.abc import Iterable


MANDELBROT_ALLOWED_CHARS = frozenset("0123456789ABCDEF ")
MANDELBROT_WIDTH = 79
MANDELBROT_HEIGHT = 25
_SEGMENT_WIDTHS = {1: 37, 2: 37, 3: 5}
_WRAPPED_FIRST_WIDTH = 40
_WRAPPED_SECOND_WIDTH = MANDELBROT_WIDTH - _WRAPPED_FIRST_WIDTH
_MAX_COMMON_INDENT = 4


def is_mandelbrot_art_line(line: str) -> bool:
    return len(line) == MANDELBROT_WIDTH and all(char in MANDELBROT_ALLOWED_CHARS for char in line)


def extract_mandelbrot_art_block(lines: Iterable[str]) -> list[str]:
    best: list[str] = []
    current: list[str] = []
    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if is_mandelbrot_art_line(line):
            current.append(line)
            if len(current) >= MANDELBROT_HEIGHT:
                best = list(current[-MANDELBROT_HEIGHT:])
        else:
            current = []
    return best


def _parse_segment_line(raw_line: str) -> tuple[int, str] | None:
    line = raw_line.rstrip("\r\n")
    if len(line) < 3 or not line.endswith("|"):
        return None
    try:
        segment_index = int(line[0])
    except ValueError:
        return None
    payload = line[1:-1]
    expected_width = _SEGMENT_WIDTHS.get(segment_index)
    if expected_width is None or len(payload) != expected_width:
        return None
    if any(char not in MANDELBROT_ALLOWED_CHARS for char in payload):
        return None
    return segment_index, payload


def reconstruct_segmented_mandelbrot_lines(lines: Iterable[str]) -> list[str]:
    reconstructed: list[str] = []
    first = ""
    second = ""
    stage = 0
    for raw_line in lines:
        parsed = _parse_segment_line(raw_line)
        if parsed is None:
            stage = 0
            continue
        segment_index, payload = parsed
        if segment_index == 1:
            first = payload
            second = ""
            stage = 1
            continue
        if segment_index == 2 and stage == 1:
            second = payload
            stage = 2
            continue
        if segment_index == 3 and stage == 2:
            reconstructed.append(first + second + payload)
            stage = 0
            continue
        stage = 0
    if len(reconstructed) >= MANDELBROT_HEIGHT:
        return reconstructed[-MANDELBROT_HEIGHT:]
    return []


def _is_first_plain_segment_fragment(line: str) -> bool:
    return 1 <= len(line) <= _SEGMENT_WIDTHS[1] and all(char in MANDELBROT_ALLOWED_CHARS for char in line)


def _is_second_plain_segment_fragment(line: str) -> bool:
    return 30 <= len(line) <= _SEGMENT_WIDTHS[2] and all(char in MANDELBROT_ALLOWED_CHARS for char in line)


def _is_third_plain_segment_fragment(line: str) -> bool:
    return len(line) == _SEGMENT_WIDTHS[3] and all(char in MANDELBROT_ALLOWED_CHARS for char in line)


def reconstruct_plain_segmented_mandelbrot_lines(lines: Iterable[str]) -> list[str]:
    materialized = [line.rstrip("\r\n") for line in lines]
    reconstructed: list[str] = []
    index = 0
    while index < len(materialized):
        first = materialized[index]
        if index + 2 < len(materialized):
            second = materialized[index + 1]
            third = materialized[index + 2]
            if (
                _is_first_plain_segment_fragment(first)
                and _is_second_plain_segment_fragment(second)
                and _is_third_plain_segment_fragment(third)
            ):
                logical = (
                    first.ljust(_SEGMENT_WIDTHS[1])
                    + second.ljust(_SEGMENT_WIDTHS[2])
                    + third.ljust(_SEGMENT_WIDTHS[3])
                )
                if is_mandelbrot_art_line(logical):
                    reconstructed.append(logical)
                    index += 3
                    continue
        if index + 1 < len(materialized):
            second = materialized[index]
            third = materialized[index + 1]
            if (
                _is_second_plain_segment_fragment(second)
                and _is_third_plain_segment_fragment(third)
            ):
                logical = (
                    (" " * _SEGMENT_WIDTHS[1])
                    + second.ljust(_SEGMENT_WIDTHS[2])
                    + third.ljust(_SEGMENT_WIDTHS[3])
                )
                if is_mandelbrot_art_line(logical):
                    reconstructed.append(logical)
                    index += 2
                    continue
        index += 1
    if len(reconstructed) >= MANDELBROT_HEIGHT:
        return reconstructed[-MANDELBROT_HEIGHT:]
    return []


def _is_wrapped_mandelbrot_fragment(line: str, *, max_width: int) -> bool:
    return 12 <= len(line) <= max_width and all(char in MANDELBROT_ALLOWED_CHARS for char in line)


def extract_wrapped_mandelbrot_physical_lines(lines: Iterable[str]) -> list[str]:
    materialized = [line.rstrip("\r\n") for line in lines]
    return [
        line
        for line in materialized
        if _is_wrapped_mandelbrot_fragment(line, max_width=_WRAPPED_FIRST_WIDTH)
        or _is_wrapped_mandelbrot_fragment(line, max_width=_WRAPPED_SECOND_WIDTH)
    ]


def reconstruct_wrapped_mandelbrot_lines(lines: Iterable[str]) -> list[str]:
    materialized = extract_wrapped_mandelbrot_physical_lines(lines)
    reconstructed: list[str] = []
    index = 0
    while index < len(materialized):
        current = materialized[index]
        if _is_wrapped_mandelbrot_fragment(current, max_width=_WRAPPED_SECOND_WIDTH):
            logical = (" " * _WRAPPED_FIRST_WIDTH) + current
            if is_mandelbrot_art_line(logical):
                reconstructed.append(logical)
                index += 1
                continue
        if _is_wrapped_mandelbrot_fragment(current, max_width=_WRAPPED_FIRST_WIDTH) and index + 1 < len(materialized):
            next_line = materialized[index + 1]
            if _is_wrapped_mandelbrot_fragment(next_line, max_width=_WRAPPED_SECOND_WIDTH):
                logical = current.ljust(_WRAPPED_FIRST_WIDTH) + next_line
                if is_mandelbrot_art_line(logical):
                    reconstructed.append(logical)
                    index += 2
                    continue
        index += 1
    return reconstructed


def extract_wrapped_mandelbrot_block(lines: Iterable[str]) -> list[str]:
    reconstructed = reconstruct_wrapped_mandelbrot_lines(lines)
    if len(reconstructed) >= MANDELBROT_HEIGHT:
        return reconstructed[-MANDELBROT_HEIGHT:]
    return []


def has_segmented_mandelbrot_fragments(lines: Iterable[str]) -> bool:
    materialized = [line.rstrip("\r\n") for line in lines]
    if any(_parse_segment_line(line) is not None for line in materialized):
        return True
    return _has_plain_segment_run(materialized)


def _has_plain_segment_run(lines: list[str]) -> bool:
    for index in range(len(lines)):
        current = lines[index]
        if index + 2 < len(lines):
            if (
                _is_first_plain_segment_fragment(current)
                and _is_second_plain_segment_fragment(lines[index + 1])
                and _is_third_plain_segment_fragment(lines[index + 2])
            ):
                return True
        if index + 1 < len(lines):
            if _is_second_plain_segment_fragment(current) and _is_third_plain_segment_fragment(lines[index + 1]):
                logical = (" " * _SEGMENT_WIDTHS[1]) + current.ljust(_SEGMENT_WIDTHS[2]) + lines[index + 1]
                if is_mandelbrot_art_line(logical):
                    return True
    return False


def has_wrapped_mandelbrot_fragments(lines: Iterable[str]) -> bool:
    materialized = [line.rstrip("\r\n") for line in lines]
    if reconstruct_wrapped_mandelbrot_lines(materialized):
        return True
    return any(
        _is_wrapped_mandelbrot_fragment(line, max_width=_WRAPPED_FIRST_WIDTH)
        or _is_wrapped_mandelbrot_fragment(line, max_width=_WRAPPED_SECOND_WIDTH)
        for line in materialized
    )


def has_mandelbrot_art_fragments(lines: Iterable[str]) -> bool:
    materialized = [line.rstrip("\r\n") for line in lines]
    if any(is_mandelbrot_art_line(line) for line in materialized):
        return True
    if has_segmented_mandelbrot_fragments(materialized):
        return True
    if has_wrapped_mandelbrot_fragments(materialized):
        return True
    for dedented in _dedented_materializations(materialized):
        if any(is_mandelbrot_art_line(line) for line in dedented):
            return True
        if has_segmented_mandelbrot_fragments(dedented):
            return True
        if has_wrapped_mandelbrot_fragments(dedented):
            return True
    return False


def extract_any_mandelbrot_block(lines: Iterable[str]) -> list[str]:
    materialized = [line.rstrip("\r\n") for line in lines]
    candidates = [materialized, *_dedented_materializations(materialized)]
    for candidate in candidates:
        direct = extract_mandelbrot_art_block(candidate)
        if direct:
            return direct
        segmented = reconstruct_segmented_mandelbrot_lines(candidate)
        if segmented:
            return segmented
        plain_segmented = reconstruct_plain_segmented_mandelbrot_lines(candidate)
        if plain_segmented:
            return plain_segmented
    for candidate in candidates:
        wrapped = extract_wrapped_mandelbrot_block(candidate)
        if wrapped:
            return wrapped
    return []


def _dedented_materializations(lines: list[str]) -> list[list[str]]:
    dedented: list[list[str]] = []
    for indent in range(1, _MAX_COMMON_INDENT + 1):
        prefix = " " * indent
        if not any(line.startswith(prefix) for line in lines):
            continue
        dedented.append([line[indent:] if line.startswith(prefix) else line for line in lines])
    return dedented
