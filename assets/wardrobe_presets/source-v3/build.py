"""Build four extra outfits from unchanged source atlases with Sprite-Gen 3.10.1.

Usage: <pinned-venv-python> build.py --scratch <fresh-dir> --output <fresh-dir>
Unpack and composition use Sprite-Gen; authored fitting uses art.py. The output
uses the existing curated-v2 lookup, verified frame by frame before publishing.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont
import numpy as np
import art


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(root/"Lib/site-packages"))
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage, QPixmap
    app = QApplication.instance() or QApplication([])
    if args.scratch.exists() or args.output.exists():
        raise SystemExit("Use fresh scratch and output paths; existing files are preserved.")
    if importlib.metadata.version("sprite-gen") != "3.10.1":
        raise SystemExit("This build requires the reviewed Sprite-Gen 3.10.1 environment.")
    args.scratch.mkdir(parents=True)
    args.output.mkdir(parents=True)
    lookup = json.loads((root/"assets/wardrobe_presets/curated-v2/lookup.json").read_text())["frames"]
    commands = []

    def sprite(*parts):
        command = [sys.executable,"-m","sprite_gen.cli",*map(str,parts)]
        commands.append(command)
        result = subprocess.run(command,check=True,capture_output=True,text=True,encoding="utf8",timeout=180)
        print(result.stdout[-500:],flush=True)

    sources = {}
    for kind, rel in (("main","assets"),("thinking","assets/thinking")):
        source = root/rel
        run = args.scratch/f"source-{kind}"
        sprite("unpack-atlas","--atlas",source/"sprite-sheet-alpha.png","--manifest",source/"manifest.json","--out-dir",run)
        sources[kind] = run
    art.configure(sources["main"]/"frames",sources["thinking"]/"frames")
    previews = []
    count = 0
    for choice in art.CHOICES:
        art.fallback(choice).save(args.output/f"{choice}.png")
        for kind, source in sources.items():
            run = args.scratch/choice/kind
            shutil.copytree(source,run)
            original_manifest = json.loads((root/("assets" if kind=="main" else "assets/thinking")/"manifest.json").read_text())
            expected = {}
            for state, rects in original_manifest["frame_layout"]["rows"].items():
                for index, _ in enumerate(rects):
                    image = art.render(state,index,choice)
                    image.save(run/"frames"/state/f"frame-{index}.png")
                    expected[state,index] = image
                    if state in ("idle","walk","wave","sleep","drag","thinking") and index==0:
                        previews.append((choice,state,image.copy()))
                    count += 1
            sprite("compose-atlas","--run-dir",run)
            atlas = Image.open(run/"sprite-sheet-alpha.png").convert("RGBA")
            for (state,index), image in expected.items():
                # Qt's premultiplied decode rounds antialiased RGB values. The
                # runtime lookup hashes that same Qt decode, not PIL's bytes.
                raw = QPixmap(str(source/"frames"/state/f"frame-{index}.png"))
                rgba = raw.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
                key = hashlib.sha256(bytes(rgba.constBits())).hexdigest()
                entry = lookup[key]
                entry_kind,x,y,w,h = entry
                expected_pixels = np.asarray(image)
                actual_pixels = np.asarray(atlas.crop((x,y,x+w,y+h)))
                visible = expected_pixels[:,:,3] > 0
                if (entry_kind!=kind or not np.array_equal(actual_pixels[:,:,3],expected_pixels[:,:,3])
                        or not np.array_equal(actual_pixels[visible],expected_pixels[visible])):
                    raise ValueError(f"Existing lookup mismatch: {choice}/{state}/{index}")
            shutil.copyfile(run/"sprite-sheet-alpha.png",args.output/f"{choice}-{kind}.png")
    font = ImageFont.truetype("C:/Windows/Fonts/malgun.ttf",16)
    board = Image.new("RGB",(6*170,4*205),(255,253,250))
    draw = ImageDraw.Draw(board)
    for i,(choice,state,image) in enumerate(previews):
        col,row = i%6,i//6
        board.paste(image.resize((160,160)),(col*170+5,row*205+38),image.resize((160,160)))
        draw.text((col*170+4,row*205+3),choice,font=font,fill=(45,42,55))
        draw.text((col*170+4,row*205+20),state,font=font,fill=(80,75,90))
    board.save(args.output/"contact-sheet.png")
    (args.output/"build-report.json").write_text(json.dumps({
        "sprite_gen":"3.10.1","frames_rendered":count,"commands":commands,
        "image_generation_used":False,"existing_lookup_verified":True,
        "source_sha256":{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (root/"assets/sprite-sheet-alpha.png",root/"assets/thinking/sprite-sheet-alpha.png")},
    },ensure_ascii=False,indent=2),encoding="utf8")
    print(f"Built {count} fitted frames; existing atlas lookup verified.",flush=True)


if __name__=="__main__":
    main()
