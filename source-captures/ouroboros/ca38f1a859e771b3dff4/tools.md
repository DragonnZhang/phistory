# ouroboros — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## ouroboros/tool_result_delivery.py

Source: https://github.com/razzant/ouroboros/blob/add0ce3296c722ef99a536c52f8dcddf36494b36/ouroboros/tool_result_delivery.py

SHA-256: `ab253fa0091b1d0c0fa85c5eea5e3eae67a1dc35401622cd09db2128ec4ac114`

```
"""Source-addressed first-show delivery of tool results.

One completed call batch is projected as ONE candidate against the measured
context frame its caller supplies (``fit_candidate(messages, schemas)`` returns
the existing Main measurement fields: ``estimated_input_tokens``,
``response_reserve_tokens``, ``target_total_tokens``, ``capacity_total_tokens``,
``measurement_basis``, ``measurement_density``). Every result keeps its typed
facts and an exact source address before any body is allocated; the bodies then
share one physical allowance derived from the measurement, never from a tool's
name or a path. A body the frame cannot hold is shown head+tail with exact
character ranges over the same text basis ``read_file`` pages (universal
newlines), so the omitted middle is one bounded read away and never a rerun of
the tool. A whole delivery is byte-identical to what the tool produced.

Two policies share the machinery:

``accepted_prefix``
    The consolidator's existing contract: the callback's ``accepted`` verdict
    drives a prefix search and the view is ``[Tool result source view]``.

``measured_frame``
    Unasked bodies share the measured eighth. Chosen bodies arrive as asked,
    even beside a crowded transcript: Main's pre-send recovery narrows memory
    and host material first. Only a chosen body exceeding usable input by itself
    may be cut here. A resulting non-fitting candidate is named for recovery,
    never mistaken for an accepted provider request.

"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import pathlib
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ouroboros.tool_capabilities import PAGEABLE_TOOL_RESULTS, requested_result_view

log = logging.getLogger(__name__)

FitCandidate = Callable[[list, list], Mapping[str, Any]]

RESULT_VIEW_BASIS = "unicode_text_universal_newlines"
RESULT_VIEW_MARKER = "RESULT_VIEW_JSON="
FULL_SOURCE_MARKER = "FULL_RESULT_SOURCE_JSON="
FULL_SOURCE_UNAVAILABLE = "FULL_RESULT_SOURCE_UNAVAILABLE=true"
SOURCE_UNAVAILABLE_NOTE = "Do not treat this partial result as complete; exact source persistence failed."
RECOVER_PAGEABLE = "Read the exact source above, or request another range using this tool's declared parameters."
RECOVER_OTHER = "Do not rerun this tool to recover omitted output. Read the exact source above."

# Re-measurements of the complete candidate after the first allocation. Each one
# shrinks the bodies over their bound by the measured overshoot; the last resort
# is their minimum view (status ``minimum_view_unfit`` when the window is still over).
_MAX_REMEASURES = 4


def reader_text(text: Any) -> str:
    """The text basis every range here addresses: what ``read_file`` pages."""
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def persist_result_source(
    drive_root: pathlib.Path | str, task_id: str, source_id: str, text: Any,
) -> Dict[str, Any]:
    """Write-once exact source for one result, verified by an exact read-back.

    ``{}`` when the source cannot be persisted or read back: the caller then
    discloses the gap and claims no readability.
    """
    from ouroboros.artifacts import persist_exact_text_source

    _, ref, issue = persist_exact_text_source(
        pathlib.Path(drive_root), str(task_id), source_id=str(source_id), text=str(text),
    )
    if issue or not ref:
        log.warning("Tool result source not persisted for %s: %s", source_id, (issue or {}).get("reason"))
        return {}
    return ref


def compact_verification(fact: Any) -> Dict[str, Any]:
    """A check's recorded criterion verdict, compact: pass/fail, kind, match mode, the
    evaluated match (absent when no comparison ran) and the claim it supports (#1585)."""
    if not isinstance(fact, Mapping):
        return {}
    return {key: fact[key] for key in ("status", "contract_kind", "expected_match", "matched", "criterion_id")
            if key in fact}


def typed_result_facts(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Status, code, process facts and any check verdict the producer published; nothing read from the text."""
    from ouroboros.tools.process_facts import PROCESS_FACT_KEYS

    meta = row.get("result_meta") if isinstance(row.get("result_meta"), Mapping) else {}
    typed = row.get("tool_result")
    facts: Dict[str, Any] = {"is_error": row.get("is_error") if isinstance(row.get("is_error"), bool) else None}
    status = getattr(typed, "status", None) or meta.get("tool_result_status") or meta.get("status")
    code = getattr(typed, "code", None) or meta.get("tool_result_code")
    if status:
        facts["status"] = str(status)
    if code:
        facts["code"] = str(code)
    facts.update({key: meta[key] for key in PROCESS_FACT_KEYS if key in meta})
    if verification := compact_verification(meta.get("verification")):
        facts["verification"] = verification
    return facts


def prepare_producer_sources(results: Sequence[Mapping[str, Any]], drive_root: Any, task_id: str,
                             notes: Sequence[str] = ()) -> list:
    """Retain clean producer text before measuring its model-visible locator.

    A row the host notes only at delivery (``notes``, aligned with ``results``) keeps the
    note as ``delivery_note`` and its own result as that clean text.
    """
    rows = [dict(row) for row in results]
    for index, row in enumerate(rows):
        producer_text = getattr(row.get("tool_result"), "producer_text", None)
        note = notes[index] if index < len(notes) else ""
        if note:
            row["delivery_note"] = note
        if producer_text is not None or note:
            clean = row["result"] if producer_text is None else producer_text
            row["producer_source_ref"] = (persist_result_source(drive_root, task_id,
                str(row["tool_call_id"]) + ".producer", clean) if drive_root is not None else {})
    return rows


def with_delivery_note(row: Mapping[str, Any], text: str) -> str:
    """What the mind is shown: ``text`` with the row's delivery note trailing it. Measurement
    and the transcript use this; the host readers of a result keep ``text``."""
    note = row.get("delivery_note")
    return f"{text}\n\n{note}" if note else text


def with_producer_source(row: Mapping[str, Any], text: str) -> str:
    """The same complete delivery text for measurement and transcript publication."""
    typed = row.get("tool_result")
    if getattr(typed, "producer_text", None) is None:
        return text
    if text != str(row["result"]) and typed.host_annotations:
        text += "\n\n" + "\n\n".join(typed.host_annotations)
    ref = row.get("producer_source_ref") or {}
    return text + (
        "\nPRODUCER_RESULT_SOURCE_JSON=" + json.dumps(ref, ensure_ascii=False)
        + "\nUnannotated tool data for programmatic reading; host notes and outcome still apply."
        if ref else
        "\nPRODUCER_RESULT_SOURCE_UNAVAILABLE=true"
        "\nHost notes remain in the result; no clean producer file was retained.")


def _span_text(ranges: Sequence[Sequence[int]]) -> str:
    return " and ".join(f"{start}\u2013{end}" for start, end in ranges) or "nothing"


def render_result_view(
    text: str, *, head_chars: int, tail_chars: int, tool_name: str = "",
    source_ref: Optional[Mapping[str, Any]] = None, facts: Optional[Mapping[str, Any]] = None,
    delivery: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Head+tail view of ``text`` with exact ranges, or ``text`` itself.

    Returns ``(delivered, view)``. ``view`` is ``None`` for a whole delivery:
    when the requested head and tail cover the text, or when the view would not
    be smaller than the text (the anti-growth floor). Otherwise ``delivered`` is
    the head, one line naming the omitted range, the tail, then -- outside the
    omitted body -- the typed facts and exact ranges as ``RESULT_VIEW_JSON=``,
    the source address as ``FULL_RESULT_SOURCE_JSON=`` (or the disclosed
    ``FULL_RESULT_SOURCE_UNAVAILABLE=true``) and the recovery sentence.
    """
    total = len(text)
    head = max(0, min(int(head_chars or 0), total))
    tail = max(0, min(int(tail_chars or 0), total - head))
    if head + tail >= total:
        return text, None
    omitted = [head, total - tail]
    shown = [[lo, hi] for lo, hi in ([0, head], [total - tail, total]) if lo < hi]
    ref = dict(source_ref) if isinstance(source_ref, Mapping) and source_ref else {}
    view: Dict[str, Any] = {
        "complete_chars": total,
        "complete_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "shown_ranges": shown,
        "omitted_ranges": [omitted],
        "range_basis": RESULT_VIEW_BASIS,
        "facts": dict(facts or {}),
    }
    if delivery is not None:
        # Host allocation facts never become producer stdout or producer status.
        view["delivery"] = {**delivery, "source_status": "ready" if ref else "source_unavailable"}
    if ref:
        view["read_omitted"] = {
            "tool": "read_file", "root": str(ref.get("root") or "artifact_store"), "path": ref.get("path"),
            "start_line": 1, "max_lines": max(1, len(text.splitlines())),
            "start_char": omitted[0], "max_chars": omitted[1] - omitted[0],
        }
    marker = (
        f"\n... (truncated from {total} chars: shown {_span_text(shown)}, "
        f"omitted {omitted[0]}\u2013{omitted[1]}; exact source and facts below)\n"
    )
    lines = [RESULT_VIEW_MARKER + json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(",", ":"))]
    if ref:
        lines.append(FULL_SOURCE_MARKER + json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        lines.append(RECOVER_PAGEABLE if tool_name in PAGEABLE_TOOL_RESULTS else RECOVER_OTHER)
    else:
        lines.extend([FULL_SOURCE_UNAVAILABLE, SOURCE_UNAVAILABLE_NOTE])
    delivered = text[:head] + marker + text[total - tail:] + "\n" + "\n".join(lines)
    if len(delivered) >= total:
        return text, None
    view.update(text_chars=len(delivered), delivered_range=[0, head])
    return delivered, view


def split_allowance(allowance: int, request: Optional[Tuple[Optional[int], Optional[int]]], total: int) -> Tuple[int, int]:
    """Head and tail chars for one result from its allowance and the call's request.

    A head/tail request keeps its proportions when it must shrink; any other
    allocation is split half and half. ``allowance >= total`` means whole.
    """
    allowance = max(0, min(int(allowance), total))
    if allowance >= total:
        return total, 0
    if request and request[0] is not None and request[1] is not None and (request[0] + request[1]) > 0:
        head_want, tail_want = int(request[0]), int(request[1])
        if head_want + tail_want <= allowance:
            return head_want, tail_want
        head = int(round(allowance * head_want / (head_want + tail_want)))
        return head, allowance - head
    head = int(math.ceil(allowance / 2))
    return head, allowance - head


def _water_fill(wants: Dict[int, int], budget: int) -> Dict[int, int]:
    """Equal shares, the smaller wants satisfied first; nothing exceeds its want."""
    out: Dict[int, int] = {}
    pending = sorted(wants.items(), key=lambda item: item[1])
    remaining = max(0, int(budget))
    for position, (index, want) in enumerate(pending):
        share = remaining // (len(pending) - position)
        out[index] = max(0, min(int(want), share))
        remaining -= out[index]
    return out


def _positive(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if int(value) > 0 else None


def _tokens(measurement: Mapping[str, Any]) -> int:
    return max(0, int(measurement.get("estimated_input_tokens") or 0))


def frame_bounds(measurement: Mapping[str, Any]) -> Tuple[Optional[int], int, int]:
    """``(B, R, eighth)`` of one measurement: the smaller known positive of the
    owner target and the route capacity, the reply reserve, and the one
    structural eighth ``ceil(B / RECLAIM_LOW_WATER_DIVISOR)`` (0 when B is unknown)."""
    from ouroboros.context_fit import reclaim_low_water_margin

    target = _positive(measurement.get("target_total_tokens"))
    capacity = _positive(measurement.get("capacity_total_tokens"))
    known = [value for value in (target, capacity) if value]
    reserve = max(0, int(measurement.get("response_reserve_tokens") or 0))
    return (min(known) if known else None), reserve, reclaim_low_water_margin(target, capacity)


def _standin_source_ref(call_id: Any, text: str, digest: str) -> Dict[str, Any]:
    """An address-shaped stand-in of the real locator's size, for measuring the
    minimum request before any source is written. Never delivered."""
    path = f"source_handles/tool_results/{call_id}-{digest}.txt"
    return {"kind": "task_source", "root": "artifact_store", "path": path, "size": len(text.encode("utf-8")),
            "sha256": digest, "read": {"tool": "read_file", "arguments": {
                "root": "artifact_store", "path": path, "start_line": 1, "max_lines": 2000, "start_char": 0}}}


def project_tool_result_batch(
    results: List[Dict[str, Any]], messages: List[Dict[str, Any]], tool_schemas: list,
    *, drive_root: pathlib.Path, task_id: str, fit_candidate: FitCandidate,
    policy: str = "accepted_prefix",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Retain full results before constructing a fitting multi-result view.

    The caller supplies one completed call batch (rows with ``tool_call_id`` and
    ``result``; ``fn_name``, ``tool_args``, ``tool_result``, ``result_meta`` and
    ``is_error`` inform the measured policy) and its actual prospective send
    measurement. No result is appended to the live transcript here; source
    persistence, projection and callback failures never imply a full read.
    Returns copies of the rows (the row dict and its ``result_meta`` are the
    only things this projection writes; a typed ``tool_result`` is shared, not
    copied) with ``result`` replaced by the delivered text and, for a partial
    row, ``result_partial``, ``result_source_ref``, ``result_source_status`` and
    ``result_source_view``; plus a receipt whose ``status`` is ``complete``,
    ``projected``, ``minimum_view_unfit`` or (the measured policy only)
    ``capacity_unknown``.
    """
    rows = [{**row, **({"result_meta": dict(row["result_meta"])} if isinstance(row.get("result_meta"), dict) else {})}
            for row in results]
    full = [str(row["result"]) for row in rows]

    def measure(contents: Sequence[str]) -> Dict[str, Any]:
        # A fresh list over the live message dicts: the callback measures and
        # must not mutate (``measure_main_fit`` only serializes its inputs).
        candidate = [*messages, *({"role": "tool", "tool_call_id": row["tool_call_id"], "content": text}
                                  for row, text in zip(rows, contents))]
        return dict(fit_candidate(candidate, list(tool_schemas or [])))

    if policy == "measured_frame":
        return _measured_frame(rows, full, measure, drive_root=drive_root, task_id=task_id)
    if policy != "accepted_prefix":
        raise ValueError(f"unknown tool result delivery policy: {policy!r}")
    return _accepted_prefix(rows, full, measure, drive_root=drive_root, task_id=task_id)


def _mark_partial(row: Dict[str, Any], source: Dict[str, Any], view: Dict[str, Any]) -> None:
    status = "ready" if source else "source_unavailable"
    row.update(result_partial=True, result_source_ref=source, result_source_status=status,
               result_source_view={"source_ref": source, "source_status": status, **view})
    if isinstance(row.get("result_meta"), dict) and "knowledge_source_complete" in row["result_meta"]:
        row["result_meta"]["knowledge_source_complete"] = False


def _accepted_prefix(
    rows: List[Dict[str, Any]], full: List[str], measure: Callable[[Sequence[str]], Dict[str, Any]],
    *, drive_root: pathlib.Path, task_id: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """The consolidator's contract: the callback's ``accepted`` verdict drives a prefix search."""
    initial = measure(full)
    if initial.get("accepted") is True:
        return rows, {"status": "complete", "fit": initial}
    sources = [persist_result_source(drive_root, task_id, row["tool_call_id"], text) for row, text in zip(rows, full)]
    shown = [0] * len(rows)

    def render(index: int) -> str:
        text, source, count = full[index], sources[index], shown[index]
        info = {"source_ref": source, "source_status": "ready" if source else "source_unavailable",
                "complete_chars": len(text), "complete_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "requested_range": [0, len(text)], "delivered_range": [0, count], "text_chars": count,
                "text_sha256": hashlib.sha256(text[:count].encode("utf-8")).hexdigest()}
        rows[index]["result"] = text[:count] + "\n[Tool result source view]\n" + json.dumps(info, ensure_ascii=False, separators=(",", ":"))
        _mark_partial(rows[index], source, info)
        rows[index]["result_partial"] = count < len(text)
        return rows[index]["result"]

    # Reserve every call's result envelope and source locator before allocating
    # body text. A later result can never disappear because an earlier one grew.
    contents = [render(index) for index in range(len(rows))]
    minimum = measure(contents)
    if minimum.get("accepted") is not True:
        return rows, {"status": "minimum_view_unfit", "fit": minimum}
    for index, text in enumerate(full):
        low, high = 0, len(text)
        while low < high:
            shown[index] = (low + high + 1) // 2
            contents[index] = render(index)
            if measure(contents).get("accepted") is True:
                low = shown[index]
            else:
                high = shown[index] - 1
        shown[index] = low
        contents[index] = render(index)
    final = measure(contents)
    return rows, {"status": "projected" if final.get("accepted") is True else "minimum_view_unfit", "fit": final}


def _measured_frame(
    rows: List[Dict[str, Any]], full: List[str], measure: Callable[[Sequence[str]], Dict[str, Any]],
    *, drive_root: pathlib.Path, task_id: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Main's first show: one measured batch allowance, requested forms first."""

    count = len(rows)
    texts = [reader_text(text) for text in full]
    digests = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
    names = [str(row.get("fn_name") or row.get("tool") or "") for row in rows]
    facts = [typed_result_facts(row) for row in rows]
    requests = [requested_result_view(row.get("tool_args")) for row in rows]

    whole = measure(full)
    boundary, reserve, eighth = frame_bounds(whole)
    target = _positive(whole.get("target_total_tokens"))
    capacity = _positive(whole.get("capacity_total_tokens"))
    limiters = [set() for _ in rows]

    def diagnostic(index: int) -> Dict[str, Any]:
        return {"selection": "automatic" if requests[index] is None else "explicit",
                "limiters": sorted(limiters[index]), "target_tokens": target, "capacity_tokens": capacity,
                "reserve_tokens": reserve, "batch_share_tokens": eighth,
                "unasked_results": sum(request is None for request in requests)}

    for i, request in enumerate(requests):
        if capacity is None:
            limiters[i].add("capacity_unknown")
        if request is not None and request != (None, None) and sum(request) < len(texts[i]):
            limiters[i].add("explicit_view")
    receipt: Dict[str, Any] = {"fit": whole, "measurements": 1, "boundary_tokens": boundary, "capacity_tokens": capacity,
                               "reserve_tokens": reserve, "results": count, "partial": []}
    if boundary is None:
        # Unknown capacity does not cancel the form the caller explicitly chose.
        # Preserve unrequested/full outputs, and retain exact sources for named views.
        for index, request in enumerate(requests):
            if request is None or request == (None, None):
                continue
            head, tail = split_allowance(sum(request), request, len(texts[index]))
            if head + tail >= len(texts[index]):
                continue
            source = persist_result_source(drive_root, task_id, rows[index]["tool_call_id"], full[index])
            delivered, view = render_result_view(texts[index], head_chars=head, tail_chars=tail,
                tool_name=names[index], source_ref=source, facts=facts[index], delivery=diagnostic(index))
            if view is not None:
                rows[index]["result"] = delivered
                _mark_partial(rows[index], source, {**view, "requested": True})
                receipt["partial"].append({"tool_call_id": rows[index]["tool_call_id"],
                    "shown_ranges": view["shown_ranges"], "source_status": rows[index]["result_source_status"]})
        final = measure([row["result"] for row in rows]) if receipt["partial"] else whole
        return rows, {**receipt, "status": "capacity_unknown", "fit": final,
                      "measurements": 2 if receipt["partial"] else 1}

    def envelope(index: int, source: Mapping[str, Any]) -> str:
        return render_result_view(texts[index], head_chars=0, tail_chars=0, tool_name=names[index],
                                  source_ref=source, facts=facts[index], delivery=diagnostic(index))[0]

    def form(index: int, chars: int) -> str:  # a body at ``chars``, on its stand-in address
        head, tail = split_allowance(chars, requests[index], len(texts[index]))
        if head + tail >= len(texts[index]):
            return full[index]
        return render_result_view(texts[index], head_chars=head, tail_chars=tail, tool_name=names[index],
                                  source_ref=standins[index], facts=facts[index], delivery=diagnostic(index))[0]

    standins = [_standin_source_ref(row["tool_call_id"], text, digest) for row, text, digest in zip(rows, texts, digests)]
    envelopes = [envelope(index, standins[index]) for index in range(count)]
    minimum = measure(envelopes)
    p_min, p_whole = _tokens(minimum), _tokens(whole)
    # Stand-ins size allocation only; minimum_view_unfit is measured below
    # with actual source outcomes (a failed persistence has a shorter envelope).
    free = boundary - reserve - p_min
    room = None if capacity is None else capacity - reserve - p_min  # what requested forms may use
    unsolicited_allowance = max(0, min(free, eighth))
    # Body chars beyond each minimum envelope.
    body_chars = [max(0, len(text) - len(envelopes[index])) for index, text in enumerate(texts)]
    # What each call asked to see: the whole produced form, or a head+tail view.
    wants = {i: (len(texts[i]) if requests[i] == (None, None) else min(len(texts[i]), requests[i][0] + requests[i][1]))
             for i in range(count) if requests[i] is not None}
    receipt.update(measurements=2, minimum_tokens=p_min, whole_tokens=p_whole, free_tokens=free,
                   requested_room_tokens=room, unsolicited_allowance_tokens=unsolicited_allowance)
    whole_fits = (room is None or p_whole + reserve < capacity) and all(wants[i] >= len(texts[i]) for i in wants)
    complete = "complete" if room is not None else "capacity_unknown"
    if whole_fits and p_whole + reserve <= boundary and p_whole - p_min <= unsolicited_allowance:
        return rows, {**receipt, "status": complete}  # every body together inside the unasked share
    # Every chosen form whole beside the minimum unasked envelopes, as ONE request: the
    # measured cost of what the calls chose on THIS transcript, and so of the unasked rest.
    chosen_contents = [form(i, wants[i]) if i in wants else envelopes[i] for i in range(count)]
    chosen = minimum if not wants else whole if chosen_contents == full else measure(chosen_contents)
    p_chosen = _tokens(chosen)
    requested_capacity = capacity
    body_costs = {}
    for i in wants:
        empty = list(envelopes)
        empty[i] = ""
        baseline = measure(empty)
        one = list(empty)
        one[i] = chosen_contents[i]
        body_costs[i] = max(0, _tokens(measure(one)) - _tokens(baseline))
    protected = {i for i in wants if capacity is None or body_costs[i] <= capacity - reserve}
    for i in wants.keys() - protected:
        facts[i] = {**facts[i], "cut_reason": "chosen_body_exceeds_usable_input"}
    receipt.update(measurements=2 + (chosen is not minimum and chosen is not whole) + 2 * len(wants),
                   chosen_tokens=p_chosen, chosen_body_tokens={rows[i]["tool_call_id"]: cost for i, cost in body_costs.items()})
    chosen_chars = sum(max(0, len(chosen_contents[i]) - len(envelopes[i])) for i in wants)
    requested_rate = max(p_chosen - p_min, 1) / max(chosen_chars, 1)
    # Initial estimate of unasked cost: price omitted requested text at its shown
    # rate. Different escaping in that omitted text can skew this estimate; the
    # corrective pass below uses the measured request without unasked bodies.
    unsolicited_tokens = max(0.0, p_whole - p_chosen - requested_rate * sum(
        len(full[i]) - len(chosen_contents[i]) for i in wants if chosen_contents[i] is not full[i]))
    unsolicited_rate = max(unsolicited_tokens, 1) / max(sum(body_chars[i] for i in range(count) if i not in wants), 1)
    # Unsolicited bodies stay inside their eighth AND inside the target room requested forms left.
    if (whole_fits and unsolicited_tokens <= unsolicited_allowance
            and (not unsolicited_tokens or p_whole + reserve <= boundary)):
        return rows, {**receipt, "status": complete}

    allocation = [wants.get(i, 0) if i in protected else 0 for i in range(count)]
    if room is None or p_chosen - p_min <= room:  # unknown window, or every chosen form fits it whole
        allocation = [wants.get(index, 0) for index in range(count)]
        requested_tokens = float(p_chosen - p_min)
    else:  # the window shares out among the chosen forms at their own measured rate
        for index, chars in _water_fill({i: want for i, want in wants.items() if i not in protected},
                int(max(0, room - sum(body_costs[i] for i in protected)) / requested_rate)).items():
            allocation[index] = chars
        requested_tokens = sum(allocation[i] for i in wants) * requested_rate
    left = min(free, float("inf") if room is None else room) - requested_tokens
    pool = int(max(0.0, min(left, float(unsolicited_allowance))) / unsolicited_rate)
    for index, chars in _water_fill({i: body_chars[i] for i in range(count) if requests[i] is None}, pool).items():
        allocation[index] = chars
    # Explain the bound that actually allocated this batch, not merely which
    # window was selected. A known window can leave ample room while the target
    # or the unsolicited eighth limits a view to nothing.
    unasked_bounds = {"unsolicited_share": float(eighth)}
    if target is not None:
        unasked_bounds["target_pressure"] = target - reserve - p_min - requested_tokens
    if capacity is not None:
        unasked_bounds["physical_pressure"] = capacity - reserve - p_min - requested_tokens
    binding = min(unasked_bounds.values())
    for i, request in enumerate(requests):
        if request is None:
            limiters[i].update(name for name, value in unasked_bounds.items() if value == binding)
        elif allocation[i] < wants[i]:
            limiters[i].add("physical_pressure")

    sources: Dict[int, Dict[str, Any]] = {}

    def render(index: int, allowance: Optional[int] = None, *, publish: bool = True) -> str:
        head, tail = split_allowance(allocation[index] if allowance is None else allowance,
                                     requests[index], len(texts[index]))
        if head + tail >= len(texts[index]):
            if publish:
                rows[index]["result"] = full[index]
                for key in ("result_partial", "result_source_ref", "result_source_status", "result_source_view"):
                    rows[index].pop(key, None)
            return full[index]
        if index not in sources:  # persisted once, before any partial view of it is handed out
            sources[index] = persist_result_source(drive_root, task_id, rows[index]["tool_call_id"], full[index])
        delivered, view = render_result_view(texts[index], head_chars=head, tail_chars=tail, tool_name=names[index],
                                             source_ref=sources[index], facts=facts[index], delivery=diagnostic(index))
        if view is None:  # the anti-growth floor made it whole after all
            if publish:
                rows[index]["result"] = full[index]
            return full[index]
        if publish:
            rows[index]["result"] = delivered
            _mark_partial(rows[index], sources[index], {**view, "requested": requests[index] is not None})
        return delivered

    attempt = 0
    while True:
        contents = [render(index) for index in range(count)]
        final = measure(contents)
        receipt["measurements"] += 1
        without_unsolicited = measure([render(i, 0, publish=False) if requests[i] is None else contents[i]
                                       for i in range(count)])
        receipt["measurements"] += 1
        body_tokens = max(0, _tokens(final) - _tokens(without_unsolicited))
        receipt["unsolicited_body_tokens"] = body_tokens
        # Only the window shrinks a requested form; the target binds unsolicited bodies alone.
        capacity_over = _tokens(final) + reserve - requested_capacity if capacity is not None else 0
        economy_over = max(body_tokens - unsolicited_allowance,
                           _tokens(final) + reserve - boundary if body_tokens else 0)
        over = max(capacity_over, economy_over)
        # An unasked overshoot cannot take space from chosen forms that fit
        # beside the minimum unasked envelopes. Both eligibility and cost use
        # that already measured baseline, including real source locators.
        shrink_chosen = capacity is not None and _tokens(without_unsolicited) + reserve > requested_capacity
        shrinking = [i for i in range(count) if (shrink_chosen and i not in protected) or requests[i] is None]
        shown_total = sum(min(allocation[i], len(texts[i])) for i in shrinking)
        if capacity_over > 0 and shown_total <= 0 and not any("minimum_view_unfit" in causes for causes in limiters):
            actual_minimum = measure([render(i, 0, publish=False) for i in range(count)])
            receipt["measurements"] += 1
            if _tokens(actual_minimum) + reserve > capacity:
                for causes in limiters:
                    causes.add("minimum_view_unfit")
                continue  # include the new diagnostic bytes in the delivered measurement
        if over <= 0 or shown_total <= 0 or attempt > _MAX_REMEASURES:
            break
        for i in shrinking:
            if capacity_over > 0:
                limiters[i].add("physical_pressure")
            if requests[i] is None and economy_over > 0:
                if body_tokens > unsolicited_allowance:
                    limiters[i].update(name for name, value in unasked_bounds.items() if value == binding)
                if _tokens(final) + reserve > boundary:
                    limiters[i].add("target_pressure" if target == boundary else "physical_pressure")
        attempt += 1
        if attempt > _MAX_REMEASURES:
            for i in shrinking:  # last resort: the minimum view of every body still over
                allocation[i] = 0
        else:  # chars at the shrinking bodies' own measured cost on this candidate
            spent = _tokens(final) - (p_min if shrink_chosen else _tokens(without_unsolicited))
            cut = min(shown_total, int(over * shown_total / max(spent, 1)) + 1)
            for i in shrinking:
                allocation[i] = int(allocation[i] * max(0.0, 1.0 - (cut * 1.1) / shown_total))
    status = ("capacity_unknown" if capacity is None
              else "projected" if _tokens(final) + reserve <= capacity else "minimum_view_unfit")
    receipt["recovery_required"] = capacity is not None and _tokens(final) + reserve > capacity
    for i in wants:
        if allocation[i] < wants[i] and rows[i].get("result_partial"):
            rows[i]["result_source_view"]["cut_reason"] = "chosen_body_exceeds_usable_input"
    receipt["target_excess_tokens"] = max(0, _tokens(final) + reserve - target) if target else None
    receipt["partial"] = [{"tool_call_id": rows[i]["tool_call_id"], "tool": names[i],
                           "shown_ranges": rows[i]["result_source_view"]["shown_ranges"],
                           "complete_chars": len(texts[i]), "source_status": rows[i]["result_source_status"]}
                          for i in range(count) if rows[i].get("result_partial")]
    return rows, {**receipt, "status": status, "fit": final}

```

