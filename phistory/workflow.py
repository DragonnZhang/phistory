from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from phistory import packages
from phistory.capture import capture_target
from phistory.models import AgentSpec, CaptureResult, CaptureTarget, CaptureVariant, VersionInfo
from phistory.registry import get_agent
from phistory.storage import remove_if_exists


def capture_latest(
    agent_ids: Iterable[str],
    *,
    root: Path,
    cache_dir: Path,
    variant_ids: Iterable[str] | None = None,
    force: bool = False,
    keep_tap: bool = False,
) -> list[CaptureResult]:
    results: list[CaptureResult] = []
    selected_ids = tuple(variant_ids) if variant_ids is not None else None
    for agent_id in agent_ids:
        try:
            agent = get_agent(agent_id)
            version = packages.latest_version(agent)
            variants = _selected_variants(agent, selected_ids)
        except Exception as exc:
            results.append(CaptureResult(agent_id, "unknown", "default", "failed", error=str(exc)))
            continue
        for variant in _variants_for_version(variants, version):
            try:
                result = capture_target(
                    CaptureTarget(agent, version, variant, root),
                    cache_dir=cache_dir,
                    force=force,
                    keep_tap=keep_tap,
                )
            except Exception as exc:
                result = CaptureResult(agent_id, version.version, variant.id, "failed", error=str(exc))
            results.append(result)
    return results


def backfill(
    agent_id: str,
    *,
    start: str,
    end: str,
    root: Path,
    cache_dir: Path,
    variant_ids: Iterable[str] | None = None,
    force: bool = False,
    keep_tap: bool = False,
    limit: int | None = None,
    newest_first: bool = False,
    include_prerelease: bool = False,
    extract_static: bool = True,
    prune_installs: bool = False,
    shard_index: int | None = None,
    shard_count: int | None = None,
) -> list[CaptureResult]:
    return list(
        iter_backfill(
            agent_id,
            start=start,
            end=end,
            root=root,
            cache_dir=cache_dir,
            variant_ids=variant_ids,
            force=force,
            keep_tap=keep_tap,
            limit=limit,
            newest_first=newest_first,
            include_prerelease=include_prerelease,
            extract_static=extract_static,
            prune_installs=prune_installs,
            shard_index=shard_index,
            shard_count=shard_count,
        )
    )


def iter_backfill(
    agent_id: str,
    *,
    start: str,
    end: str,
    root: Path,
    cache_dir: Path,
    variant_ids: Iterable[str] | None = None,
    force: bool = False,
    keep_tap: bool = False,
    limit: int | None = None,
    newest_first: bool = False,
    include_prerelease: bool = False,
    extract_static: bool = True,
    prune_installs: bool = False,
    shard_index: int | None = None,
    shard_count: int | None = None,
) -> Iterator[CaptureResult]:
    _validate_shard(shard_index, shard_count)
    agent = get_agent(agent_id)
    versions: list[VersionInfo] = packages.versions_between(agent, start, end, include_prerelease=include_prerelease)
    if newest_first:
        versions = list(reversed(versions))
    if limit is not None:
        versions = versions[:limit]
    if shard_index is not None and shard_count is not None:
        versions = versions[shard_index::shard_count]
    variants = _selected_variants(agent, tuple(variant_ids) if variant_ids is not None else None)
    for version in versions:
        try:
            for variant in _variants_for_version(variants, version):
                yield capture_target(
                    CaptureTarget(agent, version, variant, root),
                    cache_dir=cache_dir,
                    force=force,
                    keep_tap=keep_tap,
                    extract_static=extract_static,
                )
        finally:
            if prune_installs:
                install_dir = (cache_dir / "installs" / agent.id / version.version).resolve()
                remove_if_exists(install_dir)


def _validate_shard(shard_index: int | None, shard_count: int | None) -> None:
    if (shard_index is None) != (shard_count is None):
        raise ValueError("--shard-index and --shard-count must be provided together")
    if shard_count is None:
        return
    if shard_count < 1:
        raise ValueError("--shard-count must be greater than zero")
    if shard_index is None or not 0 <= shard_index < shard_count:
        raise ValueError("--shard-index must be between zero and --shard-count - 1")


def _selected_variants(agent: AgentSpec, variant_ids: tuple[str, ...] | None) -> tuple[CaptureVariant, ...]:
    if variant_ids is None:
        return agent.capture_variants
    return tuple(agent.variant(variant_id) for variant_id in variant_ids)


def _variants_for_version(variants: tuple[CaptureVariant, ...], version: VersionInfo) -> tuple[CaptureVariant, ...]:
    return tuple(variant for variant in variants if variant.supports_version(version.version))
