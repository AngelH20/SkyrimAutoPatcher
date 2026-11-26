# SkyrimAutoPatcher

Programa para **Skyrim SE/AE (1.6.1170) + Mod Organizer 2**. Lee tu lista de mods y te dice qué parches
de compatibilidad te faltan. **No modifica nada**: solo lee tus archivos y genera un informe HTML.

## Qué detecta

| # | Qué | De dónde lo saca |
|---|-----|------------------|
| 1 | **Masters faltantes o mal ordenados** (la causa típica de que el juego se cierre al iniciar) | Cabeceras de tus `.esp/.esm/.esl` |
| 2 | **Mods que tienes que reinstalar** porque su instalador FOMOD trae un parche para otro mod tuyo que no marcaste (o que instalaste antes que el otro mod) | Los `.7z/.zip/.rar` de tu carpeta de descargas de MO2 |
| 3 | **Parches desactivados**: instalados, con todos sus masters activos, pero sin marcar en la pestaña de plugins | Tu perfil de MO2 |
| 4 | **Parches publicados en Nexus** que mencionan mods que tienes | API oficial de Nexus (sirve la cuenta gratuita) |
| 5 | **Avisos de LOOT**: parches recomendados, incompatibilidades y requisitos | Masterlist oficial de LOOT |
| 6 | **Plugins sucios** (ITM/UDR) que conviene limpiar con SSEEdit | Masterlist de LOOT y el CRC de tus archivos |

### Cómo decide que un FOMOD trae un parche para ti
Para cada plugin que hay dentro del archivo descargado y que **no** tienes instalado, lee sus *masters*. Lo recomienda
solo si se cumplen estas tres condiciones:
- **todos** sus masters están activos en tu lista;
- depende de **otro mod** tuyo (no del juego base ni del propio mod);
- no elegiste ya otra opción en un grupo de "elige solo una".

Si además el instalador lo marca como recomendado, lo instala automáticamente cuando detecta tus mods o lo llama "patch",
la confianza sube a **alta**. El informe te dice exactamente en qué **paso → grupo → opción** del instalador está.

## Legal y seguro
- Usa solo la **API pública de Nexus** con **tu propia API key personal**, en modo lectura. No hace scraping
  ni pide enlaces de descarga directa, que en Nexus son solo para Premium.
- Para descargar, el informe te da el enlace a la pestaña de archivos del mod. Ahí pulsas **Mod Manager Download**
  y MO2 lo baja e instala como siempre (funciona con la cuenta gratuita).
- Respeta los límites de la API y guarda las respuestas en caché 24 h.
- Tu API key se queda en tu PC: solo se envía a `api.nexusmods.com`.

## Instalación (Windows)
1. Instala [Python 3.10 o superior](https://www.python.org/downloads/) y marca **"Add Python to PATH"**.
2. Recomendado: instala [7-Zip](https://www.7-zip.org/). Con él se leen bien todos los `.7z` y `.rar`.
3. Descarga este repositorio. En la carpeta, abre una terminal y ejecuta:
   ```
   pip install -r requirements.txt
   ```
4. Consigue tu API key: nexusmods.com → tu avatar → **Site preferences → API Keys** → *Personal API Key*.

## Uso
```
python -m autopatcher --mo2 "C:\Modding\MO2" --nexus-key TU_KEY --guardar-key
```
La primera vez usa `--guardar-key`; las siguientes basta con:
```
python -m autopatcher --mo2 "C:\Modding\MO2"
```
También puedes editar `analizar.bat` (la línea `set MO2=...`) y ejecutarlo con doble clic.

`--mo2` es la carpeta que contiene `ModOrganizer.ini`. Si instalaste MO2 como *instancia* (no portable), esa carpeta
suele ser `%LOCALAPPDATA%\ModOrganizer\Skyrim Special Edition`.

Opciones útiles:

| Opción | Para qué |
|--------|----------|
| `--perfil NOMBRE` | analizar otro perfil |
| `--sin-nexus` / `--sin-loot` / `--sin-archivos` | saltar una parte |
| `--offline` | no usar internet (usa la caché) |
| `--json resultados.json` | guardar también los datos crudos |
| `--salida informe.html` | dónde guardar el informe |

Al terminar se abre `informe_parches.html` en tu navegador.

## Para que funcione mejor
- **No borres tus descargas en MO2**: sin el archivo original no se puede revisar su instalador FOMOD.
- Instala los mods desde Nexus con *Mod Manager Download*. Así MO2 guarda el `modid`/`fileid` y el programa sabe qué mod es.
- Después de instalar parches, **ordena con LOOT** y vuelve a ejecutar el programa.

## Limitaciones
- Las sugerencias de Nexus se basan en los **nombres y descripciones** de los archivos. Revisa la descripción
  antes de instalar: un parche puede necesitar un tercer mod que no tienes.
- No crea parches nuevos. Para conflictos sin parche publicado sigue usando **Synthesis** o **SSEEdit**.
- MO2 no guarda qué opciones elegiste en cada FOMOD. El programa lo deduce de los plugins que tienes instalados.

## Desarrollo
```
pip install -r requirements.txt pytest
python -m pytest
```
