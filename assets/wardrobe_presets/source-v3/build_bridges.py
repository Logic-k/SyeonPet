"""Pose-fit existing clothes to canonical sleep/turn transition rows.

Requires the pinned Sprite-Gen 3.10.1 virtualenv. Unpack and compose are actual
Sprite-Gen stages; clothing is authored with the established source-v2/v3 art.
Only fresh scratch/output paths are written. Runtime assets publish separately
after native-size visual review. No original sprites or lookup are overwritten.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import art
from build_motions import components, wear_motion_hat

STATES = ("sleep_bridge", "turn_bridge")
BODY_COLORS = {
    "scarf_blue": (126,192,216), "scarf_pink": (237,173,192),
    "cardigan": (165,213,229), "apron": (245,220,159), **art.COLORS,
}


def preserve_body_foreground(raw, dressed):
    """Restore source coverage and non-cloth pixels after resampling.

    Bicubic interpolation can bleed cloth colour into adjacent bag/limb
    pixels. Only neutral body pixels may receive clothing, just as during
    the original pose fitting; alpha always comes from the canonical source.
    """
    source=np.asarray(raw)
    result=np.asarray(dressed).copy()
    rgb=source[:,:,:3].astype(int)
    cloth=(source[:,:,3]>230)&(rgb.min(axis=2)>145)&((rgb.max(axis=2)-rgb.min(axis=2))<38)
    result[~cloth]=source[~cloth]
    result[:,:,3]=source[:,:,3]
    return Image.fromarray(result)


def measure(image, state, index):
    a=np.asarray(image)
    r,g,b=(a[:,:,i].astype(int) for i in range(3))
    visible=a[:,:,3]>80
    leaf=components(visible&(g>r+18)&(g>b+18))[0]
    cheeks=[c for c in components(visible&(r>220)&(g>140)&(g<225)&(b>170)&(r>g+20)) if c['area']>=60][:2]
    if not cheeks:
        raise ValueError(f'Missing cheek anchor: {state}/{index}')
    cheeks.sort(key=lambda c:c['center'][0])
    angle=0.0
    if len(cheeks)==2:
        (x1,y1),(x2,y2)=(c['center'] for c in cheeks)
        angle=math.degrees(math.atan2(y2-y1,x2-x1))
    red=components(visible&(r>160)&(r>g+60)&(b<180))
    if not red:
        raise ValueError(f'Missing bag anchor: {state}/{index}')
    cutoff=sum(c['bbox'][3] for c in cheeks)/len(cheeks)
    # A turning face shifts to the right independently from the torso. The
    # clothing stays centred on the opaque body contour below the neck.
    centres=[]
    for y in range(max(0,round(cutoff)+10),min(256,round(cutoff)+21)):
        xs=np.where(a[y,:,3]>200)[0]
        if len(xs)>15:
            centres.append((int(xs.min())+int(xs.max()))/2)
    cx=float(np.median(centres)) if centres else float(np.mean([c['center'][0] for c in cheeks]))
    return {'leaf':leaf,'cheeks':cheeks,'cheek_axis_degrees':angle,'red':red,
            'cutoff':cutoff,'body_cx':cx,'side':state=='turn_bridge' and index>=2,
            'reclining':state=='sleep_bridge' and index>=3}


def body_frame(raw, md, choice):
    original=np.asarray(raw)
    scarf=choice.startswith('scarf')
    if md['reclining'] and (scarf or choice=='sailor'):
        return raw.copy()  # the neck is underneath the tilted head
    cx,cutoff=md['body_cx'],md['cutoff']
    angle=md['cheek_axis_degrees'] if not md['side'] else 0.0
    x,cy=round(cx),round(cutoff)
    shape=Image.new('L',(256,256)); detail=Image.new('RGBA',(256,256))
    s,d=ImageDraw.Draw(shape),ImageDraw.Draw(detail)
    yy,xx=np.indices((256,256))
    neckline=cutoff+1+math.tan(math.radians(angle))*(xx-cx)
    body=yy>neckline
    cardigan=choice in ('cardigan','cardigan_mint')
    if md['reclining']:
        # Only exposed back beyond the pouch receives cloth; face and the
        # foreground arms remain untouched even in the lowest sleeping pose.
        bx,by=md['red'][0]['center']
        body=(xx>bx+6)&(yy>by-7)&(yy<245)
        s.rectangle((round(bx),round(by)-10,255,245),fill=255)
    elif choice in ('sailor','cape_cozy'):
        shape,detail=art._layers(choice,x,cy,md['side'])
    elif cardigan:
        body &= yy < neckline+50-.004*(xx-cx)**2
        s.rounded_rectangle((x-50,cy-4,x+50,cy+75),radius=22,fill=255)
        ink=(86,143,113,185) if choice=='cardigan_mint' else (91,143,161,185)
        d.line([(x+u,round(cy+48-.004*u*u)) for u in range(-48,49)],fill=ink,width=2)
        if md['side']:
            d.arc((x-44,cy-5,x+43,cy+23),5,155,fill=ink,width=2)
        else:
            d.line((x,cy+15,x,cy+48),fill=ink,width=2)
            d.ellipse((x-2,cy+25,x+2,cy+29),fill=(250,220,118,255),outline=(133,114,87,220))
            for sign in (-1,1):
                d.polygon([(x+sign*27,cy+3),(x+sign*12,cy+17),(x,cy+8),(x,cy+3)],
                          fill=(246,252,251,255),outline=ink)
    elif scarf:
        s.ellipse((x-49,cy-9,x+49,cy+16),fill=255)
        s.rounded_rectangle((x-34,cy+9,x-18,cy+40),radius=6,fill=255)
        color=BODY_COLORS[choice]; ink=tuple(max(0,c-55) for c in color)+(200,)
        d.line((x-35,cy+12,x-19,cy+12),fill=ink,width=2)
        d.line((x-32,cy+32,x-20,cy+32),fill=ink,width=1)
    else:
        body &= yy < neckline+48-.006*(xx-cx)**2
        s.rounded_rectangle((x-32,cy+9,x+32,cy+70),radius=22,fill=255)
        d.line([(x+u,round(cy+46-.006*u*u)) for u in range(-30,31)],fill=(186,154,91,165),width=2)
        if not md['side']:
            d.rounded_rectangle((x-15,cy+36,x+15,cy+50),radius=5,outline=(186,154,91,185))
            for sign in (-1,1):
                d.line((x+sign*25,cy+12,x+sign*17,cy+26),fill=(186,154,91,170),width=2)
    if not md['reclining']:
        shape=shape.rotate(-angle,Image.Resampling.BICUBIC,center=(cx,cutoff))
        detail=detail.rotate(-angle,Image.Resampling.BICUBIC,center=(cx,cutoff))
    rgb=original[:,:,:3]
    neutral=(original[:,:,3]>230)&(rgb.min(axis=2)>145)&((rgb.max(axis=2)-rgb.min(axis=2))<38)
    mask=neutral&body&(np.asarray(shape)>10)
    result=original.copy()
    strength=.88 if choice in ('sailor','cape_cozy') else .8
    cloth=rgb*(np.array(BODY_COLORS[choice])/255)
    result[:,:,:3]=np.where(mask[:,:,None],rgb*(1-strength)+cloth*strength,rgb).astype('uint8')
    extra=np.asarray(detail); opacity=extra[:,:,3].astype(float)/255*mask
    result[:,:,:3]=(result[:,:,:3]*(1-opacity[:,:,None])+extra[:,:,:3]*opacity[:,:,None]).astype('uint8')
    return Image.fromarray(result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.scratch.exists() or args.output.exists():
        raise SystemExit('Choose fresh scratch and output folders.')
    if importlib.metadata.version('sprite-gen')!='3.10.1':
        raise SystemExit('Use reviewed Sprite-Gen 3.10.1 virtualenv.')
    root=Path(__file__).resolve().parents[3]
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    sys.path[:0]=[str(root),str(root/'Lib/site-packages')]
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage,QPixmap
    from wardrobe_presets import PRESETS
    app=QApplication.instance() or QApplication([])
    spec=importlib.util.spec_from_file_location('syeon_bridge_hats',Path(__file__).parents[1]/'source-v2/hats.py')
    hats=importlib.util.module_from_spec(spec); spec.loader.exec_module(hats)
    hat_art={choice:hats.draw_hat(choice) for choice in hats.HATS}
    curated=root/'assets/wardrobe_presets/curated-v2'
    existing_lookup=json.loads((curated/'lookup.json').read_text(encoding='utf8'))['frames']

    def existing_frame(choice, entry):
        kind,x,y,w,h=entry
        return Image.open(curated/f'{choice}-{kind}.png').convert('RGBA').crop((x,y,x+w,y+h))

    original_manifest=json.loads((root/'assets/manifest.json').read_text(encoding='utf8'))
    original_sheet=QPixmap(str(root/'assets/sprite-sheet-alpha.png'))
    lying_rect=original_manifest['frame_layout']['rows']['liesleep'][0]
    lying_qt=original_sheet.copy(*(lying_rect[k] for k in ('x','y','w','h'))).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    lying_hash=hashlib.sha256(bytes(lying_qt.constBits())).hexdigest()
    lying_entry=existing_lookup[lying_hash]
    def sleeping_endpoint(choice, raw):
        # Scale the complete dressed frame about the same ground anchor as
        # the source and runtime. Original-body bbox cropping would cut hats.
        scale=.82
        scaled=existing_frame(choice,lying_entry).transform((256,256),Image.Transform.AFFINE,
            (1/scale,0,128-128/scale,0,1/scale,245-245/scale),Image.Resampling.BICUBIC)
        return scaled if choice in hats.HATS else preserve_body_foreground(raw,scaled)
    args.scratch.mkdir(parents=True); args.output.mkdir(parents=True)
    commands=[]

    def sprite(*parts):
        command=[sys.executable,'-m','sprite_gen.cli',*map(str,parts)]
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf8',timeout=180)
        commands.append(command)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)

    sources={}; landmarks={}; source_hashes={}
    for state in STATES:
        source=root/'assets/motion_bridges'/state
        run=args.scratch/f'source-{state}'
        sprite('unpack-atlas','--atlas',source/'sprite-sheet-alpha.png','--manifest',source/'manifest.json','--out-dir',run)
        sources[state]=run
        landmarks[state]=[measure(Image.open(run/'frames'/state/f'frame-{i}.png').convert('RGBA'),state,i) for i in range(6)]
        source_manifest=json.loads((source/'manifest.json').read_text(encoding='utf8'))
        sheet=QPixmap(str(source/'sprite-sheet-alpha.png'))
        for i,rect in enumerate(source_manifest['frame_layout']['rows'][state]):
            im=sheet.copy(*(rect[k] for k in ('x','y','w','h'))).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            source_hashes[state,i]=hashlib.sha256(bytes(im.constBits())).hexdigest()
    (args.output/'landmarks.json').write_text(json.dumps(landmarks,indent=2),encoding='utf8')
    expected={}; jobs=[]
    for choice in PRESETS:
        for state,source in sources.items():
            run=args.scratch/choice/state; shutil.copytree(source,run)
            jobs.append((choice,state,run))
            for i in range(6):
                raw=Image.open(source/'frames'/state/f'frame-{i}.png').convert('RGBA')
                md=landmarks[state][i]
                key=source_hashes[state,i]
                if key in existing_lookup and existing_lookup[key][0] not in STATES:
                    frame=existing_frame(choice,existing_lookup[key])
                elif state=='sleep_bridge' and i==5:
                    frame=sleeping_endpoint(choice,raw)
                elif choice in hats.HATS:
                    frame=wear_motion_hat(hats,raw,md,max(-45,min(45,md['cheek_axis_degrees'])),hat_art[choice])
                else:
                    frame=body_frame(raw,md,choice)
                if choice not in hats.HATS:
                    a,b=np.asarray(raw),np.asarray(frame)
                    protected=((a[:,:,3]>230)&(a[:,:,:3].max(axis=2).astype(int)-a[:,:,:3].min(axis=2).astype(int)>=50))|(a[:,:,:3].max(axis=2)<90)
                    if not np.array_equal(a[:,:,3],b[:,:,3]) or not np.array_equal(a[protected],b[protected]):
                        raise ValueError(f'Garment damaged source foreground: {choice}/{state}/{i}')
                frame.save(run/'frames'/state/f'frame-{i}.png')
                expected[choice,state,i]=frame
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(sprite,'compose-atlas','--run-dir',run) for _,_,run in jobs]
        for future in futures: future.result()
    additions={}
    for choice,state,run in jobs:
        manifest=json.loads((run/'manifest.json').read_text(encoding='utf8'))
        atlas=Image.open(run/'sprite-sheet-alpha.png').convert('RGBA')
        for i,rect in enumerate(manifest['frame_layout']['rows'][state]):
            x,y,w,h=(rect[k] for k in ('x','y','w','h'))
            entry=[state,x,y,w,h]; key=source_hashes[state,i]
            if key not in existing_lookup:
                if key in additions and additions[key]!=entry: raise ValueError('Conflicting lookup coordinates.')
                additions[key]=entry
            frame=atlas.crop((x,y,x+w,y+h)); a=np.asarray(expected[choice,state,i]); b=np.asarray(frame)
            visible=a[:,:,3]>0
            if not np.array_equal(a[:,:,3],b[:,:,3]) or not np.array_equal(a[visible],b[visible]):
                raise ValueError(f'Compose changed visible pixels: {choice}/{state}/{i}')
            bbox=frame.getbbox()
            if bbox is None or min(bbox[:2])<1 or max(bbox[2:])>255: raise ValueError('Clipped frame.')
            if choice in hats.HATS:
                c=b.astype(int); green=(c[:,:,3]>30)&(c[:,:,1]>c[:,:,0]+18)&(c[:,:,1]>c[:,:,2]+18)
                if green.any(): raise ValueError(f'Leaf remains: {choice}/{state}/{i}')
        shutil.copyfile(run/'sprite-sheet-alpha.png',args.output/f'{choice}-{state}.png')
    (args.output/'lookup-additions.json').write_text(json.dumps(additions,sort_keys=True,indent=2),encoding='utf8')
    font=ImageFont.truetype('C:/Windows/Fonts/malgun.ttf',15)
    for state in STATES:
        board=Image.new('RGB',(6*180,len(PRESETS)*195),(249,247,243)); draw=ImageDraw.Draw(board)
        for row,choice in enumerate(PRESETS):
            for i in range(6):
                frame=expected[choice,state,i].resize((168,168),Image.Resampling.LANCZOS)
                x,y=i*180,row*195; board.paste(frame,(x+6,y+24),frame)
                draw.text((x+4,y+3),f'{PRESETS[choice][0]} {i+1}',font=font,fill=(45,40,45))
        board.save(args.output/f'contact-{state}.png')
    (args.output/'report.json').write_text(json.dumps({'frames':132,'atlases':22,'lookup_entries':len(additions),
        'sprite_gen':'3.10.1','visible_pixels_preserved':True,'body_alpha_and_foreground_preserved':True,
        'clipped_frames':0,'hats_leaf_pixels_remaining':0,'commands':commands},ensure_ascii=False,indent=2),encoding='utf8')
    print('READY',args.output,flush=True)


if __name__=='__main__':
    main()
