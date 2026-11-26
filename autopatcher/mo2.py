"""Lectura (solo lectura) de una instalación de Mod Organizer 2."""
from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .plugins import BASE_GAME_PLUGINS, PLUGIN_EXTS

BASE_OWNER = "<Juego base>"
OVERWRITE_OWNER = "<Overwrite>"


# --------------------------------------------------------------------------- ini
def _qt_value(value: str) -> str:
    """Convierte un valor de ini de Qt (``@ByteArray(...)``, comillas, ``\\\\``) a texto."""
    value = value.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        value = value[1:-1]
    m = re.fullmatch(r"@ByteArray\((.*)\)", value, re.DOTALL)
    if m:
        value = m.group(1)
    return value.replace("\\\\", "\\")


def read_qt_ini(path: Path) -> dict[str, dict[str, str]]:
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # respetar mayúsculas
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        parser.read_string(text)
    except configparser.Error:
        # algunos meta.ini traen líneas raras; se ignoran las que no parsean
        cleaned = "\n".join(l for l in text.splitlines() if "=" in l or l.strip().startswith("["))
        parser.read_string(cleaned)
    return {s: {k: _qt_value(v) for k, v in parser.items(s)} for s in parser.sections()}


# --------------------------------------------------------------------------- modelo
@dataclass
class Mod:
    name: str
    path: Path
    enabled: bool
    priority: int  # mayor = gana los conflictos
    modid: int | None = None
    fileid: int | None = None
    version: str = ""
    installation_file: str = ""
    installed_file_ids: set[int] = field(default_factory=set)
    nexus_name: str = ""
    is_separator: bool = False

    @property
    def nexus_url(self) -> str | None:
        if self.modid:
            return f"https://www.nexusmods.com/skyrimspecialedition/mods/{self.modid}"
        return None

    def root_plugins(self) -> list[str]:
        try:
            return sorted(
                e.name for e in os.scandir(self.path) if e.is_file() and e.name.lower().endswith(PLUGIN_EXTS)
            )
        except OSError:
            return []


@dataclass
class Download:
    path: Path
    modid: int | None
    fileid: int | None
    mod_name: str
    name: str
    installed: bool


class VirtualData:
    """Vista combinada de la carpeta Data tal como la ve el juego dentro de MO2."""

    def __init__(self) -> None:
        # ruta relativa en minúsculas -> (dueño, ruta real)
        self.files: dict[str, tuple[str, Path]] = {}
        # carpeta relativa en minúsculas -> {nombre en minúsculas: nombre original}
        self.dirs: dict[str, dict[str, str]] = {}

    def add_tree(self, root: Path, owner: str) -> None:
        if not root.is_dir():
            return
        stack = [(root, "")]
        while stack:
            current, rel = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                low = entry.name.lower()
                if entry.is_dir():
                    if not rel and low in {"fomod", "meta.ini"}:
                        continue
                    stack.append((Path(entry.path), f"{rel}{low}/"))
                elif entry.is_file():
                    if not rel and low == "meta.ini":
                        continue
                    key = rel + low
                    self.files[key] = (owner, Path(entry.path))
                    self.dirs.setdefault(rel.rstrip("/"), {})[low] = entry.name

    def get(self, rel: str) -> tuple[str, Path] | None:
        return self.files.get(rel.replace("\\", "/").lower().lstrip("/"))

    def owner(self, rel: str) -> str | None:
        hit = self.get(rel)
        return hit[0] if hit else None

    def listdir(self, rel_dir: str) -> dict[str, str]:
        return self.dirs.get(rel_dir.replace("\\", "/").lower().strip("/"), {})


@dataclass
class Setup:
    mo2_dir: Path
    game_dir: Path | None
    profile: str
    mods: list[Mod]
    downloads: list[Download]
    downloads_dir: Path
    data: VirtualData
    load_order: list[str]  # todos los plugins presentes, en orden de carga
    active: list[str]  # plugins activos, en orden de carga
    warnings: list[str] = field(default_factory=list)

    @property
    def active_lower(self) -> set[str]:
        return {p.lower() for p in self.active}

    def present_plugins(self) -> dict[str, str]:
        """plugins presentes en Data -> nombre original."""
        return {k: v for k, v in self.data.listdir("").items() if k.endswith(PLUGIN_EXTS)}

    def mod_by_name(self, name: str) -> Mod | None:
        for m in self.mods:
            if m.name == name:
                return m
        return None

    @property
    def enabled_mods(self) -> list[Mod]:
        return [m for m in self.mods if m.enabled and not m.is_separator]


# --------------------------------------------------------------------------- carga
def _int(value: str | None) -> int | None:
    try:
        n = int(str(value).strip())
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _resolve(value: str | None, base: Path, default: str) -> Path:
    raw = value or default
    raw = raw.replace("%BASE_DIR%", str(base))
    p = Path(raw.replace("\\", os.sep)) if os.sep == "/" else Path(raw)
    return p if p.is_absolute() else base / p


def _read_mod(path: Path, name: str, enabled: bool, priority: int) -> Mod:
    mod = Mod(name=name, path=path, enabled=enabled, priority=priority)
    mod.is_separator = name.endswith("_separator")
    meta_path = path / "meta.ini"
    if meta_path.is_file():
        try:
            ini = read_qt_ini(meta_path)
        except Exception:
            ini = {}
        gen = ini.get("General", {})
        mod.modid = _int(gen.get("modid"))
        mod.version = gen.get("version", "")
        mod.installation_file = gen.get("installationFile", "")
        files = ini.get("installedFiles", {})
        for key, val in files.items():
            if key.endswith("fileid") and _int(val):
                mod.installed_file_ids.add(int(val))
        if mod.installed_file_ids:
            mod.fileid = min(mod.installed_file_ids)
    return mod


