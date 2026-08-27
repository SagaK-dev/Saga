"""Saga resource model and opt-in deployment budgets.

Saga deliberately specifies no normative numeric ceilings for source size,
token count, syntax depth, AST nodes, exact integer size, exponent magnitude,
module count, package size, precision, worker count, or execution steps.

Hosts that execute untrusted input still need predictable resource envelopes.
``ResourceBudget`` is therefore deployment policy rather than language
semantics: every field is opt-in, and the default public APIs remain unlimited.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from threading import Lock

from .errors import LexLimitError, ParseLimitError, RuntimeResourceError


NORMATIVE_RESOURCE_LIMITS: dict[str, int] = {}
RESOURCE_MODEL = "no-fixed-normative-ceilings"


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    """Implementation/service limits for code that is not fully trusted.

    ``None`` means unlimited for that dimension.  These values never change
    whether a Saga program is conforming; they only decide whether a particular
    host is willing to compile or execute it.
    """

    max_source_bytes: int | None = None
    max_tokens: int | None = None
    max_ast_nodes: int | None = None
    max_import_depth: int | None = None
    max_modules: int | None = None
    max_steps: int | None = None
    max_output_bytes: int | None = None

    def __post_init__(self) -> None:
        for descriptor in fields(self):
            value = getattr(self, descriptor.name)
            if value is None:
                continue
            minimum = 0 if descriptor.name == "max_import_depth" else 1
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                relation = "0以上" if minimum == 0 else "1以上"
                raise ValueError(f"{descriptor.name} はNoneまたは{relation}の整数にしてください")


@dataclass(frozen=True, slots=True)
class ProcessBudget:
    """Host-process ceilings that require an actual child-process boundary.

    These limits must never be installed into an embedding application's own
    process. Saga's strict Linux CLI sandbox applies them in the re-exec child.
    """

    max_cpu_seconds: int | None = None
    max_address_space_bytes: int | None = None
    max_wall_seconds: int | None = None

    def __post_init__(self) -> None:
        for descriptor in fields(self):
            value = getattr(self, descriptor.name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{descriptor.name} はNoneまたは1以上の整数にしてください")


# Conservative reference policy for public playgrounds, bots, web services, and
# other deployments where the program author is not trusted. Operators should
# tune or replace it for their own workload instead of treating these numbers as
# Saga language limits.
UNTRUSTED_RESOURCE_BUDGET = ResourceBudget(
    max_source_bytes=512 * 1024,
    max_tokens=50_000,
    max_ast_nodes=50_000,
    max_import_depth=32,
    max_modules=128,
    max_steps=250_000,
    max_output_bytes=1024 * 1024,
)

# Applied only when the untrusted CLI profile is combined with the strict Linux
# whole-program sandbox. CPU is process CPU time, max_address_space_bytes maps
# to RLIMIT_AS rather than claiming an RSS limit, and max_wall_seconds is enforced
# by the parent watchdog so sleeping or blocked children cannot run indefinitely.
UNTRUSTED_PROCESS_BUDGET = ProcessBudget(
    max_cpu_seconds=5,
    max_address_space_bytes=512 * 1024 * 1024,
    max_wall_seconds=10,
)


def source_size_bytes(source: str) -> int:
    """Count UTF-8 bytes without allocating a second source-sized buffer.

    Surrogate code points are counted as three bytes, matching Python's
    ``surrogatepass`` UTF-8 representation. The lexer remains responsible for
    deciding whether the characters themselves are legal Saga source text.
    """
    total = 0
    for char in source:
        codepoint = ord(char)
        if codepoint <= 0x7F:
            total += 1
        elif codepoint <= 0x7FF:
            total += 2
        elif codepoint <= 0xFFFF:
            total += 3
        else:
            total += 4
    return total


def check_source_bytes(byte_count: int, filename: str, budget: ResourceBudget | None) -> None:
    limit = budget.max_source_bytes if budget is not None else None
    if limit is not None and byte_count > limit:
        raise LexLimitError(
            f"ソースサイズが実行環境の予算を超えています ({byte_count} > {limit} bytes)",
            1,
            1,
            filename,
            "ResourceBudget.max_source_bytes を見直すか、ソースを分割してください。これはSaga言語仕様の固定上限ではありません",
        )


def check_token_count(token_count: int, filename: str, budget: ResourceBudget | None) -> None:
    limit = budget.max_tokens if budget is not None else None
    if limit is not None and token_count > limit:
        raise LexLimitError(
            f"トークン数が実行環境の予算を超えています ({token_count} > {limit})",
            1,
            1,
            filename,
            "ResourceBudget.max_tokens を見直すか、ソースを分割してください。これはSaga言語仕様の固定上限ではありません",
        )


def check_import_depth(depth: int, filename: str, budget: ResourceBudget | None) -> None:
    limit = budget.max_import_depth if budget is not None else None
    if limit is not None and depth > limit:
        raise ParseLimitError(
            f"source importの深さが実行環境の予算を超えています ({depth} > {limit})",
            1,
            1,
            filename,
            "ResourceBudget.max_import_depth を見直すか、依存の段数を浅くしてください。これはSaga言語仕様の固定上限ではありません",
        )


def check_module_count(module_count: int, filename: str, budget: ResourceBudget | None) -> None:
    limit = budget.max_modules if budget is not None else None
    if limit is not None and module_count > limit:
        raise ParseLimitError(
            f"source unit数が実行環境の予算を超えています ({module_count} > {limit})",
            1,
            1,
            filename,
            "ResourceBudget.max_modules を見直すか、依存を整理してください。これはSaga言語仕様の固定上限ではありません",
        )


def effective_step_limit(explicit: int | None, budget: ResourceBudget | None) -> int | None:
    """Resolve the runtime step ceiling without allowing a caller to relax policy."""
    policy = budget.max_steps if budget is not None else None
    if explicit is None:
        return policy
    if policy is None:
        return explicit
    return min(explicit, policy)


def bounded_output(
    output: Callable[[str], None],
    filename: str,
    budget: ResourceBudget | None,
) -> Callable[[str], None]:
    """Wrap Saga output with one shared UTF-8 byte budget.

    One logical line-separator byte is counted for each output event, matching
    the default print sink. The wrapper itself is shared by source modules and
    task forks, so imports cannot reset the budget.
    """
    limit = budget.max_output_bytes if budget is not None else None
    if limit is None:
        return output

    used = 0
    lock = Lock()

    def emit(text: str) -> None:
        nonlocal used
        event_bytes = source_size_bytes(text) + 1
        with lock:
            next_used = used + event_bytes
            if next_used > limit:
                raise RuntimeResourceError(
                    f"出力量が実行環境の予算を超えています ({next_used} > {limit} bytes)",
                    1,
                    1,
                    filename,
                    "ResourceBudget.max_output_bytes を見直すか、出力を減らしてください。これはSaga言語仕様の固定上限ではありません",
                )
            used = next_used
            output(text)

    return emit
