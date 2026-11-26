"""Análisis de instaladores FOMOD (fomod/ModuleConfig.xml)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .plugins import is_plugin_name


@dataclass
class FomodFile:
    source: str
    destination: str | None
    is_folder: bool


@dataclass
class Dependency:
    """Árbol de condiciones de un FOMOD (operador And/Or)."""

    operator: str = "And"
    files: list[tuple[str, str]] = field(default_factory=list)  # (plugin, Active|Inactive|Missing)
    flags: list[tuple[str, str]] = field(default_factory=list)  # (flag, valor)
    children: list["Dependency"] = field(default_factory=list)


@dataclass
class FomodOption:
    step: str
    group: str
    group_type: str  # SelectExactlyOne, SelectAtMostOne, SelectAny, SelectAll, SelectAtLeastOne
    name: str
    description: str
    files: list[FomodFile]
    flags: dict[str, str]
    default_type: str  # Required, Optional, Recommended, NotUsable, CouldBeUsable
    patterns: list[tuple[Dependency, str]] = field(default_factory=list)

    @property
    def exclusive(self) -> bool:
        return self.group_type in {"SelectExactlyOne", "SelectAtMostOne"}


@dataclass
class ConditionalInstall:
    dependency: Dependency
    files: list[FomodFile]


@dataclass
class Fomod:
    module_name: str
    root: str  # prefijo dentro del archivo donde está la carpeta fomod ('' si está en la raíz)
    required: list[FomodFile]
    options: list[FomodOption]
    conditionals: list[ConditionalInstall]


# ----------------------------------------------------------------------------- XML
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _children(el: ET.Element | None, name: str) -> list[ET.Element]:
    if el is None:
        return []
    return [c for c in el if _local(c.tag) == name.lower()]


def _child(el: ET.Element | None, name: str) -> ET.Element | None:
    found = _children(el, name)
    return found[0] if found else None


def _attr(el: ET.Element | None, name: str, default: str = "") -> str:
    if el is None:
        return default
    for k, v in el.attrib.items():
        if _local(k) == name.lower():
            return v
    return default


def _parse_xml(raw: bytes) -> ET.Element:
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        pass
    for enc in ("utf-16", "utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text.lstrip("﻿"))
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            continue
    raise ValueError("ModuleConfig.xml ilegible")


def _files(el: ET.Element | None) -> list[FomodFile]:
    result = []
    if el is None:
        return result
    for c in el:
        kind = _local(c.tag)
        if kind not in {"file", "folder"}:
            continue
        src = _attr(c, "source").replace("\\", "/").strip("/")
        dest = c.attrib.get("destination")
        if dest is not None:
            dest = dest.replace("\\", "/").strip("/")
        result.append(FomodFile(src, dest, kind == "folder"))
    return result


def _dependency(el: ET.Element | None) -> Dependency:
    dep = Dependency(operator=_attr(el, "operator", "And") or "And")
    if el is None:
        return dep
    for c in el:
        kind = _local(c.tag)
        if kind == "filedependency":
            dep.files.append((_attr(c, "file"), _attr(c, "state", "Active")))
        elif kind == "flagdependency":
            dep.flags.append((_attr(c, "flag"), _attr(c, "value")))
        elif kind == "dependencies":
            dep.children.append(_dependency(c))
    return dep


def parse_module_config(raw: bytes, root: str = "") -> Fomod:
    xml = _parse_xml(raw)
    module_name = (_child(xml, "moduleName").text or "").strip() if _child(xml, "moduleName") is not None else ""
    required = _files(_child(xml, "requiredInstallFiles"))
    options: list[FomodOption] = []
    for step in _children(_child(xml, "installSteps"), "installStep"):
        step_name = _attr(step, "name")
        for group in _children(_child(step, "optionalFileGroups"), "group"):
            gname, gtype = _attr(group, "name"), _attr(group, "type", "SelectAny")
            for plugin in _children(_child(group, "plugins"), "plugin"):
                desc_el = _child(plugin, "description")
                flags = {
                    _attr(f, "name"): (f.text or "").strip()
                    for f in _children(_child(plugin, "conditionFlags"), "flag")
                }
                td = _child(plugin, "typeDescriptor")
                default_type, patterns = "Optional", []
                if _child(td, "type") is not None:
                    default_type = _attr(_child(td, "type"), "name", "Optional")
                elif _child(td, "dependencyType") is not None:
                    dt = _child(td, "dependencyType")
                    default_type = _attr(_child(dt, "defaultType"), "name", "Optional")
                    for pat in _children(_child(dt, "patterns"), "pattern"):
                        patterns.append(
                            (_dependency(_child(pat, "dependencies")), _attr(_child(pat, "type"), "name", "Optional"))
                        )
                options.append(
                    FomodOption(
                        step=step_name,
                        group=gname,
                        group_type=gtype,
                        name=_attr(plugin, "name"),
                        description=((desc_el.text or "") if desc_el is not None else "").strip(),
                        files=_files(_child(plugin, "files")),
                        flags=flags,
                        default_type=default_type,
                        patterns=patterns,
                    )
                )
    conditionals = []
    for pat in _children(_child(_child(xml, "conditionalFileInstalls"), "patterns"), "pattern"):
        conditionals.append(
            ConditionalInstall(_dependency(_child(pat, "dependencies")), _files(_child(pat, "files")))
        )
    return Fomod(module_name, root, required, options, conditionals)


def find_module_config(names: list[str]) -> tuple[str, str] | None:
    """Devuelve (ruta interna de ModuleConfig.xml, prefijo raíz) si el archivo es un FOMOD."""
    best = None
    for n in names:
        low = n.lower()
        if low == "fomod/moduleconfig.xml" or low.endswith("/fomod/moduleconfig.xml"):
            root = n[: -len("fomod/moduleconfig.xml")]
            if best is None or len(root) < len(best[1]):
                best = (n, root)
    return best


# ----------------------------------------------------------------------- resolución
def plugin_targets(files: list[FomodFile], root: str, names: list[str]) -> list[tuple[str, str]]:
    """Para una lista de <file>/<folder>, devuelve [(ruta interna, nombre de plugin instalado)]
    solo para plugins que terminarían en la raíz de Data."""
    lower_map = {n.lower(): n for n in names}
    result = []
    for f in files:
        src = (root + f.source).strip("/")
        if not f.is_folder:
            real = lower_map.get(src.lower())
            if real is None or not is_plugin_name(real):
                continue
            dest = f.destination if f.destination is not None else f.source
            # si el destino es una carpeta (no termina en plugin), el archivo conserva su nombre
            if dest and not is_plugin_name(dest):
                dest = dest.rstrip("/") + "/" + real.rsplit("/", 1)[-1]
            dest = dest or real.rsplit("/", 1)[-1]
            if "/" in dest.strip("/"):
                continue  # el plugin no queda en la raíz: el juego no lo carga
            result.append((real, dest.strip("/")))
        else:
            prefix = src.lower() + "/" if src else ""
            dest_dir = (f.destination or "").strip("/")
            for low, real in lower_map.items():
                if not low.startswith(prefix):
                    continue
                rel = real[len(prefix) :]
                if "/" in rel or not is_plugin_name(rel):
                    continue
                if dest_dir:
                    continue  # va a una subcarpeta, no a la raíz de Data
                result.append((real, rel))
    return result


def evaluate(dep: Dependency, active: set[str], present: set[str], flags: dict[str, str] | None) -> bool | None:
    """Evalúa una condición. Devuelve None si depende de flags que no conocemos."""
    results: list[bool | None] = []
    for plugin, state in dep.files:
        low = plugin.lower()
        st = state.lower()
        if st == "active":
            results.append(low in active)
        elif st == "inactive":
            results.append(low in present and low not in active)
        elif st == "missing":
            results.append(low not in present)
        else:
            results.append(None)
    for flag, value in dep.flags:
        if flags is None or flag not in flags:
            results.append(None)
        else:
            results.append(flags[flag] == value)
    for child in dep.children:
        results.append(evaluate(child, active, present, flags))
    if not results:
        return True
    if dep.operator.lower() == "or":
        if any(r is True for r in results):
            return True
        return None if any(r is None for r in results) else False
    if any(r is False for r in results):
        return False
    return None if any(r is None for r in results) else True
