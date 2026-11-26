"""Informe HTML (y resumen en consola) en español."""
from __future__ import annotations

import html
import re
from datetime import datetime

from .analysis import LocalReport
from .loot import LootMessage
from .mo2 import Setup
from .nexus import NexusSuggestion

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def md(text: str) -> str:
    """Markdown mínimo (enlaces y negrita) de los mensajes de LOOT."""
    out = html.escape(text)
    out = _LINK.sub(lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>', out)
    return _BOLD.sub(r"<strong>\1</strong>", out)


def e(text) -> str:
    return html.escape(str(text or ""))


CSS = """
:root{--bg:#f6f4ef;--card:#fff;--fg:#1f1d1a;--muted:#6b665d;--line:#e2ded5;--accent:#8a5a1f;
--hi:#1f7a3d;--mid:#9a6b00;--err:#b3261e;--chip:#f0ebe1}
@media (prefers-color-scheme:dark){:root{--bg:#171614;--card:#211f1c;--fg:#ece8e1;--muted:#a49d91;
--line:#34312c;--accent:#e0a65a;--hi:#6fcf8e;--mid:#e6b84f;--err:#ff8a80;--chip:#2c2925}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 8px;padding-top:8px;border-top:1px solid var(--line)}
.sub{color:var(--muted);margin:0 0 16px}.muted{color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.stat b{display:block;font-size:24px}.stat span{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
.card h3{margin:0 0 6px;font-size:16px}.chip{display:inline-block;background:var(--chip);border-radius:999px;
padding:1px 9px;font-size:12px;margin:2px 4px 2px 0}
.alta{color:var(--hi);font-weight:600}.media{color:var(--mid);font-weight:600}
.err{color:var(--err);font-weight:600}.warn{color:var(--mid);font-weight:600}
a{color:var(--accent)}a.btn{display:inline-block;background:var(--accent);color:var(--bg);text-decoration:none;
padding:5px 12px;border-radius:7px;font-weight:600;font-size:13px;margin-top:6px}
code{background:var(--chip);padding:0 5px;border-radius:4px;font-size:13px;overflow-wrap:anywhere}
ol.steps{margin:6px 0 0 18px;padding:0}details{margin:8px 0}summary{cursor:pointer;color:var(--muted)}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);
vertical-align:top}th{font-size:13px;color:var(--muted)}.tablewrap{overflow-x:auto}
"""


def build_html(
    setup: Setup,
    local: LocalReport | None,
    loot: list[LootMessage] | None,
    nexus: list[NexusSuggestion] | None,
    notes: list[str],
) -> str:
    reinstall = local.reinstall if local else []
    covered = {h.plugin.lower() for h in reinstall}
    nexus = [s for s in (nexus or []) if not (s.plugins_in_file and all(p.lower() in covered for p in s.plugins_in_file))]
    loot = loot or []
    loot_patch = [m for m in loot if m.kind != "dirty" and (m.patch_related or m.kind in {"inc", "req", "error"})]
    loot_other = [m for m in loot if m.kind != "dirty" and m not in loot_patch]
    dirty = [m for m in loot if m.kind == "dirty"]
    masters = local.master_problems if local else []

    p = []
    a = p.append
    a("<!doctype html><html lang='es'><head><meta charset='utf-8'>")
    a("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    a(f"<title>Informe de parches</title><style>{CSS}</style></head><body><main>")
    a("<h1>Informe de parches y compatibilidad</h1>")
    a(
        f"<p class='sub'>Perfil <b>{e(setup.profile)}</b> · {len(setup.enabled_mods)} mods activos · "
        f"{len(setup.active)} plugins activos · {datetime.now():%d/%m/%Y %H:%M}. "
        "Este programa solo lee tus archivos: no ha cambiado nada.</p>"
    )
    a("<div class='stats'>")
    for n, label in [
        (len(masters), "masters faltantes / mal ordenados"),
        (len(reinstall), "parches dentro de tus descargas"),
        (len(nexus), "parches en Nexus para revisar"),
        (len(loot_patch), "avisos de LOOT"),
        (len(dirty), "plugins sucios"),
    ]:
        a(f"<div class='stat'><b>{n}</b><span>{label}</span></div>")
    a("</div>")
    for n in notes:
        a(f"<p class='muted'>ℹ️ {e(n)}</p>")

    # 1. masters
    a("<h2>1. Masters faltantes (provocan cierres al iniciar)</h2>")
    if not masters:
        a("<p class='muted'>Todo bien: cada plugin activo tiene sus masters activos y cargados antes.</p>")
    for mp in masters:
        a("<div class='card'>")
        a(f"<h3><code>{e(mp.plugin)}</code> <span class='muted'>({e(mp.mod)})</span></h3>")
        if mp.missing:
            a(f"<p class='err'>Le falta: {', '.join(f'<code>{e(m)}</code>' for m in mp.missing)}</p>")
            a("<p>Instala/activa ese mod, o desactiva este plugin. Si es un parche para un mod que ya no usas, quítalo.</p>")
        if mp.late:
            a(f"<p class='warn'>Carga antes que su master: {', '.join(f'<code>{e(m)}</code>' for m in mp.late)}. Ordena con LOOT.</p>")
        a("</div>")

    # 2. reinstalar
    a("<h2>2. Reinstala estos mods y marca el parche en el instalador</h2>")
    a(
        "<p class='muted'>Estos parches ya están <b>dentro de archivos que descargaste</b>: no necesitas bajar nada. "
        "En MO2: clic derecho sobre el mod → <i>Reinstall Mod</i> → elige <i>Merge</i> (o <i>Replace</i> "
        "repitiendo tus opciones) y marca la opción indicada.</p>"
    )
    if not reinstall:
        a("<p class='muted'>No se encontraron opciones de parche sin instalar.</p>")
    by_mod: dict[str, list] = {}
    for h in reinstall:
        by_mod.setdefault(h.mod, []).append(h)
    for mod, hints in by_mod.items():
        a("<div class='card'>")
        a(f"<h3>{e(mod)}</h3><p class='muted'>Archivo: <code>{e(hints[0].archive)}</code></p>")
        a("<div class='tablewrap'><table><tr><th>Dónde está en el instalador</th><th>Plugin</th><th>Para</th><th>Confianza</th></tr>")
        for h in hints:
            where = " → ".join(x for x in [h.step, h.group, h.option] if x) or "Archivo suelto dentro del comprimido (no es FOMOD)"
            excl = "<br><span class='muted'>Grupo de una sola opción: elige la que corresponda.</span>" if h.exclusive_group else ""
            a(
                f"<tr><td>{e(where)}{excl}</td><td><code>{e(h.plugin)}</code></td>"
                f"<td>{''.join(f'<span class=chip>{e(m)}</span>' for m in h.for_mods)}</td>"
                f"<td><span class='{h.confidence}'>{h.confidence}</span><br><span class='muted'>{e(h.reason)}</span></td></tr>"
            )
        a("</table></div></div>")

    inactive = local.inactive_patches if local else []
    if inactive:
        a("<h3>Parches instalados pero desactivados</h3>")
        a("<p class='muted'>Están en tu lista y todos sus masters están activos; seguramente solo falta marcarlos en la pestaña de plugins.</p>")
        for ip in inactive:
            a(f"<div class='card'><code>{e(ip.plugin)}</code> <span class='muted'>({e(ip.mod)})</span> — para "
              f"{''.join(f'<span class=chip>{e(m)}</span>' for m in ip.for_mods)}</div>")

    # 3. Nexus
    a("<h2>3. Parches publicados en Nexus que mencionan mods que tienes</h2>")
    if not nexus:
        a("<p class='muted'>Sin sugerencias (o no se consultó Nexus).</p>")
    else:
        a(
            "<p class='muted'>Abre el enlace y pulsa <b>Mod Manager Download</b>: MO2 lo descarga como siempre "
            "(funciona con cuenta gratuita). Lee la descripción: algunos parches necesitan además otro mod.</p>"
        )
    for s in nexus:
        a("<div class='card'>")
        a(f"<h3>{e(s.file_name)}</h3>")
        a(f"<p class='muted'>En la página de <b>{e(s.page_mod)}</b> · categoría {e(s.category)}</p>")
        a(f"<p>Menciona: {''.join(f'<span class=chip>{e(m)}</span>' for m in s.mentions)}</p>")
        if s.plugins_in_file:
            a(f"<p class='muted'>Contiene: {', '.join(f'<code>{e(x)}</code>' for x in s.plugins_in_file)}</p>")
        if s.description:
            a(f"<details><summary>Descripción</summary><p>{e(s.description[:1200])}</p></details>")
        if s.already_downloaded:
            a("<p class='warn'>Ya está en tu carpeta de descargas: solo instálalo desde la pestaña Downloads de MO2.</p>")
        a(f"<a class='btn' href='{e(s.url)}' target='_blank' rel='noopener'>Abrir en Nexus</a>")
        a("</div>")

    # 4. LOOT
    a("<h2>4. Avisos de LOOT sobre parches, incompatibilidades y requisitos</h2>")
    if not loot_patch:
        a("<p class='muted'>Sin avisos relevantes.</p>")
    for m in loot_patch:
        cls = {"error": "err", "inc": "err", "req": "err", "warn": "warn"}.get(m.kind, "muted")
        a(f"<div class='card'><span class='{cls}'>{e(m.kind.upper())}</span> "
          f"<code>{e(m.plugin or 'General')}</code><p>{md(m.text)}</p></div>")
    if loot_other:
        a(f"<details><summary>Otros {len(loot_other)} mensajes de LOOT</summary>")
        for m in loot_other:
            a(f"<div class='card'><code>{e(m.plugin or 'General')}</code><p>{md(m.text)}</p></div>")
        a("</details>")

    # 5. sucios
    a("<h2>5. Plugins sucios (limpiar con SSEEdit → Quick Auto Clean)</h2>")
    if not dirty:
        a("<p class='muted'>Ningún plugin sucio conocido.</p>")
    for m in dirty:
        a(f"<div class='card'><code>{e(m.plugin)}</code><p>{md(m.text)}</p></div>")

    # 6. notas
    if local and (local.unreadable or local.no_archive):
        a("<h2>6. No se pudo revisar</h2>")
        if local.no_archive:
            a(f"<details><summary>{len(local.no_archive)} mods sin su archivo en la carpeta de descargas "
              "(no se puede revisar su instalador)</summary><p>")
            a(", ".join(e(x) for x in local.no_archive))
            a("</p></details>")
        if local.unreadable:
            a(f"<details><summary>{len(local.unreadable)} archivos ilegibles</summary><ul>")
            a("".join(f"<li>{e(x)}</li>" for x in local.unreadable))
            a("</ul></details>")

    a("<h2>Después de instalar parches</h2><ol class='steps'>"
      "<li>Ordena con LOOT.</li><li>Vuelve a ejecutar este programa para confirmar que no falta nada.</li>"
      "<li>Si usas Synthesis/DynDOLOD/Nemesis/Pandora, regenera sus salidas.</li></ol>")
    a("</main></body></html>")
    return "".join(p)


def console_summary(local: LocalReport | None, loot: list[LootMessage] | None, nexus: list[NexusSuggestion] | None) -> str:
    lines = []
    if local:
        lines.append(f"  Masters faltantes/mal ordenados : {len(local.master_problems)}")
        lines.append(f"  Parches para reinstalar (FOMOD) : {len(local.reinstall)}")
        lines.append(f"  Parches desactivados            : {len(local.inactive_patches)}")
    if nexus is not None:
        lines.append(f"  Parches en Nexus para revisar   : {len(nexus)}")
    if loot is not None:
        lines.append(f"  Avisos de LOOT                  : {len([m for m in loot if m.kind != 'dirty'])}")
        lines.append(f"  Plugins sucios                  : {len([m for m in loot if m.kind == 'dirty'])}")
    return "\n".join(lines)