def _read_downloads(downloads_dir: Path) -> list[Download]:
    result = []
    if not downloads_dir.is_dir():
        return result
    for meta in downloads_dir.glob("*.meta"):
        archive = meta.with_suffix("")
        try:
            gen = read_qt_ini(meta).get("General", {})
        except Exception:
            continue
        result.append(
            Download(
                path=archive,
                modid=_int(gen.get("modID")),
                fileid=_int(gen.get("fileID")),
                mod_name=gen.get("modName", ""),
                name=gen.get("name", archive.name),
                installed=gen.get("installed", "").lower() == "true",
            )
        )
    return result


def _read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def load_setup(mo2_dir: str | Path, profile: str | None = None, game_dir: str | Path | None = None) -> Setup:
    mo2_dir = Path(mo2_dir)
    ini_path = mo2_dir / "ModOrganizer.ini"
    if not ini_path.is_file():
        raise FileNotFoundError(
            f"No encuentro ModOrganizer.ini en {mo2_dir}. Indica la carpeta de MO2 "
            "(o la carpeta de la instancia en %LOCALAPPDATA%\\ModOrganizer\\<instancia>)."
        )
    ini = read_qt_ini(ini_path)
    general = ini.get("General", {})
    settings = ini.get("Settings", {})
    warnings: list[str] = []

    base = _resolve(settings.get("base_directory"), mo2_dir, str(mo2_dir))
    mods_dir = _resolve(settings.get("mod_directory"), base, "%BASE_DIR%/mods")
    downloads_dir = _resolve(settings.get("download_directory"), base, "%BASE_DIR%/downloads")
    profiles_dir = _resolve(settings.get("profiles_directory"), base, "%BASE_DIR%/profiles")
    overwrite_dir = _resolve(settings.get("overwrite_directory"), base, "%BASE_DIR%/overwrite")

    profile = profile or general.get("selected_profile") or "Default"
    profile_dir = profiles_dir / profile
    if not profile_dir.is_dir():
        raise FileNotFoundError(f"No existe el perfil '{profile}' en {profiles_dir}")

    if game_dir is None and general.get("gamePath"):
        game_dir = general["gamePath"]
    game_path = Path(game_dir) if game_dir else None
    if game_path is not None and not game_path.is_dir():
        warnings.append(f"No encuentro la carpeta del juego ({game_path}); se omiten los plugins base.")
        game_path = None

    # modlist.txt: la primera línea es la de MAYOR prioridad
    entries = _read_lines(profile_dir / "modlist.txt")
    mods: list[Mod] = []
    total = len(entries)
    for idx, line in enumerate(entries):
        flag, name = line[0], line[1:]
        if flag not in "+-*":
            continue
        if flag == "*":  # DLC / no gestionados: viven en la carpeta del juego
            continue
        mods.append(_read_mod(mods_dir / name, name, flag == "+", total - idx))

    data = VirtualData()
    if game_path:
        data.add_tree(game_path / "Data", BASE_OWNER)
    for mod in sorted(mods, key=lambda m: m.priority):
        if mod.enabled and not mod.is_separator:
            data.add_tree(mod.path, mod.name)
    data.add_tree(overwrite_dir, OVERWRITE_OWNER)

    present = {k: v for k, v in data.listdir("").items() if k.endswith(PLUGIN_EXTS)}

    # plugins.txt: '*' = activo. Los maestros oficiales y el Creation Club siempre están activos.
    implicit = [p for p in ["Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm", "Dragonborn.esm"]]
    if game_path:
        implicit += _read_lines(game_path / "Skyrim.ccc")
    implicit = [present.get(p.lower(), p) for p in implicit if p.lower() in present]

    plugin_lines = _read_lines(profile_dir / "plugins.txt")
    explicit_active = [l[1:] for l in plugin_lines if l.startswith("*")]
    loadorder_lines = _read_lines(profile_dir / "loadorder.txt")

    order: list[str] = []
    seen: set[str] = set()
    for name in implicit + loadorder_lines + [l.lstrip("*") for l in plugin_lines]:
        low = name.lower()
        if low in present and low not in seen:
            seen.add(low)
            order.append(present[low])
    for low, name in sorted(present.items()):
        if low not in seen:
            order.append(name)
            seen.add(low)

    active_set = {p.lower() for p in implicit + explicit_active}
    active = [p for p in order if p.lower() in active_set]

    for p in BASE_GAME_PLUGINS:
        if p not in present and game_path:
            warnings.append(f"Falta el plugin oficial {p} en la carpeta Data del juego.")

    return Setup(
        mo2_dir=mo2_dir,
        game_dir=game_path,
        profile=profile,
        mods=mods,
        downloads=_read_downloads(downloads_dir),
        downloads_dir=downloads_dir,
        data=data,
        load_order=order,
        active=active,
        warnings=warnings,
    )


def find_archive(setup: Setup, mod: Mod) -> Path | None:
    """Encuentra el archivo descargado (.7z/.zip/.rar) desde el que se instaló un mod."""
    if mod.installation_file:
        raw = mod.installation_file
        cand = Path(raw)
        if not cand.is_absolute():
            cand = setup.downloads_dir / raw
        if cand.is_file():
            return cand
        cand = setup.downloads_dir / Path(raw.replace("\\", "/")).name
        if cand.is_file():
            return cand
    if mod.modid and mod.installed_file_ids:
        for d in setup.downloads:
            if d.modid == mod.modid and d.fileid in mod.installed_file_ids and d.path.is_file():
                return d.path
    return None