## ouroboros/tools/compact_context.py

Source: https://github.com/razzant/ouroboros/blob/add0ce3296c722ef99a536c52f8dcddf36494b36/ouroboros/tools/compact_context.py

SHA-256: `31ed3ec37cf0151492ad7e151336eaba110271308adec47fd7e5bd315d60ee1a`

```
"""The mind's own context view: inspect the observed send, author a working note, restore sources."""

from __future__ import annotations

import logging
import copy
import json
from collections import Counter
from pathlib import Path
from typing import List

from ouroboros.tools.registry import ToolEntry

log = logging.getLogger(__name__)
_CANONICAL_OBSERVATION = object()
_NOTE_REQUIRED = ("authored_note_required: inspect the working view, then supply your own working_note "
                  "(keep_unit_ids or keep_last_n select what stays raw); no helper writes it for you.")


def record_context_view(ctx, messages, tool_schemas, *, physical_capture=_CANONICAL_OBSERVATION) -> None:
    """Capture a usable turn's canonical source; prospective pricing never calls this.

    Nothing is added to the prompt or cache identity. Inspect pins this one
    observation for a later authored request, so its own tool pair and newer
    owner messages cannot make that request stale by construction.
    Physical vision/provider projections remain in the existing request artifacts.
    """
    from ouroboros.context_compaction import context_reclaim_transcript_sha256

    observed = copy.deepcopy(messages)
    plan, mode = getattr(ctx, "context_fit_plan", None), getattr(ctx, "active_context_mode", "") or "max"
    memory = bool(getattr(getattr(plan, "core", None), "memory_view_json", ""))
    ctx._last_context_observation = {
        "revision": context_reclaim_transcript_sha256(observed),
        "messages": observed, "tool_schemas": copy.deepcopy(tool_schemas),
        # Continuations retain values, never the live plan. The same core can
        # render this captured selection even after another form was chosen.
        "memory_core_sha256": plan.core_sha256 if memory else "",
        "memory_facts": {"floor": copy.deepcopy(plan.projection(mode).memory_facts.get("floor") or {})} if memory else {},
    }
    if physical_capture is _CANONICAL_OBSERVATION:
        return  # Existing non-Main actors retain their own observation boundary.
    observation = ctx._last_context_observation
    observation.update(exposed_units=[], physical_source_status="unavailable")
    if physical_capture is None or not physical_capture.candidate_manifest_ref:
        return
    from ouroboros.context_compaction import exposed_context_units, exposed_context_units_from_atoms
    from ouroboros.observability import read_blob_ref, read_call_manifest_ref

    for root in (getattr(ctx, "budget_drive_root", None), getattr(ctx, "drive_root", None)):
        if root is None:
            continue
        try:
            manifest = read_call_manifest_ref(Path(root), physical_capture.candidate_manifest_ref, task_id=ctx.task_id)
            # The send path records the content identities of the exact prepared bytes before
            # observability redaction (``model_send_seal.persist_physical_candidate``): a unit
            # whose result carries a redacted secret or native content is still recognized.
            # Older manifests fall back to the redacted custody projection they stored. The
            # physical source status names the same attempt manifest either way (the
            # historical-input reader copies its redacted projection); ``exposure_basis``
            # tells which identities decided the exposure.
            atoms = manifest.get("exposure_atoms")
            if isinstance(atoms, dict) and atoms:
                exposed = exposed_context_units_from_atoms(observed, atoms)
                basis = str(manifest.get("exposure_atoms_basis") or "prepared_atoms")
            else:
                payload = read_blob_ref(Path(root), manifest["full_payload_ref"])
                physical_messages = payload.get("messages")
                if not isinstance(physical_messages, list):
                    continue
                exposed, basis = exposed_context_units(observed, physical_messages), "redacted_projection"
            observation.update(
                exposed_units=list(exposed), exposure_basis=basis,
                physical_source_status="observed_projection", physical_attempt_id=physical_capture.attempt_id,
                physical_source_ref=physical_capture.candidate_manifest_ref,
            )
            return
        except (OSError, ValueError, KeyError, TypeError):
            log.debug("Physical context source unavailable", exc_info=True)


def _inspect(ctx, observed: dict, *, offset: int) -> str:
    """A bounded inventory of the observed view: counts and sizes by kind, then one page of
    units from ``offset`` within the tool's producer page size (``tool_result_limit``), naming
    ``next_offset`` while units remain. The reader rebuilds a restore reference from the one
    ``restore_checkpoint`` and a unit's id and hash instead of receiving it repeated per unit."""
    from ouroboros.context_compaction import context_units, owner_protected_unit_ids, unit_kind
    from ouroboros.review_history_view import attachment_transfer_options, review_note_options
    from ouroboros.tool_capabilities import tool_result_limit

    messages = observed["messages"]
    units = context_units(messages, scope="dialogue")
    protected = owner_protected_unit_ids(messages, units)
    checkpoint = observed.get("checkpoint_ref")
    if not checkpoint and getattr(ctx, "task_id", "") and getattr(ctx, "drive_root", None):
        from ouroboros.artifacts import store_actor_source_bytes

        try:
            roots = dict.fromkeys((Path(getattr(ctx, "budget_drive_root", None) or ctx.drive_root).resolve(),
                                   Path(ctx.drive_root).resolve()))
            raw = json.dumps({"messages": messages,
                "observed_view_revision": observed["revision"], "selection_fingerprint": observed["revision"],
                "selected_unit_ids": [unit.unit_id for unit in units]}, ensure_ascii=False).encode("utf-8")
            for root in roots:
                checkpoint = store_actor_source_bytes(root, ctx.task_id, category="context_checkpoints",
                    source_id="view", data=raw, extension="json")
            observed["checkpoint_ref"] = checkpoint
        except (OSError, ValueError):
            checkpoint = None
            log.debug("Inspected context source could not be retained", exc_info=True)
    # Every restore ref remains self-contained. The reader needs the stable
    # content-addressed path/size/hash, not a repeated read_file invocation.
    # Keep the full reader hint on the pinned observation, outside this reply.
    restore_checkpoint = ({key: checkpoint[key] for key in ("kind", "root", "path", "size", "sha256")}
                          if checkpoint else None)
    transfers = attachment_transfer_options(ctx) if getattr(ctx, "task_id", "") else {}
    decisions = review_note_options(ctx) if getattr(ctx, "task_id", "") else {}
    observed["review_decisions"] = copy.deepcopy(decisions)
    exposed = ({(ref.get("unit_id"), ref.get("raw_sha256")) for ref in observed["exposed_units"]}
               if "exposed_units" in observed else None)
    rows, counts, tokens = [], Counter(), Counter()
    for unit in units:
        kind = unit_kind(messages, unit)
        counts[kind] += 1
        tokens[kind] += unit.context_size_tokens
        row = {"unit_id": unit.unit_id, "raw_sha256": unit.raw_sha256, "kind": kind,
               "tokens": unit.context_size_tokens, "eligible": unit.unit_id not in protected}
        if exposed is not None:
            row["exposed"] = (unit.unit_id, unit.raw_sha256) in exposed
        rows.append(row)
    from ouroboros.memory_floor import view_inventory
    plan = getattr(ctx, "context_fit_plan", None)
    memory_available = bool(observed.get("memory_core_sha256")) and (
        observed["memory_core_sha256"] == getattr(plan, "core_sha256", None))
    memory_rows = view_inventory(plan if memory_available else None, getattr(ctx, "active_context_mode", "") or "max",
                                 memory_facts=observed.get("memory_facts"))
    rows.extend(memory_rows)
    counts["memory"] = len(memory_rows)
    tokens["memory"] = sum(row["tokens"] for row in memory_rows)
    body = {
        **({"review_attachment_transfer": transfers} if transfers else {}),
        **({"review_decisions": decisions} if decisions else {}),
        "view_revision": observed["revision"],
        "restore_checkpoint": restore_checkpoint,
        "memory_view_status": "observed_snapshot" if memory_available else "unavailable",
        "memory_re_read_price": "Changing a memory form rewrites the system prefix; later cached input may need re-reading.",
        "inventory": {"units": len(rows), "eligible": sum(row["eligible"] for row in rows),
                      "tokens": sum(tokens.values()),
                      "by_kind": {kind: {"units": counts[kind], "tokens": tokens[kind]} for kind in sorted(counts)}},
        "schema_names": [s["function"]["name"] for s in observed["tool_schemas"]],
        "rule": ("This revision names the observed messages; schemas are listed separately. Units are complete tool "
                 "units, earlier records, your own completed replies (kind assistant) and completed prose rows (kind user; "
                 "eligibility distinguishes governing owner words); a unit with eligible=false carries protected dialogue "
                 "and stays whole. units is one page in transcript order (offset, total; next_offset while more remain: "
                 "call inspect again with that offset). Select complete unit IDs to keep and write a local working_note; "
                 "a restore_unit_refs entry is {checkpoint_ref: restore_checkpoint, unit_id, raw_sha256}. Earlier notes "
                 "stay where they are unless selected for replacement; select several to merge their account and sources. "
                 "view_address selects listed memory forms from this snapshot. Core, governing words, the assignment "
                 "and newer owner/tool messages stay whole."),
    }
    start = max(0, int(offset or 0))

    def render(page: list, next_offset) -> str:
        return json.dumps({**body, "offset": start, "total": len(rows), "units": page,
                           **({"next_offset": next_offset} if next_offset is not None else {})},
                          ensure_ascii=False, separators=(",", ":"))

    limit, page = tool_result_limit("compact_context"), []
    for index in range(start, len(rows)):
        candidate = [*page, rows[index]]
        if page and len(render(candidate, index + 1)) > limit:
            break
        page = candidate
    end = start + len(page)
    return render(page, end if end < len(rows) else None)


def _compact_context(ctx, keep_last_n: int | None = None, *, inspect: bool = False, offset: int = 0,
                     expected_view_revision: str = "", working_note: str | None = None,
                     keep_unit_ids: List[str] | None = None, restore_unit_refs: List[dict] | None = None,
                     schema_names: List[str] | None = None, review_transfers: List[dict] | None = None,
                     review_notes: List[dict] | None = None, view_address: List[str] | None = None, **kwargs) -> str:
    """Inspect or queue a replacement through the existing context materializer.

    The authored view (inspect / working_note / restore) addresses the dialogue scope:
    complete tool units, capsules, the actor's own completed replies and host prose rows,
    all under the same positional, hash-bound unit ids. Rows carrying the owner's typed
    words are listed but never eligible. Standalone restoration keeps current units/schemas
    and authors no note. A count-only request (no note, no restore) is refused: no helper
    writes the note.
    """
    from ouroboros.context_compaction import context_units, unit_kind
    from ouroboros.tools.tool_result import ToolResult, _publish_tool_result

    if inspect:
        observed = getattr(ctx, "_last_context_observation", None)
        if not isinstance(observed, dict):
            return _publish_tool_result(ctx, ToolResult(status="error", code="TOOL_REPORTED_FAILURE",
                text="Context view unavailable: this actor has no recorded model-send observation yet."))
        ctx._inspected_context_view = observed
        return _inspect(ctx, observed, offset=offset)
    if working_note is None and restore_unit_refs:
        if (any(value is not None for value in (keep_last_n, keep_unit_ids, schema_names))
                or review_transfers or review_notes):
            return _publish_tool_result(ctx, ToolResult(status="error", code="TOOL_ARG_ERROR",
                text="Standalone restore_unit_refs keeps current units, schemas and review accounts. "
                     "Omit other selectors, or supply working_note to combine restoration with an authored view."))
        working_note = ""
    if working_note is None and view_address:
        working_note = ""
    if working_note is None:
        return _publish_tool_result(ctx, ToolResult(status="error", code="TOOL_ARG_ERROR", text=_NOTE_REQUIRED))
    # This invocation is a response to the actor's recorded physical send.
    # The host already owns that causal binding; echoing its hash is only
    # needed when the actor explicitly selects an earlier inspected view.
    observed = (getattr(ctx, "_inspected_context_view", None) if expected_view_revision
                else getattr(ctx, "_last_context_observation", None))
    if expected_view_revision and (not isinstance(observed, dict) or observed.get("revision") != expected_view_revision):
        last = getattr(ctx, "_last_context_observation", None)
        if isinstance(last, dict) and last.get("revision") == expected_view_revision:
            observed = last
    if not isinstance(observed, dict) or expected_view_revision and observed.get("revision") != expected_view_revision:
        return _publish_tool_result(ctx, ToolResult(status="error", code="TOOL_ARG_ERROR",
            text="Context view mismatch: call compact_context(inspect=true) and use its view_revision. Current context is unchanged."))
    if (not isinstance(working_note, str)
            or any(values is not None and (not isinstance(values, list)
                   or not all(isinstance(v, str) and v for v in values))
                   for values in (keep_unit_ids, schema_names, view_address))
            or review_notes is not None and not isinstance(review_notes, list)
            or review_transfers is not None and not isinstance(review_transfers, list)
            or restore_unit_refs is not None and (not isinstance(restore_unit_refs, list)
                or not all(isinstance(ref, dict) for ref in restore_unit_refs))):
        return _publish_tool_result(ctx, ToolResult(status="error", code="TOOL_ARG_ERROR",
            text="Invalid context view request: use a prose working_note, arrays of exact unit/schema names and checkpoint reference objects."))
    from ouroboros.review_history_view import resolve_observed_review_notes
    review_notes = resolve_observed_review_notes(review_notes or [], observed)
    if view_address:
        from ouroboros.memory_floor import select_view
        try:
            plan = getattr(ctx, "context_fit_plan", None)
            if plan is None or observed.get("memory_core_sha256") != plan.core_sha256:
                raise ValueError("memory snapshot changed; inspect the current view")
            select_view(plan, getattr(ctx, "active_context_mode", "") or "max", view_address,
                        owner_note=bool(working_note.strip()))
        except ValueError as exc:
            return _publish_tool_result(ctx, ToolResult(status="error", code="TOOL_ARG_ERROR", text=str(exc)))
    if keep_unit_ids is None and keep_last_n is not None:
        # keep_last_n counts completed tool units; everything from the N-th most recent
        # tool unit onward (prose rows between and after them included) stays raw.
        count = max(2, min(keep_last_n, 20))
        units = context_units(observed["messages"], scope="dialogue")
        tool_units = [unit for unit in units if unit_kind(observed["messages"], unit) == "tool"]
        anchor = tool_units[-count].start if len(tool_units) >= count else 0
        keep_unit_ids = [unit.unit_id for unit in units if unit.start >= anchor
                         or unit_kind(observed["messages"], unit) == "capsule"]
    ctx._pending_compaction = {
        "observed": observed, "working_note": working_note, "review_transfers": list(review_transfers or []),
        "review_notes": list(review_notes or []),
        "view_address": tuple(view_address or ()),
        "expected_view_revision": observed["revision"],
        "keep_unit_ids": None if keep_unit_ids is None else tuple(keep_unit_ids),
        "restore_unit_refs": tuple(restore_unit_refs or ()),
        "schema_names": None if schema_names is None else tuple(schema_names),
    }
    return "Working view requested. The next complete tool boundary preserves exact sources and checks the full candidate before applying it; the resulting receipt reports actual changes."


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            name="compact_context",
            schema={
                "name": "compact_context",
                "description": (
                    "Fold complete-input context under your own authored working_note (inspect optional), or "
                    "restore exact sources with restore_unit_refs alone. No helper summarizes for you: a request "
                    "without working_note is refused unless it only selects view_address memory forms or restores sources. Native review does not apply "
                    "schema_names, review_notes or review_transfers; its inspect response reports window evidence, "
                    "serialized characters and previous-request input, not current free tokens. "
                    "inspect answers a bounded inventory (counts and tokens by kind) plus one page of units in "
                    "transcript order (offset / next_offset), each with kind and eligibility; the owner's words and "
                    "your typed owner-directed dialogue, the assignment and the core stay whole. "
                    "Earlier notes stay in place unless selected for replacement; select several to merge them. "
                    "The note occupies the first replaced unit. Rewriting can lose prefix-cache "
                    "reuse and provider-signed reasoning from the earliest change: fold for space/clarity in useful batches, with a note shorter than its sources. "
                    "A selected older assistant tool call and all of its "
                    "contiguous matching results stay atomic; Ouroboros checkpoints their exact "
                    "actor-visible bytes before replacement, with checkpoint/CAS provenance. Raw evidence remains retrievable (restore_unit_refs; an emergency "
                    "record lists its checkpoint once and its member units separately)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "view_address": {"type": "array", "items": {"type": "string"}, "description": "Exact memory form addresses from inspect. Apply at the next tool boundary from the same snapshot, retaining earlier choices and new arrivals. May accompany a working_note fold. Core and governing words cannot be selected. The receipt prices prefix re-reading and measures the complete result."},
                        "inspect": {"type": "boolean", "description": "Return and pin the last observed view revision: an inventory by kind and size, one page of complete unit IDs, the restore checkpoint and current schema names, without changing context."},
                        "offset": {"type": "integer", "minimum": 0, "description": "With inspect: zero-based position of the first unit of the page; the reply names next_offset while units remain."},
                        "expected_view_revision": {"type": "string", "description": "Optional view_revision from inspect, checked exactly. Omitted binds the actual model-send view that produced this call; inspect is not required to replace all completed units."},
                        "working_note": {"type": "string", "description": "For a future reader without selected sources in view: preserve the source-supported situation and purpose behind conclusions, who decided what, its status within those sources, scope, changes, corrections and unresolved work. Distinguish inference and gaps; invent no detail. Later grants/cancellations supersede only their scope, not unaffected branches. Proposals are not approvals; do not retain obsolete restrictions after a later scoped approval. Be concise, no fixed template. Required for every fold except a standalone restore."},
                        "keep_unit_ids": {"type": "array", "items": {"type": "string"}, "description": "Exact inspected complete units to retain raw; takes precedence over keep_last_n, empty keeps none eligible. If both selectors are omitted, an authored note keeps all. Owner words, the assignment, the system view and the newer tail are preserved whatever the selection."},
                        "restore_unit_refs": {"type": "array", "items": {"type": "object", "properties": {
                            "checkpoint_ref": {"type": "object"}, "unit_id": {"type": "string"}, "raw_sha256": {"type": "string"}},
                            "required": ["checkpoint_ref", "unit_id", "raw_sha256"]},
                            "description": "Read exact checkpoint-local units back as labelled sources, never live tool protocol replay (checkpoint_ref is inspect's restore_checkpoint). In Main a nonempty list may be used alone; native review also requires working_note (empty is valid). Supply a note to combine restoration with other view selectors."},
                        "schema_names": {"type": "array", "items": {"type": "string"}, "description": "Nano only: desired canonical schemas. This selects residency, never execution permissions. Low/Max retain their full permitted envelope."},
                        "review_notes": {"type": "array", "items": {"type": "object", "properties": {
                            "bound_decision": {"anyOf": [{"type": "object"}, {"type": "string"}]},
                            "bound_decisions": {"type": "array", "items": {"anyOf": [{"type": "object"}, {"type": "string"}]}},
                            "remark": {"type": "string"}, "reason": {"type": "string"}},
                            "required": ["remark", "reason"], "additionalProperties": False},
                            "description": "With working_note: bound_decision keeps one decision's status with your short remark/reason. Alternatively bound_decisions explicitly replaces a group of closed historical decisions with your account. Use exact bindings or decision_ref values from this observed view; positional IDs alone do not bind a version. Current contracts/statuses, open/deferred/pending facts and gaps stay visible. Missing/stale entries stay full with reasons; no new verdict, host clipping or helper summary."},
                        "review_transfers": {"type": "array", "items": {"type": "object", "properties": {
                            "source": {"type": "object"}, "operative_spec_sha256": {"type": "string"},
                            "decision_ids": {"type": "array", "items": {"type": "string"}}},
                            "required": ["source", "operative_spec_sha256", "decision_ids"]},
                            "description": "With working_note only: explicitly declare that an attachment's decisions and reasons have been transferred into the named current plan spec decisions. Inspect supplies exact source/spec identities. No semantic completeness is inferred; absent or stale transfer keeps the attachment full."},
                        "keep_last_n": {
                            "type": "integer",
                            "description": "With working_note and no keep_unit_ids: number of recent completed atomic tool units to keep raw (range 2-20). Without a note the request is refused.",
                        },
                    },
                    "required": [],
                },
            },
            handler=_compact_context,
            timeout_sec=15,
        ),
    ]

```

