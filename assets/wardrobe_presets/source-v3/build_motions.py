"""Fit all wardrobe presets to the two added rows using Sprite-Gen 3.10.1.

Run in the pinned Sprite-Gen venv with --scratch and --output fresh folders.
Publishes new atlases and lookup-additions.json, never overwrites lookup.json.
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


def components(mask):
    pending = set(zip(*np.where(mask)))
    result = []
    while pending:
        seed = pending.pop()
        seen = [seed]
        stack = [seed]
        while stack:
            y, x = stack.pop()
            for p in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
                if p in pending:
                    pending.remove(p)
                    seen.append(p)
                    stack.append(p)
        if len(seen) < 12:
            continue
        ys, xs = zip(*seen)
        result.append({'bbox':list(map(int,(min(xs),min(ys),max(xs),max(ys)))),
                       'area':len(seen),'center':[float(np.mean(xs)),float(np.mean(ys))]})
    return sorted(result, key=lambda c: -c['area'])


def measure(image):
    a = np.asarray(image)
    r,g,b = (a[:,:,i].astype(int) for i in range(3))
    visible = a[:,:,3] > 80
    leaf = components(visible & (g>r+18) & (g>b+18))[0]
    cheeks = components(visible & (r>220) & (g>140) & (g<225) & (b>170) & (r>g+20))[:2]
    if len(cheeks) != 2:
        raise ValueError('Expected two clearly visible pink cheeks.')
    cheeks.sort(key=lambda c: c['center'][0])
    (x1,y1),(x2,y2) = (c['center'] for c in cheeks)
    return {'leaf':leaf, 'cheeks':cheeks,
            'cheek_axis_degrees':math.degrees(math.atan2(y2-y1,x2-x1))}


def wear_motion_hat(hats, raw, md, angle, hat_art):
    """Seat a forward-bending leaf's replacement on the crown, not its tip."""
    a=np.asarray(raw).copy()
    leaf=hats._leaf_mask(a,md['leaf']['bbox'])
    neutral=(a[:,:,3]>200)&(a[:,:,:3].min(axis=2)>180)&~leaf
    head_y=int(np.where(neutral)[0].min())
    ly,lx=np.where(leaf)
    leaf_bottom=int(np.percentile(ly,97))
    if leaf_bottom-head_y<50:
        return hats.wear_hat(raw,md,angle,hat_art)
    # A deep bow pitches the leaf onto the forehead. The original generic
    # leaf-tip anchor would lower the hat across the face; clamp to the white
    # crown already drawn in this pose, preserving the source face and body.
    brim_y=head_y+27
    leaf_w=int(lx.max()-lx.min()+1)
    cx=float(np.median(lx))
    run=hats._head_run(a[:,:,3],brim_y,cx)
    width=leaf_w*1.25 if run is None else run[1]-run[0]+1
    if run is not None:
        cx=(run[0]+run[1])/2
    width=max(leaf_w*1.1,min(width,leaf_w*1.5))*1.06
    hat=hat_art.resize((round(width),round(hat_art.height*width/hat_art.width)),Image.Resampling.LANCZOS)
    layer=Image.new('RGBA',(256,256))
    layer.alpha_composite(hat,(round(cx-hat.width/2),round(brim_y-hat.height*.9)))
    layer=layer.rotate(-angle,Image.Resampling.BICUBIC,center=(cx,brim_y))
    restored=hats._remove_leaf(a,leaf)
    # The folded leaf is wider than the generic 70px fill search. Fill only
    # between the actually visible head pixels on either side of that row.
    # This closes removed-leaf holes without extending the head silhouette.
    solid=(a[:,:,3]>200)&~leaf
    for y in np.where(leaf.any(axis=1))[0]:
        xs=np.where(solid[y])[0]
        if len(xs)>1:
            fill=leaf[y].copy()
            fill[:xs.min()+1]=False
            fill[xs.max():]=False
            restored[y,fill]=hats.HEAD_WHITE
    # At the outer forehead the folded leaf itself replaces the head edge,
    # so no white sample exists to its right. Below the new brim it belongs
    # to the head: restore only already-opaque source coverage there.
    yy=np.indices(leaf.shape)[0]
    forehead=leaf&(a[:,:,3]>200)&(yy>=brim_y-8)
    restored[forehead]=hats.HEAD_WHITE
    result=Image.fromarray(restored)
    result.alpha_composite(layer)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.scratch.exists() or args.output.exists():
        raise SystemExit('Choose fresh output and scratch paths.')
    if importlib.metadata.version('sprite-gen') != '3.10.1':
        raise SystemExit('Requires Sprite-Gen 3.10.1.')
    root=Path(__file__).resolve().parents[3]
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    sys.path[:0]=[str(root),str(root/'Lib/site-packages')]
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage
    from pet import SpriteBank
    from wardrobe_presets import PRESETS
    app=QApplication.instance() or QApplication([])
    bank=SpriteBank(root/'assets')
    spec=importlib.util.spec_from_file_location('syeon_motion_hats',Path(__file__).parents[1]/'source-v2/hats.py')
    hats=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hats)
    hat_art={choice:hats.draw_hat(choice) for choice in hats.HATS}
    args.scratch.mkdir(parents=True)
    args.output.mkdir(parents=True)
    commands=[]

    def sprite(*parts):
        command=[sys.executable,'-m','sprite_gen.cli',*map(str,parts)]
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf8',timeout=180)
        commands.append(command)
        result.check_returncode()
        return result

    sources={}
    landmarks={}
    for state in ('stretch','bow'):
        source=root/'assets/extra_motions'/state
        run=args.scratch/f'source-{state}'
        sprite('unpack-atlas','--atlas',source/'sprite-sheet-alpha.png',
               '--manifest',source/'manifest.json','--out-dir',run)
        sources[state]=run
        landmarks[state]=[measure(Image.open(run/'frames'/state/f'frame-{i}.png').convert('RGBA')) for i in range(6)]
    art.body_v2.DATA.update(landmarks)
    (args.output/'landmarks.json').write_text(json.dumps(landmarks,indent=2),encoding='utf8')
    rows=[]
    expected={}
    for choice in PRESETS:
        for state,source in sources.items():
            run=args.scratch/choice/state
            shutil.copytree(source,run)
            art.configure(source/'frames',source/'frames')
            for i in range(6):
                raw=Image.open(source/'frames'/state/f'frame-{i}.png').convert('RGBA')
                if choice in hats.HATS:
                    frame=wear_motion_hat(hats,raw,landmarks[state][i],hats.head_tilt(landmarks[state],i),hat_art[choice])
                elif choice in art.CHOICES:
                    frame=art.render(state,i,choice)
                else:
                    frame=art.body_v2.test(state,i,{'scarf_blue':'scarf'}.get(choice,choice))
                frame.save(run/'frames'/state/f'frame-{i}.png')
                expected[choice,state,i]=frame.copy()
                rows.append((choice,state,i,frame.copy()))
            print('DRAW',choice,state,flush=True)
    jobs=[(choice,state,args.scratch/choice/state) for choice in PRESETS for state in sources]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(sprite,'compose-atlas','--run-dir',run) for _,_,run in jobs]
        for future in futures:
            future.result()
    additions={}
    for choice,state,run in jobs:
        manifest=json.loads((run/'manifest.json').read_text(encoding='utf8'))
        atlas=Image.open(run/'sprite-sheet-alpha.png').convert('RGBA')
        for i,rect in enumerate(manifest['frame_layout']['rows'][state]):
            x,y,w,h=(rect[k] for k in ('x','y','w','h'))
            im=bank.states[state]['frames'][i].toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            key=hashlib.sha256(bytes(im.constBits())).hexdigest()
            entry=[state,x,y,w,h]
            if key in additions and additions[key]!=entry:
                raise ValueError('Conflicting frame coordinates.')
            additions[key]=entry
            frame=atlas.crop((x,y,x+w,y+h))
            expected_a=np.asarray(expected[choice,state,i]); actual=np.asarray(frame)
            visible=expected_a[:,:,3]>0
            if not np.array_equal(expected_a[:,:,3],actual[:,:,3]) or not np.array_equal(expected_a[visible],actual[visible]):
                raise ValueError(f'Compose changed visible pixels: {choice}/{state}/{i}')
            bbox=frame.getbbox()
            if bbox is None or min(bbox[:2])<1 or max(bbox[2:])>255:
                raise ValueError(f'Clipped frame: {choice}/{state}/{i}')
            if choice in hats.HATS:
                a=actual.astype(int)
                green=(a[:,:,3]>30)&(a[:,:,1]>a[:,:,0]+18)&(a[:,:,1]>a[:,:,2]+18)
                if green.any():
                    raise ValueError(f'Visible leaf remains: {choice}/{state}/{i}')
        shutil.copyfile(run/'sprite-sheet-alpha.png',args.output/f'{choice}-{state}.png')
    (args.output/'lookup-additions.json').write_text(json.dumps(additions,sort_keys=True,indent=2),encoding='utf8')
    font=ImageFont.truetype('C:/Windows/Fonts/malgun.ttf',15)
    for state in sources:
        board=Image.new('RGB',(6*175,11*190),(250,248,244)); draw=ImageDraw.Draw(board)
        for row,choice in enumerate(PRESETS):
            for i in range(6):
                frame=expected[choice,state,i].resize((164,164),Image.Resampling.LANCZOS)
                x,y=i*175,row*190
                board.paste(frame,(x+5,y+22),frame)
                draw.text((x+4,y+2),f'{PRESETS[choice][0]} {i+1}',font=font,fill=(50,45,50))
        board.save(args.output/f'contact-{state}.png')
    report={'frames':132,'atlases':22,'lookup_entries':len(additions),'sprite_gen':'3.10.1',
            'compose_visible_pixels_unchanged':True,'hats_leaf_pixels_remaining':0,
            'clipped_frames':0,'commands':commands}
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('READY',args.output,flush=True)


if __name__=='__main__':
    main()
