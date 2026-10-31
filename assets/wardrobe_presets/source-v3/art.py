"""Authored garment layers, fitted to the original measured Syeon poses.

This development-only code extends source-v2's illustration technique. It
does not call a generation service. Only neutral body pixels receive cloth;
the original silhouette, face, leaf, wings, bag and feet remain unchanged.
"""
from pathlib import Path
import importlib.util
import math

import numpy as np
from PIL import Image, ImageDraw

SOURCE_V2 = Path(__file__).resolve().parents[1] / "source-v2"
spec = importlib.util.spec_from_file_location("syeon_body_v2", SOURCE_V2 / "body_art.py")
body_v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(body_v2)

CHOICES = ("cardigan_mint", "scarf_lavender", "sailor", "cape_cozy")
COLORS = {
    "cardigan_mint": (153, 210, 181),
    "scarf_lavender": (186, 165, 218),
    "sailor": (72, 102, 153),
    "cape_cozy": (224, 168, 138),
}


def configure(main, thinking):
    body_v2.ROOT, body_v2.THINKING = Path(main), Path(thinking)


def _layers(choice, x, y, side=False):
    shape = Image.new("L", (256, 256))
    detail = Image.new("RGBA", (256, 256))
    s, d = ImageDraw.Draw(shape), ImageDraw.Draw(detail)
    if choice == "sailor":
        # A short collar leaves the bag and the white belly readable at 150px.
        points = [(x-48,y-4), (x+48,y-4), (x+36,y+22), (x,y+38), (x-36,y+22)]
        s.polygon(points, fill=255)
        d.line([(x-39,y+4),(x-29,y+17),(x,y+29),(x+29,y+17),(x+39,y+4)],
               fill=(249,242,215,255), width=3)
        if not side:
            d.polygon([(x,y+18),(x-8,y+27),(x,y+37),(x+8,y+27)], fill=(243,208,121,255))
            d.line((x,y+35,x-5,y+45), fill=(243,208,121,255), width=4)
    else:
        s.polygon([(x-30,y-5),(x+30,y-5),(x+59,y+43),(x+32,y+54),
                   (x,y+58),(x-32,y+54),(x-59,y+43)], fill=255)
        d.line([(x-49,y+42),(x-28,y+49),(x,y+53),(x+28,y+49),(x+49,y+42)],
               fill=(169,113,91,220), width=2)
        d.pieslice((x-40,y-13,x+40,y+24),0,180,fill=(255,243,220,255))
        if not side:
            d.line((x,y+14,x,y+51),fill=(169,113,91,200),width=2)
            d.ellipse((x-3,y+18,x+3,y+24),fill=(250,215,137,255),outline=(159,111,75,255))
    return shape, detail


def render(state, index, choice):
    source = body_v2.THINKING if state == "thinking" else body_v2.ROOT
    image = Image.open(source / state / f"frame-{index}.png").convert("RGBA")
    original = np.asarray(image)
    if choice in ("cardigan_mint", "scarf_lavender"):
        old = "cardigan" if choice == "cardigan_mint" else "scarf"
        rendered = np.asarray(body_v2.test(state, index, old)).copy()
        changed = np.any(rendered != original, axis=2)
        # Color only the authored cloth, retaining cream collar and buttons.
        rgb = rendered[:, :, :3].astype(float)
        cloth = changed & (rgb[:, :, 2] > rgb[:, :, 0] + 8)
        old_color = np.array((165,213,229) if old == "cardigan" else (126,192,216))
        adjusted = rgb * (np.array(COLORS[choice]) / old_color)
        rendered[:, :, :3] = np.where(cloth[:, :, None], np.clip(adjusted,0,255), rgb).astype("uint8")
        return Image.fromarray(rendered)
    md = body_v2.DATA[state][index]
    cheeks = md["cheeks"][:2]
    angle = md.get("cheek_axis_degrees") or 0.0
    if state == "walk":
        cx, cutoff, angle = 124, 158, 0.0
    elif state in ("liesleep", "still"):
        if choice == "sailor":
            return image  # collar is occluded beneath the head
        cx, cutoff, angle = 201, 184, 0.0
    else:
        cutoff = sum(c["bbox"][3] for c in cheeks)/len(cheeks) if cheeks else 163
        cx = sum(c["center"][0] for c in cheeks)/len(cheeks) if cheeks else 128
        if state == "drag":
            cutoff += 5
    shape, detail = _layers(choice, round(cx), round(cutoff), state == "walk")
    shape = shape.rotate(-angle, Image.Resampling.BICUBIC, center=(cx,cutoff))
    detail = detail.rotate(-angle, Image.Resampling.BICUBIC, center=(cx,cutoff))
    yy, xx = np.indices((256,256))
    neckline = cutoff + 1 + math.tan(math.radians(angle)) * (xx-cx)
    rgb = original[:,:,:3]
    white = (original[:,:,3]>230)&(rgb.min(axis=2)>145)&((rgb.max(axis=2)-rgb.min(axis=2))<38)
    body = yy > neckline
    if state in ("liesleep","still"):
        bag = md["red"][0]["center"]
        body = (xx>bag[0]+6)&(yy>bag[1]-7)&(yy<240)
    mask = white & body & (np.asarray(shape)>10)
    result = original.copy()
    cloth = rgb * (np.array(COLORS[choice])/255)
    result[:,:,:3] = np.where(mask[:,:,None], .12*rgb+.88*cloth, rgb).astype("uint8")
    extra = np.asarray(detail)
    strength = extra[:,:,3].astype(float)/255*mask
    result[:,:,:3] = (result[:,:,:3]*(1-strength[:,:,None])+extra[:,:,:3]*strength[:,:,None]).astype("uint8")
    return Image.fromarray(result)


def fallback(choice):
    """Static garment art used by the existing unknown-frame anchor fallback."""
    if choice in ("cardigan_mint", "scarf_lavender"):
        old = "cardigan" if choice == "cardigan_mint" else "scarf_blue"
        image = Image.open(SOURCE_V2.parent / f"{old}.png").convert("RGBA")
        a = np.asarray(image).copy()
        rgb = a[:,:,:3].astype(float)
        cloth = (a[:,:,3]>0)&(rgb[:,:,2]>rgb[:,:,0]+8)
        old_color = np.array((165,213,229) if old=="cardigan" else (126,192,216))
        a[:,:,:3] = np.where(cloth[:,:,None],np.clip(rgb*(np.array(COLORS[choice])/old_color),0,255),rgb).astype("uint8")
        return Image.fromarray(a)
    shape, detail = _layers(choice, 128, 90)
    image = Image.new("RGBA", (256,256), (*COLORS[choice],255))
    image.putalpha(shape)
    image.alpha_composite(detail)
    return image
