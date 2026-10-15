"""Build the independent Windows desktop executable in the project directory."""
from pathlib import Path
import argparse
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description='Build the Windows photo album')
parser.add_argument('--dist-dir', type=Path, default=ROOT)
args = parser.parse_args()
dist_dir = args.dist_dir.resolve()
arguments = [
    sys.executable, '-m', 'PyInstaller',
    '--noconfirm', '--clean', '--onefile', '--windowed',
    '--name', '拾光相册', '--icon', str(ROOT / 'assets' / 'app.ico'),
    '--distpath', str(dist_dir), '--workpath', str(ROOT / '.desktop-build'),
    '--specpath', str(ROOT / '.desktop-build'),
    '--add-data', f'{ROOT / "ui"};ui',
    '--add-data', f'{ROOT / "assets"};assets',
    '--collect-all', 'imageio_ffmpeg',
    '--hidden-import', 'webview.platforms.winforms',
    '--hidden-import', 'webview.platforms.edgechromium',
]
for module in ('PyQt5','PyQt6','PySide2','PySide6','qtpy','cefpython3','gi','matplotlib','numpy','pandas','scipy','IPython'):
    arguments += ['--exclude-module', module]
arguments += [str(ROOT / 'desktop.py')]
subprocess.run(arguments, cwd=ROOT, check=True)
