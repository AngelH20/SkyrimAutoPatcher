import json

from autopatcher.analysis import analyze_local
from autopatcher.cli import main
from autopatcher.fomod import parse_module_config
from autopatcher.loot import Evaluator, analyze, compare_versions
from autopatcher.mo2 import load_setup
from autopatcher.nexus import find_nexus_patches, normalize
from autopatcher.plugins import parse_header

from conftest import FOMOD_XML, make_plugin


def test_plugin_header():
    h = parse_header(make_plugin(["Skyrim.esm", "Foo.esp"], flags=0x201, description="Version 1.2.3"), "x.esp")
    assert h.masters == ["Skyrim.esm", "Foo.esp"]
    assert h.is_master and h.is_light
    assert h.version == "1.2.3"


def test_load_setup(mo2):
    s = load_setup(mo2)
    assert s.profile == "Default"
    assert "Other Mod" not in {m.name for m in s.enabled_mods}
    assert s.active[:2] == ["Skyrim.esm", "Update.esm"]
    assert "DP.esp" not in s.active and "Weather.esp" in s.active
    assert s.data.owner("Weather.esp") == "Weather Mod"
    assert s.mod_by_name("Weather Mod").modid == 100


def test_fomod_parse():
    f = parse_module_config(FOMOD_XML.encode())
    assert [o.name for o in f.options] == ["Weather Mod", "Other Mod", "Normal", "Dawnguard"]
    assert len(f.conditionals) == 2


def test_local_analysis(mo2, tmp_path):
    s = load_setup(mo2)
    rep = analyze_local(s, tmp_path / "cache")
    plugins = {h.plugin: h for h in rep.reinstall}
    assert set(plugins) == {"Lighting - Weather Patch.esp", "Lighting - Weather Auto.esp"}
    assert plugins["Lighting - Weather Patch.esp"].option == "Weather Mod"
    assert plugins["Lighting - Weather Patch.esp"].for_mods == ["Weather Mod"]
    assert plugins["Lighting - Weather Auto.esp"].confidence == "alta"
    assert [p.plugin for p in rep.master_problems] == ["Broken.esp"]
    assert rep.master_problems[0].missing == ["Missing.esp"]
    assert [p.plugin for p in rep.inactive_patches] == ["DP.esp"]


def test_already_installed_patch_not_suggested(mo2, tmp_path):
    from conftest import write
    write(mo2 / "mods" / "Lighting Mod" / "Lighting - Weather Patch.esp",
          make_plugin(["Skyrim.esm", "Lighting.esp", "Weather.esp"]))
    rep = analyze_local(load_setup(mo2), None)
    assert {h.plugin for h in rep.reinstall} == {"Lighting - Weather Auto.esp"}


def test_loot(mo2):
    s = load_setup(mo2)
    ev = Evaluator(s)
    assert ev.check('active("Weather.esp") and not active("Other.esp")')
    assert ev.check('file("Weather\\.esp|Nope\\.esp")')
    assert ev.check('version("Weather.esp", >=, "2.0") and not version("Weather.esp", >, "2.1")')
    assert not ev.check('active("Other.esp") or (file("x.esp"))')
    assert ev.check('is_master("Skyrim.esm")')
    crc = ev.crc_of("Weather.esp")
    ml = {
        "globals": [{"type": "warn", "content": "global", "condition": 'active("Lighting.esp")'}],
        "plugins": [
            {"name": "Weather.esp",
             "msg": [{"type": "say", "content": "Use the {0} patch", "subs": ["Lighting"],
                      "condition": 'active("Lighting.esp") and not active("Lighting - Weather Patch.esp")'}],
             "dirty": [{"crc": crc, "util": "SSEEdit", "itm": 3, "udr": 1}],
             "req": ["Required.esp"]},
            {"name": "Light.*\\.esp", "inc": [{"name": "Weather.esp", "display": "Weather Mod"}]},
            {"name": "Other.esp", "msg": [{"type": "warn", "content": "nope"}]},
        ],
    }
    msgs, unknown = analyze(s, ml)
    kinds = sorted((m.plugin or "", m.kind) for m in msgs)
    assert kinds == [("", "warn"), ("Lighting.esp", "inc"), ("Weather.esp", "dirty"),
                     ("Weather.esp", "req"), ("Weather.esp", "say")]
    assert not unknown


def test_versions():
    assert compare_versions("1.6.1170.0", "1.6.1170") == 0
    assert compare_versions("1.10", "1.9") > 0
    assert compare_versions("4.2.5b", "4.2.5a") > 0


class FakeClient:
    def mod_files(self, modid):
        if modid == 100:
            return [
                {"file_id": 1000, "name": "Weather Main", "category_name": "MAIN", "description": ""},
                {"file_id": 1001, "name": "Lighting Mod Patch", "category_name": "OPTIONAL",
                 "description": "Compatibility patch for Lighting Mod"},
                {"file_id": 1002, "name": "Random Patch", "category_name": "OPTIONAL", "description": "For Foo"},
                {"file_id": 1003, "name": "Old Lighting Mod Patch", "category_name": "OLD_VERSION", "description": ""},
            ]
        return []

    def content_preview(self, url, fid):
        return []


def test_nexus_matching(mo2):
    s = load_setup(mo2)
    sug, errors = find_nexus_patches(s, FakeClient())
    assert [x.file_id for x in sug] == [1001]
    assert sug[0].mentions == ["Lighting Mod"]
    assert "tab=files&file_id=1001" in sug[0].url
    assert normalize("Skyrim 3D Trees SE v1.2.3.esp") == "skyrim 3d trees"


def test_cli_end_to_end(mo2, tmp_path):
    out = tmp_path / "r.html"
    js = tmp_path / "r.json"
    rc = main(["--mo2", str(mo2), "--sin-nexus", "--sin-loot", "--salida", str(out), "--json", str(js),
               "--cache", str(tmp_path / "c"), "--no-abrir"])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert "Lighting - Weather Patch.esp" in html and "Missing.esp" in html
    data = json.loads(js.read_text(encoding="utf-8"))
    assert len(data["local"]["reinstall"]) == 2


def test_7z_archive(tmp_path):
    import py7zr
    from autopatcher.archives import Archive
    src = tmp_path / "src"
    (src / "Patches").mkdir(parents=True)
    (src / "Patches" / "A - B Patch.esp").write_bytes(make_plugin(["A.esp", "B.esp"]))
    arc = tmp_path / "x.7z"
    with py7zr.SevenZipFile(arc, "w") as z:
        z.writeall(src, arcname="")
    a = Archive(arc, seven_zip="")
    name = a.find("patches/a - b patch.esp")
    assert name and parse_header(a.read(name)).masters == ["A.esp", "B.esp"]
