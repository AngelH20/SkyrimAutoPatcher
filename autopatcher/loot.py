"""Evaluación del masterlist de LOOT (avisos, parches faltantes, incompatibilidades,
requisitos y plugins sucios) sobre la instalación de MO2."""
from __future__ import annotations

import re
import struct
import time
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .mo2 import Setup
from .plugins import BASE_GAME_PLUGINS, PluginParseError, read_header

MASTERLIST_URLS = [
    "https://raw.githubusercontent.com/loot/skyrimse/v0.29/masterlist.yaml",
    "https://raw.githubusercontent.com/loot/skyrimse/master/masterlist.yaml",
]
REGEX_CHARS = set(":\\*?|")
PATCH_WORDS = re.compile(r"patch|parche|compatib|incompatib", re.IGNORECASE)


# ---------------------------------------------------------------------- descarga
def get_masterlist(cache_dir: Path, offline: bool = False, max_age_h: float = 24) -> Path | None:
    import requests

    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "masterlist.yaml"
    fresh = target.is_file() and (time.time() - target.stat().st_mtime) < max_age_h * 3600
    if offline or fresh:
        return target if target.is_file() else None
    for url in MASTERLIST_URLS:
        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": "SkyrimAutoPatcher"})
            if resp.ok and "plugins:" in resp.text:
                target.write_text(resp.text, encoding="utf-8")
                return target
        except requests.RequestException:
            continue
    return target if target.is_file() else None


