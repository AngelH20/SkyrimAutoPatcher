"""Lectura de la cabecera (registro TES4) de plugins .esp/.esm/.esl."""
from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

PLUGIN_EXTS = (".esp", ".esm", ".esl")

# Plugins oficiales: no cuentan como "otro mod" al decidir si algo es un parche.
BASE_GAME_PLUGINS = {
    "skyrim.esm",
    "update.esm",
    "dawnguard.esm",
    "hearthfires.esm",
    "dragonborn.esm",
}

FLAG_MASTER = 0x1
FLAG_LIGHT = 0x200


def is_plugin_name(name: str) -> bool:
    return name.lower().endswith(PLUGIN_EXTS)


@dataclass
class PluginHeader:
    masters: list[str] = field(default_factory=list)
    flags: int = 0
    description: str = ""
    is_master: bool = False
    is_light: bool = False

    @property
    def version(self) -> str | None:
        return version_from_description(self.description)


class PluginParseError(Exception):
    pass


def _zstring(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("cp1252", errors="replace")


def parse_header(stream: io.BufferedIOBase | bytes, name: str = "") -> PluginHeader:
    if isinstance(stream, (bytes, bytearray)):
        stream = io.BytesIO(stream)
    head = stream.read(24)
    if len(head) < 24 or head[:4] != b"TES4":
        raise PluginParseError(f"{name or 'plugin'}: no es un plugin de Skyrim SE válido")
    data_size, flags = struct.unpack_from("<II", head, 4)
    data = stream.read(data_size)
    if len(data) < data_size:
        raise PluginParseError(f"{name or 'plugin'}: cabecera truncada")

    hdr = PluginHeader(flags=flags)
    pos = 0
    big_size = None
    while pos + 6 <= len(data):
        sub_type = data[pos : pos + 4]
        (size,) = struct.unpack_from("<H", data, pos + 4)
        pos += 6
        if big_size is not None:
            size, big_size = big_size, None
        payload = data[pos : pos + size]
        pos += size
        if sub_type == b"XXXX":
            (big_size,) = struct.unpack_from("<I", payload, 0)
        elif sub_type == b"MAST":
            hdr.masters.append(_zstring(payload))
        elif sub_type == b"SNAM":
            hdr.description = _zstring(payload)

    lower = name.lower()
    hdr.is_master = bool(flags & FLAG_MASTER) or lower.endswith((".esm", ".esl"))
    hdr.is_light = bool(flags & FLAG_LIGHT) or lower.endswith(".esl")
    return hdr


def read_header(path: str | Path) -> PluginHeader:
    path = Path(path)
    with open(path, "rb") as fh:
        return parse_header(fh, path.name)


_VERSION_RE = re.compile(
    r"(?:\bversion\b\s*[:.\-]?\s*|\bv\s*)(\d+(?:[.,]\d+)*[a-z]?)", re.IGNORECASE
)


def version_from_description(desc: str) -> str | None:
    """Extrae la versión del texto de descripción, como hace LOOT."""
    if not desc:
        return None
    m = _VERSION_RE.search(desc)
    return m.group(1).replace(",", ".") if m else None
