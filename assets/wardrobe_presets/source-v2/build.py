"""Offline prototype: original Sprite-Gen frames to reviewed clothing atlases.

Run only in a fresh scratch folder with the pinned Sprite-Gen virtualenv.
Output paths are new, never user data or original sprites.
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, os, shutil, subprocess, sys
from PIL import Image

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source-main',type=Path,required=True,help='Sprite-Gen unpack-atlas main run')
parser.add_argument('--source-thinking',type=Path,required=True,help='Sprite-Gen unpack-atlas thinking run')
parser.add_argument('--scratch',type=Path,required=True,help='New scratch directory; never overwrite')
parser.add_argument('--output',type=Path,required=True,help='New output directory; never overwrite')
parser.add_argument('--sprite-python',type=Path,required=True,help='Python with pinned Sprite-Gen')
args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[3]
SOURCE_MAIN=args.source_main.resolve(); SOURCE_THINKING=args.source_thinking.resolve()
SCRATCH=args.scratch.resolve(); DEST=args.output.resolve(); PYTHON=args.sprite_python.resolve()
if SCRATCH.exists() or DEST.exists():
 raise SystemExit('Choose fresh build and output directories; nothing overwritten.')
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path[:0]=[str(ROOT),str(ROOT/'Lib'/'site-packages')]
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from pet import SpriteBank,ASSETS
app=QApplication([]); bank=SpriteBank(ASSETS)
spec=importlib.util.spec_from_file_location('draft_wearables',Path(__file__).with_name('body_art.py'))
draft=importlib.util.module_from_spec(spec); spec.loader.exec_module(draft)
draft.ROOT=SOURCE_MAIN/'frames'; draft.THINKING=SOURCE_THINKING/'frames'
data=draft.DATA
hspec=importlib.util.spec_from_file_location('hats',Path(__file__).with_name('hats.py'))
hats=importlib.util.module_from_spec(hspec); hspec.loader.exec_module(hats)
hat_art={name:hats.draw_hat(name) for name in hats.HATS}
def digest(pm):
 im=pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
 return hashlib.sha256(bytes(im.constBits())).hexdigest()
def hat(state,idx,name):
 raw=(SOURCE_THINKING/'frames'/state/f'frame-{idx}.png') if state=='thinking' else (SOURCE_MAIN/'frames'/state/f'frame-{idx}.png')
 return hats.wear_hat(Image.open(raw),data[state][idx],hats.head_tilt(data[state],idx),hat_art[name])

SCRATCH.mkdir(parents=True); DEST.mkdir(parents=True)
lookup={}; duplicates=[]
for choice in ('beret','sunhat','ribbon_hat','scarf_blue','scarf_pink','cardigan','apron'):
 basedirs={}
 for kind,source in (('main',SOURCE_MAIN),('thinking',SOURCE_THINKING)):
  run=SCRATCH/choice/kind
  shutil.copytree(source,run)
  basedirs[kind]=run
 for state,meta in bank.states.items():
  kind='thinking' if state=='thinking' else 'main'; run=basedirs[kind]
  for idx,frame in enumerate(meta['frames']):
   key=digest(frame)
   if choice in ('beret','sunhat','ribbon_hat'): image=hat(state,idx,choice)
   else: image=draft.test(state,idx,{'scarf_blue':'scarf'}.get(choice,choice))
   image.save(run/'frames'/state/f'frame-{idx}.png')
  print(choice,state,flush=True)
 for kind,run in basedirs.items():
  subprocess.run([str(PYTHON),'-m','sprite_gen.cli','compose-atlas','--run-dir',str(run)],
                 check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180)
  shutil.copyfile(run/'sprite-sheet-alpha.png',DEST/f'{choice}-{kind}.png')
  if choice=='beret':
   manifest=json.loads((run/'manifest.json').read_text(encoding='utf8'))
   for state,meta in bank.states.items():
    if (state=='thinking') != (kind=='thinking'):continue
    for idx,frame in enumerate(meta['frames']):
     rect=manifest['frame_layout']['rows'][state][idx]; key=digest(frame)
     entry=[kind,rect['x'],rect['y'],rect['w'],rect['h']]
     if key in lookup and lookup[key]!=entry: duplicates.append([state,idx])
     else: lookup[key]=entry
(DEST/'lookup.json').write_text(json.dumps({'schema':1,'frames':lookup},ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf8')
print('DIGESTS',len(lookup),'DUPLICATES',duplicates)