## ouroboros/tools/core.py

Source: https://github.com/razzant/ouroboros/blob/add0ce3296c722ef99a536c52f8dcddf36494b36/ouroboros/tools/core.py

SHA-256: `180b2360522214b15c6a14a9a6c3e1e50b07748965061b908c9142be63b78b43`

```
"""File/data tools plus code search and digest helpers."""

from __future__ import annotations

from ouroboros.tools.tool_result import ToolResult, _publish_tool_result, completed_local_read, publish_no_effect

from ouroboros.tools.arg_feedback import payload_item_feedback, with_argument_notes

import copy
import json
import logging
import os
import pathlib
import re
import subprocess
import uuid
from typing import Dict, List

from ouroboros.artifacts import artifact_store_path_block_reason, copy_file_to_task_artifacts
from ouroboros.project_facts import filter_out_project_store as _filter_out_project_store  # noqa: F401
from ouroboros.project_facts import project_store_access_block as _project_store_access_block
from ouroboros.protected_artifacts import block_reason_for_path
from ouroboros.tools.registry import ToolContext, ToolEntry, active_repo_dir_for  # noqa: F401
from ouroboros.tool_access import (
    ResolvedResourceBinding,
    build_resolved_resource_binding,  # noqa: F401
    canonical_data_root,
    decide_tool_access,  # noqa: F401
    active_tool_profile,  # noqa: F401
    normalize_root,  # noqa: F401
    normalize_runtime_data_path,  # noqa: F401
    UserFilesPathBlockedError,
    user_files_path_block_reason,
)
from ouroboros.utils import atomic_write_json, read_text, safe_relpath, utc_now_iso, write_text_atomic  # noqa: F401
from ouroboros.contracts.task_constraint import normalize_task_constraint, resolve_payload_path
from ouroboros.contracts.skill_payload_policy import (
    SKILL_PAYLOAD_ALL_BUCKETS,
    SKILL_PAYLOAD_CONTROL_DIRNAMES,
    SKILL_PAYLOAD_CONTROL_FILENAMES,
    SKILL_OWNER_STATE_FILENAMES,  # noqa: F401
    SkillPayloadPathError,
    SkillPayloadTarget,
    cross_skill_redirect_error,
    decide_payload_short_form,
    is_skill_control_plane_path as _policy_is_skill_control_plane_path,
    is_skill_owner_state_alias,
    is_skill_owner_state_target as _policy_is_skill_owner_state_target,  # noqa: F401
    is_skill_create_typo,
    resolve_skill_payload_target,
)
from ouroboros.tools.core_secret_paths import is_restricted_subagent_profile  # noqa: F401
from ouroboros.tools.core_file_tools import (  # noqa: F401
    _ListingFailure,
    _MEMORY_AT_DRIVE_MEMORY,
    _SKILL_OWNER_STATE_FILENAMES,
    _access_or_block,
    _annotate_reread,
    _coerce_line_window,
    _coerce_start_char,
    _data_list,
    _data_read,
    _direct_resource_binding,
    _is_cognitive_data_path,
    _is_skill_owner_state_target,
    _list_dir,
    _list_files,
    _list_user_files_dir,
    _normalize_data_read_path,
    _profile_roots_hint,
    _read_file,
    _render_line_slice,
    _repo_list,
    _repo_read,
    _root_display_path,
    _runtime_data_read_check,
)
from ouroboros.delegate_reply import REPLY_TO_SCHEMA, validate_reply_target
from ouroboros.tools.core_artifacts import (  # noqa: F401
    _MAX_DOCUMENT_FILE_BYTES,
    _MAX_PHOTO_FILE_BYTES,
    _MAX_VIDEO_FILE_BYTES,
    _detect_document_mime,
    _detect_image_mime,
    _detect_video_mime,
    _send_file,
    _send_photo,
    _send_video,
    LinkActionsValidationError,
    QuizValidationError,
    _MAX_LINK_ACTIONS,
    _MAX_QUIZ_OPTIONS,
    ESCALATE_TOOL_SCHEMA,
    _escalate,
    _send_links,
    validate_link_actions,
    validate_quiz_payload,
)
from ouroboros.tools.cards import SHOW_CARD_TOOL_SCHEMA, _show_card

log = logging.getLogger(__name__)


# Payload-local provenance sidecars are launcher/marketplace-owned, not
# skill-author-editable. Generic write/delete/upload paths must block them.
_SELF_AUTHORED_MARKER = ".self_authored.json"


def _binding_skill_control_plane_path(binding: ResolvedResourceBinding) -> bool:
    """Apply the existing payload-sidecar vocabulary to any physical source."""

    try:
        relative = binding.target_path.relative_to(binding.base_path)
    except ValueError:
        return False
    lowered = tuple(part.lower() for part in relative.parts)
    return bool(
        (lowered and lowered[-1] in SKILL_PAYLOAD_CONTROL_FILENAMES)
        or any(part in SKILL_PAYLOAD_CONTROL_DIRNAMES for part in lowered)
    )


def _skill_payload_parts(target: pathlib.Path, data_root: pathlib.Path) -> tuple[str, str, pathlib.Path] | None:
    """Return (bucket, skill, payload_root) for data/skills payload paths."""
    for candidate in (target, pathlib.Path(target).resolve(strict=False)):
        try:
            rel = candidate.relative_to(data_root)
        except (OSError, ValueError):
            continue
        parts = rel.parts
        if len(parts) < 3 or parts[0].lower() != "skills":
            continue
        bucket = parts[1]
        if bucket.lower() not in SKILL_PAYLOAD_ALL_BUCKETS:
            continue
        skill_name = parts[2]
        if not skill_name or skill_name in {".", ".."}:
            continue
        return bucket.lower(), skill_name, data_root / "skills" / bucket / skill_name
    return None


def _native_payload_mutation_block_reason(
    target: pathlib.Path, data_root: pathlib.Path,
) -> str:
    from ouroboros.config import get_runtime_mode
    from ouroboros.runtime_mode_policy import mode_has_unrestricted_agency

    if mode_has_unrestricted_agency(get_runtime_mode()):
        return ""
    payload = _skill_payload_parts(target, data_root)
    if payload is None:
        return ""
    bucket, _skill_name, payload_root = payload
    if bucket != "native":
        return ""
    if (payload_root / ".seed-origin").is_file():
        return (
            "launcher-seeded data/skills/native/<skill>/ payloads are "
            "read/review only; edit their seed via root=system_repo"
        )
    if not payload_root.is_dir():
        return (
            "data/skills/native/<skill>/ is not a user-managed payload yet; "
            "create new skills under data/skills/external/<skill>/"
        )
    return ""


def _data_skill_target(path: str, drive_root: pathlib.Path) -> SkillPayloadTarget | None:
    """Single resolver for an explicit data-plane skills/<bucket>/<skill>/... write target (None when
    the path is not inside a skill payload). SSOT for both _data_skill_path and the _data_write
    manifest-first typo guard, so the payload resolution is never duplicated."""
    try:
        return resolve_skill_payload_target(pathlib.Path(drive_root), path)
    except SkillPayloadPathError:
        return None


def _data_skill_path(path: str, drive_root: pathlib.Path) -> pathlib.Path | None:
    target = _data_skill_target(path, drive_root)
    return target.target_path if target is not None else None


def is_skill_control_plane_path(target: pathlib.Path, data_root: pathlib.Path) -> bool:
    """Return True for skill owner/provenance files blocked from generic writes."""
    return _policy_is_skill_control_plane_path(target, data_root)


def _is_workspace_executor_control_state_path(target: pathlib.Path, data_root: pathlib.Path) -> bool:
    try:
        rel_parts = pathlib.Path(target).resolve(strict=False).relative_to(
            pathlib.Path(data_root).resolve(strict=False)
        ).parts
    except (OSError, ValueError):
        return False
    lowered = [str(part).casefold() for part in rel_parts]
    return "state" in lowered and "workspace_executor_processes" in lowered


def _str_match_replace(
    text: str, old_str: str, new_str: str, display_path: str, error_tag: str
):
    """Shared exact, byte-level, single-occurrence replacement for both str-replace
    editors — the repo editor (``git._str_replace_editor``) and the data-plane editor
    (``_edit_text``) — so they give IDENTICAL match feedback (deferral 4). Returns
    ``(new_text, None)`` on a unique match, else ``(None, error_message)``: a miss
    carries the bounded edit-miss locator (plus the whole file when it is small),
    duplicates name positions. ``error_tag`` is the caller's error prefix (e.g.
    ``EDIT_TEXT_ERROR``)."""
    count = text.count(old_str)
    if count == 0:
        from ouroboros.tools.edit_ops import locate_edit_miss, whole_file_preview
        return None, (
            f"⚠️ {error_tag}: old_str not found in {display_path}.\n"
            f"{locate_edit_miss(text, old_str)}{whole_file_preview(text)}"
        )
    if count > 1:
        from ouroboros.tools.edit_ops import edit_source_line

        positions = []
        start = 0
        for _ in range(min(count, 5)):
            idx = text.index(old_str, start)
            positions.append(f"line {edit_source_line(text, idx)}")
            start = idx + 1
        return None, (
            f"⚠️ {error_tag}: old_str found {count} times in {display_path} "
            f"(must be unique). Occurrences at: {', '.join(positions)}. "
            f"Include more surrounding context in old_str to make it unique."
        )
    return text.replace(old_str, new_str, 1), None


def _check_data_shrink_guard(
    target: pathlib.Path, new_content: str, force: bool = False
) -> "str | None":
    """Block likely accidental truncation of an EXISTING data-plane file on OVERWRITE,
    unless force=True (deferral 5). Mirrors the repo shrink-guard
    (``git._check_shrink_guard``) but WITHOUT the ``git ls-files`` tracking check — the
    data plane is not a git tree. Skips a non-existent target (a fresh create is any
    size) and appends (the caller only invokes this on overwrite). Never raises."""
    from ouroboros.config import get_runtime_mode
    from ouroboros.runtime_mode_policy import mode_has_unrestricted_agency

    if force or mode_has_unrestricted_agency(get_runtime_mode()):
        return None
    try:
        if not target.exists():
            return None
        old_len = len(target.read_text(encoding="utf-8"))
        new_len = len(new_content)
        if old_len > 0 and new_len < old_len * 0.7:
            pct = round(new_len / old_len * 100)
            return (
                f"⚠️ WRITE_BLOCKED: new content for '{target.name}' is {pct}% of original "
                f"({old_len} -> {new_len} chars). This looks like accidental truncation. "
                f"Use edit_text for surgical edits, or pass force=true to confirm an "
                f"intentional rewrite."
            )
    except Exception:
        return None
    return None


def _data_write(
    ctx: ToolContext,
    path: str,
    content: str,
    mode: str = "overwrite",
    bucket: str = "",
    skill_name: str = "",
    display_root: str = "runtime_data",
    force: bool = False,
    _resolved_binding: ResolvedResourceBinding | None = None,
) -> str:
    if (
        (_resolved_binding is None or _resolved_binding.root == "runtime_data")
        and (b := _project_store_access_block(_normalize_data_read_path(ctx, path)))
    ):
        return b
    # bucket+skill_name synthesize a payload-confined skill_repair constraint.
    short_form = None if _resolved_binding is not None else decide_payload_short_form(
        bucket=bucket, skill_name=skill_name, path_text=path,
        repo_dir=pathlib.Path(ctx.repo_dir), drive_root=pathlib.Path(ctx.drive_root),
    )
    if short_form is not None and short_form.error:
        return f"⚠️ DATA_WRITE_ERROR: {short_form.error}"
    synth = short_form.constraint if short_form is not None else None
    existing_tc = normalize_task_constraint(getattr(ctx, "task_constraint", None))
    redirect_err = cross_skill_redirect_error(existing_tc, synth)
    if redirect_err:
        return f"⚠️ SKILL_REDIRECT_BLOCKED: {redirect_err}"
    # The task's selected resource wins over a synthesized short-form selector.
    if existing_tc and existing_tc.has_selected_skill:
        task_constraint = existing_tc
    else:
        task_constraint = synth or existing_tc
    write_path = _normalize_data_read_path(ctx, path)
    # Resolved skills payload target (None unless this is an explicit skills/<bucket>/<skill> path).
    # The manifest-first typo guard runs LATER, AFTER the owner-state/control-plane/content blocks, so
    # those security blocks take precedence over a missing-payload typo.
    _skill_target = None
    if _resolved_binding is not None:
        p = _resolved_binding.target_path
    elif task_constraint and task_constraint.has_selected_skill and task_constraint.payload_root:
        try:
            p = resolve_payload_path(pathlib.Path(ctx.drive_root), task_constraint, path)
        except ValueError as e:
            return f"⚠️ DATA_WRITE_ERROR: {e}"
    else:
        # Resolve the skills target on the NORMALIZED write_path (the exact path the write uses below)
        # so the manifest-first typo guard can never be skipped by a redundant drive-root / .tmp-data-*
        # prefix that _normalize_data_read_path would later strip into a real skills/<bucket>/<skill>.
        _skill_target = _data_skill_target(write_path, pathlib.Path(ctx.drive_root))
        explicit_skill_target = _skill_target.target_path if _skill_target is not None else None
        p = explicit_skill_target if explicit_skill_target is not None else ctx.drive_path(write_path)
    # Defense-in-depth: settings.json is owner-only. Use inode-aware matching
    # for symlinks/hardlinks/case-insensitive APFS/NTFS, with a fallback for
    # not-yet-existing case variants.
    from ouroboros import config as _cfg
    target_path = pathlib.Path(p)
    settings_path = pathlib.Path(_cfg.SETTINGS_PATH)
    data_root = (
        _resolved_binding.state_drive_root
        if _resolved_binding is not None
        else pathlib.Path(_cfg.DATA_DIR).resolve(strict=False)
    )
    ctx_data_root = pathlib.Path(ctx.drive_root).resolve(strict=False)
    if _resolved_binding is not None:
        lexical_target = pathlib.Path(p).resolve(strict=False)
    elif task_constraint and task_constraint.has_selected_skill and task_constraint.payload_root:
        lexical_target = pathlib.Path(p).resolve(strict=False)
    else:
        lexical_target = pathlib.Path(ctx.drive_root).resolve(strict=False) / safe_relpath(write_path)
    from ouroboros.runtime_mode_policy import mode_has_unrestricted_agency
    cyber = mode_has_unrestricted_agency(_cfg.get_runtime_mode())
    native_block = (
        _native_payload_mutation_block_reason(lexical_target, data_root)
        or _native_payload_mutation_block_reason(target_path, data_root)
    )
    if native_block and not cyber:
        return f"⚠️ DATA_WRITE_BLOCKED: {native_block}."
    skill_owner_state_path = (
        _is_skill_owner_state_target(lexical_target, data_root)
        or _is_skill_owner_state_target(target_path, data_root)
    )
    if not skill_owner_state_path:
        skill_owner_state_path = is_skill_owner_state_alias(target_path, data_root)
    if skill_owner_state_path and not cyber:
        return (
            "⚠️ DATA_WRITE_BLOCKED: skill review, enablement, grants, and "
            "marketplace provenance are owner/review controlled state. Edit "
            "the skill payload under data/skills/ and use skill_review, the "
            "Skills UI toggle, or the desktop launcher grant flow."
        )
    # Block marketplace/launcher sidecars for every data_write path, not only heal mode.
    if not cyber and (
        (_resolved_binding is not None and _binding_skill_control_plane_path(_resolved_binding))
        or is_skill_control_plane_path(lexical_target, data_root)
        or is_skill_control_plane_path(target_path, data_root)
    ):
        return (
            "⚠️ DATA_WRITE_BLOCKED: marketplace provenance and launcher "
            "seed markers (.clawhub.json, .ouroboroshub.json, "
            "SKILL.openclaw.md, .seed-origin) are owner/review controlled. "
            "Edit the payload's user-authored files instead and rerun skill_review."
        )
    if not cyber and (
        _is_workspace_executor_control_state_path(lexical_target, ctx_data_root)
        or _is_workspace_executor_control_state_path(target_path, ctx_data_root)
        or _is_workspace_executor_control_state_path(lexical_target, data_root)
        or _is_workspace_executor_control_state_path(target_path, data_root)
    ):
        return (
            "⚠️ DATA_WRITE_BLOCKED: workspace executor process records are "
            "owner/runtime control-plane state. Use process/service lifecycle "
            "tools instead of writing state/workspace_executor_processes directly."
        )
    matches = False
    try:
        if target_path.exists() and settings_path.exists():
            matches = target_path.samefile(settings_path)
    except OSError:
        matches = False
    if not matches:
        try:
            same_parent = target_path.parent.resolve() == settings_path.parent.resolve()
        except OSError:
            same_parent = False
        if same_parent and target_path.name.lower() == settings_path.name.lower():
            matches = True
    if matches and not cyber:
        return (
            "⚠️ DATA_WRITE_BLOCKED: settings.json is the canonical owner-edited "
            "file. Tool-level writes must route through /api/settings (which "
            "applies key-by-key policy — OUROBOROS_RUNTIME_MODE is owner-only "
            "and dropped on POST; other keys flow through normally). To change "
            "owner-only values, stop the agent, edit ~/Ouroboros/data/settings.json "
            "directly, then restart."
        )
    # Manifest-first typo guard (SSOT with the bucket/skill_name short-form via is_skill_create_typo),
    # applied AFTER the owner-state / control-plane / content DATA_WRITE_BLOCKED guards above so those
    # take precedence: an explicit runtime_data write into a NON-existent skills/<bucket>/<skill>
    # payload is a typo unless it is the root manifest of a NEW external skill — never silently mkdir a
    # bogus payload from a misspelled name (resolution ran on the normalized write_path).
    if (
        _resolved_binding is not None
        and _resolved_binding.source in {"external", "clawhub", "ouroboroshub"}
        and not _resolved_binding.base_path.exists()
        and (
            _resolved_binding.source != "external"
            or is_skill_create_typo(
                payload_root=_resolved_binding.base_path,
                bucket="external",
                rel_within_payload=_resolved_binding.target_path.relative_to(
                    _resolved_binding.base_path
                ).as_posix(),
            )
        )
    ):
        return (
            f"⚠️ DATA_WRITE_ERROR: skill payload not found: "
            f"skills/{_resolved_binding.source}/{_resolved_binding.skill_name}. Use an existing skill; for a "
            "NEW skill write its manifest (SKILL.md/skill.json) at the payload root under "
            "bucket=external; this path looks like a typo into a missing payload."
        )
    if _skill_target is not None and is_skill_create_typo(
        payload_root=_skill_target.payload_root,
        bucket=_skill_target.bucket,
        rel_within_payload=_skill_target.rel_path,
    ):
        return (
            f"⚠️ DATA_WRITE_ERROR: skill payload not found: "
            f"skills/{_skill_target.bucket}/{_skill_target.skill}. Use an existing skill; for a "
            "NEW skill write its manifest (SKILL.md/skill.json) at the payload root under "
            "bucket=external; this path looks like a typo into a missing payload."
        )
    marker_payload = _skill_payload_parts(lexical_target, data_root) or _skill_payload_parts(target_path, data_root)
    should_mark_self_authored = False
    marker_path: pathlib.Path | None = None
    if (
        mode == "overwrite"
        # A genuine NEW external skill is self-authored even when reached via the bucket+skill_name
        # short-form (which synthesizes a skill_repair constraint, so the old `not skill_repair`
        # guard wrongly suppressed provenance on create). Require BOTH the manifest AND the payload
        # directory to be new (`not marker_payload[2].exists()`, evaluated before the mkdir below) so
        # writing a SKILL.md into an ALREADY-EXISTING external skill is never mis-marked self-authored.
        and marker_payload is not None
        and marker_payload[0] == "external"
        and pathlib.PurePosixPath(str(path or "")).name.lower() in {"skill.md", "skill.json"}
        and not target_path.exists()
        and not marker_payload[2].exists()
    ):
        marker_path = marker_payload[2] / _SELF_AUTHORED_MARKER
        should_mark_self_authored = not marker_path.exists()

    # X3 hash-bind: the ADMITTED repair task's payload writes CAS-check the
    # repair's own hash chain (covers overwrite AND append — an append drifts
    # the payload exactly like an overwrite). Foreign lanes pass through.
    _repair_cas_constraint = (
        task_constraint
        if task_constraint and task_constraint.has_selected_skill
        and (_resolved_binding is None or _resolved_binding.skill_name)
        and str(getattr(task_constraint, "skill_name", "") or "")
        else None
    )
    if _repair_cas_constraint is not None:
        from ouroboros.skill_repair_admission import repair_write_cas_error

        _cas = repair_write_cas_error(
            canonical_data_root(ctx), _repair_cas_constraint,
            task_id=str(getattr(ctx, "task_id", "") or ""),
            # The TASK's own constraint decides whether the binding is mandatory:
            # a short-form `bucket`+`skill_name` selector synthesizes the same
            # constraint shape for an ordinary payload edit, and that lane must
            # not need a repair admission.
            repair_task=bool(existing_tc and existing_tc.has_selected_skill))
        if _cas:
            return _cas
    p.parent.mkdir(parents=True, exist_ok=True)
    if mode == "overwrite":
        # Deferral 5: block likely-accidental truncation of an existing data-plane file
        # (e.g. settings.json, skill state) unless force=true. Append is exempt.
        if (shrink := _check_data_shrink_guard(p, content, force)):
            return shrink
        write_text_atomic(p, content)  # crash-safe full overwrite (G)
    else:
        with p.open("a", encoding="utf-8") as f:
            f.write(content)  # append is intentionally NOT atomized
    if _repair_cas_constraint is not None:
        from ouroboros.skill_repair_admission import advance_repair_expected_hash

        advance_repair_expected_hash(
            canonical_data_root(ctx), _repair_cas_constraint,
            task_id=str(getattr(ctx, "task_id", "") or ""))
    if should_mark_self_authored and marker_path is not None:
        from ouroboros.skill_loader import compute_content_hash

        marker_payload[2].mkdir(parents=True, exist_ok=True)
        try:
            initial_hash = compute_content_hash(marker_payload[2])
        except Exception:
            initial_hash = ""
        marker_payload_data = {
            "schema_version": 1,
            "origin": "self_authored",
            "created_at": utc_now_iso(),
            "chat_id": int(getattr(ctx, "current_chat_id", 0) or 0),
            "task_id": str(getattr(ctx, "task_id", "") or ""),
            "created_by_tool": "data_write",
            "initial_content_hash": initial_hash,
        }
        atomic_write_json(marker_path, marker_payload_data, trailing_newline=True)
        state_marker_root = (
            _resolved_binding.state_drive_root
            if _resolved_binding is not None
            else pathlib.Path(ctx.drive_root)
        )
        state_marker = state_marker_root / "state" / "skills" / marker_payload[1] / "self_authored.json"
        state_marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(state_marker, marker_payload_data, trailing_newline=True)
    result = f"OK: wrote {mode} {_root_display_path(display_root, write_path)} ({len(content)} chars)"
    if _resolved_binding is not None:
        result += (
            f" (resolved_root={_resolved_binding.base_path}; "
            f"source={_resolved_binding.source})"
        )
    if short_form is not None and short_form.ignored_reason:
        result += f"\n⚠️ SKILL_SHORT_FORM_IGNORED: {short_form.ignored_reason}."
    return result


def _join_write_results(results: List[str]) -> str:
    rendered = "\n".join(results) if results else "⚠️ TOOL_ARG_ERROR: files must contain {path, content} objects."
    if any(str(line).lstrip().startswith("⚠️") for result in results for line in str(result).splitlines()):
        return "⚠️ WRITE_FILE_BATCH_PARTIAL_FAILURE: one or more writes failed.\n" + rendered
    return rendered


# Consumed item fields own both schema and pre-write validation. Extras remain
# schema-permitted: their value determines omission versus a typed refusal.
_WRITE_FILE_ITEM_PROPERTIES = {"path": {"type": "string"}, "content": {"type": "string"}}
_WRITE_FILE_ITEM_KEYS = tuple(_WRITE_FILE_ITEM_PROPERTIES)


def _write_file(
    ctx: ToolContext,
    path: str = "",
    content: str = "",
    files: List[Dict[str, str]] | None = None,
    root: str | None = None,
    mode: str = "overwrite",
    force: bool = False,
    bucket: str = "",
    skill_name: str = "",
    _resolved_binding: ResolvedResourceBinding | tuple[ResolvedResourceBinding, ...] | None = None,
) -> str:
    from ouroboros.tools.tool_resolution import inferred_file_root

    paths = [str(item.get("path") or "") for item in files if isinstance(item, dict)] if files else [path]
    root, root_error = inferred_file_root(ctx, "write_file", root, paths)
    if root_error:
        return root_error
    normalized, block = _access_or_block(ctx, root, "write")
    if block:
        return publish_no_effect(ctx, block, tool_name="write_file")
    notes = []
    if files is not None:
        refusal, notes = payload_item_feedback(
            ctx, files, _WRITE_FILE_ITEM_PROPERTIES, item_label="file",
            options={"root": normalized, "mode": mode, "force": force,
                     "bucket": bucket, "skill_name": skill_name},
        )
        if refusal:
            return refusal
    try:
        if _resolved_binding is None and files:
            bindings: ResolvedResourceBinding | tuple[ResolvedResourceBinding, ...] = tuple(
                _direct_resource_binding(
                    ctx, None, root=normalized, operation="write",
                    path=str(item.get("path") or ""), bucket=bucket, skill_name=skill_name,
                )
                for item in files
            )
        else:
            bindings = _direct_resource_binding(
                ctx, _resolved_binding, root=normalized, operation="write", path=path,
                bucket=bucket, skill_name=skill_name,
            ) if _resolved_binding is None else _resolved_binding
    except Exception as exc:
        prefix = "SKILL_PAYLOAD_ARG_ERROR" if normalized == "skill_payload" else "WRITE_FILE_ERROR"
        return f"⚠️ {prefix}: {exc}"
    binding_items = bindings if isinstance(bindings, tuple) else (bindings,)
    protected_block = next((
        f"⚠️ WRITE_FILE_BLOCKED: protected artifact path blocked: {reason}"
        for item in binding_items
        if (reason := block_reason_for_path(ctx, item.target_path, "write", item))
    ), "")
    if protected_block:
        return protected_block
    if normalized in {"active_workspace", "system_repo"}:
        from ouroboros.tools.git import _repo_write

        result = _repo_write(
            ctx, path=path, content=content, files=files or [], mode=mode, force=force,
            display_root=normalized, _resolved_binding=bindings,
        )
        return with_argument_notes(ctx, result, notes)
    if normalized in {"runtime_data", "skill_payload"}:
        if files:
            results = []
            binding_iter = iter(binding_items)
            for item in files:
                rel = str(item.get("path") or "")
                body = str(item.get("content") or "")
                item_binding = next(binding_iter, None)
                if item_binding is None:
                    results.append("⚠️ TOOL_ARG_ERROR: files must contain {path, content} objects.")
                    continue
                results.append(_data_write(
                    ctx,
                    rel,
                    body,
                    mode=mode,
                    bucket=bucket,
                    skill_name=skill_name,
                    display_root=normalized,
                    force=force,
                    _resolved_binding=item_binding,
                ))
            return with_argument_notes(ctx, _join_write_results(results), notes)
        return _data_write(
            ctx, path=path, content=content, mode=mode, bucket=bucket,
            skill_name=skill_name, display_root=normalized, force=force,
            _resolved_binding=binding_items[0],
        )
    try:
        if files:
            results = []
            binding_iter = iter(binding_items)
            for item in files:
                rel_path = str(item.get("path") or "")
                item_binding = next(binding_iter, None)
                if item_binding is None:
                    results.append("⚠️ TOOL_ARG_ERROR: files must contain {path, content} objects.")
                    continue
                target = item_binding.target_path
                if normalized == "artifact_store":
                    block_reason = artifact_store_path_block_reason(
                        target, base_path=item_binding.base_path,
                    )
                    if block_reason:
                        results.append(f"⚠️ WRITE_FILE_BLOCKED: artifact_store path blocked: {block_reason}")
                        continue
                target.parent.mkdir(parents=True, exist_ok=True)
                body = str(item.get("content") or "")
                if mode == "append":
                    # Batch items honor the declared mode like the single-file path below:
                    # silently overwriting here destroyed every prior chunk of a chunked
                    # large-file write while reporting success (#447 D2).
                    with target.open("a", encoding="utf-8", newline="") as fh:
                        fh.write(body)  # append is intentionally NOT atomized
                else:
                    # Deferral 5: batch items overwrite too — shrink-guard each (parity with the
                    # single-file path), force=true bypasses.
                    if (shrink := _check_data_shrink_guard(target, body, force)):
                        results.append(shrink)
                        continue
                    write_text_atomic(target, body)  # crash-safe (G)
                result = f"OK: wrote {_root_display_path(normalized, rel_path)} ({len(body)} chars)"
                if normalized == "user_files":
                    record = copy_file_to_task_artifacts(ctx, target, kind="user_file")
                    if record:
                        result += f"\nARTIFACT_OUTPUTS: registered user file -> artifact_store:{record.get('name')}"
                results.append(result)
            return with_argument_notes(ctx, _join_write_results(results), notes)
        target = binding_items[0].target_path
        if normalized == "artifact_store":
            block_reason = artifact_store_path_block_reason(
                target, base_path=binding_items[0].base_path,
            )
            if block_reason:
                return f"⚠️ WRITE_FILE_BLOCKED: artifact_store path blocked: {block_reason}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with target.open("a", encoding="utf-8", newline="") as fh:
                fh.write(content)  # append is intentionally NOT atomized
        else:
            # Deferral 5: shrink-guard the full overwrite (e.g. active_workspace rewrites)
            # — force=true bypasses, matching the tool-schema `force` description.
            if (shrink := _check_data_shrink_guard(target, content, force)):
                return shrink
            write_text_atomic(target, content)  # crash-safe full overwrite (G)
        result = f"OK: wrote {_root_display_path(normalized, path)} ({len(content)} chars)"
        if normalized == "user_files":
            record = copy_file_to_task_artifacts(ctx, target, kind="user_file")
            if record:
                result += f"\nARTIFACT_OUTPUTS: registered user file -> artifact_store:{record.get('name')}"
        return result
    except Exception as exc:
        return f"⚠️ WRITE_FILE_ERROR: {type(exc).__name__}: {exc}"


def _edit_text(
    ctx: ToolContext,
    path: str,
    old_str: str | None = None,
    new_str: str | None = None,
    root: str | None = None,
    bucket: str = "",
    skill_name: str = "",
    force: bool = False,
    edits: List[Dict[str, str]] | None = None,
    _resolved_binding: ResolvedResourceBinding | None = None,
) -> str:
    from ouroboros.tools.tool_resolution import inferred_file_root

    root, root_error = inferred_file_root(ctx, "edit_text", root, [path])
    if root_error:
        return publish_no_effect(ctx, root_error, tool_name="edit_text")
    if edits is not None:
        if old_str is not None or new_str is not None:
            return publish_no_effect(ctx, "⚠️ EDIT_TEXT_ERROR: use either edits or old_str/new_str, not both.", tool_name="edit_text")
        if not isinstance(edits, list) or not edits:
            return publish_no_effect(ctx, "⚠️ EDIT_TEXT_ERROR: edits must be a non-empty array.", tool_name="edit_text")
        from ouroboros.tools.edit_ops import _edit_batch

        if any(not isinstance(item, dict) or "path" in item for item in edits):
            return publish_no_effect(ctx, "⚠️ EDIT_TEXT_ERROR: each edit must contain old_str/new_str/count only; path belongs to the call.", tool_name="edit_text")
        return _edit_batch(
            ctx, [{"path": path, **item} for item in edits], root=root,
            bucket=bucket, skill_name=skill_name, force=force,
            _resolved_binding=tuple(_resolved_binding for _ in edits) if _resolved_binding else None,
            source_tool="edit_text",
        )
    if not isinstance(old_str, str) or not old_str or not isinstance(new_str, str):
        return publish_no_effect(ctx, "⚠️ EDIT_TEXT_ERROR: old_str must be non-empty and new_str must be a string.", tool_name="edit_text")

    normalized, block = _access_or_block(ctx, root, "edit")
    if block:
        return publish_no_effect(ctx, block, tool_name="edit_text")
    try:
        binding = _direct_resource_binding(
            ctx, _resolved_binding, root=normalized, operation="edit", path=path,
            bucket=bucket, skill_name=skill_name,
        )
    except Exception as exc:
        prefix = "SKILL_PAYLOAD_ARG_ERROR" if normalized == "skill_payload" else "EDIT_TEXT_ERROR"
        return publish_no_effect(ctx, f"⚠️ {prefix}: {exc}", tool_name="edit_text")
    from ouroboros.tools.edit_ops import _edit_mutation_block

    if mutation_block := _edit_mutation_block(ctx, binding, path):
        if mutation_block.startswith("skill provenance") and (normalized == "skill_payload" or binding.skill_name):
            return publish_no_effect(ctx, f"⚠️ STR_REPLACE_BLOCKED: {mutation_block}", tool_name="edit_text")
        return publish_no_effect(ctx, f"⚠️ EDIT_TEXT_BLOCKED: {mutation_block}", tool_name="edit_text")
    bound_skill_payload = bool(
        binding.skill_name
        and binding.source in {"external", "clawhub", "ouroboroshub", "native", "user_repo"}
    )
    if normalized in {"active_workspace", "system_repo"} and not bound_skill_payload:
        from ouroboros.tools.git import _str_replace_editor

        result = _str_replace_editor(
            ctx, path=path, old_str=old_str, new_str=new_str,
            display_root=normalized, force=force, _resolved_binding=binding,
        )
        short_form = decide_payload_short_form(
            bucket=bucket,
            skill_name=skill_name,
            path_text=path,
            repo_dir=pathlib.Path(ctx.repo_dir),
            drive_root=pathlib.Path(ctx.drive_root),
        )
        if short_form.ignored_reason:
            result += f"\n⚠️ SKILL_SHORT_FORM_IGNORED: {short_form.ignored_reason}."
        return result
    selected_payload = normalized == "skill_payload" or bound_skill_payload
    try:
        target = binding.target_path
        try:
            text = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return publish_no_effect(ctx, f"⚠️ EDIT_TEXT_ERROR: file not found: {_root_display_path(normalized, path)}", tool_name="edit_text")
        except OSError as exc:
            return publish_no_effect(ctx, f"⚠️ EDIT_TEXT_ERROR: {type(exc).__name__}: {exc}", tool_name="edit_text")
        new_text, match_error = _str_match_replace(
            text, old_str, new_str, _root_display_path(normalized, path), "EDIT_TEXT_ERROR"
        )
        if match_error:
            return publish_no_effect(ctx, match_error, tool_name="edit_text")
        from ouroboros.tools.edit_ops import _edit_content_block, newline_normalization_note

        newline_note = newline_normalization_note(target)
        if content_block := _edit_content_block(binding, new_text, force):
            return publish_no_effect(ctx, content_block, tool_name="edit_text")
        constraint = normalize_task_constraint(getattr(ctx, "task_constraint", None))
        repair = selected_payload and constraint and constraint.has_selected_skill
        if repair:
            from ouroboros.skill_repair_admission import repair_write_cas_error

            refusal = repair_write_cas_error(
                binding.state_drive_root, constraint, task_id=ctx.task_id, repair_task=True,
            )
            if refusal:
                return refusal
        from ouroboros.tools.edit_ops import edit_source_line, numbered_edit_preview

        replacement_offset = text.index(old_str)
        replacement_line = edit_source_line(text, replacement_offset)
        write_text_atomic(target, new_text)
        if repair:
            from ouroboros.skill_repair_admission import advance_repair_expected_hash

            advance_repair_expected_hash(binding.state_drive_root, constraint, task_id=ctx.task_id)
        if selected_payload:
            context_preview = numbered_edit_preview(new_text, [replacement_offset])
            result = (
                f"✅ Replaced in {_root_display_path(normalized, path)} "
                f"(line {replacement_line}; resolved_root={binding.base_path}; "
                f"source={binding.source}).\nContext:\n{context_preview}\n\n"
                "File is on disk but NOT committed.\n"
                "Run skill_review for this skill before enabling or declaring it ready."
            )
            return result + (f"\n{newline_note}" if newline_note else "")
        result = (
            f"OK: edited {_root_display_path(normalized, path)} "
            f"(line {replacement_line}; resolved_root={binding.base_path}; source={binding.source})"
        )
        from ouroboros.tools.edit_ops import _partial_write_failure, numbered_edit_preview

        result += f"\nContext:\n{numbered_edit_preview(new_text, [replacement_offset])}"
        if normalized == "user_files":
            try:
                record = copy_file_to_task_artifacts(ctx, target, kind="user_file")
                if not record:
                    raise OSError("user file recovery copy was not registered")
            except Exception as exc:  # the edit itself landed; say so instead of a refusal
                rel = target.relative_to(binding.base_path).as_posix()
                return _partial_write_failure(
                    ctx, [rel], "edit_text", "EDIT_TEXT_ERROR",
                    f"{rel} WAS written but its user artifact registration failed: {exc}", binding)
            result += f"\nARTIFACT_OUTPUTS: registered user file -> artifact_store:{record.get('name')}"
        if newline_note:
            result += f"\n{newline_note}"
        return result
    except FileNotFoundError:
        return f"⚠️ EDIT_TEXT_ERROR: file not found: {_root_display_path(normalized, path)}"
    except Exception as exc:
        return f"⚠️ EDIT_TEXT_ERROR: {type(exc).__name__}: {exc}"

_MAX_SEARCH_RESULTS = 200
# Search file-skip helper and caps live in ouroboros.code_search_rg (the search
# module SSOT); imported with the historical private names used by call sites.
from ouroboros.code_search_rg import (  # noqa: E402
    MAX_SEARCH_FILES_SCANNED as _MAX_SEARCH_FILES_SCANNED,
    _search_wall_clock_sec,
    is_search_skippable as _is_search_skippable,  # noqa: F401 — re-exported for tests/call sites
    search_skip_reason as _search_skip_reason, matches_include,
    MATCH_WINDOW_CHARS, match_window, search_text,
)


@completed_local_read
def _code_search(ctx: ToolContext, query: str, path: str = ".",
                 regex: bool = False, max_results: int = 200,
                 include: str = "", root: str = "active_workspace",
                 bucket: str = "", skill_name: str = "",
                 _resolved_binding: ResolvedResourceBinding | None = None) -> str:
    """Search repo text with optional regex, path, glob, and result cap."""
    if not query:
        return "⚠️ SEARCH_ERROR: query is required."
    if not isinstance(query, str):
        return "⚠️ SEARCH_ERROR: query must be a string."
    # Validate a regex query UP FRONT, before any path fact: an invalid pattern is an
    # error whatever the path, and the contract holds for BOTH the ripgrep path and the
    # Python fallback. ripgrep accepts some malformed patterns permissively (e.g. an
    # unterminated '[' yields "no matches" instead of erroring), so without this the rg
    # path would silently swallow an invalid regex while only the fallback rejected it.
    # Non-regex queries are matched literally and need no check.
    if regex:
        try:
            re.compile(query)
        except re.error as e:
            return f"⚠️ SEARCH_ERROR: invalid regex: {e}"
    normalized, block = _access_or_block(ctx, root, "search")
    if block:
        return block
    try:
        binding = _direct_resource_binding(
            ctx, _resolved_binding, root=normalized, operation="search", path=path,
            bucket=bucket, skill_name=skill_name,
        )
    except UserFilesPathBlockedError as exc:
        return f"⚠️ USER_FILES_PATH_BLOCKED: {exc}"
    except Exception as exc:
        return f"⚠️ SEARCH_ERROR: {type(exc).__name__}: {exc}"
    if normalized == "runtime_data" and (b := _project_store_access_block(binding.target_path.relative_to(binding.base_path).as_posix())):
        return b

    max_results = min(max(1, max_results), _MAX_SEARCH_RESULTS)
    root_path = binding.base_path
    display_path = binding.target_path.relative_to(root_path).as_posix() if normalized in {"active_workspace", "system_repo"} else path
    display_search_path = _root_display_path(normalized, display_path)
    search_root = binding.target_path
    if normalized != "user_files":
        # Reject a search ROOT that escapes its resource root (e.g. the requested path is an
        # in-tree symlink pointing outside — untrusted child project/deliverable trees) BEFORE
        # any rg/os.walk. Parity with _list_dir + the per-file _path_allowed_for_rg guard.
        try:
            search_root.relative_to(root_path.resolve(strict=False))
        except ValueError:
            return f"⚠️ SEARCH_ERROR: path escapes root: {display_search_path}"
    runtime_check = _runtime_data_read_check(ctx, root=binding.root)
    if runtime_block := runtime_check(search_root):
        return runtime_block
    protected_root_block = block_reason_for_path(
        ctx, search_root, "static_introspection", binding
    )
    if protected_root_block:
        return protected_root_block
    # An admitted, absent path is an ordinary discovery miss: a typed warning
    # (parity with list_files/read_file), never a denial or an invalid query.
    from ouroboros.evidence_packs import materialize_tree
    materialize_tree(search_root)  # a task's packed checkpoint handles are written back before the walk
    if not search_root.exists():
        return f"⚠️ SEARCH_NOT_FOUND: path not found: {display_search_path}"
    protected_root_read_block = block_reason_for_path(
        ctx, search_root, "read_bytes", binding
    )
    if protected_root_read_block and search_root.is_file():
        return protected_root_read_block
    root_resolved = root_path.resolve(strict=False)
    _rt_search_root = str(root_resolved) if normalized == "runtime_data" else ""
    # Filter receipt (D3, capinv-447): every file present under the search root
    # but excluded before reading is COUNTED by typed reason, so "No matches"
    # can honestly distinguish a clean empty result from a filtered one.
    search_drops: dict[str, int] = {}

    def _drop(reason: str) -> bool:
        search_drops[reason] = search_drops.get(reason, 0) + 1
        return False

    def _path_allowed_for_rg(fp: pathlib.Path) -> bool:
        # Resolve, then CONFINE to the resource root: a path whose resolved target
        # escapes it (e.g. an in-root symlink to outside) is rejected — no leak.
        try:
            fp = pathlib.Path(fp).resolve(strict=False)
            rel_parts = fp.relative_to(root_resolved).parts
        except Exception:
            return _drop("escapes_root")
        # runtime_data per-project store is reachable only via project-scoped tools.
        if normalized == "runtime_data" and rel_parts and str(rel_parts[0]).casefold() == "projects":
            return _drop("project_store_scoped")
        if normalized == "user_files" and user_files_path_block_reason(ctx, fp, operation="search"):
            return _drop("user_files_policy")
        if runtime_check(fp):
            return _drop("runtime_data_policy")
        if block_reason_for_path(ctx, fp, "read_bytes", binding):
            return _drop("protected_artifact")
        skip = _search_skip_reason(fp)
        if skip:
            return _drop(skip)
        return True

    import time as _time
    _search_t0 = _time.monotonic()  # start the wall-clock budget BEFORE rg, so a
    # subsequent fallback degradation shares ONE budget (not a fresh 2nd one).
    try:
        from ouroboros.code_search_rg import format_search_result, search_with_rg

        if search_root.is_dir():
            rg_result = search_with_rg(
                search_root, query, regex=bool(regex), include=include,
                max_results=max_results, path_allowed=_path_allowed_for_rg,
            )
            return format_search_result(
                display_path=display_search_path, root_name=normalized,
                root_path=root_path, query=query, regex=bool(regex),
                max_results=max_results, result=rg_result, dropped=search_drops,
            )
    except (FileNotFoundError, RuntimeError, subprocess.SubprocessError, OSError) as e:
        # Degrade to the policy-aware Python scanner for rg absent/failed/timeout
        # AND OSError (wrong-arch/non-executable bundled rg -> 'Exec format
        # error'). MemoryError etc. still propagate rather than silently degrade.
        logging.getLogger(__name__).debug("search_code: ripgrep unavailable, using fallback: %s", e)

    try:
        if regex:
            pattern = re.compile(query)
        else:
            pattern = re.compile(re.escape(query))
    except re.error as e:
        return f"⚠️ SEARCH_ERROR: invalid regex: {e}"

    matches: List[str] = []
    files_searched = 0
    search_drops.clear()  # the fallback re-walks the tree; drop any partial rg counts
    truncated = False
    files_capped = False
    deadline_hit = False
    # search_code on a single FILE: os.walk yields nothing for a file path, which
    # would make the search a silent no-op. Feed the scanner a one-file "walk".
    if search_root.is_file():
        _walker = [(str(search_root.parent), [], [search_root.name])]
    else:
        _walker = os.walk(str(search_root))
    # Bound TOTAL (rg attempt + this fallback walk) to one budget, but always grant
    # the fallback a small floor so an rg that ate the budget still makes some progress.
    _search_deadline = max(_search_t0 + _search_wall_clock_sec(), _time.monotonic() + 5.0)
    for dirpath, dirnames, filenames in _walker:
        # Wall-clock cap: the file-count cap bounds memory but a walk over a very
        # large root (user_files == / under a bench HOME) can traverse for minutes.
        if _time.monotonic() > _search_deadline:
            deadline_hit = True  # ran out of TIME (distinct from the file-count cap)
            break
        # Prune skipped dirs in-place. For runtime_data, also prune the top-level
        # per-project store (reachable only via the project-scoped tools).
        from ouroboros.code_intelligence import SKIP_DIRS

        # SKIP_DIRS pruning is a documented walk convention (parity with rg's
        # skip of .git/node_modules); POLICY prunes below are counted — a whole
        # subtree removed by policy must not read as "0 files searched" with no
        # receipt (#447 D3).
        dirnames[:] = [d for d in sorted(dirnames) if d not in SKIP_DIRS]
        if normalized == "runtime_data" and str(pathlib.Path(dirpath).resolve(strict=False)) == _rt_search_root:
            for d in dirnames:
                if d.casefold() == "projects":
                    _drop("project_store_scoped")
            dirnames[:] = [d for d in dirnames if d.casefold() != "projects"]
        if normalized == "user_files":
            kept_dirs = []
            for d in dirnames:
                if user_files_path_block_reason(ctx, pathlib.Path(dirpath) / d, operation="search"):
                    _drop("user_files_policy")
                else:
                    kept_dirs.append(d)
            dirnames[:] = kept_dirs
        for fname in sorted(filenames):
            fp = pathlib.Path(dirpath) / fname

            if not matches_include(fname, include):
                continue

            if normalized == "user_files" and user_files_path_block_reason(ctx, fp, operation="search"):
                _drop("user_files_policy")
                continue
            if runtime_check(fp):
                _drop("runtime_data_policy")
                continue
            if block_reason_for_path(ctx, fp, "read_bytes", binding):
                _drop("protected_artifact")
                continue

            if _skip := _search_skip_reason(fp):
                _drop(_skip)
                continue

            # CONFINE to the root before reading (parity with the rg path's _path_allowed_for_rg
            # and _list_dir): a resolved file escaping the root — e.g. an in-tree symlink in an
            # untrusted child project/deliverable tree — must never have its target read out.
            try:
                fp.resolve(strict=False).relative_to(root_resolved)
            except (OSError, ValueError):
                _drop("escapes_root")
                continue

            if files_searched >= _MAX_SEARCH_FILES_SCANNED:
                files_capped = True
                break

            try:
                text, decodable = search_text(fp.read_bytes())
            except Exception:
                _drop("read_error")
                continue

            files_searched += 1
            rel = fp.relative_to(root_path).as_posix()

            # These lines are read_file's own (universal newlines, splitlines, code points).
            for lineno, line in enumerate(text.splitlines(), 1):
                if found := pattern.search(line):
                    shown = line.rstrip() if len(line) <= MATCH_WINDOW_CHARS else match_window(
                        line, found.start(), found.end(), count=sum(1 for _ in pattern.finditer(line)),
                        read_line=lineno if decodable else 0)
                    matches.append(f"{_root_display_path(normalized, rel)}:{lineno}: {shown}")
                    if len(matches) >= max_results:
                        truncated = True
                        break
            if truncated:
                break
        if truncated or files_capped or deadline_hit:
            break

    # A deadline cutoff means even a "no matches" may be INCOMPLETE (parity with the rg
    # path's deadline signal) — never let a timed-out fallback read as authoritative empty.
    deadline_note = (
        " Search stopped at the time budget before the whole tree was scanned — results "
        "may be incomplete; narrow the path or glob, or raise OUROBOROS_SEARCH_CODE_WALL_SEC."
        if deadline_hit else ""
    )
    protected_omitted = search_drops.get("protected_artifact", 0)
    # Filter receipt for the remaining ordinary exclusions (D3): oversized,
    # symlinks, excluded names, unreadable, policy-scoped — never silent.
    from ouroboros.code_search_rg import format_dropped_files_note

    other_dropped_note = format_dropped_files_note({
        key: count for key, count in search_drops.items()
        if key != "protected_artifact"
    })
    if not matches:
        suffix = f" {protected_omitted} protected artifact file(s) omitted." if protected_omitted else ""
        cap_note = f" Scan stopped after {_MAX_SEARCH_FILES_SCANNED} files — narrow the path or glob." if files_capped else ""
        return f"No matches found for {'regex' if regex else 'literal'} `{query}` in {display_search_path} ({files_searched} files searched).{suffix}{other_dropped_note}{cap_note}{deadline_note}"

    header = f"Found {len(matches)} match{'es' if len(matches) != 1 else ''} in {display_search_path} ({files_searched} files searched)"
    if files_capped:
        header += f" — scan stopped at {_MAX_SEARCH_FILES_SCANNED} files (narrow the path or glob)"
    if truncated:
        header += f" — truncated at {max_results} results"
    if deadline_hit:
        header += " — stopped at the time budget (results may be incomplete)"
    if protected_omitted:
        header += f" — {protected_omitted} protected artifact file(s) omitted"
    if other_dropped_note:
        header += " —" + other_dropped_note.rstrip(".")
    return header + "\n\n" + "\n".join(matches)


def _forward_to_worker(
    ctx: ToolContext, task_id: str, message: str, relayed_from_task_id: str = "", reply_to: dict | None = None,
) -> str:
    """Write task context to the recipient's mailbox, never owner text.
    Descendants receive ancestor/relayed context; contributions inside one tree
    (parent, sibling, any task sharing the root) retain their relation. Listed
    roots and inline Presence receive independent task context. Receipts prove
    persistence, not a read, in the recipient's drive."""
    from ouroboros.owner_mailbox import (
        PEER_RELATION_LABELS, PROVENANCE_INDEPENDENT_TASK, PROVENANCE_PEER_TASK, TASK_MESSAGE_MAX_CHARS,
        write_task_message,
    )
    from ouroboros.peer_roster import (
        durable_descendant_of, independent_message_target, peer_contribution_admission,
    )
    from ouroboros.task_results import STATUS_REQUESTED, STATUS_RUNNING, STATUS_SCHEDULED, validate_task_id
    from ouroboros.task_status import FINAL_STATUSES, load_effective_task_result

    try:
        tid = validate_task_id(task_id)
        reply_to = validate_reply_target(ctx, reply_to, recipient=tid)
    except ValueError as exc:
        return f"⚠️ TOOL_ARG_ERROR (forward_to_worker): {exc}"
    if len(str(message or "")) > TASK_MESSAGE_MAX_CHARS:
        return _publish_tool_result(ctx, ToolResult(status="error", code="TOOL_ARG_ERROR", text=(
            f"⚠️ TOOL_ARG_ERROR (forward_to_worker): message is {len(str(message))} chars; the "
            f"limit is {TASK_MESSAGE_MAX_CHARS} chars. Nothing was written — shorten the "
            "message (it is never truncated or spilled to an artifact).")))
    metadata = getattr(ctx, "task_metadata", {}) if isinstance(getattr(ctx, "task_metadata", {}), dict) else {}
    status_drive_root = pathlib.Path(str(metadata.get("budget_drive_root") or getattr(ctx, "budget_drive_root", "") or ctx.drive_root))
    data = load_effective_task_result(status_drive_root, tid)
    status = str(data.get("status") or "").lower()
    if not data:
        return _publish_tool_result(ctx, ToolResult(status="unavailable", code="LEGACY_UNAVAILABLE", text=(f"⚠️ TASK_NOT_FOUND: task {tid} is not registered.")))
    if status in FINAL_STATUSES:
        return _publish_tool_result(ctx, ToolResult(status="blocked", code="LEGACY_BLOCKED", text=(f"⚠️ TASK_NOT_ACTIVE: task {tid} is already {status}.")))
    # A requested child's mailbox exists in its recorded drive from the request on: its words are
    # read when it starts, or stay in its result as unread mail, the same receipt a queued one gives.
    if status not in (STATUS_RUNNING, STATUS_SCHEDULED, STATUS_REQUESTED):
        return _publish_tool_result(ctx, ToolResult(status="blocked", code="LEGACY_BLOCKED", text=(f"⚠️ TASK_NOT_ACTIVE: task {tid} is {status or 'unknown'}, neither running nor queued.")))
    # Cancellation may still report running; consult intent and legacy latch
    # before accepting new steering, just as steer_task/mailbox do.
    try:
        from ouroboros.cancel_intents import cancel_pending

        if cancel_pending(status_drive_root, tid):
            return (
                _publish_tool_result(ctx, ToolResult(status="blocked", code="LEGACY_BLOCKED", text=(f"⚠️ TASK_CANCEL_PENDING: task {tid} has a pending cancellation — the "
                "supervisor is tearing it down; the message was NOT delivered. Wait for "
                "the settled outcome or start a new task.")))
            )
    except Exception:
        log.debug("forward_to_worker cancel-pending check failed for %s", tid, exc_info=True)
    current_task_id = str(getattr(ctx, "task_id", "") or "").strip()
    if not current_task_id:
        return "⚠️ TASK_FORBIDDEN: forward_to_worker requires an active task context."
    relayed_from = str(relayed_from_task_id or "").strip()
    provenance = "ancestor_task"
    relation = ""
    listed_root = None
    if not durable_descendant_of(status_drive_root, tid, data, current_task_id):
        # Same-tree peer admission precedes the independent roster; it refuses
        # relay and reads cancellation strictly.
        relation, refusal = peer_contribution_admission(
            status_drive_root, current_task_id, metadata, tid, data, relayed_from=relayed_from)
        if refusal is not None:
            return _publish_tool_result(ctx, refusal)
        if relation:
            provenance = PROVENANCE_PEER_TASK
        else:
            listed_root = independent_message_target(status_drive_root, tid, data)
            if listed_root is None:
                return (f"⚠️ TASK_FORBIDDEN: task {tid} is neither a descendant of the current task, nor a task "
                        "in its tree (the parent nor a sibling nor any task sharing its root), nor an active "
                        "independent root the host lists or an inline Presence mailbox.")
            if relayed_from:
                return f"⚠️ TASK_FORBIDDEN: a relayed message reaches only your own descendants; task {tid} is an independent recipient."
            provenance = PROVENANCE_INDEPENDENT_TASK
    if relayed_from:
        try:
            relayed_from = validate_task_id(relayed_from)
        except ValueError as exc:
            return f"⚠️ TOOL_ARG_ERROR (forward_to_worker): {exc}"
        source = load_effective_task_result(status_drive_root, relayed_from)
        if not source or not durable_descendant_of(
            status_drive_root, relayed_from, source, current_task_id,
        ):
            return (
                f"⚠️ TASK_FORBIDDEN: relay source {relayed_from} is not a child or "
                "descendant of the current task."
            )
        provenance = "peer_via_ancestor"
    child_drive = str(data.get("child_drive_root") or data.get("headless_child_drive_root") or data.get("drive_root") or "").strip()
    if not child_drive and listed_root is not None:
        child_drive = str(listed_root.get("drive_root") or "").strip()
    # The recipient drains ITS recorded drive, else the canonical status root — never
    # the sender's ``ctx.drive_root`` (a forked sender's private execution drive).
    mailbox_drive = pathlib.Path(child_drive) if child_drive else status_drive_root
    from ouroboros.install_identity import StoreForeign

    try:
        written = write_task_message(
            mailbox_drive,
            message,
            task_id=tid,
            source_task_id=current_task_id,
            provenance=provenance,
            relayed_from_task_id=relayed_from,
            msg_id=uuid.uuid4().hex,
            relation=relation,
            **({"reply_to": reply_to} if reply_to else {}),
        )
    except StoreForeign as exc:
        return _publish_tool_result(ctx, ToolResult(status="blocked", code="ACCESS_BLOCKED", text=(
            f"⚠️ TASK_FOREIGN_INSTALL: task {tid}'s mailbox {exc}; the message was NOT delivered."),
            meta={"reason": exc.reason, "path": str(exc.path)}))
    if not written:
        return _publish_tool_result(ctx, ToolResult(status="error", code="TOOL_ERROR", text=(f"⚠️ TASK_MESSAGE_UNWRITTEN: message to task {tid} was not persisted.")))
    from ouroboros.owner_mailbox import MAIL_QUEUED, MAIL_RETAINED_UNREAD, mail_write_receipt, mailbox_drain_ended

    try:
        drain_ended = mailbox_drain_ended(mailbox_drive, tid)
    except Exception:
        drain_ended = False
    receipt = mail_write_receipt(status, drain_ended=drain_ended)["receipt"]
    if receipt == MAIL_RETAINED_UNREAD:
        return (f"Message forwarded to task {tid}: written to its mailbox ({MAIL_RETAINED_UNREAD}); task {tid}'s "
                "own drain has already ended, so no checkpoint will read it: its result keeps it as unread mail. "
                "Files cannot be attached to messages between tasks.")
    if listed_root is not None and listed_root.get("target_kind") == "inline_presence":
        observation = json.dumps(listed_root["execution_observation"], ensure_ascii=False, sort_keys=True)
        return (f"Message forwarded to task {tid}: written to its mailbox as a message from this task "
                "(independent_task, never owner text). This proves persistence, not that its model read it; "
                "if the turn continues, its checkpoint can read it. "
                f"execution_observation={observation}. Files cannot be attached to messages between tasks.")
    # A Presence root reached from inside its own tree keeps the shared execution
    # observation on the receipt (persistence proof, not a read), as the roster path gives it.
    presence = data.get("execution_observation") if provenance == PROVENANCE_PEER_TASK else None
    observed = (f" execution_observation={json.dumps(presence, ensure_ascii=False, sort_keys=True)}."
                if isinstance(presence, dict) and presence.get("kind") == "presence" else "")
    as_peer = PEER_RELATION_LABELS.get(relation, {}).get("receipt") or relation
    if receipt == MAIL_QUEUED:
        as_from = (f" as a message from a peer task ({as_peer}; never owner text or an ancestor's steering)"
                   if provenance == PROVENANCE_PEER_TASK else " as a message from this task (never owner text)"
                   if listed_root is not None else "")
        return (f"Message forwarded to task {tid}: written to its mailbox{as_from} ({MAIL_QUEUED}); task {tid} has not "
                "started, so nothing has read it: it reads it when it starts, and if it ends unstarted its result keeps "
                f"it as unread mail.{observed} Files cannot be attached to messages between tasks.")
    if provenance == PROVENANCE_PEER_TASK:
        # A Presence turn may already be over: persistence is proven, a read is not.
        read_note = ("This proves persistence, not that its model read it; if the turn continues, "
                     "its checkpoint can read it." if observed else "it reads it at its next checkpoint.")
        return (f"Message forwarded to task {tid}: written to its mailbox as a message from a peer task "
                f"({as_peer}; never owner text or an ancestor's steering){'.' if observed else ';'} "
                f"{read_note}{observed} Files cannot be attached to messages between tasks.")
    if listed_root is not None:
        return (f"Message forwarded to task {tid}: written to its mailbox as a message from this task "
                "(never owner text); it reads it at its next checkpoint. Files cannot be attached to messages between tasks.")
    return f"Message forwarded to task {tid}"


def get_tools() -> List[ToolEntry]:
    from ouroboros.tools.edit_ops import _EDIT_BATCH_ITEM_PROPERTIES, _EDIT_BATCH_ITEM_REQUIRED, _FILE_ROOT_DESCRIPTION
    return [
        ToolEntry("read_file", {
            "name": "read_file",
            "description": (
                "Read a UTF-8 text file from a declared resource root. "
                "With root omitted, relative paths use active_workspace; absolute paths select their permitted physical root, including user_files for home paths. "
                "Use max_lines (default 2000) and start_line (default 1) to read large files in chunks. "
                "The result header shows root:path and 'lines X\u2013Y of Z'; each displayed source line has a cat -n style number and tab. "
                "Line numbers are display guides, not file content: exclude them when copying edit context. "
                "Prefer this over cat/head/sed-as-reader in run_command; to locate code first use query_code "
                "(symbols/definitions/callers) or search_code (text/regex), then read_file the hit."
            ),
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "root": {"type": "string", "enum": ["active_workspace", "system_repo", "runtime_data", "task_drive", "skill_payload", "artifact_store", "user_files", "subagent_projects", "deliverables"], "description": _FILE_ROOT_DESCRIPTION},
                "max_lines": {"type": "integer", "default": 2000,
                              "description": "Maximum number of lines to return (default 2000)."},
                "start_line": {"type": "integer", "default": 1,
                               "description": "1-indexed line to start reading from (default 1 = beginning)."},
                "start_char": {"type": "integer", "default": 0,
                               "description": "Source-character cursor: skip this many characters (Unicode code points) "
                                              "of the selected source window before adding display line numbers. Use it "
                                              "to advance WITHIN a single line longer than what one turn can hold; a "
                                              "partial result view names the exact start_char of its omitted range."},
                "max_chars": {"type": "integer", "minimum": 1,
                              "description": "Exact source-character budget for THIS view of the selected window, counted "
                                             "after start_char; display line numbers are not counted. Choose the piece instead "
                                             "of having the result cut from outside; the header names the next start_char. "
                                             "Omit for the whole window."},
                "bucket": {"type": "string", "description": "Required only for root=skill_payload."},
                "skill_name": {"type": "string", "description": "Required only for root=skill_payload."},
            }, "required": ["path"]},
        }, _read_file),
        ToolEntry("list_files", {
            "name": "list_files",
            "description": "List a directory, or use depth/include for a bounded live tree. Gaps make complete=false. "
                           "Generated subtrees are skipped only in code roots; symlink directories are not followed.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "default": "."},
                "root": {"type": "string", "enum": ["active_workspace", "system_repo", "runtime_data", "task_drive", "skill_payload", "artifact_store", "user_files", "subagent_projects", "deliverables"], "description": _FILE_ROOT_DESCRIPTION},
                "max_entries": {"type": "integer", "default": 500},
                "depth": {"type": "integer", "minimum": 1, "maximum": 20, "default": 1, "description": "Tree levels; depth=1 with no include preserves the ordinary one-directory listing."},
                "include": {"type": "string", "description": "Optional basename glob, including braces such as *.{py,js}; directories are traversed but not returned as matches."},
                "max_scan": {"type": "integer", "minimum": 1, "maximum": 20000, "default": 2000, "description": "Opt-in tree's examined-entry bound, not just match count; one additional lookahead detects a cut directory. Packed checkpoints are omitted with a direct listing recipe."},
                "bucket": {"type": "string", "description": "Required only for root=skill_payload."},
                "skill_name": {"type": "string", "description": "Required only for root=skill_payload."},
            }, "required": []},
        }, _list_files),
        ToolEntry("write_file", {
            "name": "write_file",
            "description": (
                (
                'Create, overwrite or append UTF-8 files using path+content or files=[...]. Canonical output uses '
                'root=artifact_store, created lazily. Omitted root selects active_workspace for relative paths and the '
                'permitted physical root for absolute paths. Repo overwrites return a diff; check it. Invalid '
                "Python/JSON is blocked unless force=true. Use mode='append' for chunks. bucket/skill_name apply only "
                'to skill_payload.'
            )
            ),
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "files": {"type": "array", "items": {"type": "object",
                    "properties": {k: dict(v) for k, v in _WRITE_FILE_ITEM_PROPERTIES.items()},
                    "required": list(_WRITE_FILE_ITEM_KEYS)}},
                "root": {"type": "string", "enum": ["active_workspace", "system_repo", "runtime_data", "task_drive", "skill_payload", "artifact_store", "user_files"], "description": _FILE_ROOT_DESCRIPTION},
                "mode": {"type": "string", "enum": ["overwrite", "append"], "default": "overwrite"},
                "force": {"type": "boolean", "default": False, "description": "Bypass the shrink guard for an intentional full rewrite on any root where it applies (active_workspace via the repo guard; runtime_data/task_drive/skill_payload/artifact_store/user_files via the data-plane guard)."},
                "bucket": {
                    "type": "string",
                    "description": "Skill payload bucket — set ONLY when root=skill_payload (skill authoring); leave empty for normal file edits.",
                },
                "skill_name": {
                    "type": "string",
                    "description": "Skill slug — set ONLY when root=skill_payload; leave empty otherwise.",
                },
            }, "required": []},
        }, _write_file, is_code_tool=True),
        ToolEntry("edit_text", {
            "name": "edit_text",
            "description": (
                "Replace exactly one occurrence of old_str with new_str in a file, "
                "or pass edits=[{old_str,new_str,count?}, ...] for ordered atomic "
                "replacements in that SAME file. Do not mix the forms. "
                "With root omitted, relative paths use active_workspace; absolute paths select their permitted physical root, including user_files for home paths. Result messages show root:path. "
                "For several files use edit_batch or apply_patch; editor calls run one after another in call order. "
                "Set bucket/skill_name ONLY for root=skill_payload (skill authoring); leave empty for normal edits."
            ),
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"},
                "edits": {"type": "array", "minItems": 1, "items": {"type": "object",
                          "properties": {k: dict(v) for k, v in _EDIT_BATCH_ITEM_PROPERTIES.items() if k != "path"},
                          "required": [k for k in _EDIT_BATCH_ITEM_REQUIRED if k != "path"]}},
                "root": {"type": "string", "enum": ["active_workspace", "system_repo", "runtime_data", "task_drive", "skill_payload", "artifact_store", "user_files"], "description": _FILE_ROOT_DESCRIPTION},
                "bucket": {"type": "string", "description": "Skill payload bucket — set ONLY when root=skill_payload; leave empty otherwise."},
                "skill_name": {"type": "string", "description": "Skill slug — set ONLY when root=skill_payload; leave empty otherwise."},
                "force": {"type": "boolean", "default": False, "description": "Confirm an intentional shrink (>30% smaller)."},
            }, "required": ["path"]},
        }, _edit_text, is_code_tool=True),
        ToolEntry("send_photo", {
            "name": "send_photo",
            "description": (
                "Send an image to the owner's chat. "
                "Preferred: use file_path to send a local file. "
                "Legacy: use image_base64 with raw base64 or __last_screenshot__. "
                "Use after browse_page(output='screenshot') or browser_action(action='screenshot')."
            ),
            "parameters": {"type": "object", "properties": {
                "file_path": {"type": "string", "description": "Local file path to image (preferred)"},
                "image_base64": {"type": "string", "description": "Base64-encoded image data or __last_screenshot__"},
                "caption": {"type": "string", "description": "Optional caption for the photo"},
            }, "required": []},
        }, _send_photo),
        ToolEntry("send_video", {
            "name": "send_video",
            "description": "Send a local video file to the owner's chat, with an optional caption. Requires file_path.",
            "parameters": {"type": "object", "properties": {
                "file_path": {"type": "string", "description": "Local file path to the video."},
                "caption": {"type": "string", "description": "Optional caption for the video"},
            }, "required": ["file_path"]},
        }, _send_video),
        ToolEntry("send_file", {
            "name": "send_file",
            "description": (
                "Send an arbitrary document/file to the owner's chat (e.g. a report, .md/.csv/.html, "
                "PDF, archive, or code file — anything that is not an image or video). "
                "Requires a local file_path; large files use a captured download reference. Deliver a finished file result "
                "the owner can download, rather than only describing it or sending a screenshot."
            ),
            "parameters": {"type": "object", "properties": {
                "file_path": {"type": "string", "description": "Local file path to the document/file"},
                "caption": {"type": "string", "description": "Optional caption for the file"},
            }, "required": ["file_path"]},
        }, _send_file),
        ToolEntry("send_links", {
            "name": "send_links",
            "description": "Send one or more HTTP(S) links as prominent clickable buttons in the owner's chat.",
            "parameters": {"type": "object", "properties": {
                "title": {"type": "string", "description": "Optional heading above the buttons"},
                "links": {"type": "array", "minItems": 1, "maxItems": _MAX_LINK_ACTIONS, "items": {
                    "type": "object", "properties": {
                        "label": {"type": "string"},
                        "url": {"type": "string"},
                    }, "required": ["label", "url"]}},
            }, "required": ["links"]},
        }, _send_links),
        ToolEntry("show_card", copy.deepcopy(SHOW_CARD_TOOL_SCHEMA), _show_card,
                  # Owner-chat delivery like send_file; an app runs only in the opaque sandbox frame.
                  safety_policy="skip"),
        ToolEntry("search_code", {
            "name": "search_code",
            "description": (
                (
                'Search literal text (default) or regex under root/path. Returns root:file:line matches with context; '
                'long lines include a read_file range around the first match. Skips binaries, caches, vendor '
                'directories and files over 1MB. max_results bounds matches. Use query_code for definitions, callers, '
                'references or impact.'
            )
            ),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "Search pattern (literal or regex)"},
                "path": {"type": "string", "default": ".", "description": "Subdirectory to search, relative to the selected root"},
                "root": {"type": "string", "enum": ["active_workspace", "system_repo", "runtime_data", "task_drive", "skill_payload", "artifact_store", "user_files", "subagent_projects", "deliverables"], "description": _FILE_ROOT_DESCRIPTION},
                "bucket": {"type": "string", "description": "Required only for root=skill_payload."},
                "skill_name": {"type": "string", "description": "Required only for root=skill_payload."},
                "regex": {"type": "boolean", "default": False, "description": "Treat query as a regular expression"},
                "max_results": {"type": "integer", "default": 200, "description": "Maximum number of matches to return (max 200)"},
                "include": {"type": "string", "default": "", "description": "Basename glob, including brace alternatives (e.g. '*.py', '*.{js,css}'); applies on every backend"},
            }, "required": ["query"]},
        }, _code_search),
        # A fresh copy per catalog, as the inline literals are.
        ToolEntry("escalate", copy.deepcopy(ESCALATE_TOOL_SCHEMA), _escalate),
        ToolEntry("forward_to_worker", {
            "name": "forward_to_worker",
            "description": (
                "Write an addressed task-tree message into a running, queued or requested task's mailbox: a child "
                "or descendant of yours (delivered as the ancestor's message), any other task in your "
                "tree — your parent, a sibling, or any task sharing your root (delivered as a message "
                "from a peer task naming the relation — a contribution it weighs, never steering; relay "
                "is refused there), or any active independent root the host lists or a source-bound "
                "inline Presence turn (a message from an independent task). Presence observation gaps "
                "are disclosed, also when the Presence root is in your own tree; a write never "
                "proves a read. It is never labelled owner dialogue, files cannot be attached, the body "
                "is limited to 8000 chars (longer is refused, never truncated), and the "
                "result says written, not read: a running task drains it at its next checkpoint, a queued "
                "one when it starts, and a task that ends without reading it keeps it as unread mail in its "
                "result. To wait for a reply without spending model rounds, call await_messages."
            ),
            "parameters": {"type": "object", "properties": {
                "task_id": {"type": "string", "description": "ID of the running, queued or requested task to forward to"},
                "message": {"type": "string", "description": "Message text to forward (at most 8000 chars)"},
                "reply_to": REPLY_TO_SCHEMA,
                "relayed_from_task_id": {"type": "string", "description":
                    "Optional sibling/descendant task whose output this ancestor relays. "
                    "The recipient sees both peer and ancestor provenance."},
            }, "required": ["task_id", "message"]},
        }, _forward_to_worker),
    ]

```

