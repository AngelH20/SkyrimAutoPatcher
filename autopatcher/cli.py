"""Línea de comandos: python -m autopatcher --mo2 "C:\\Modding\\MO2" """
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .analysis import analyze_local
from .archives import find_7z
from .mo2 import load_setup
from .report import build_html, console_summary

DEFAULT_CACHE = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "SkyrimAutoPatcher"


def _progress(label: str):
    def cb(i: int, total: int, name: str):
        width = 28
        done = int(width * i / max(total, 1))
        sys.stdout.write(f"\r  {label} [{'#' * done}{'.' * (width - done)}] {i}/{total} {name[:40]:<40}")
        sys.stdout.flush()
        if i == total:
            sys.stdout.write("\n")

    return cb


def _read_key(args) -> str | None:
    if args.nexus_key:
        return args.nexus_key.strip()
    if os.environ.get("NEXUS_API_KEY"):
        return os.environ["NEXUS_API_KEY"].strip()
    key_file = args.cache / "nexus_api_key.txt"
    if key_file.is_file():
        return key_file.read_text(encoding="utf-8").strip() or None
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="autopatcher",
        description="Detecta parches faltantes en tu lista de Mod Organizer 2 (solo lectura).",
    )
    p.add_argument("--mo2", required=True, type=Path, help="Carpeta de MO2 (la que tiene ModOrganizer.ini)")
    p.add_argument("--perfil", "--profile", dest="profile", help="Perfil de MO2 (por defecto, el seleccionado)")
    p.add_argument("--juego", "--game", dest="game", type=Path, help="Carpeta de Skyrim (si MO2 no la tiene)")
    p.add_argument("--nexus-key", help="Tu API key personal de Nexus (o variable NEXUS_API_KEY)")
    p.add_argument("--guardar-key", action="store_true", help="Guarda la API key en la caché para no volver a pasarla")
    p.add_argument("--sin-nexus", action="store_true", help="No consultar Nexus")
    p.add_argument("--sin-loot", action="store_true", help="No usar el masterlist de LOOT")
    p.add_argument("--sin-archivos", action="store_true", help="No revisar los instaladores (FOMOD) descargados")
    p.add_argument("--offline", action="store_true", help="No usar internet (usa la caché)")
    p.add_argument("--salida", type=Path, default=Path("informe_parches.html"), help="Archivo HTML del informe")
    p.add_argument("--json", type=Path, help="Además, guarda los resultados en JSON")
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="Carpeta de caché")
    p.add_argument("--no-abrir", action="store_true", help="No abrir el informe al terminar")
    p.add_argument("--version", action="version", version=__version__)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.cache.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    print(f"SkyrimAutoPatcher {__version__} — solo lectura, no se modifica nada.\n")
    print("1) Leyendo Mod Organizer 2…")
    try:
        setup = load_setup(args.mo2, args.profile, args.game)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 2
    notes.extend(setup.warnings)
    print(f"   Perfil '{setup.profile}': {len(setup.enabled_mods)} mods activos, {len(setup.active)} plugins activos.")

    local = None
    if not args.sin_archivos:
        print("2) Revisando instaladores FOMOD y masters…")
        if not find_7z():
            notes.append("No se encontró 7-Zip: los .7z se leen con py7zr y los .rar pueden fallar. Instalar 7-Zip lo hace más rápido y completo.")
        local = analyze_local(setup, args.cache, _progress("Archivos"))

    loot_msgs = None
    if not args.sin_loot:
        print("3) Evaluando el masterlist de LOOT…")
        from .loot import analyze as loot_analyze, get_masterlist, load_masterlist

        ml_path = get_masterlist(args.cache, offline=args.offline)
        if ml_path is None:
            notes.append("No se pudo descargar el masterlist de LOOT (sin conexión y sin caché).")
        else:
            loot_msgs, unknown = loot_analyze(setup, load_masterlist(ml_path))
            if unknown:
                notes.append(f"Funciones de LOOT no soportadas (sus avisos se omitieron): {', '.join(sorted(unknown))}")

    nexus = None
    if not args.sin_nexus and not args.offline:
        key = _read_key(args)
        if not key:
            notes.append(
                "No se consultó Nexus: falta tu API key personal (nexusmods.com → tu perfil → Site preferences → API Keys)."
            )
        else:
            from .nexus import NexusClient, NexusError, find_nexus_patches

            if args.guardar_key:
                (args.cache / "nexus_api_key.txt").write_text(key, encoding="utf-8")
            print("4) Buscando parches en Nexus (API oficial, respetando límites)…")
            client = NexusClient(key, args.cache)
            try:
                user = client.validate()
                print(f"   Conectado como {user.get('name', '?')} ({'Premium' if user.get('is_premium') else 'gratuita'}).")
                nexus, errors = find_nexus_patches(setup, client, _progress("Nexus"))
                notes.extend(errors[:10])
                if len(errors) > 10:
                    notes.append(f"… y {len(errors) - 10} errores más de Nexus.")
            except (NexusError, OSError) as exc:
                notes.append(f"No se pudo consultar Nexus: {exc}")

    html_text = build_html(setup, local, loot_msgs, nexus, notes)
    args.salida.write_text(html_text, encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "local": asdict(local) if local else None,
                    "loot": [asdict(m) for m in loot_msgs] if loot_msgs is not None else None,
                    "nexus": [asdict(s) for s in nexus] if nexus is not None else None,
                    "notes": notes,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    print("\nResumen:")
    print(console_summary(local, loot_msgs, nexus))
    for n in notes:
        print(f"  ! {n}")
    print(f"\nInforme: {args.salida.resolve()}")
    if not args.no_abrir:
        try:
            webbrowser.open(args.salida.resolve().as_uri())
        except Exception:  # noqa: BLE001
            pass
    return 0
