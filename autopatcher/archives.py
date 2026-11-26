"""Lectura de archivos comprimidos de mods (.zip, .7z, .rar) sin extraerlos a disco.

Orden de preferencia:
  1. ``zipfile`` (incluido en Python) para .zip
  2. el ejecutable de 7-Zip (7z.exe) si está instalado: lee .7z y .rar, rápido y completo
  3. ``py7zr`` / ``rarfile`` si están instalados
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

_SEVEN_ZIP_CANDIDATES = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]


class ArchiveError(Exception):
    pass


def find_7z() -> str | None:
    for name in ("7z", "7za", "7zz"):
        hit = shutil.which(name)
        if hit:
            return hit
    for cand in _SEVEN_ZIP_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return None


def _norm(name: str) -> str:
    return name.replace("\\", "/").strip("/")


class Archive:
    def __init__(self, path: str | Path, seven_zip: str | None = None):
        self.path = Path(path)
        self.seven_zip = seven_zip if seven_zip is not None else find_7z()
        self._names: list[str] | None = None
        self._backend = self._pick_backend()

    # ------------------------------------------------------------------ backends
    def _pick_backend(self) -> str:
        suffix = self.path.suffix.lower()
        if zipfile.is_zipfile(self.path):
            return "zip"
        if self.seven_zip:
            return "7zexe"
        if suffix == ".7z":
            try:
                import py7zr  # noqa: F401

                return "py7zr"
            except ImportError:
                pass
        if suffix == ".rar":
            try:
                import rarfile  # noqa: F401

                return "rarfile"
            except ImportError:
                pass
        raise ArchiveError(
            f"No puedo abrir {self.path.name}: instala 7-Zip (https://www.7-zip.org) "
            "o ejecuta 'pip install py7zr rarfile'."
        )

    def names(self) -> list[str]:
        if self._names is None:
            self._names = self._list()
        return self._names

    def _list(self) -> list[str]:
        try:
            if self._backend == "zip":
                with zipfile.ZipFile(self.path) as zf:
                    return [_norm(i.filename) for i in zf.infolist() if not i.is_dir()]
            if self._backend == "7zexe":
                out = subprocess.run(
                    [self.seven_zip, "l", "-slt", "-ba", "-sccUTF-8", str(self.path)],
                    capture_output=True,
                    check=True,
                ).stdout.decode("utf-8", errors="replace")
                names, current, is_dir = [], None, False
                for line in out.splitlines() + [""]:
                    if line.startswith("Path = "):
                        current, is_dir = line[7:], False
                    elif line.startswith("Folder = +") or (line.startswith("Attributes = ") and "D" in line[13:14]):
                        is_dir = True
                    elif not line.strip() and current is not None:
                        if not is_dir:
                            names.append(_norm(current))
                        current = None
                return names
            if self._backend == "py7zr":
                import py7zr

                with py7zr.SevenZipFile(self.path) as z:
                    return [_norm(i.filename) for i in z.list() if not i.is_directory]
            if self._backend == "rarfile":
                import rarfile

                with rarfile.RarFile(self.path) as rf:
                    return [_norm(i.filename) for i in rf.infolist() if not i.is_dir()]
        except Exception as exc:  # noqa: BLE001
            raise ArchiveError(f"No pude listar {self.path.name}: {exc}") from exc
        raise ArchiveError(f"Backend desconocido para {self.path.name}")

    def read(self, name: str) -> bytes:
        """Lee un archivo interno (``name`` tal como aparece en :meth:`names`)."""
        try:
            if self._backend == "zip":
                with zipfile.ZipFile(self.path) as zf:
                    for info in zf.infolist():
                        if _norm(info.filename) == name:
                            return zf.read(info)
                raise KeyError(name)
            if self._backend == "7zexe":
                # -so escribe a stdout; el nombre se pasa tal cual (sin comodines)
                res = subprocess.run(
                    [self.seven_zip, "e", "-so", "-spd", str(self.path), name.replace("/", os.sep)],
                    capture_output=True,
                    check=True,
                )
                if not res.stdout:
                    res = subprocess.run(
                        [self.seven_zip, "e", "-so", "-spd", str(self.path), name],
                        capture_output=True,
                        check=True,
                    )
                return res.stdout
            if self._backend == "py7zr":
                import py7zr

                with tempfile.TemporaryDirectory() as tmp:
                    with py7zr.SevenZipFile(self.path) as z:
                        real = next((n for n in z.getnames() if _norm(n) == name), name)
                        z.extract(path=tmp, targets=[real])
                    return (Path(tmp) / real).read_bytes()
            if self._backend == "rarfile":
                import rarfile

                with rarfile.RarFile(self.path) as rf:
                    real = next((i for i in rf.infolist() if _norm(i.filename) == name), None)
                    return rf.read(real or name)
        except Exception as exc:  # noqa: BLE001
            raise ArchiveError(f"No pude leer {name} de {self.path.name}: {exc}") from exc
        raise ArchiveError(f"Backend desconocido para {self.path.name}")

    def find(self, name: str) -> str | None:
        """Busca un archivo interno sin distinguir mayúsculas."""
        low = _norm(name).lower()
        for n in self.names():
            if n.lower() == low:
                return n
        return None
