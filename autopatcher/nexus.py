"""Consulta LEGAL a la API pública de Nexus Mods (válida para cuentas gratuitas).

- Solo usa endpoints de lectura (lista de archivos de cada mod).
- NUNCA pide enlaces de descarga directa (eso es solo para Premium): el informe te
  da el enlace a la pestaña de archivos y tú pulsas "Mod Manager Download", así MO2
  lo descarga e instala como siempre.
- Respeta los límites de la API (cabeceras X-RL-*) y cachea las respuestas 24 h.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from . import APP_NAME, __version__
from .mo2 import Mod, Setup

API = "https://api.nexusmods.com/v1"
GAME = "skyrimspecialedition"
SKIP_CATEGORIES = {"OLD_VERSION", "ARCHIVED", "DELETED", "REMOVED"}
PATCH_WORDS = re.compile(r"\b(patch(es)?|compat\w*|integration|add-?on|parche)\b", re.IGNORECASE)

STOPWORDS = {
    "patch", "patches", "main", "core", "file", "files", "textures", "texture", "meshes", "fix", "fixes",
    "optional", "update", "skyrim", "special", "edition", "anniversary", "esp", "esl", "esm", "mod", "mods",
    "compatibility", "addon", "version", "the", "and", "for", "with", "sse", "ae", "se", "all", "in", "one",
    "aio", "plugin", "plugins", "data", "base", "lite", "full", "light", "patched", "redone", "overhaul",
    "enhanced", "improved", "better", "simple", "new", "more", "immersive", "realistic", "vanilla",
}


class NexusError(Exception):
    pass


class RateLimited(NexusError):
    pass


@dataclass
class NexusSuggestion:
    page_mod: str  # mod en cuya página está el archivo
    modid: int
    file_id: int
    file_name: str
    category: str
    description: str
    mentions: list[str]  # otros mods tuyos que menciona
    url: str
    already_downloaded: bool = False
    plugins_in_file: list[str] = field(default_factory=list)


class NexusClient:
    def __init__(self, api_key: str, cache_dir: Path, max_age_h: float = 24):
        self.cache_dir = cache_dir / "nexus"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age = max_age_h * 3600
        self.session = requests.Session()
        self.session.headers.update(
            {
                "apikey": api_key,
                "Application-Name": APP_NAME,
                "Application-Version": __version__,
                "User-Agent": f"{APP_NAME}/{__version__}",
                "Accept": "application/json",
            }
        )
        self.hourly_left: int | None = None
        self.daily_left: int | None = None

    def _get(self, url: str, cache_name: str | None = None):
        cache_file = self.cache_dir / cache_name if cache_name else None
        if cache_file and cache_file.is_file() and time.time() - cache_file.stat().st_mtime < self.max_age:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        if self.hourly_left is not None and self.hourly_left < 5 and (self.daily_left or 0) < 5:
            raise RateLimited("Se alcanzó el límite de la API de Nexus; vuelve a intentarlo en una hora.")
        resp = self.session.get(url, timeout=30)
        self.hourly_left = _int(resp.headers.get("X-RL-Hourly-Remaining"), self.hourly_left)
        self.daily_left = _int(resp.headers.get("X-RL-Daily-Remaining"), self.daily_left)
        if resp.status_code == 429:
            raise RateLimited("Nexus pidió esperar (límite de peticiones). Vuelve a intentarlo más tarde.")
        if resp.status_code == 401:
            raise NexusError("La API key de Nexus no es válida.")
        if resp.status_code == 404:
            data = None
        elif not resp.ok:
            raise NexusError(f"Nexus respondió {resp.status_code} para {url}")
        else:
            data = resp.json()
        if cache_file:
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        return data

    def validate(self) -> dict:
        return self._get(f"{API}/users/validate.json") or {}

    def mod_files(self, modid: int) -> list[dict]:
        data = self._get(f"{API}/games/{GAME}/mods/{modid}/files.json", f"files_{modid}.json")
        return (data or {}).get("files", [])

    def content_preview(self, url: str, file_id: int) -> list[str]:
        """Lista de archivos dentro de un archivo de Nexus (endpoint público de vista previa)."""
        cache_file = self.cache_dir / f"preview_{file_id}.json"
        if cache_file.is_file():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        else:
            try:
                resp = requests.get(url, timeout=30, headers={"User-Agent": f"{APP_NAME}/{__version__}"})
                data = resp.json() if resp.ok else {}
            except (requests.RequestException, ValueError):
                data = {}
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        names: list[str] = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "file" and node.get("name"):
                    names.append(node["name"])
                for child in node.get("children") or []:
                    walk(child)

        walk(data)
        return names


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ coincidencias
def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\.(esp|esm|esl)\b", " ", text)
    text = re.sub(r"\bv?\d+(\.\d+)+\b", " ", text)  # versiones
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\b(sse|se|ae|special edition|anniversary edition)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def aliases_for(mod: Mod, nexus_name: str = "") -> set[str]:
    raw = {mod.name, nexus_name, *[p.rsplit(".", 1)[0] for p in mod.root_plugins()]}
    out = set()
    for r in raw:
        n = normalize(r or "")
        words = [w for w in n.split() if w not in STOPWORDS]
        if not words:
            continue
        if len(n) >= 5 or (len(n) >= 4 and n.isalpha()):
            out.add(n)
    return out


def _mentions(text: str, alias: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None


def find_nexus_patches(
    setup: Setup,
    client: NexusClient,
    progress: Callable[[int, int, str], None] | None = None,
    use_previews: bool = True,
) -> tuple[list[NexusSuggestion], list[str]]:
    errors: list[str] = []
    mods = [m for m in setup.enabled_mods if m.modid]
    dl_names = {d.modid: d.mod_name for d in setup.downloads if d.modid and d.mod_name}
    alias_map = {m.name: aliases_for(m, dl_names.get(m.modid, "")) for m in mods}
    for m in setup.enabled_mods:
        if m.name not in alias_map:
            alias_map[m.name] = aliases_for(m)

    installed_ids = {fid for m in setup.mods for fid in m.installed_file_ids}
    downloaded_ids = {d.fileid for d in setup.downloads if d.fileid}
    present = set(setup.present_plugins())

    seen_pages: set[int] = set()
    suggestions: list[NexusSuggestion] = []
    for i, mod in enumerate(mods, 1):
        if mod.modid in seen_pages:
            continue
        seen_pages.add(mod.modid)
        if progress:
            progress(i, len(mods), mod.name)
        try:
            files = client.mod_files(mod.modid)
        except RateLimited as exc:
            errors.append(str(exc))
            break
        except (NexusError, requests.RequestException) as exc:
            errors.append(f"{mod.name}: {exc}")
            continue
        own_aliases = alias_map.get(mod.name, set())
        same_page = {m.name for m in setup.mods if m.modid == mod.modid}
        for f in files:
            if (f.get("category_name") or "").upper() in SKIP_CATEGORIES:
                continue
            fid = f.get("file_id")
            if fid in installed_ids:
                continue
            text_raw = f"{f.get('name', '')} {f.get('file_name', '')} {f.get('description') or ''}"
            if not PATCH_WORDS.search(text_raw):
                continue
            text = normalize(re.sub(r"<[^>]+>|\[/?[a-z]+[^\]]*\]", " ", text_raw))
            mentions = []
            for other, aliases in alias_map.items():
                if other in same_page:
                    continue
                if any(_mentions(text, a) for a in aliases - own_aliases):
                    mentions.append(other)
            if not mentions:
                continue
            sug = NexusSuggestion(
                page_mod=mod.name,
                modid=mod.modid,
                file_id=fid,
                file_name=f.get("name") or f.get("file_name", ""),
                category=f.get("category_name") or "",
                description=re.sub(r"<[^>]+>", " ", f.get("description") or "").strip(),
                mentions=sorted(mentions),
                url=f"https://www.nexusmods.com/{GAME}/mods/{mod.modid}?tab=files&file_id={fid}",
                already_downloaded=fid in downloaded_ids,
            )
            if use_previews and f.get("content_preview_link"):
                plugins = [n for n in client.content_preview(f["content_preview_link"], fid)
                           if n.lower().endswith((".esp", ".esm", ".esl"))]
                sug.plugins_in_file = plugins
                if plugins and all(p.lower() in present for p in plugins):
                    continue  # ya tienes esos plugins instalados
            suggestions.append(sug)
    suggestions.sort(key=lambda s: (s.page_mod.lower(), s.file_name.lower()))
    return suggestions, errors