## ouroboros/tools/shell.py

Source: https://github.com/razzant/ouroboros/blob/add0ce3296c722ef99a536c52f8dcddf36494b36/ouroboros/tools/shell.py

SHA-256: `de2ab1cafa5bd2de7cb14fc48354ee6224fa53ac853a0d090d12074e082d9cb9`

```
"""Process tools: run_command and run_script."""

from __future__ import annotations

import hashlib  # noqa: F401
from hashlib import sha256  # noqa: F401
import json
import logging
import os
import pathlib
import re
import shlex
import shutil
import tempfile
import signal  # noqa: F401
import stat  # noqa: F401
import subprocess
import threading  # noqa: F401
import time
import uuid
from typing import Dict, List  # noqa: F401

from ouroboros.owner_pause import OwnerPauseRefused
from ouroboros.tool_capabilities import RESULT_VIEW_PARAMS
from ouroboros.tools.tool_result import launch_refusal_result, _publish_tool_result
from ouroboros.artifacts import copy_directory_to_task_artifacts, copy_file_to_task_artifacts, record_task_scratch  # noqa: F401
from ouroboros.platform_layer import bootstrap_process_path, kill_process_tree, scrub_repo_from_pythonpath, subprocess_new_group_kwargs  # noqa: F401
from ouroboros.process_interpreters import (
    active_node_resolution,
    apply_env_path_prepend,
    interpreter_path_overlay,
)
from ouroboros.config import SETTINGS_DEFAULTS, load_settings  # noqa: F401
from ouroboros.runtime_mode_policy import (
    is_protected_runtime_path,  # noqa: F401
)
from ouroboros.tools.commit_gate import _invalidate_advisory
from ouroboros.shell_parse import POSIX_SHELL_HEADS, is_absolute_path_text, recover_stringified_argv  # noqa: F401
from ouroboros.shell_parse import inline_operand_start
from ouroboros.tools.tool_result import _publish_process_result
from ouroboros.tools.verify import check_exit_masking  # noqa: F401 -- ONE exit-masking sensor shared with verify_and_record (pinned here); its disclosure lives in shell_audit
from ouroboros.tools.registry import (
    ToolContext,
    ToolEntry,
)
from ouroboros.tools import shell_audit as _shell_audit
from ouroboros.tools.deliverables_shell import lexical_user_files_block_reason  # noqa: F401
from ouroboros.tools.shell_audit import (
    _UNDECLARED_OUTPUTS_MARKER,
    _disclose_output_audit_failure,
    _masked_green_disclosure,
    _mentioned_user_file_outputs_without_declaration,
    _presence_allows_user_output,  # noqa: F401
    _allowed_output_roots,  # noqa: F401
)
from ouroboros.tools.shell_process import (  # noqa: F401
    _RUN_SHELL_DEFAULT_TIMEOUT_SEC,
    _active_subprocesses,
    _describe_returncode,
    _executor_can_run_cwd,
    _format_process_output,
    _kill_process_group,
    _resolve_effective_timeout,
    _shell_env_for_cwd,
    _subprocess_lock,
    _tracked_subprocess_run,
    kill_all_tracked_subprocesses,
)
from ouroboros.tools.shell_effects import (  # noqa: F401
    _get_changed_files,
    _get_diff_stat,
    _record_scratch_fingerprints,
    _resolve_git_root,
    _resolve_scratch_abs,
    _scratch_safety_reason,
    _shallow_listing,
    _status_snapshot,
    _tree_fingerprint,
    _user_files_run_had_effect,
)
from ouroboros.tools.shell_outputs import (  # noqa: F401
    _directory_fingerprint,
    _changed_path_covers,
    _declared_output_baseline, _declared_output_refusal, _prepare_declared_output_context,
    _directory_fingerprint_from_entries,
    _fingerprint_output,
    _protected_output_source_reason,
    _register_process_outputs,
    _resolve_declared_output,
    _scan_directory_output_members,
    _sensitive_output_component_reason,
    _snapshot_declared_outputs,
)

# Preserve private module attributes used by existing callers/tests while the
# implementation lives in the extracted audit module.
_EMBEDDED_OUTPUT_PATH_RE = _shell_audit._EMBEDDED_OUTPUT_PATH_RE
_OUTPUT_CALL_PATH_RE = _shell_audit._OUTPUT_CALL_PATH_RE
_OUTPUT_REDIRECT_PATH_RE = _shell_audit._OUTPUT_REDIRECT_PATH_RE
_OUTPUT_STAT_SLACK_SEC = _shell_audit._OUTPUT_STAT_SLACK_SEC
_USER_FILE_OPEN_WRITE_CALL_RE = _shell_audit._USER_FILE_OPEN_WRITE_CALL_RE
_USER_FILE_REDIRECT_RE = _shell_audit._USER_FILE_REDIRECT_RE
_USER_FILE_WRITE_CALL_RE = _shell_audit._USER_FILE_WRITE_CALL_RE
from ouroboros.tool_access import (
    ResolvedResourceBinding,
    _deliverables_root_lexical,  # noqa: F401
    _deliverables_root_lexical_alias,  # noqa: F401
    _lexical_path_is_relative_to_casefold,  # noqa: F401
    _path_is_relative_to_casefold,  # noqa: F401
    build_resolved_resource_binding,
    path_is_relative_to,  # noqa: F401
    resource_root_path,  # noqa: F401
    shell_cwd_block_message,
    user_files_path_block_reason,  # noqa: F401
)
from ouroboros.utils import safe_relpath  # noqa: F401
from ouroboros.deadline_utils import deadline_remaining_sec  # noqa: F401
from ouroboros.workspace_executor import execute as executor_execute
from ouroboros.workspace_executor import executor_ref_from_ctx
from ouroboros.workspace_executor import map_backend_path as executor_map_backend_path  # noqa: F401
from ouroboros.workspace_executor import map_backend_path_lexical as executor_map_backend_path_lexical  # noqa: F401
from ouroboros.workspace_executor import map_host_path as executor_map_host_path

log = logging.getLogger(__name__)
# Tracked process groups let panic kill descendant trees too.
_CONTROL_DIR_BACKUP_MAX_BYTES = 5 * 1024 * 1024
# Typed process-facts channel (R5) seam: ouroboros/tools/process_facts.py.
# Historical private spellings stay as aliases for call sites and tests.
from ouroboros.tools.process_facts import (  # noqa: E402
    active_resolved_runtime as _active_resolved_runtime,
    process_environment_tool, record_runtime_selection, selected_process_environment, wrap_script_result, script_refusal_with_source,
    publish_process_facts as _publish_process_facts,  # noqa: F401 — historical private spelling for call sites and tests
)
from ouroboros.tools.shell_process import (  # noqa: E402
    _publish_finished_process_facts,
    _publish_unfinished_process_facts,
)



# v6.90.x (submarine unwind) — the DECLARATION-NUDGE marker, deliberately typed
# APART from the real ``ARTIFACT_OUTPUT_ERROR`` registration failure above. The
# command SUCCEEDED (exit_code=0) and this only asks for ``outputs=[...]`` to be
# declared, so its status lands in the v6.57.0 POLICY-DENIAL partition
# (``_outcome_tool_errors._POLICY_DENIAL_STATUSES``) instead of degrading execution
# to ``tool_failure``. The submarine wave-3 incident was exactly this: a moot nudge
# on an already-registered artifact fed the failure record. SSOT for both
# ``run_command`` and ``run_script`` so the two nudges cannot drift apart.


_SHELL_BUILTINS = frozenset([
    "cd", "source", ".", "export", "alias", "eval",
    "set", "unset", "pushd", "popd", "read", "ulimit",
])

_SHELL_OPERATORS = frozenset(["&&", "||", "|", ";", ">", ">>", "<", "<<"])
# A redirect GLUED into a single argv element ("2>/dev/null", "2>&1", ">out.log",
# "&>x") — the standalone-operator set above misses these. Anchored at the element
# START so a '>' inside a sed/awk/grep expression ("s/a>b/c/g") is NOT flagged.
# Output redirects keep a permissive glued tail. Input redirects are restricted to
# UNAMBIGUOUS shapes — heredoc/herestring ("<<EOF", "<<<s"), an fd-prefixed input
# ("0<f", "2<&1"), or a bare standalone "<" — because a plain "<word" element is
# indistinguishable from a legitimate literal angle-bracket arg (grep "<div>",
# "<stdin>"), and false-flagging those is worse than missing a rare glued "<file"
# input redirect. Pipes/control operators are deliberately NOT matched (a glued
# '|' is valid regex alternation, grep "a|b").
_GLUED_REDIRECT_RE = re.compile(
    r'^(?:(?:\d+>>?|>>?&?\d*|\d*>&\d*|&>>?)(?:\S.*)?|\d+<\S*|<<\S*|<)$'
)
_SHELL_INTERPRETERS = POSIX_SHELL_HEADS | frozenset({"fish", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"})
_ENV_REF_PATTERN = re.compile(r'\$(?:\{[A-Z][A-Z0-9_]*\}|[A-Z][A-Z0-9_]*)')


# Portable grep fix: GNU basic-regex "\|" fails on BSD grep in argv mode.
_GREP_TOOLS = frozenset(("grep", "egrep", "fgrep"))
_GREP_REGEX_MODE_FLAGS = frozenset((
    "-E", "--extended-regexp",
    "-P", "--perl-regexp",
    "-F", "--fixed-strings",
    "-G", "--basic-regexp",
))
_GREP_BACKSLASH_PIPE_PATTERN = re.compile(r'\\\|')
_NO_MATCH_EXIT_TOOLS = frozenset(("grep", "egrep", "fgrep", "rg", "ag", "ack"))


def _is_search_no_match(res: subprocess.CompletedProcess) -> bool:
    tool = pathlib.Path(str(res.args[0] if res.args else "")).name.lower()
    return (
        int(res.returncode) == 1
        and tool in _NO_MATCH_EXIT_TOOLS
        and not str(res.stderr or "").strip()
    )


def _grep_has_explicit_regex_mode(cmd: List[str]) -> bool:
    """Return whether grep argv already chooses regex/string flavor."""
    if not cmd:
        return False
    tool = pathlib.Path(cmd[0]).name.lower()
    if tool in ("egrep", "fgrep"):
        return True
    for arg in cmd[1:]:
        if not isinstance(arg, str):
            continue
        if arg in _GREP_REGEX_MODE_FLAGS:
            return True
        if arg.startswith("--"):
            continue
        # Short options may be clustered, e.g. `grep -rnE pattern path`.
        if arg.startswith("-") and any(flag in arg[1:] for flag in ("E", "P", "F", "G")):
            return True
    return False


def _maybe_autocorrect_grep_backslash_pipe(cmd: List[str]) -> tuple[List[str], str]:
    if not cmd or pathlib.Path(cmd[0]).name.lower() not in _GREP_TOOLS:
        return cmd, ""
    if _grep_has_explicit_regex_mode(cmd):
        return cmd, ""
    corrected = list(cmd)
    changed_args: list[str] = []
    for idx, arg in enumerate(corrected[1:], start=1):
        if isinstance(arg, str) and _GREP_BACKSLASH_PIPE_PATTERN.search(arg):
            corrected[idx] = _GREP_BACKSLASH_PIPE_PATTERN.sub("|", arg)
            changed_args.append(arg)
    if not changed_args:
        return cmd, ""
    corrected.insert(1, "-E")
    return corrected, (
        "⚠️ SHELL_REGEX_AUTO_CORRECTED: converted grep backslash-escaped "
        "alternation (\\|) to extended regex mode (`grep -E`) and rewrote "
        f"{changed_args!r} to use `|`.\n"
    )


def _literal_argv_notes(cmd: List[str]) -> str:
    """Disclosure notes for shell-syntax-looking bytes in direct argv (#447 A5).

    A commit message naming ``$HOME``, an awk ``|`` field separator, a ``2>/dev/null`` element — run_command
    starts no shell for direct argv, so these are ordinary arguments carrying no authority question. They used
    to be REFUSED as errors, blocking commands that would have worked; the in-file autocorrect precedent applies
    instead: run the command and DISCLOSE what was passed, so a genuinely mistaken spelling still explains its
    own cryptic program error. The operand of an explicit ``cmd /c|/k`` or PowerShell ``-c|-Command`` is that
    interpreter's own command text, not an argument (#927); an argv element gets one note, its first category's.
    """
    def _note(subject: str, remedy: str) -> str:
        return (f"⚠️ SHELL_LITERAL_ARGV_NOTE: {subject} in the cmd array was passed to the program as an "
                f"ordinary argument; run_command starts no shell for direct argv. {remedy}\n")

    notes: list[str] = []
    head = pathlib.PurePath(cmd[0].replace("\\", "/")).name.lower() if cmd else ""
    env_at, env_ref = next(((i, m) for i, a in enumerate(cmd) if head not in _SHELL_INTERPRETERS
                            and (m := _ENV_REF_PATTERN.search(a))), (-1, None))
    if env_ref:
        notes.append(_note(
            f'literal env reference "{env_ref.group(0)}"',
            'Use ["sh", "-c", "..."] if you intended shell expansion.'))
    shell = head.removesuffix(".exe")
    operand = inline_operand_start(cmd, shell, strict=True) if shell in {"cmd", "powershell", "pwsh"} else 0
    passed = cmd[:operand] if operand else cmd
    if found_ops := _SHELL_OPERATORS.intersection(passed):
        notes.append(_note(
            f'shell operator "{sorted(found_ops)[0]}"',
            'Use ["sh", "-c", "cmd1 && cmd2"] for pipes/chaining.'))
    glued = next((a for i, a in enumerate(passed)
                  if i != env_at and a not in found_ops and _GLUED_REDIRECT_RE.match(a)), "")
    if glued:
        notes.append(_note(
            f'redirect-looking argument "{glued}"',
            'Use ["sh", "-c", "..."] for real redirection.'))
    return "".join(notes)


def _pre_spawn_refusal(ctx: ToolContext, text: str, *, tool: str = "run_command") -> str:
    """This producer refused before submission; arbitrary errors carry no such fact."""
    from ouroboros.tools.tool_result import LegacyTextResultAdapter, _replace_tool_result

    result = LegacyTextResultAdapter.from_text(tool, text)
    return _publish_tool_result(ctx, _replace_tool_result(
        result, meta_updates={"operation_outcome": "completed_no_effect"}))


@process_environment_tool
def _run_shell(
    ctx: ToolContext,
    cmd,
    cwd: str = "",
    outputs: List[str] | None = None,
    scratch: List[str] | None = None,
    _resolved_binding: ResolvedResourceBinding | None = None,
    **kwargs,
) -> str:
    # Per-call timeout override (canonical timeout_sec; timeout accepted as alias).
    timeout_sec = kwargs.get("timeout_sec")
    timeout = kwargs.get("timeout")
    _timeout_override = timeout_sec if timeout_sec is not None else timeout
    bucket = str(kwargs.get("bucket") or "")
    skill_name = str(kwargs.get("skill_name") or "")
    if isinstance(cmd, str):
        # Shared recovery keeps run_command and verify argv semantics aligned.
        recovered = recover_stringified_argv(cmd)
        # Malformed structured literals are not shell commands; refuse explicitly.
        if recovered is None:
            stripped = cmd.lstrip()
            is_posix_test_cmd = stripped.startswith("[ ") and stripped.rstrip().endswith(" ]")
            # A `{ ...; }` brace group is valid shell, not malformed JSON.
            is_brace_group = stripped.startswith("{ ") and stripped.rstrip().endswith("}")
            if is_brace_group:
                return _pre_spawn_refusal(ctx, '⚠️ SHELL_CMD_ERROR: `{ ...; }` is a shell brace group, which run_command '
                    'cannot execute directly (it runs argv without a shell). Wrap it in a shell:\n'
                    '  run_command(cmd=["sh", "-c", "{ cmd1; cmd2; }"])')
            if stripped[:1] in ("[", "{") and not is_posix_test_cmd:
                return _pre_spawn_refusal(ctx, '⚠️ SHELL_ARG_ERROR: `cmd` looks like a JSON/Python list literal '
                    'but failed to parse cleanly (likely an escape or quote-mismatch '
                    'issue). Pass cmd as an actual array, not a stringified array.\n\n'
                    'Correct usage:\n'
                    '  run_command(cmd=["git", "log", "--oneline", "-10"])\n\n'
                    'Wrong usage (the failure that brought you here):\n'
                    '  run_command(cmd=\'["git", "log", "--oneline", "-10"]\')\n\n'
                    'For reading files, prefer `read_file`.\n'
                    'For searching code, prefer `search_code`.')
            try:
                parts = shlex.split(cmd)
                if parts:
                    recovered = parts
            except ValueError:
                pass
        if recovered is not None:
            cmd = recovered
        else:
            return _pre_spawn_refusal(ctx, '⚠️ SHELL_ARG_ERROR: `cmd` must be a JSON array of strings, not a plain string.\n\n'
                'Correct usage:\n'
                '  run_command(cmd=["grep", "-r", "pattern", "path/"])\n'
                '  run_command(cmd=["python", "-c", "print(1+1)"])\n\n'
                'Wrong usage:\n'
                '  run_command(cmd="grep -r pattern path/")\n\n'
                'For reading files, prefer `read_file`.\n'
                'For searching code, prefer `search_code`.')

    if not isinstance(cmd, list):
        return _pre_spawn_refusal(ctx, "⚠️ SHELL_ARG_ERROR: cmd must be a list of strings.")
    cmd = [str(x) for x in cmd]
    if not cmd:
        return _pre_spawn_refusal(ctx, "⚠️ SHELL_ARG_ERROR: cmd must not be empty.")

    if cmd and cmd[0] in _SHELL_BUILTINS:
        if cmd[0] == "cd":
            return _pre_spawn_refusal(ctx, '⚠️ SHELL_CMD_ERROR: "cd" is a shell builtin, not an executable. '
                'Use the "cwd" parameter instead: '
                'run_command(cmd=["git", "log"], cwd="/target/dir")')
        return _pre_spawn_refusal(ctx, f'⚠️ SHELL_CMD_ERROR: "{cmd[0]}" is a shell builtin and cannot '
            'be executed directly via subprocess. '
            'Use ["sh", "-c", "your command"] if you need shell builtins.')

    cmd, autocorrect_note = _maybe_autocorrect_grep_backslash_pipe(cmd)
    regex_autocorrected = bool(autocorrect_note)
    autocorrect_note += _literal_argv_notes(cmd)

    try:
        binding = _resolved_binding or build_resolved_resource_binding(
            ctx, operation="shell", process_cwd=cwd, bucket=bucket, skill_name=skill_name,
        )
        work_dir = pathlib.Path(binding.target_path)
        cwd_root = binding.root
    except (OSError, ValueError) as exc:
        return _pre_spawn_refusal(ctx, shell_cwd_block_message(ctx, cwd, operation="shell", error=exc))
    if not work_dir.exists() or not work_dir.is_dir():
        return _pre_spawn_refusal(ctx, f"⚠️ SHELL_CWD_BLOCKED: cwd is not a directory: {work_dir}. "
            f"root={binding.root}, source={binding.source}.")
    output_context, output_error = _prepare_declared_output_context(ctx, outputs, work_dir)
    if output_error is not None:
        return _publish_tool_result(ctx, output_error)
    if output_refusal := _declared_output_refusal(ctx, outputs, pathlib.Path(work_dir), cwd_root=cwd_root, binding=binding,
                                                 output_context=output_context):
        return _pre_spawn_refusal(ctx, output_refusal)
    # Disclose the room-lens default once; explicit cwd is already caller-visible.
    if not str(cwd or "").strip() and not getattr(ctx, "_room_cwd_noted", False):
        try:
            from ouroboros.tool_access import project_room_lens_dir

            _room = project_room_lens_dir(ctx)
        except Exception:
            _room = None
        if _room is not None and pathlib.Path(work_dir).resolve(strict=False) == _room:
            ctx._room_cwd_noted = True
            autocorrect_note += (
                f"[project-room cwd: this command ran in {_room} (the room's folder). "
                'The Ouroboros system repo needs an explicit cwd.]\n\n'
            )
    repo_root = _resolve_git_root(pathlib.Path(work_dir))
    before_changed = _status_snapshot(repo_root)
    # Bounded snapshot makes the user_files artifact nudge effect-based.
    before_listing = (
        _shallow_listing(pathlib.Path(work_dir))
        if (cwd_root == "user_files" and repo_root is None and not outputs)
        else None
    )
    before_outputs, baseline_problem = _declared_output_baseline(ctx, outputs, pathlib.Path(work_dir), cwd_root=cwd_root,
                                                                 changed_paths=set(before_changed or []), binding=binding,
                                                                 output_context=output_context)

    # Scratch is confined/new/untracked, patch-excluded, and never an artifact.
    scratch_abs = _resolve_scratch_abs(scratch, work_dir)
    if scratch_abs:
        _scratch_reason = _scratch_safety_reason(ctx, scratch_abs, pathlib.Path(work_dir), repo_root)
        if _scratch_reason:
            return _pre_spawn_refusal(ctx, f"⚠️ SCRATCH_BLOCKED: {_scratch_reason}.")
    timeout_sec = _resolve_effective_timeout(_RUN_SHELL_DEFAULT_TIMEOUT_SEC, ctx, override_sec=_timeout_override)
    bootstrap_process_path()
    # Emergency bundled-node PATH prepend; None on every healthy path (env stays byte-identical).
    node_resolution = active_node_resolution(ctx)
    from ouroboros.workspace_executor import overlay_env
    selected_env = selected_process_environment()
    run_env = apply_env_path_prepend(overlay_env(_shell_env_for_cwd(ctx, pathlib.Path(work_dir)), selected_env), node_resolution)
    record_runtime_selection(ctx, cmd, work_dir, run_env)
    # Two clocks (D2-1): EPOCH feeds the st_mtime audit; MONOTONIC feeds durations.
    _command_start_epoch = time.time()
    _command_start_ts = time.monotonic()
    res = None
    try:
        if output_context is not None or _executor_can_run_cwd(ctx, pathlib.Path(work_dir)):
            res = executor_execute(ctx, cmd, pathlib.Path(work_dir), timeout_sec,
                                   env_overlay=interpreter_path_overlay(node_resolution),
                                   **({"executor": output_context.executor} if output_context else {}),
                                   **({"target_env": selected_env} if selected_env else {}))
        else:
            res = _tracked_subprocess_run(
                cmd, cwd=str(work_dir),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=timeout_sec,
                **({"env": run_env} if run_env is not None else {}),
            )
        process_meta = {"operation_outcome": getattr(res, "operation_outcome", "completed")}
        _lived_ms = _publish_finished_process_facts(ctx, res, _command_start_ts)
        # Post-run hashes exclude scratch only while its exact bytes still match.
        _record_scratch_fingerprints(ctx, scratch_abs)
        executor_note = ""
        if getattr(res, "backend_trace", None):
            executor_note = "\n\nEXECUTOR_TRACE:\n" + json.dumps(res.backend_trace, ensure_ascii=False, indent=2)
        if res.returncode != 0:
            if _is_search_no_match(res):
                text = autocorrect_note + (
                    f"{_describe_returncode(res.returncode, cwd=work_dir, binding=binding)} (no matches)\n"
                    f"{_format_process_output(res.stdout or '', '')}"
                    f"{executor_note}"
                )
                return _publish_process_result(ctx, "SHELL_NO_MATCH", text, exit_code=res.returncode, shell_regex_auto_corrected=regex_autocorrected, meta=process_meta)
            text = autocorrect_note + f"⚠️ SHELL_EXIT_ERROR: command exited with {_describe_returncode(res.returncode, cwd=work_dir, binding=binding, lived_ms=_lived_ms, resolved_runtime=_active_resolved_runtime(ctx))}.\n\n{_format_process_output(res.stdout or '', res.stderr or '')}{executor_note}"
            return _publish_process_result(ctx, "SHELL_EXIT_ERROR", text, exit_code=res.returncode, shell_regex_auto_corrected=regex_autocorrected, meta=process_meta)
        after_changed = _status_snapshot(repo_root)
        if after_changed != before_changed:
            # This resolved cwd may be outside the live-repo dispatcher snapshot.
            _invalidate_advisory(
                ctx,
                changed_paths=after_changed or before_changed,
                mutation_root=repo_root,
                source_tool="run_command",
            )
        audit_error = ""
        try:
            undeclared_user_outputs = _mentioned_user_file_outputs_without_declaration(
                ctx, cmd, outputs, scratch_abs=scratch_abs,
                command_start_ts=_command_start_epoch, cwd=work_dir,
            )
        except Exception as exc:
            # The child already finished. This observational failure must not
            # reach the spawn-error handler and overwrite its measured facts.
            undeclared_user_outputs, audit_error = [], type(exc).__name__
        if undeclared_user_outputs:
            # Declaration NUDGE, not a failure — see _UNDECLARED_OUTPUTS_MARKER.
            text = (
                autocorrect_note
                + f"{_UNDECLARED_OUTPUTS_MARKER}: command appears to write user_files outputs "
                "without declaring outputs=[...]. Declare generated user-visible files so "
                "they are copied into the task artifact store before claiming completion. "
                f"Paths: {', '.join(undeclared_user_outputs[:5])}.\n\n"
                + f"{_describe_returncode(0, cwd=work_dir, binding=binding)}\n"
                + _format_process_output(res.stdout or "", res.stderr or "")
            )
            return _masked_green_disclosure(ctx, _publish_process_result(ctx, "ARTIFACT_OUTPUT_UNDECLARED", text, exit_code=0, shell_regex_auto_corrected=regex_autocorrected, meta=process_meta), cmd)
        artifact_note, artifact_failed, artifact_registered = _register_process_outputs(
            ctx,
            outputs,
            pathlib.Path(work_dir),
            cwd_root=cwd_root,
            changed_paths=set(after_changed or []),
            before_outputs=before_outputs,
            binding=binding,
            output_context=output_context,
        )
        artifact_note += f"\n- output baseline unavailable before the run ({baseline_problem}): a registered output may predate it" if baseline_problem and artifact_note else ""
        audit_note = ""
        if cwd_root == "user_files" and not outputs:
            # Remove scratch effects without hiding a simultaneous real deliverable.
            _after_for_audit = after_changed
            if scratch_abs and repo_root is not None:
                _repo = pathlib.Path(repo_root).resolve(strict=False)
                _scratch_rel: set[str] = set()
                for _sp in scratch_abs:
                    try:
                        _scratch_rel.add(_sp.resolve(strict=False).relative_to(_repo).as_posix())
                    except ValueError:
                        continue
                if _scratch_rel:
                    _after_for_audit = [p for p in (after_changed or []) if p not in _scratch_rel]
            if _user_files_run_had_effect(before_changed, _after_for_audit, before_listing, pathlib.Path(work_dir)):
                audit_note = (
                    "\n\n⚠️ ARTIFACT_AUDIT_GAP: command modified files in a user_files cwd without "
                    "outputs=[...]. If it created a deliverable, rerun/register the file "
                    "with outputs or write_file(root=artifact_store) before claiming it."
                )
        scratch_note = ""
        _scratch_remaining = [str(p) for p in scratch_abs if p.exists()]
        if _scratch_remaining:
            scratch_note = (
                "\n\n⚠️ SCRATCH_REMAINS: declared scratch still on disk after the command: "
                + ", ".join(_scratch_remaining[:5])
                + ". It is excluded from the workspace patch, but delete it before finishing so it does not linger."
            )
        if artifact_failed:
            text = (
                autocorrect_note
                + "⚠️ ARTIFACT_OUTPUT_ERROR: command succeeded but declared output registration failed. "
                + f"{_describe_returncode(0, cwd=work_dir, binding=binding)}\n"
                + f"{_format_process_output(res.stdout or '', res.stderr or '')}"
                + artifact_note
                + executor_note
            )
            result = _masked_green_disclosure(ctx, _publish_process_result(ctx, "ARTIFACT_OUTPUT_ERROR", text, exit_code=0, shell_regex_auto_corrected=regex_autocorrected, meta=process_meta), cmd)
            return _disclose_output_audit_failure(ctx, result, audit_error)
        text = autocorrect_note + f"{_describe_returncode(0, cwd=work_dir, binding=binding)}\n{_format_process_output(res.stdout or '', res.stderr or '')}{artifact_note}{audit_note}{scratch_note}{executor_note}"
        result = _masked_green_disclosure(ctx, _publish_process_result(ctx, "SHELL_REGEX_AUTO_CORRECTED" if regex_autocorrected else "OK", text, exit_code=0, artifact_registered=bool(artifact_registered and not artifact_failed), shell_regex_auto_corrected=regex_autocorrected, meta=process_meta), cmd)
        return _disclose_output_audit_failure(ctx, result, audit_error)
    except OwnerPauseRefused as exc:
        return _publish_tool_result(ctx, launch_refusal_result(exc))
    except subprocess.TimeoutExpired:
        _publish_unfinished_process_facts(ctx, _command_start_ts, timed_out=True)
        # Timeout-created scratch still needs its exclusion fingerprint.
        _record_scratch_fingerprints(ctx, scratch_abs)
        return (
            f"⚠️ TOOL_TIMEOUT (run_command): command exceeded the per-command timeout of {timeout_sec}s "
            f"and the host killed what it could still reach of its process tree; a background child may survive untracked (root={binding.root}, cwd={work_dir}). NOTE: this is the per-command "
            f"FOREGROUND timeout, NOT the task deadline. For genuinely long-running compute (training, "
            f"sampling, large builds/downloads), start it with start_service and poll "
            f"service_status/service_logs while you do other work, or pass an explicit timeout_sec=<seconds> "
            f"(up to the per-call ceiling) — and preserve a best-effort deliverable before the task deadline."
        )
    except Exception as e:
        if res is None:
            _publish_unfinished_process_facts(ctx, _command_start_ts, spawn_error=e)
        _record_scratch_fingerprints(ctx, scratch_abs)
        if res is None and getattr(e, "process_not_started", False) is True and isinstance(e, FileNotFoundError) and len(cmd) == 1:
            return _pre_spawn_refusal(ctx,
                "⚠️ SHELL_ARG_ERROR: the sole cmd element was treated as ONE executable name, "
                "and that executable was not found. Pass the program and each argument as "
                'separate array elements, e.g. ["git", "status", "--porcelain"]. For pipes, '
                'redirects or chaining, explicitly use ["sh", "-c", "..."] or run_script. '
                f"No command was started. root={binding.root}, cwd={work_dir}"
            )
        text = f"⚠️ SHELL_ERROR: {e}. root={binding.root}, cwd={work_dir}"
        return (_pre_spawn_refusal(ctx, text) if res is None and getattr(e, "process_not_started", False) is True
                else text)


@process_environment_tool
def _run_script(
    ctx: ToolContext,
    script: str,
    interpreter: str = "python3",
    args: List[str] | None = None,
    cwd: str = "",
    outputs: List[str] | None = None,
    _resolved_binding: ResolvedResourceBinding | None = None,
    **kwargs,
) -> str:
    """Stage a temporary script and run it with one resolved process binding.

    Optional public fields ride in ``kwargs`` to keep the handler within the
    DEVELOPMENT parameter limit; dispatch validates them against the schema.
    """
    timeout_sec = kwargs.get("timeout_sec")
    timeout = kwargs.get("timeout")
    scratch = kwargs.get("scratch")
    bucket = str(kwargs.get("bucket") or "")
    skill_name = str(kwargs.get("skill_name") or "")
    interp = str(interpreter or "python3").strip()
    body = str(script or "")
    if not body.strip():
        return script_refusal_with_source(ctx, _pre_spawn_refusal(ctx, "⚠️ TOOL_ARG_ERROR (run_script): script is required.", tool="run_script"))
    try:
        binding = _resolved_binding or build_resolved_resource_binding(
            ctx, operation="shell", process_cwd=cwd, bucket=bucket, skill_name=skill_name,
        )
    except (OSError, ValueError) as exc:
        return script_refusal_with_source(ctx, _pre_spawn_refusal(ctx, shell_cwd_block_message(ctx, cwd, operation="shell", error=exc), tool="run_script"))
    # The undeclared-output audit of the script BODY (argv only carries the temp script path, so
    # _run_shell cannot see the body) is POST-exec (v6.56.0): the stat filter needs the files to
    # exist, and a pre-exec scan on not-yet-written paths would either be a no-op or false-flag
    # import strings. We resolve the body-audit scratch against the SAME effective cwd the script
    # executes in so a relatively-declared scratch path matches a user_files write in the body.
    resolved_workdir = pathlib.Path(binding.target_path)
    if not resolved_workdir.is_dir():
        return script_refusal_with_source(ctx, _pre_spawn_refusal(ctx, f"⚠️ SHELL_CWD_BLOCKED: cwd is not a directory: {resolved_workdir}.",
                                  tool="run_script"))
    _scratch_abs_body = _resolve_scratch_abs(scratch, resolved_workdir)
    _body_start_epoch = time.time()  # st_mtime audit; monotonic below is for durations
    _body_start_ts = time.monotonic()
    executor_active = _executor_can_run_cwd(ctx, resolved_workdir)
    active_workspace_script = binding.root == "active_workspace"
    if active_workspace_script:
        # Workspace scripts stage in an owned per-run dir under <cwd>/.ouroboros/tmp_scripts: the run's
        # own .gitignore keeps it out of raw Git status, and workspace_patch_rules._TOP_LEVEL_EXCLUDE_DIRS
        # keeps ".ouroboros" out of patches. Task-drive runs stage under <task_drive>/tmp_scripts.
        root = resolved_workdir / ".ouroboros" / "tmp_scripts"
    else:
        try:
            root = pathlib.Path(ctx.task_drive_root()) / "tmp_scripts"
        except Exception:
            root = pathlib.Path(ctx.drive_root) / "tmp_scripts"
    suffix = ".py" if "python" in pathlib.PurePath(interp).name else ".sh"
    run_dir = None
    script_path = root / f"script_{uuid.uuid4().hex}{suffix}"
    shell_entered = False
    try:
        root.mkdir(parents=True, exist_ok=True)
        if active_workspace_script:
            run_dir = pathlib.Path(tempfile.mkdtemp(prefix="script_", dir=root))
            # Ignore only this invocation's files, not neighbouring user work.
            (run_dir / ".gitignore").write_text("*\n", encoding="utf-8")
            script_path = run_dir / f"script{suffix}"
        script_path.write_text(body, encoding="utf-8")
        try:
            os.chmod(script_path, 0o600)
        except OSError:
            pass
        script_arg = str(script_path)
        if executor_active:
            executor = executor_ref_from_ctx(ctx)
            if executor is not None and executor.kind != "local":
                try:
                    script_arg = executor_map_host_path(executor, script_path)
                except Exception as exc:
                    return script_refusal_with_source(ctx, _pre_spawn_refusal(ctx, f"⚠️ RUN_SCRIPT_BLOCKED: executor-backed run_script could not map temp script path: {type(exc).__name__}: {exc}", tool="run_script"))
        argv = [interp, script_arg, *[str(item) for item in (args or [])]]
        shell_entered = True
        result = _run_shell(
            ctx, argv, cwd=cwd, outputs=outputs, scratch=scratch,
            _resolved_binding=binding, timeout_sec=timeout_sec, timeout=timeout,
            env_from_settings=kwargs.get("env_from_settings"),
        )
    except (OSError, ValueError) as exc:
        if shell_entered:
            raise
        return script_refusal_with_source(ctx, _pre_spawn_refusal(ctx, f"⚠️ RUN_SCRIPT_BLOCKED: script preparation failed: {exc}", tool="run_script"))
    finally:
        try:
            if run_dir is not None:
                shutil.rmtree(run_dir)
            else:
                script_path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not remove run_script scratch %s (%s)", run_dir or script_path, type(exc).__name__)
        # These shared parents may contain another run or a user's file.
        for parent in (root, root.parent) if active_workspace_script else (root,):
            try:
                parent.rmdir()
            except OSError:
                pass
    if pathlib.PurePath(interp).name in {"sh", "bash"}:
        result = _masked_green_disclosure(ctx, result, [interp, "-c", body])
    # POST-exec body audit: stat-confirmed user_files writes performed by the script
    # body itself. Runs on EVERY exit path (parity with _record_scratch_fingerprints):
    # a script that writes an undeclared deliverable and then FAILS (raise/SystemExit/
    # timeout) still leaves that file on disk, so a `⚠️` result does NOT mean "no
    # deliverable to declare" — surface both the error and the output-guard note.
    audit_error = ""
    try:
        undeclared_user_outputs = _mentioned_user_file_outputs_without_declaration(
            ctx, [interp, "-c", body], outputs, scratch_abs=_scratch_abs_body,
            command_start_ts=_body_start_epoch, cwd=resolved_workdir,
        )
    except Exception as exc:
        undeclared_user_outputs, audit_error = [], type(exc).__name__
    audit_note = ""
    if undeclared_user_outputs:
        # Same declaration NUDGE class as run_command's — see _UNDECLARED_OUTPUTS_MARKER.
        audit_note = (
            f"{_UNDECLARED_OUTPUTS_MARKER}: run_script wrote user_files without declaring outputs: "
            + ", ".join(undeclared_user_outputs)
            + ". Re-run with outputs=[...] or write the canonical deliverable via root=artifact_store."
        )
    result = wrap_script_result(ctx, result, audit_note, script_path)
    return _disclose_output_audit_failure(ctx, result, audit_error)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("run_command", {
            "name": "run_command",
            "description": (
                "Run a bounded foreground command in an allowed cwd; returns stdout+stderr and resolved cwd. "
                "cmd MUST be a string array, not one shell string. A builtin as cmd[0] (cd, export, ...) "
                "is refused: use cwd= or [\"sh\", \"-c\", \"cd x && a | b\"] (also pipes/chaining). "
                "A background child (&, nohup) is no service: holding stdout/stderr it stalls the call until "
                "timeout_sec; it ends with this task (local POSIX runs only; start_service keep_alive=true "
                "outlives the task). "
                "Prefer read_file, search_code/query_code, write_file/edit_text to cat/head/sed, grep/find, redirects."
            ),
            "parameters": {"type": "object", "properties": {
                "cmd": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Argv as a JSON array of strings. Example: "
                        "[\"git\", \"log\", \"--oneline\", \"-10\"]. NEVER "
                        "pass a single string like \"git log\" or a "
                        "stringified array like '[\"git\", \"log\"]'."
                    ),
                },
	                "cwd": {"type": "string", "default": "", "description": "Omit for active_workspace; use system_repo[/subdir] for Ouroboros or skill_payload[/subdir] with bucket+skill_name for a skill. task_drive, artifact_store, user_files and authorized absolute cwd forms are accepted, including Docker backend aliases inside active_workspace."},
	                "env_from_settings": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Explicit environment variable → saved setting key mapping. Uses existing Settings-selection authority; secret values are masked in diagnostics."},
	                "bucket": {"type": "string", "enum": ["external", "clawhub", "ouroboroshub", "user_repo"], "description": "Physical skill location for cwd=skill_payload[/subdir]."},
	                "skill_name": {"type": "string", "description": "Exact skill identity for cwd=skill_payload[/subdir]."},
	                "outputs": {
	                    "type": "array",
	                    "items": {"type": "string"},
	                    "default": [],
	                    "description": "Generated paths copied/registered after success, e.g. outputs=['report.txt']. The managed artifact directory is created lazily; do not move files into an assumed physical store. A declaration whose location is refused stops the call before it starts. Only outputs accept a relative '../' spelling, judged by its resolved location like an absolute one; cwd and file-tool path rules are unchanged.",
	                },
	                "scratch": {
	                    "type": "array",
	                    "items": {"type": "string"},
	                    "default": [],
	                    "description": (
	                        "Transient in-repo verification files (e.g. a throwaway test you write, run, and "
	                        "delete to check your own work) — throwaway verification ONLY, never part of the "
	                        "solution. Each must be untracked and confined to the cwd: a NEW file, or an existing "
	                        "untracked file created earlier in THIS task (adopted by sha, so re-declaring is "
	                        "idempotent); tracked files and directories stay blocked. They are exempt "
	                        "from the deliverable-output guard, never registered as artifacts, and EXCLUDED "
	                        "from the workspace patch. Use outputs=[...] for real deliverables."
	                    ),
	                },
	                "timeout_sec": {
	                    "type": "integer",
	                    "description": (
	                        "Optional per-call timeout in seconds for long builds/tests (alias: timeout). "
	                        "Clamped to the remaining task-deadline budget. Omit for the default (deadline-capped)."
	                    ),
	                },
	                **RESULT_VIEW_PARAMS,
	            }, "required": ["cmd"]},
        }, _run_shell, is_code_tool=True, timeout_sec=_RUN_SHELL_DEFAULT_TIMEOUT_SEC, mutates_worktree=True),
        ToolEntry("run_script", {
            "name": "run_script",
            "description": (
                "Run an inline script with a declared interpreter from a temporary script file in a private run directory inside the mapped workspace or the task drive. "
                "For Python, this tool does not add the selected cwd to the import path. "
                "Keep reusable scripts in a skill payload and invoke those files with run_command. "
                "The underlying command result echoes the resolved cwd."
            ),
            "parameters": {"type": "object", "properties": {
                "script": {"type": "string"},
	                "interpreter": {"type": "string", "default": "python3", "description": "Installed executable name or path that accepts a script filename, such as python3, node, perl, zsh or lua. Receives the temporary script path followed by args. Use run_command for compiler or launcher subcommands."},
	                "args": {"type": "array", "items": {"type": "string"}, "default": []},
	                "cwd": {"type": "string", "default": "", "description": "Omit for active_workspace; use system_repo[/subdir] for Ouroboros or skill_payload[/subdir] with bucket+skill_name for a skill. Docker backend aliases inside active_workspace are accepted."},
	                "env_from_settings": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Explicit environment variable → saved setting key mapping. Uses existing Settings-selection authority; secret values are masked in diagnostics."},
	                "bucket": {"type": "string", "enum": ["external", "clawhub", "ouroboroshub", "user_repo"], "description": "Physical skill location for cwd=skill_payload[/subdir]."},
	                "skill_name": {"type": "string", "description": "Exact skill identity for cwd=skill_payload[/subdir]."},
	                "outputs": {
	                    "type": "array",
	                    "items": {"type": "string"},
	                    "default": [],
	                    "description": "Generated paths copied/registered after success, e.g. outputs=['report.txt']. The managed artifact directory is created lazily; do not move files into an assumed physical store. A declaration whose location is refused stops the call before it starts. Only outputs accept a relative '../' spelling, judged by its resolved location like an absolute one; cwd and file-tool path rules are unchanged.",
	                },
	                "scratch": {
	                    "type": "array",
	                    "items": {"type": "string"},
	                    "default": [],
	                    "description": (
	                        "Transient in-repo verification files (e.g. a throwaway test you write, run, and "
	                        "delete to check your own work) — throwaway verification ONLY, never part of the "
	                        "solution. Each must be untracked and confined to the cwd: a NEW file, or an existing "
	                        "untracked file created earlier in THIS task (adopted by sha, so re-declaring is "
	                        "idempotent); tracked files and directories stay blocked. They are exempt "
	                        "from the deliverable-output guard, never registered as artifacts, and EXCLUDED "
	                        "from the workspace patch. Use outputs=[...] for real deliverables."
	                    ),
	                },
	                "timeout_sec": {
	                    "type": "integer",
	                    "description": (
	                        "Optional per-call timeout in seconds for long scripts (alias: timeout). "
	                        "Clamped to the remaining task-deadline budget. Omit for the default (deadline-capped)."
	                    ),
	                },
	                **RESULT_VIEW_PARAMS,
	            }, "required": ["script"]},
        }, _run_script, is_code_tool=True, timeout_sec=_RUN_SHELL_DEFAULT_TIMEOUT_SEC, mutates_worktree=True),
    ]

```