def load_masterlist(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# --------------------------------------------------------------- versiones / PE
def _version_tokens(v: str) -> list:
    parts = re.split(r"[.\-_,+ ]+", v.strip().lower())
    out = []
    for p in parts:
        if not p:
            continue
        for chunk in re.findall(r"\d+|[a-z]+", p):
            out.append(int(chunk) if chunk.isdigit() else chunk)
    while out and out[-1] == 0:
        out.pop()
    return out


def compare_versions(a: str, b: str) -> int:
    ta, tb = _version_tokens(a), _version_tokens(b)
    for x, y in zip(ta, tb):
        if x == y:
            continue
        if isinstance(x, int) and isinstance(y, int):
            return -1 if x < y else 1
        return -1 if str(x) < str(y) else 1
    return (len(ta) > len(tb)) - (len(ta) < len(tb))


def pe_version(path: Path, product: bool = False) -> str | None:
    """Lee VS_FIXEDFILEINFO de un .exe/.dll de Windows."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    idx = data.find(b"\xbd\x04\xef\xfe")
    if idx < 0 or idx + 24 > len(data):
        return None
    ms, ls, pms, pls = struct.unpack_from("<IIII", data, idx + 8)
    hi, lo = (pms, pls) if product else (ms, ls)
    return f"{hi >> 16}.{hi & 0xFFFF}.{lo >> 16}.{lo & 0xFFFF}"


# ------------------------------------------------------------------- condiciones
_TOKEN_RE = re.compile(r'\s*(?:(?P<str>"[^"]*")|(?P<op>==|!=|<=|>=|<|>)|(?P<punct>[(),])|(?P<word>[A-Za-z0-9_.]+))')


class ConditionError(Exception):
    pass


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise ConditionError(f"No entiendo la condición: {text!r}")
        pos = m.end()
        kind = m.lastgroup
        val = m.group(kind)
        tokens.append((kind, val[1:-1] if kind == "str" else val))
    return tokens


class Evaluator:
    def __init__(self, setup: Setup):
        self.setup = setup
        self.active = setup.active_lower
        self.unknown_functions: set[str] = set()

    # ------------------------------------------------------------- resolución rutas
    def _split(self, path: str) -> tuple[str, str, bool]:
        """Devuelve (carpeta relativa, nombre/regex, sale_de_data)."""
        path = path.replace("\\\\", "\\")
        outside = False
        if path.startswith("../"):
            outside, path = True, path[3:]
        # si es una regex, solo el último componente lo es
        if "/" in path:
            folder, name = path.rsplit("/", 1)
        else:
            folder, name = "", path
        return folder, name, outside

    def _is_regex(self, name: str) -> bool:
        return bool(set(name) & REGEX_CHARS)

    def _listing(self, folder: str, outside: bool) -> dict[str, str]:
        if outside:
            gd = self.setup.game_dir
            if not gd:
                return {}
            target = gd / folder if folder else gd
            try:
                return {p.name.lower(): p.name for p in target.iterdir() if p.is_file()}
            except OSError:
                return {}
        return self.setup.data.listdir(folder)

    def _real_path(self, folder: str, name: str, outside: bool) -> Path | None:
        if outside:
            gd = self.setup.game_dir
            if not gd:
                return None
            p = gd / folder / name if folder else gd / name
            return p if p.is_file() else None
        hit = self.setup.data.get(f"{folder}/{name}" if folder else name)
        return hit[1] if hit else None

    def _matches(self, path: str) -> list[tuple[str, Path | None]]:
        folder, name, outside = self._split(path)
        if self._is_regex(name):
            try:
                rx = re.compile(name, re.IGNORECASE)
            except re.error:
                return []
            listing = self._listing(folder, outside)
            return [(orig, self._real_path(folder, orig, outside)) for low, orig in listing.items() if rx.fullmatch(orig)]
        real = self._real_path(folder, name, outside)
        if real is None and not outside and not folder:
            ghost = self._real_path(folder, name + ".ghost", outside)
            real = ghost
        return [(name, real)] if real else []

    # ------------------------------------------------------------------ funciones
    def f_file(self, path: str) -> bool:
        return bool(self._matches(path))

    def f_readable(self, path: str) -> bool:
        return bool(self._matches(path))

    def f_is_executable(self, path: str) -> bool:
        return any(n.lower().endswith((".exe", ".dll")) for n, _ in self._matches(path))

    def f_active(self, path: str) -> bool:
        if self._is_regex(path):
            try:
                rx = re.compile(path, re.IGNORECASE)
            except re.error:
                return False
            return any(rx.fullmatch(p) for p in self.setup.active)
        return path.lower() in self.active

    def f_many(self, path: str) -> bool:
        return len(self._matches(path)) > 1

    def f_many_active(self, path: str) -> bool:
        try:
            rx = re.compile(path, re.IGNORECASE)
        except re.error:
            return False
        return sum(1 for p in self.setup.active if rx.fullmatch(p)) > 1

    @lru_cache(maxsize=None)
    def _header(self, real: Path):
        try:
            return read_header(real)
        except (OSError, PluginParseError):
            return None

    def f_is_master(self, path: str) -> bool:
        for _, real in self._matches(path):
            hdr = self._header(real) if real else None
            if hdr and hdr.is_master:
                return True
        return False

    @lru_cache(maxsize=None)
    def _crc(self, real: Path) -> int | None:
        try:
            crc = 0
            with open(real, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    crc = zlib.crc32(chunk, crc)
            return crc & 0xFFFFFFFF
        except OSError:
            return None

    def f_checksum(self, path: str, crc: str) -> bool:
        try:
            want = int(crc, 16)
        except ValueError:
            return False
        return any(real and self._crc(real) == want for _, real in self._matches(path))

    def crc_of(self, plugin: str) -> int | None:
        hit = self.setup.data.get(plugin)
        return self._crc(hit[1]) if hit else None

    def _cmp(self, actual: str | None, op: str, wanted: str) -> bool:
        if actual is None:
            return op in {"!=", "<", "<="}
        c = compare_versions(actual, wanted)
        return {"==": c == 0, "!=": c != 0, "<": c < 0, ">": c > 0, "<=": c <= 0, ">=": c >= 0}[op]

    def _file_version(self, path: str, product: bool) -> str | None:
        for name, real in self._matches(path):
            if not real:
                continue
            if name.lower().endswith((".esp", ".esm", ".esl")):
                hdr = self._header(real)
                return hdr.version if hdr else None
            return pe_version(real, product=product)
        return None

    def f_version(self, path: str, op: str, ver: str) -> bool:
        return self._cmp(self._file_version(path, False), op, ver)

    def f_product_version(self, path: str, op: str, ver: str) -> bool:
        return self._cmp(self._file_version(path, True), op, ver)

    def f_description_contains(self, path: str, regex: str) -> bool:
        for _, real in self._matches(path):
            hdr = self._header(real) if real else None
            if hdr and re.search(regex, hdr.description, re.IGNORECASE):
                return True
        return False

    def f_file_size(self, path: str, size: str) -> bool:
        for _, real in self._matches(path):
            try:
                if real and real.stat().st_size == int(size):
                    return True
            except (OSError, ValueError):
                pass
        return False

    # ------------------------------------------------------------------- parser
    @lru_cache(maxsize=None)
    def evaluate(self, condition: str) -> bool:
        tokens = _tokenize(condition)
        pos = 0

        def peek():
            return tokens[pos] if pos < len(tokens) else (None, None)

        def take():
            nonlocal pos
            tok = peek()
            pos += 1
            return tok

        def expr():
            val = term()
            while peek() == ("word", "or"):
                take()
                rhs = term()
                val = val or rhs
            return val

        def term():
            val = factor()
            while peek() == ("word", "and"):
                take()
                rhs = factor()
                val = val and rhs
            return val

        def factor():
            kind, val = peek()
            if (kind, val) == ("word", "not"):
                take()
                return not factor()
            if (kind, val) == ("punct", "("):
                take()
                v = expr()
                if take() != ("punct", ")"):
                    raise ConditionError(condition)
                return v
            if kind == "word":
                take()
                if take() != ("punct", "("):
                    raise ConditionError(condition)
                args = []
                while peek() != ("punct", ")"):
                    k, v = take()
                    if k is None:
                        raise ConditionError(condition)
                    if k != "punct":
                        args.append(v)
                take()
                fn = getattr(self, f"f_{val}", None)
                if fn is None:
                    self.unknown_functions.add(val)
                    return False
                try:
                    return bool(fn(*args))
                except TypeError:
                    self.unknown_functions.add(val)
                    return False
            raise ConditionError(condition)

        result = expr()
        if pos != len(tokens):
            raise ConditionError(condition)
        return result

    def check(self, condition: str | None) -> bool:
        if not condition:
            return True
        try:
            return self.evaluate(condition)
        except ConditionError:
            return False


# --------------------------------------------------------------------- resultados
@dataclass
class LootMessage:
    plugin: str | None  # None = aviso global
    kind: str  # say | warn | error | inc | req | dirty
    text: str

    @property
    def patch_related(self) -> bool:
        return bool(PATCH_WORDS.search(self.text)) or self.kind == "inc"


def _content(msg: dict, lang: str = "es") -> str:
    content = msg.get("content", "")
    if isinstance(content, list):
        by_lang = {c.get("lang"): c.get("text", "") for c in content if isinstance(c, dict)}
        content = by_lang.get(lang) or by_lang.get("en") or next(iter(by_lang.values()), "")
    subs = msg.get("subs") or []
    try:
        return content.format(*subs) if subs else content
    except (IndexError, KeyError, ValueError):
        return content


def _name_matches(entry_name: str, plugins: list[str]) -> list[str]:
    if set(entry_name) & REGEX_CHARS:
        try:
            rx = re.compile(entry_name, re.IGNORECASE)
        except re.error:
            return []
        return [p for p in plugins if rx.fullmatch(p)]
    low = entry_name.lower()
    return [p for p in plugins if p.lower() == low]


def _file_ref(item) -> tuple[str, str, str | None]:
    if isinstance(item, str):
        return item, item, None
    name = item.get("name", "")
    return name, item.get("display") or name, item.get("condition")


def analyze(setup: Setup, masterlist: dict) -> tuple[list[LootMessage], set[str]]:
    ev = Evaluator(setup)
    messages: list[LootMessage] = []

    for msg in masterlist.get("globals") or []:
        if ev.check(msg.get("condition")):
            messages.append(LootMessage(None, msg.get("type", "say"), _content(msg)))

    active = setup.active
    for entry in masterlist.get("plugins") or []:
        name = entry.get("name")
        if not name:
            continue
        for plugin in _name_matches(name, active):
            for msg in entry.get("msg") or []:
                if ev.check(msg.get("condition")):
                    messages.append(LootMessage(plugin, msg.get("type", "say"), _content(msg)))
            for item in entry.get("inc") or []:
                fname, display, cond = _file_ref(item)
                if not ev.check(cond):
                    continue
                hit = ev.f_active(fname) if fname.lower().endswith((".esp", ".esm", ".esl")) else ev.f_file(fname)
                if hit:
                    messages.append(LootMessage(plugin, "inc", f"Incompatible con {display}."))
            for item in entry.get("req") or []:
                fname, display, cond = _file_ref(item)
                if not ev.check(cond):
                    continue
                if not ev.f_file(fname):
                    messages.append(LootMessage(plugin, "req", f"Requiere {display}, que no está instalado."))
            dirty = entry.get("dirty") or []
            if dirty:
                crc = ev.crc_of(plugin)
                for d in dirty:
                    if crc is not None and d.get("crc") == crc:
                        parts = []
                        if d.get("itm"):
                            parts.append(f"{d['itm']} ITM")
                        if d.get("udr"):
                            parts.append(f"{d['udr']} registros borrados (UDR)")
                        if d.get("nav"):
                            parts.append(f"{d['nav']} navmesh borrados (NO se arreglan limpiando; busca un parche)")
                        text = "Plugin sucio: " + ", ".join(parts or ["necesita limpieza"])
                        text += f". Límpialo con {d.get('util', 'SSEEdit')}."
                        if plugin.lower() in BASE_GAME_PLUGINS or plugin.lower().startswith("cc"):
                            text += " (Es un plugin oficial: limpiarlo es opcional.)"
                        messages.append(LootMessage(plugin, "dirty", text))
                        break
    return messages, ev.unknown_functions
