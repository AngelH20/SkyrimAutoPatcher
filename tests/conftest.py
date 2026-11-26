import struct
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_plugin(masters=(), flags=0, description=""):
    subs = b""
    hedr = struct.pack("<fII", 1.7, 0, 0x800)
    subs += b"HEDR" + struct.pack("<H", len(hedr)) + hedr
    if description:
        d = description.encode("cp1252") + b"\0"
        subs += b"SNAM" + struct.pack("<H", len(d)) + d
    for m in masters:
        z = m.encode("cp1252") + b"\0"
        subs += b"MAST" + struct.pack("<H", len(z)) + z
        subs += b"DATA" + struct.pack("<H", 8) + b"\0" * 8
    header = b"TES4" + struct.pack("<IIIIHH", len(subs), flags, 0, 0, 44, 0)
    return header + subs


FOMOD_XML = """<?xml version="1.0" encoding="UTF-8"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <moduleName>Lighting Mod</moduleName>
  <requiredInstallFiles><file source="Core/Lighting.esp" destination="Lighting.esp"/></requiredInstallFiles>
  <installSteps order="Explicit">
    <installStep name="Parches">
      <optionalFileGroups order="Explicit">
        <group name="Compatibilidad" type="SelectAny">
          <plugins order="Explicit">
            <plugin name="Weather Mod">
              <description>Patch for Weather Mod</description>
              <files><file source="Patches/Lighting - Weather Patch.esp" destination="Lighting - Weather Patch.esp"/></files>
              <typeDescriptor><type name="Optional"/></typeDescriptor>
            </plugin>
            <plugin name="Other Mod">
              <description>Patch for Other Mod</description>
              <files><file source="Patches/Lighting - Other Patch.esp" destination="Lighting - Other Patch.esp"/></files>
              <typeDescriptor><type name="Optional"/></typeDescriptor>
            </plugin>
          </plugins>
        </group>
        <group name="Variante" type="SelectExactlyOne">
          <plugins order="Explicit">
            <plugin name="Normal"><description/><files/><typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
            <plugin name="Dawnguard"><description/>
              <files><folder source="DG" destination=""/></files>
              <typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
          </plugins>
        </group>
      </optionalFileGroups>
    </installStep>
  </installSteps>
  <conditionalFileInstalls><patterns>
    <pattern><dependencies operator="And"><fileDependency file="Weather.esp" state="Active"/></dependencies>
      <files><file source="Auto/Lighting - Weather Auto.esp" destination="Lighting - Weather Auto.esp"/></files></pattern>
    <pattern><dependencies operator="And"><fileDependency file="Other.esp" state="Active"/></dependencies>
      <files><file source="Auto/Lighting - Other Auto.esp" destination="Lighting - Other Auto.esp"/></files></pattern>
  </patterns></conditionalFileInstalls>
</config>
"""


def write(path: Path, data: bytes | str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)


@pytest.fixture
def mo2(tmp_path):
    root = tmp_path / "MO2"
    game = tmp_path / "Skyrim"
    for m in ["Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm", "Dragonborn.esm"]:
        write(game / "Data" / m, make_plugin(["Skyrim.esm"] if m != "Skyrim.esm" else [], flags=1))
    write(game / "SkyrimSE.exe", b"MZ")

    write(
        root / "ModOrganizer.ini",
        "[General]\ngameName=Skyrim Special Edition\n"
        f"gamePath=@ByteArray({str(game).replace(chr(92), chr(92) * 2)})\n"
        "selected_profile=@ByteArray(Default)\n\n[Settings]\n",
    )
    mods = root / "mods"
    # Weather Mod
    write(mods / "Weather Mod" / "Weather.esp", make_plugin(["Skyrim.esm"], description="Version: 2.1"))
    write(mods / "Weather Mod" / "meta.ini", "[General]\nmodid=100\nversion=2.1\ninstallationFile=Weather-100-2-1.zip\n"
          "[installedFiles]\n1\\modid=100\n1\\fileid=1000\nsize=1\n")
    # Lighting Mod (FOMOD)
    write(mods / "Lighting Mod" / "Lighting.esp", make_plugin(["Skyrim.esm", "Update.esm"]))
    write(mods / "Lighting Mod" / "meta.ini", "[General]\nmodid=200\ninstallationFile=Lighting-200.zip\n"
          "[installedFiles]\n1\\modid=200\n1\\fileid=2000\nsize=1\n")
    # Parche roto
    write(mods / "Broken Patch" / "Broken.esp", make_plugin(["Skyrim.esm", "Missing.esp"]))
    # Parche desactivado
    write(mods / "Disabled Patch" / "DP.esp", make_plugin(["Skyrim.esm", "Weather.esp", "Lighting.esp"]))
    # Mod desactivado en MO2
    write(mods / "Other Mod" / "Other.esp", make_plugin(["Skyrim.esm"]))

    dl = root / "downloads"
    dl.mkdir(parents=True)
    with zipfile.ZipFile(dl / "Lighting-200.zip", "w") as z:
        z.writestr("fomod/ModuleConfig.xml", FOMOD_XML)
        z.writestr("Core/Lighting.esp", make_plugin(["Skyrim.esm", "Update.esm"]))
        z.writestr("Patches/Lighting - Weather Patch.esp", make_plugin(["Skyrim.esm", "Lighting.esp", "Weather.esp"]))
        z.writestr("Patches/Lighting - Other Patch.esp", make_plugin(["Skyrim.esm", "Lighting.esp", "Other.esp"]))
        z.writestr("Auto/Lighting - Weather Auto.esp", make_plugin(["Skyrim.esm", "Lighting.esp", "Weather.esp"]))
        z.writestr("Auto/Lighting - Other Auto.esp", make_plugin(["Skyrim.esm", "Lighting.esp", "Other.esp"]))
        z.writestr("DG/Lighting - DG.esp", make_plugin(["Skyrim.esm", "Dawnguard.esm", "Lighting.esp"]))
    write(dl / "Lighting-200.zip.meta", "[General]\nmodID=200\nfileID=2000\nmodName=Lighting Mod\ninstalled=true\n")
    with zipfile.ZipFile(dl / "Weather-100-2-1.zip", "w") as z:
        z.writestr("Weather.esp", make_plugin(["Skyrim.esm"]))

    prof = root / "profiles" / "Default"
    write(prof / "modlist.txt", "# MO2\n+Disabled Patch\n+Broken Patch\n+Lighting Mod\n+Weather Mod\n-Other Mod\n*DLC: Dawnguard\n")
    write(prof / "plugins.txt", "# plugins\n*Weather.esp\n*Lighting.esp\n*Broken.esp\nDP.esp\n")
    write(prof / "loadorder.txt", "Skyrim.esm\nUpdate.esm\nDawnguard.esm\nHearthFires.esm\nDragonborn.esm\nWeather.esp\nLighting.esp\nBroken.esp\nDP.esp\n")
    return root
