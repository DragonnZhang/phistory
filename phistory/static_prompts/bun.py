from __future__ import annotations

import re
import struct
import subprocess
from pathlib import Path

BUN_TRAILER = b"\n---- Bun! ----\n"
SIZEOF_OFFSETS = 32
SIZEOF_STRING_POINTER = 8
SIZEOF_MODULE_OLD = 4 * SIZEOF_STRING_POINTER + 4
SIZEOF_MODULE_NEW = 6 * SIZEOF_STRING_POINTER + 4

_SECTION_RE = re.compile(
    r"^\s*\[\s*\d+\]\s+\.bun\s+\S+\s+\S+\s+(?P<offset>[0-9a-fA-F]+)\s*\n"
    r"\s+(?P<size>[0-9a-fA-F]+)\s+",
    re.MULTILINE,
)


def extract_bun_entrypoint_js(binary_path: Path) -> str | None:
    data = binary_path.read_bytes()
    blob = (
        _bun_blob_from_macho_section(data)
        or _bun_blob_from_elf_section(binary_path, data)
        or _bun_blob_from_legacy_overlay(data)
    )
    if blob is None:
        return None
    js = _entrypoint_module(blob)
    return js.decode("utf-8", errors="replace") if js else None


def _bun_blob_from_macho_section(data: bytes) -> bytes | None:
    if not data.startswith(b"\xcf\xfa\xed\xfe") or len(data) < 32:
        return None
    command_count = struct.unpack_from("<I", data, 16)[0]
    command_offset = 32
    for _ in range(command_count):
        if command_offset + 8 > len(data):
            return None
        command, command_size = struct.unpack_from("<II", data, command_offset)
        if command_size < 8 or command_offset + command_size > len(data):
            return None
        if command == 0x19 and command_size >= 72:
            section_count = struct.unpack_from("<I", data, command_offset + 64)[0]
            section_offset = command_offset + 72
            for index in range(section_count):
                offset = section_offset + index * 80
                if offset + 80 > command_offset + command_size:
                    return None
                section_name, segment_name, _, section_size, file_offset = struct.unpack_from(
                    "<16s16sQQI", data, offset
                )
                if section_name.rstrip(b"\0") != b"__bun" or segment_name.rstrip(b"\0") != b"__BUN":
                    continue
                section = data[file_offset : file_offset + section_size]
                return _bun_blob_from_section(section)
        command_offset += command_size
    return None


def _bun_blob_from_elf_section(binary_path: Path, data: bytes) -> bytes | None:
    try:
        output = subprocess.check_output(["readelf", "-S", str(binary_path)], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    match = _SECTION_RE.search(output)
    if not match:
        return None
    offset = int(match.group("offset"), 16)
    size = int(match.group("size"), 16)
    section = data[offset : offset + size]
    return _bun_blob_from_section(section)


def _bun_blob_from_section(section: bytes) -> bytes | None:
    if len(section) < 8:
        return None
    blob_size = struct.unpack_from("<Q", section, 0)[0]
    if 8 + blob_size > len(section):
        blob_size = struct.unpack_from("<I", section, 0)[0]
        header_size = 4
    else:
        header_size = 8
    blob = section[header_size : header_size + blob_size]
    return blob if blob.endswith(BUN_TRAILER) else None


def _bun_blob_from_legacy_overlay(data: bytes) -> bytes | None:
    trailer_start = len(data) - 8 - len(BUN_TRAILER)
    if trailer_start <= SIZEOF_OFFSETS or data[trailer_start : trailer_start + len(BUN_TRAILER)] != BUN_TRAILER:
        return None
    offsets_start = trailer_start - SIZEOF_OFFSETS
    offsets = data[offsets_start:trailer_start]
    byte_count = struct.unpack_from("<Q", offsets, 0)[0]
    if byte_count <= 0 or byte_count > len(data):
        return None
    tail_len = 8 + len(BUN_TRAILER) + SIZEOF_OFFSETS
    data_start = len(data) - tail_len - byte_count
    if data_start < 0:
        return None
    return data[data_start : len(data) - tail_len] + offsets + BUN_TRAILER


def _entrypoint_module(blob: bytes) -> bytes | None:
    offsets_start = len(blob) - len(BUN_TRAILER) - SIZEOF_OFFSETS
    if offsets_start < 0 or blob[-len(BUN_TRAILER) :] != BUN_TRAILER:
        return None
    offsets = blob[offsets_start : offsets_start + SIZEOF_OFFSETS]
    modules_offset, modules_length = struct.unpack_from("<II", offsets, 8)
    module_size = _module_struct_size(modules_length)
    if not module_size:
        return None
    for index in range(modules_length // module_size):
        base = modules_offset + index * module_size
        name = _read_pointer_text(blob, base)
        content_offset, content_length = _read_pointer(blob, base + 8)
        if _is_entrypoint_name(name):
            return blob[content_offset : content_offset + content_length]
    return None


def _module_struct_size(modules_length: int) -> int | None:
    fits_new = modules_length % SIZEOF_MODULE_NEW == 0
    fits_old = modules_length % SIZEOF_MODULE_OLD == 0
    if fits_new:
        return SIZEOF_MODULE_NEW
    if fits_old:
        return SIZEOF_MODULE_OLD
    return None


def _read_pointer(blob: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from("<II", blob, offset)


def _read_pointer_text(blob: bytes, offset: int) -> str:
    pointer_offset, length = _read_pointer(blob, offset)
    return blob[pointer_offset : pointer_offset + length].decode("utf-8", errors="replace")


def _is_entrypoint_name(name: str) -> bool:
    return (
        name.endswith("/claude")
        or name == "claude"
        or name.endswith("/claude.exe")
        or name == "claude.exe"
        or name.endswith("/src/entrypoints/cli.js")
        or name == "src/entrypoints/cli.js"
        or name == "/$bunfs/root/index.js"
    )
