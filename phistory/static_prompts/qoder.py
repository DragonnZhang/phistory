from __future__ import annotations

import struct
from pathlib import Path

QODER_CODER_TEMPLATE_MARKER = (
    b"You are {{.AppName}}, an interactive CLI tool that helps users with software engineering tasks."
)
QODER_ROLE_DEFINITION_PREFIX = b"{{if .RoleDefinition}}{{.RoleDefinition}}{{else}}"
QODER_CODER_TEMPLATE_INTRO_TAIL = b"Use the instructions below and the tools available to you to assist the user."
QODER_CODER_TEMPLATE_MAX_BYTES = 2_000_000


def extract_qoder_coder_prompt(binary_path: Path) -> str:
    data = binary_path.read_bytes()
    marker_offsets = _find_all(data, QODER_CODER_TEMPLATE_MARKER)
    if not marker_offsets:
        raise RuntimeError(f"Qoder coder prompt marker not found in native executable: {binary_path}")

    candidates: list[str] = []
    for marker_offset in marker_offsets:
        intro = data[marker_offset : marker_offset + 320]
        if QODER_CODER_TEMPLATE_INTRO_TAIL not in intro:
            continue
        template_offset = marker_offset
        prefix_offset = marker_offset - len(QODER_ROLE_DEFINITION_PREFIX)
        if prefix_offset >= 0 and data[prefix_offset:marker_offset] == QODER_ROLE_DEFINITION_PREFIX:
            template_offset = prefix_offset
        virtual_address = _file_offset_to_virtual_address(data, template_offset)
        pointer = struct.pack("<Q", virtual_address)
        header_candidate_found = False
        for header_offset in _find_all(data, pointer):
            if header_offset + 16 > len(data):
                continue
            length = struct.unpack_from("<Q", data, header_offset + 8)[0]
            if not len(QODER_CODER_TEMPLATE_MARKER) <= length <= QODER_CODER_TEMPLATE_MAX_BYTES:
                continue
            end = template_offset + length
            if end > len(data):
                continue
            content = _validated_template(data[template_offset:end])
            if content is not None and content not in candidates:
                candidates.append(content)
                header_candidate_found = True
        if header_candidate_found:
            continue
        terminator = data.find(b"\0", template_offset, template_offset + QODER_CODER_TEMPLATE_MAX_BYTES)
        if terminator >= 0:
            content = _validated_template(data[template_offset:terminator])
            if content is not None and content not in candidates:
                candidates.append(content)

    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one exact Qoder coder prompt string header in {binary_path}, found {len(candidates)}"
        )
    return candidates[0]


def _validated_template(raw: bytes) -> str | None:
    if not raw.startswith((QODER_ROLE_DEFINITION_PREFIX, QODER_CODER_TEMPLATE_MARKER)):
        return None
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    required = (
        QODER_CODER_TEMPLATE_INTRO_TAIL.decode(),
        "{{.EnvironmentInfo}}",
        "# Code References",
    )
    if not all(marker in content for marker in required) or not content.rstrip().endswith("{{end}}"):
        return None
    return content


def _file_offset_to_virtual_address(data: bytes, file_offset: int) -> int:
    if data.startswith(b"\x7fELF"):
        return _elf_file_offset_to_virtual_address(data, file_offset)
    if data.startswith(b"\xcf\xfa\xed\xfe"):
        return _macho_file_offset_to_virtual_address(data, file_offset)
    raise RuntimeError("Qoder native prompt extraction supports little-endian ELF64 and Mach-O 64 executables")


def _elf_file_offset_to_virtual_address(data: bytes, file_offset: int) -> int:
    if len(data) < 64 or data[4] != 2 or data[5] != 1:
        raise RuntimeError("unsupported ELF executable; expected little-endian ELF64")
    program_offset = struct.unpack_from("<Q", data, 32)[0]
    entry_size = struct.unpack_from("<H", data, 54)[0]
    entry_count = struct.unpack_from("<H", data, 56)[0]
    if entry_size < 56:
        raise RuntimeError("invalid ELF64 program-header size")
    for index in range(entry_count):
        offset = program_offset + index * entry_size
        if offset + 56 > len(data):
            raise RuntimeError("truncated ELF64 program-header table")
        segment_type, _, segment_file_offset, segment_address, _, file_size, _, _ = struct.unpack_from(
            "<IIQQQQQQ", data, offset
        )
        if segment_type == 1 and segment_file_offset <= file_offset < segment_file_offset + file_size:
            return segment_address + file_offset - segment_file_offset
    raise RuntimeError(f"ELF64 file offset {file_offset:#x} is not covered by a loadable segment")


def _macho_file_offset_to_virtual_address(data: bytes, file_offset: int) -> int:
    if len(data) < 32:
        raise RuntimeError("truncated Mach-O 64 header")
    command_count = struct.unpack_from("<I", data, 16)[0]
    command_offset = 32
    for _ in range(command_count):
        if command_offset + 8 > len(data):
            raise RuntimeError("truncated Mach-O load-command table")
        command, command_size = struct.unpack_from("<II", data, command_offset)
        if command_size < 8 or command_offset + command_size > len(data):
            raise RuntimeError("invalid Mach-O load command")
        if command == 0x19:
            if command_size < 72:
                raise RuntimeError("invalid Mach-O LC_SEGMENT_64 command")
            virtual_address, _, segment_file_offset, file_size = struct.unpack_from("<QQQQ", data, command_offset + 24)
            if segment_file_offset <= file_offset < segment_file_offset + file_size:
                return virtual_address + file_offset - segment_file_offset
        command_offset += command_size
    raise RuntimeError(f"Mach-O file offset {file_offset:#x} is not covered by an LC_SEGMENT_64 command")


def _find_all(data: bytes, needle: bytes) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            return offsets
        offsets.append(offset)
        start = offset + 1