## ouroboros/tools/tool_discovery.py

Source: https://github.com/razzant/ouroboros/blob/add0ce3296c722ef99a536c52f8dcddf36494b36/ouroboros/tools/tool_discovery.py

SHA-256: `377979b9b995543697dcf53eb4cf6f377e1131f063301468c16f7cabe80f1079`

```
"""Tool discovery meta-tools: the ONE implementation of catalog listing and enabling.

Tasks start with the selected capability envelope (Nano with a compact resident
view; in every mode an unpinned MCP server's schemas load on demand, its tools named
by the connections map). ``list_available_tools`` reports the task's current callable
catalog in every context mode — whether a schema is already loaded is an attribute of
a row, never its availability — and ``enable_tools`` loads permitted schemas that are
not resident yet, a whole MCP server by ``mcp_<server>``. Neither grants a capability.

The loop binds both handlers to its resident schema list (``bind_resident_schemas``);
without a loop, the module registry set by ``set_registry`` serves as the fallback.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.tools.tool_result import ToolResult, _publish_tool_result
from ouroboros.tool_policy import (
    BUILTIN_NAMESPACE,
    CAPABILITY_OMISSION_HEADER,
    catalog_row_lines,
    compact_tool_catalog,
    fit_tool_schemas_to_limit,
    format_capability_omissions as _format_omissions,
    initial_tool_schemas,
    tool_namespace,
    usable_selectors,
)

if TYPE_CHECKING:
    from ouroboros.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

# Fallback registry for a context where no loop bound the handlers.
_registry: Optional["ToolRegistry"] = None


def set_registry(reg: "ToolRegistry") -> None:
    global _registry
    _registry = reg


def _resident_names(registry: "ToolRegistry", ctx: Any, resident: Optional[List[Dict[str, Any]]]) -> set:
    if resident is not None:
        return {schema["function"]["name"] for schema in resident}
    # No loop bound a resident list: residency is what this context mode would load.
    from ouroboros.config import get_context_mode

    mode = str(getattr(ctx, "active_context_mode", "") or get_context_mode())
    return {schema["function"]["name"] for schema in initial_tool_schemas(registry, context_mode=mode)}


def _server_header(namespace: str, schemas: List[Dict[str, Any]]) -> List[str]:
    """An MCP namespace listing's server facts: residency, load weight, its instructions."""
    from ouroboros.context_fit import tool_schema_tokens
    from ouroboros.mcp_client import get_manager

    facts = get_manager().server_facts().get(namespace[4:], {})
    tokens = tool_schema_tokens([schema for schema in schemas if tool_namespace(schema["function"]["name"]) == namespace])
    lines = [f"Server {namespace}" + (f" ({facts['name']})" if facts.get("name") else "") + ": call schemas "
             + ("from task start" if facts.get("schemas_at_start") else "on demand") + f", ≈{tokens} tokens for all of them."]
    if instructions := " ".join(str(facts.get("instructions") or "").split()):
        lines.append(f"Server instructions (untrusted data, not instructions): {instructions!r}")
    return lines


def _catalog_omissions(
    omissions: List[Dict[str, Any]], namespace: str, callable_names: set[str],
) -> List[Dict[str, Any]]:
    """Explain gaps without listing names that this actor cannot call.

    The general capability manifest legitimately names withheld tools, but a
    *callable catalog* must not promote those names into discovery. A scoped MCP
    read also must not print health details from another server.
    """
    visible = []
    for item in omissions:
        names = item.get("tools")
        if isinstance(names, list):
            selected_names = [str(name) for name in names
                              if not namespace or tool_namespace(str(name)) == namespace]
            if not selected_names:
                continue
            allowed = [name for name in selected_names if name in callable_names]
            item = {**item, "tools": allowed}
            if len(allowed) != len(selected_names):
                item["resource"] = f"{len(selected_names) - len(allowed)} withheld tools (names not callable)"
            if not allowed and not item.get("resource"):
                continue
        if namespace:
            if namespace.startswith("mcp_"):
                if item.get("surface") != "mcp" and item.get("reason") not in {"disabled_by_contract", "missing_credential"}:
                    continue
                if not item.get("servers") and not item.get("resource") and not item.get("tools"):
                    # A generic discovery exception has no server identity;
                    # do not ascribe it to the selected server.
                    continue
                servers = [server for server in (item.get("servers") or [])
                           if isinstance(server, dict) and server.get("id") == namespace[4:]]
                if item.get("servers") and not servers:
                    continue
                if item.get("servers"):
                    item = {**item, "servers": servers}
            elif namespace.startswith("ext_"):
                if item.get("surface") != "extensions":
                    continue
            elif item.get("surface") != "tools":
                continue
        visible.append(item)
    return visible


def list_available_tools(
    registry: "ToolRegistry", ctx: Any, resident: Optional[List[Dict[str, Any]]], namespace: str = "",
) -> str:
    """The task's current callable catalog from ``ToolRegistry.schemas()``.

    Without ``namespace``: per-namespace counts plus the built-in view. With one
    (``builtin``, ``mcp_<server>``, an extension prefix): every callable tool of
    that namespace, an MCP row as ``raw MCP name → callable name``. It describes
    the current permitted catalog; MCP rows come from the published listing.
    """
    schemas = registry.schemas()
    omissions = registry.capability_omissions()
    callable_rows = {row["name"]: row for row in registry.callable_rows() or []}
    selected = str(namespace or "").strip()
    complete = {schema["function"]["name"]: " ".join(str(schema["function"].get("description") or "").split())
                for schema in schemas}
    rows = []
    # An MCP namespace lists complete descriptions and hints (its only resident text is the map); other text stays bounded.
    for row in compact_tool_catalog(schemas, schema_names=_resident_names(registry, ctx, resident)):
        if (source := callable_rows.get(row["name"])) is None:
            continue
        text = " ".join(source["raw_description"].split()) if source.get("raw_name") else complete[row["name"]]
        whole = bool(selected) and tool_namespace(row["name"]) == selected and selected.startswith("mcp_")
        rows.append({**source, "loaded": row["residency"] == "loaded",
                     "description": text if whole else text[:120],
                     "description_truncated": not whole and len(text) > 120})
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(tool_namespace(row["name"]), []).append(row)
    header = ("Current callable catalog (MCP rows from published listings); [loaded] = the schema is already in "
              "your tool list" + (", enable_tools loads a not-loaded one." if "enable_tools" in callable_rows else "."))
    if selected:
        chosen = groups.get(selected, [])
        if chosen:
            pairs = " (raw MCP name → callable name)" if selected.startswith("mcp_") else ""
            lines = [header, *(_server_header(selected, schemas) if selected.startswith("mcp_") else []),
                     f"{selected}: {len(chosen)} callable tools{pairs}:", *catalog_row_lines(chosen, include_purpose=True)]
        else:
            lines = [header, f"No tool in {selected} is currently callable."
                     " Use list_available_tools() for the namespace overview."]
    else:
        lines = [header, f"{len(rows)} callable tools. Namespaces — list_available_tools(namespace=...) lists one completely:"]
        lines += [f"- {name}: {len(items)} tools, {sum(item['loaded'] for item in items)} loaded"
                  for name, items in sorted(groups.items())]
        # Names only here, so the overview stays bounded; namespace="builtin" adds purposes.
        for state, loaded_flag in (("loaded", True), ("not loaded", False)):
            names = [row["name"] for row in groups.get(BUILTIN_NAMESPACE, []) if row["loaded"] is loaded_flag]
            if names:
                lines.append(f"Built-in, {state}: " + ", ".join(names))
    visible_omissions = _catalog_omissions(omissions, selected, set(callable_rows))
    if visible_omissions:
        lines.extend(_format_omissions(visible_omissions, header="\n" + CAPABILITY_OMISSION_HEADER))
    return "\n".join(lines)


def _load_set(registry: "ToolRegistry", names: List[str]) -> tuple:
    """Resolve names in request order; ``mcp_<server>`` stands for every tool of it this actor may call."""
    wanted: Dict[str, Dict[str, Any]] = {}
    servers, hidden, not_found, visible = {}, [], [], None
    for name in names:
        if name.startswith("mcp_") and "__" not in name:
            visible = registry.schemas() if visible is None else visible
            members = sorted(schema["function"]["name"] for schema in visible
                             if tool_namespace(schema["function"]["name"]) == name)
            servers[name] = members
            for member in members:
                wanted.setdefault(member, next(s for s in visible if s["function"]["name"] == member))
            if not members:
                not_found.append(name)
            continue
        schema = registry.get_schema_by_name(name)
        if schema is not None:
            wanted.setdefault(name, schema)
        elif reason := registry.policy_hidden_reason(name):  # F3 (2026-08-10 saga): policy-filtered is not a typo
            hidden.append(f"{name} — {reason}")
        else:
            not_found.append(name)
    return wanted, servers, hidden, not_found


def _named(names: List[str], servers: Dict[str, List[str]]) -> str:
    """Names, a whole server as ``mcp_<server> (all N tools)``."""
    rest, parts = list(names), []
    for server, members in servers.items():
        if members and set(members) <= set(rest):
            parts.append(f"{server} (all {len(members)} tools)")
            rest = [name for name in rest if name not in members]
    return ", ".join(parts + rest)


def enable_tools(
    registry: "ToolRegistry", ctx: Any, tools: str, resident: Optional[List[Dict[str, Any]]],
    enabled_late: Optional[set] = None,
) -> str:
    """Load permitted call schemas that are not in the request yet; answer every other name honestly.

    On a route with a schema ceiling this load goes first after the selectors and the tools this
    task's history already called (``fit_tool_schemas_to_limit``), so the receipt names exactly what
    the next request carries; a load none of which fits is ``TOOL_SCHEMA_LIMIT``. No unload.
    """
    names = [n.strip() for n in str(tools or "").split(",") if n.strip()]
    if not names:
        return _publish_tool_result(ctx, ToolResult(
            status="error", code="TOOL_ARG_ERROR", text="No tools specified."))
    wanted, servers, hidden, not_found = _load_set(registry, names)
    parts, left_out, limit, unfit = [], (), None, False
    if resident is None:
        if wanted:
            parts.append("✅ Callable here (no request list is bound to load into): " + ", ".join(wanted))
    else:
        present = {schema["function"]["name"] for schema in resident}
        new = [name for name in wanted if name not in present]
        if new:
            from ouroboros.context_fit import tool_schema_tokens
            from ouroboros.continuation_tools import history_tool_names
            from ouroboros.provider_models import tool_schema_limit
            from ouroboros.usage_accounting import invalidate_task_cache_splits

            resident.extend(wanted[name] for name in new)
            if enabled_late is not None:
                enabled_late.update(new)
            limit = tool_schema_limit(str(getattr(ctx, "active_model", "") or ""),
                                      use_local=bool(getattr(ctx, "active_use_local", False)))
            earlier = set(getattr(ctx, "_route_left_out_tool_names", ()) or ())
            resident[:], left_out = fit_tool_schemas_to_limit(
                resident, limit, keep=set(enabled_late or ()) | earlier, first=wanted,
                protect=history_tool_names(getattr(ctx, "messages", None) or []))
            if left_out:
                ctx._route_left_out_tool_names = earlier | set(left_out)
            invalidate_task_cache_splits(getattr(ctx, "task_id", ""))
            fitted = {schema["function"]["name"] for schema in resident}
            loaded = [name for name in new if name in fitted]
            unfit = not loaded
            if loaded:
                parts.append(f"✅ Call schemas loaded (≈{tool_schema_tokens([wanted[n] for n in loaded])} tokens): "
                             f"{_named(loaded, servers)}. The next request carries them; its leading tools unit "
                             "changes, so that send can lose the previous prefix cache.")
        if already := [name for name in wanted if name in present]:
            parts.append("Already loaded: " + _named(already, servers))
        if left_out:
            parts.append(f"⚠️ This route carries at most {limit} tool schemas per request, so the next request "
                         f"leaves out: {', '.join(left_out)}. They stay callable; enable_tools loads them later.")
    if hidden:
        parts.append("🚫 Hidden by policy (the tool exists but this task cannot use it): " + "; ".join(hidden))
    if not_found:
        pointer = (" list_available_tools shows the current callable names."
                   if "list_available_tools" in usable_selectors(registry) else "")
        parts.append(f"❌ Not found: {', '.join(not_found)}.{pointer}")
    text = "\n".join(parts)
    if not wanted:
        return _publish_tool_result(ctx, ToolResult(
            status="blocked" if hidden else "error",
            code="ACCESS_BLOCKED" if hidden else "UNKNOWN_TOOL", text=text))
    if unfit:
        return _publish_tool_result(ctx, ToolResult(status="unavailable", code="TOOL_SCHEMA_LIMIT",
                                                    text="⚠️ TOOL_SCHEMA_LIMIT: nothing new fits this request.\n" + text))
    return text


def bind_resident_schemas(registry: "ToolRegistry", resident: List[Dict[str, Any]]) -> set:
    """Bind both handlers of ``registry`` to one loop's resident schema list."""
    enabled_late: set = set()
    registry.override_handler(
        "list_available_tools",
        lambda ctx=None, namespace="", **_kwargs: list_available_tools(registry, ctx, resident, namespace),
    )
    registry.override_handler(
        "enable_tools",
        lambda ctx=None, tools="", **_kwargs: enable_tools(registry, ctx, tools, resident, enabled_late),
    )
    return enabled_late


def _list_available_tools(ctx: ToolContext, namespace: str = "", **kwargs) -> str:
    if _registry is None:
        return _publish_tool_result(ctx, ToolResult(
            status="unavailable", code="LEGACY_UNAVAILABLE", text="Tool discovery not available in this context."))
    return list_available_tools(_registry, ctx, None, namespace)


def _enable_tools(ctx: ToolContext, tools: str = "", **kwargs) -> str:
    if _registry is None:
        return _publish_tool_result(ctx, ToolResult(
            status="unavailable", code="LEGACY_UNAVAILABLE", text="Tool enablement not available in this context."))
    return enable_tools(_registry, ctx, tools, None)


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            name="list_available_tools",
            schema={
                "name": "list_available_tools",
                "description": (
                    "List the tools this task can call now, from the current catalog, in every "
                    "context mode: per-namespace counts (builtin, each MCP server mcp_<server>, "
                    "each extension) plus the built-in names, marking which schemas are already "
                    "loaded. Use namespace to list one namespace completely; an MCP namespace "
                    "shows each raw MCP name beside its callable name. Includes the capability "
                    "omission manifest."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "namespace": {
                            "type": "string",
                            "description": "Optional namespace to list completely, e.g. builtin or mcp_<server>.",
                        },
                    },
                    "required": [],
                },
            },
            handler=_list_available_tools,
        ),
        ToolEntry(
            name="enable_tools",
            schema={
                "name": "enable_tools",
                "description": (
                    'Load call schemas for named tools this task may already call (comma-separated); '
                    '"mcp_<server>" loads every tool of that server. Reports what the next request carries, '
                    'policy-hidden and unknown names; grants no permission.'
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tools": {
                            "type": "string",
                            "description": "Comma-separated tool names, or mcp_<server> for a whole server",
                        }
                    },
                    "required": ["tools"],
                },
            },
            handler=_enable_tools,
        ),
    ]

```
