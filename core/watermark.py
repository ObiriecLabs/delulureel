"""
Watermark server-side per output BYOC test.
Applicato DOPO che ComfyUI genera — il client non riceve mai l'originale.
"""
import io
import os
import subprocess
import tempfile

from PIL import Image, ImageDraw

_TEXT   = "DELULUREEL TEST"
_FOOTER = "delulureel.com — solo per test"


def _font(size: int):
    from PIL import ImageFont
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def watermark_image(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = img.size

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    fsize   = max(18, min(w, h) // 14)
    font    = _font(fsize)

    bbox = draw.textbbox((0, 0), _TEXT, font=font)
    tw   = bbox[2] - bbox[0]
    th   = bbox[3] - bbox[1]
    step_x = tw + 40
    step_y = th + 40

    # Diagonale ripetuta
    for row, y0 in enumerate(range(-h, h * 2, step_y)):
        x_off = (row % 2) * (step_x // 2)
        for x0 in range(-w + x_off, w * 2, step_x):
            draw.text((x0, y0), _TEXT, font=font, fill=(255, 255, 255, 65))

    watermarked = Image.alpha_composite(img, overlay)

    # Barra inferiore solida
    bar = max(28, h // 14)
    d2  = ImageDraw.Draw(watermarked)
    d2.rectangle([(0, h - bar), (w, h)], fill=(0, 0, 0, 200))
    d2.text((10, h - bar + 4), _FOOTER, font=_font(bar - 8), fill=(255, 255, 255, 240))

    out = io.BytesIO()
    watermarked.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def watermark_video(data: bytes) -> bytes:
    ffmpeg = os.environ.get("FFMPEG_PATH", "/usr/bin/ffmpeg")
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(data)
        in_path = tmp.name
    out_path = in_path.replace(".mp4", "_wm.mp4")
    try:
        vf = (
            "drawtext=text='DELULUREEL TEST':"
            "fontsize=36:fontcolor=white@0.55:"
            "x=(w-text_w)/2:y=(h-text_h)/2:"
            "box=1:boxcolor=black@0.35:boxborderw=8,"
            "drawtext=text='delulureel.com — solo per test':"
            "fontsize=18:fontcolor=white@0.8:"
            "x=10:y=h-th-10:box=1:boxcolor=black@0.5:boxborderw=4"
        )
        subprocess.run(
            [ffmpeg, "-y", "-i", in_path, "-vf", vf,
             "-c:v", "libx264", "-preset", "fast", "-crf", "28",
             "-c:a", "copy", out_path],
            check=True, capture_output=True, timeout=120,
        )
        with open(out_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(in_path)
        if os.path.exists(out_path):
            os.unlink(out_path)
