"""Análisis local: opciones FOMOD/parches dentro de archivos ya descargados,
masters faltantes y parches instalados pero desactivados."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .archives import Archive, ArchiveError
from .fomod import Fomod, FomodOption, evaluate, find_module_config, parse_module_config, plugin_targets
from .mo2 import Mod, Setup, find_archive
from .plugins import BASE_GAME_PLUGINS, PluginParseError, is_plugin_name, parse_header, read_header

PATCH_HINT = re.compile(r"patch|parche|compat|integration|addon|add-on", re.IGNORECASE)


@dataclass
class ReinstallHint:
    mod: str
    archive: str
    plugin: str  # plugin que se instalaría
    masters: list[str]
    for_mods: list[str]  # mods de los que depende el parche
    confidence: str  # alta | media
    step: str = ""
    group: str = ""
    option: str = ""
    exclusive_group: bool = False
    reason: str = ""
    nexus_url: str | None = None


@dataclass
class MasterProblem:
    plugin: str
    mod: str | None
    missing: list[str] = field(default_factory=list)
    late: list[str] = field(default_factory=list)  # masters que cargan DESPUÉS


@dataclass
class InactivePatch:
    plugin: str
    mod: str | None
    for_mods: list[str]


@dataclass
class LocalReport:
    reinstall: list[ReinstallHint] = field(default_factory=list)
    master_problems: list[MasterProblem] = field(default_factory=list)
    inactive_patches: list[InactivePatch] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)  # avisos de archivos que no se pudieron revisar
    no_archive: list[str] = field(default_factory=list)  # mods sin archivo descargado


# ------------------------------------------------------------------- caché disco
class HeaderCache:
    """Cachea masters de plugins dentro de archivos comprimidos (clave: archivo+tamaño+fecha)."""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, dict] = {}
        if path and path.is_file():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def key(self, archive: Path, inner: str) -> str:
        st = archive.stat()
        return f"{archive.name}|{st.st_size}|{int(st.st_mtime)}|{inner}"

    def get(self, archive: Path, inner: str):
        return self.data.get(self.key(archive, inner))

    def put(self, archive: Path, inner: str, masters: list[str]):
        self.data[self.key(archive, inner)] = {"masters": masters}

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data), encoding="utf-8")


# --------------------------------------------------------------------- utilidades
def plugin_owner_map(setup: Setup) -> dict[str, str]:
    """plugin en minúsculas -> nombre del mod que lo aporta."""
    return {k: setup.data.owner(k) or "?" for k in setup.present_plugins()}


def _masters_of_present(setup: Setup, plugin: str) -> list[str] | None:
    hit = setup.data.get(plugin)
    if not hit:
        return None
    try:
        return read_header(hit[1]).masters
    except (OSError, PluginParseError):
        return None


# ------------------------------------------------------------- masters faltantes
def check_masters(setup: Setup, owners: dict[str, str]) -> tuple[list[MasterProblem], list[InactivePatch]]:
    problems, inactive = [], []
    position = {p.lower(): i for i, p in enumerate(setup.active)}
    active = setup.active_lower
    for plugin in setup.active:
        masters = _masters_of_present(setup, plugin)
        if masters is None:
            continue
        missing = [m for m in masters if m.lower() not in active]
        late = [m for m in masters if m.lower() in position and position[m.lower()] > position[plugin.lower()]]
        if missing or late:
            problems.append(MasterProblem(plugin, owners.get(plugin.lower()), missing, late))

    for low, plugin in setup.present_plugins().items():
        if low in active:
            continue
        masters = _masters_of_present(setup, plugin)
        if not masters:
            continue
        own_mod = owners.get(low)
        foreign = [m for m in masters if m.lower() not in BASE_GAME_PLUGINS and owners.get(m.lower()) != own_mod]
        if foreign and all(m.lower() in active for m in masters):
            inactive.append(
                InactivePatch(plugin, own_mod, sorted({owners.get(m.lower(), m) for m in foreign}))
            )
    return problems, inactive


# --------------------------------------------------------- opciones sin instalar
@dataclass
class _Candidate:
    inner: str
    plugin: str
    option: FomodOption | None = None
    conditional: bool | None = None  # resultado de conditionalFileInstalls si aplica


def _candidates(arc_names: list[str], fomod: Fomod | None) -> list[_Candidate]:
    result: list[_Candidate] = []
    if fomod is None:
        for n in arc_names:
            base = n.rsplit("/", 1)[-1]
            if is_plugin_name(base):
                result.append(_Candidate(n, base))
        return result
    for opt in fomod.options:
        for inner, plugin in plugin_targets(opt.files, fomod.root, arc_names):
            result.append(_Candidate(inner, plugin, option=opt))
    return result


def _installed_options(fomod: Fomod, arc_names: list[str], mod_plugins: set[str]) -> list[FomodOption]:
    out = []
    for opt in fomod.options:
        targets = plugin_targets(opt.files, fomod.root, arc_names)
        if targets and all(p.lower() in mod_plugins for _, p in targets):
            out.append(opt)
    return out


def analyze_mod_archive(
    setup: Setup,
    mod: Mod,
    archive: Archive,
    owners: dict[str, str],
    cache: HeaderCache,
) -> list[ReinstallHint]:
    names = archive.names()
    present = set(setup.present_plugins())
    active = setup.active_lower
    mod_plugins = {p.lower() for p in mod.root_plugins()}

    fomod = None
    mc = find_module_config(names)
    if mc:
        try:
            fomod = parse_module_config(archive.read(mc[0]), mc[1])
        except (ArchiveError, ValueError):
            fomod = None

    archive_plugins = {n.rsplit("/", 1)[-1].lower() for n in names if is_plugin_name(n)}
    installed_opts = _installed_options(fomod, names, mod_plugins) if fomod else []
    known_flags: dict[str, str] | None = None
    if fomod:
        known_flags = {}
        for opt in installed_opts:
            known_flags.update(opt.flags)

    candidates = _candidates(names, fomod)
    if fomod:
        for cond in fomod.conditionals:
            res = evaluate(cond.dependency, active, present, known_flags)
            for inner, plugin in plugin_targets(cond.files, fomod.root, names):
                candidates.append(_Candidate(inner, plugin, conditional=res))

    hints: dict[str, ReinstallHint] = {}
    for cand in candidates:
        low = cand.plugin.lower()
        if low in present or low in hints:
            continue  # ya está instalado (en este u otro mod)
        if cand.conditional is False:
            continue
        opt = cand.option
        if opt and opt.exclusive:
            siblings = [o for o in installed_opts if o.step == opt.step and o.group == opt.group]
            if siblings:
                continue  # ya elegiste otra opción de ese grupo
        if opt and opt.default_type == "NotUsable" and not opt.patterns:
            continue

        cached = cache.get(archive.path, cand.inner)
        if cached is None:
            try:
                masters = parse_header(archive.read(cand.inner), cand.plugin).masters
            except (ArchiveError, PluginParseError):
                continue
            cache.put(archive.path, cand.inner, masters)
        else:
            masters = cached["masters"]

        if not masters or any(m.lower() not in active for m in masters):
            continue  # le falta algún mod: no aplica a tu lista
        foreign = [
            m
            for m in masters
            if m.lower() not in BASE_GAME_PLUGINS
            and m.lower() not in archive_plugins
            and owners.get(m.lower()) != mod.name
        ]
        if not foreign:
            continue  # variante del mod principal, no un parche

        confidence, reasons = "media", []
        if cand.conditional:
            confidence = "alta"
            reasons.append("el propio instalador lo instala automáticamente cuando detecta esos mods")
        if opt:
            for dep, typ in opt.patterns:
                if typ in {"Recommended", "Required"} and evaluate(dep, active, present, known_flags):
                    confidence = "alta"
                    reasons.append("el instalador lo marca como recomendado para tu lista")
                    break
            if PATCH_HINT.search(f"{opt.name} {opt.description} {opt.group} {opt.step}"):
                confidence = "alta"
                reasons.append("la opción del instalador está descrita como parche")
        if PATCH_HINT.search(cand.plugin):
            confidence = "alta"
        reasons.append("todos sus masters están activos en tu lista")

        hints[low] = ReinstallHint(
            mod=mod.name,
            archive=archive.path.name,
            plugin=cand.plugin,
            masters=masters,
            for_mods=sorted({owners.get(m.lower(), m) for m in foreign}),
            confidence=confidence,
            step=opt.step if opt else "",
            group=opt.group if opt else "",
            option=opt.name if opt else ("(instalación automática del FOMOD)" if cand.conditional else ""),
            exclusive_group=bool(opt and opt.exclusive),
            reason="; ".join(reasons),
            nexus_url=mod.nexus_url,
        )
    return list(hints.values())


def analyze_local(
    setup: Setup,
    cache_dir: Path | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> LocalReport:
    report = LocalReport()
    owners = plugin_owner_map(setup)
    report.master_problems, report.inactive_patches = check_masters(setup, owners)

    cache = HeaderCache(cache_dir / "archive_headers.json" if cache_dir else None)
    mods = setup.enabled_mods
    for i, mod in enumerate(mods, 1):
        if progress:
            progress(i, len(mods), mod.name)
        arc_path = find_archive(setup, mod)
        if arc_path is None:
            if mod.modid:
                report.no_archive.append(mod.name)
            continue
        try:
            archive = Archive(arc_path)
            report.reinstall.extend(analyze_mod_archive(setup, mod, archive, owners, cache))
        except ArchiveError as exc:
            report.unreadable.append(f"{mod.name}: {exc}")
    cache.save()
    report.reinstall.sort(key=lambda h: (h.confidence != "alta", h.mod.lower(), h.plugin.lower()))
    return report


def to_dict(obj) -> dict:
    return asdict(obj)
