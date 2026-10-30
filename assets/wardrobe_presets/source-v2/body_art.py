from pathlib import Path
import json, math
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT=None
THINKING=None
DATA=json.loads((Path(__file__).parent/'landmarks.json').read_text(encoding='utf8'))

def test(state,idx,choice):
 if ROOT is None or THINKING is None:
  raise ValueError('Configure both Sprite-Gen unpack frame folders first.')
 im=Image.open(ROOT/state/f'frame-{idx}.png').convert('RGBA') if state!='thinking' else Image.open(THINKING/state/f'frame-{idx}.png').convert('RGBA')
 is_scarf=choice in ('scarf','scarf_pink')
 a=np.asarray(im).copy(); orig=a.copy(); md=DATA[state][idx]; cheeks=sorted(md['cheeks'][:2],key=lambda c:c['center'][0]);
 if state=='walk':
  cutoff=158; slope=0.;cx=124; bottom=226
 elif state in ('liesleep','still'):
  cutoff=190; slope=0.;cx=190; bottom=235
 else:
  cutoff=sum(c['bbox'][3] for c in cheeks)/len(cheeks) if cheeks else 163
  slope=math.tan(math.radians(md.get('cheek_axis_degrees') or 0.));cx=sum(c['center'][0] for c in cheeks)/len(cheeks) if cheeks else 128; bottom=226
 if state=='drag': cutoff+=5
 yy,xx=np.indices((256,256)); neckline=cutoff+1+slope*(xx-cx)
 white=(a[:,:,3]>230)&(a[:,:,:3].min(axis=2)>145)&((a[:,:,:3].max(axis=2)-a[:,:,:3].min(axis=2))<38)
 shape=Image.new('L',(256,256)); sd=ImageDraw.Draw(shape)
 detail=Image.new('RGBA',(256,256)); dd=ImageDraw.Draw(detail)
 cy=round(cutoff)
 if state in ('liesleep','still'):
  body=(xx>md['red'][0]['center'][0]+6)&(yy>md['red'][0]['center'][1]-7)&(yy<240)
  if is_scarf: return im
  sd.ellipse((170,155,238,244),fill=255)
 else:
  body=(yy>neckline)&(xx>cx-68)&(xx<cx+68)
  if choice=='cardigan':
   body &= yy < neckline + 50 - .004*(xx-cx)**2
  elif choice=='apron':
   body &= yy < neckline + 48 - .006*(xx-cx)**2
  x=round(cx)
  if choice=='cardigan':
   sd.rounded_rectangle((x-50,cy-4,x+50,cy+75),radius=22,fill=255)
   dd.line([(x+u, round(cy+48-.004*u*u)) for u in range(-48,49)],
           fill=(91,143,161,155),width=2)
   if state!='walk':
    dd.line([(x,cy+15),(x,cy+48)], fill=(90,145,164,180),width=2)
    for y in (cy+27,): dd.ellipse((x-2,y-2,x+2,y+2),fill=(250,220,118,255),outline=(133,114,87,220),width=1)
    dd.polygon([(x-27,cy+3),(x-12,cy+17),(x,cy+8),(x,cy+3)],fill=(246,252,251,255),outline=(91,143,161,190),width=1)
    dd.polygon([(x+27,cy+3),(x+12,cy+17),(x,cy+8),(x,cy+3)],fill=(246,252,251,255),outline=(91,143,161,190),width=1)
   else:
    dd.arc((x-44,cy-5,x+43,cy+23),5,155,fill=(91,143,161,180),width=2)
  elif is_scarf:
   sd.ellipse((x-49,cy-9,x+49,cy+16),fill=255)
   sd.rounded_rectangle((x-34,cy+9,x-18,cy+40),radius=6,fill=255)
   accent=(178,104,135) if choice=='scarf_pink' else (69,145,170)
   dd.line((x-35,cy+12,x-19,cy+12),fill=(*accent,200),width=2)
   dd.line((x-32,cy+32,x-20,cy+32),fill=(*accent,190),width=1)
  else:
   sd.rounded_rectangle((x-32,cy+9,x+32,cy+70),radius=22,fill=255)
   dd.line([(x+u, round(cy+46-.006*u*u)) for u in range(-30,31)],
           fill=(186,154,91,165),width=2)
   if state!='walk':
    dd.rounded_rectangle((x-15,cy+36,x+15,cy+50),radius=5,outline=(186,154,91,185),width=1)
    dd.line((x-25,cy+12,x-17,cy+26),fill=(186,154,91,170),width=2)
    dd.line((x+25,cy+12,x+17,cy+26),fill=(186,154,91,170),width=2)
  angle=md.get('cheek_axis_degrees') or 0
  shape=shape.rotate(-angle,resample=Image.Resampling.BICUBIC,center=(cx,cutoff))
  detail=detail.rotate(-angle,resample=Image.Resampling.BICUBIC,center=(cx,cutoff))
 rgb={'cardigan':np.array((165,213,229)),'scarf':np.array((126,192,216)), 'scarf_pink':np.array((237,173,192)), 'apron':np.array((245,220,159))}[choice]
 mask=white&body&(np.asarray(shape)>10)
 for c in range(3):
  target=rgb[c]*(a[:,:,c]/255.0)
  a[:,:,c]=np.where(mask,a[:,:,c]*.20+target*.80,a[:,:,c]).astype('uint8')
 detail_arr=np.asarray(detail); strength=(detail_arr[:,:,3].astype(float)/255)*mask
 for c in range(3): a[:,:,c]=(a[:,:,c]*(1-strength)+detail_arr[:,:,c]*strength).astype('uint8')
 return Image.fromarray(a)
