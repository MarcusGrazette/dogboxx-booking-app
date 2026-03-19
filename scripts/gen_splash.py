"""
Generate iOS PWA splash screen images for Dogboxx.

Each image is a solid #1B1B1B canvas (matching theme-color) with the
white-on-black logo centred at ~42% of canvas width.

Output: app/static/splash/splash-{name}.png
"""

import os
from PIL import Image

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(SCRIPT_DIR)
LOGO_PATH    = os.path.join(REPO_ROOT, 'app', 'static', 'logo-white-on-black.png')
OUT_DIR      = os.path.join(REPO_ROOT, 'app', 'static', 'splash')
BG_COLOR     = (27, 27, 27)       # #1B1B1B
LOGO_WIDTH_RATIO = 0.42           # logo takes up 42% of canvas width

# (name, canvas_w, canvas_h, media_query)
SIZES = [
    ('iphone-se',       750,  1334, '(device-width: 375px) and (device-height: 667px) and (-webkit-device-pixel-ratio: 2)'),
    ('iphone-8-plus',  1242,  2208, '(device-width: 414px) and (device-height: 736px) and (-webkit-device-pixel-ratio: 3)'),
    ('iphone-x',       1125,  2436, '(device-width: 375px) and (device-height: 812px) and (-webkit-device-pixel-ratio: 3)'),
    ('iphone-xr',       828,  1792, '(device-width: 414px) and (device-height: 896px) and (-webkit-device-pixel-ratio: 2)'),
    ('iphone-xs-max',  1242,  2688, '(device-width: 414px) and (device-height: 896px) and (-webkit-device-pixel-ratio: 3)'),
    ('iphone-12-mini', 1080,  2340, '(device-width: 360px) and (device-height: 780px) and (-webkit-device-pixel-ratio: 3)'),
    ('iphone-12',      1170,  2532, '(device-width: 390px) and (device-height: 844px) and (-webkit-device-pixel-ratio: 3)'),
    ('iphone-12-max',  1284,  2778, '(device-width: 428px) and (device-height: 926px) and (-webkit-device-pixel-ratio: 3)'),
    ('iphone-14-pro',  1179,  2556, '(device-width: 393px) and (device-height: 852px) and (-webkit-device-pixel-ratio: 3)'),
    ('iphone-14-max',  1290,  2796, '(device-width: 430px) and (device-height: 932px) and (-webkit-device-pixel-ratio: 3)'),
    ('iphone-16-pro',  1206,  2622, '(device-width: 402px) and (device-height: 874px) and (-webkit-device-pixel-ratio: 3)'),
    ('iphone-16-max',  1320,  2868, '(device-width: 440px) and (device-height: 956px) and (-webkit-device-pixel-ratio: 3)'),
    ('ipad',           1536,  2048, '(device-width: 768px) and (device-height: 1024px) and (-webkit-device-pixel-ratio: 2)'),
    ('ipad-pro-11',    1668,  2388, '(device-width: 834px) and (device-height: 1194px) and (-webkit-device-pixel-ratio: 2)'),
    ('ipad-pro-13',    2048,  2732, '(device-width: 1024px) and (device-height: 1366px) and (-webkit-device-pixel-ratio: 2)'),
]


def generate():
    os.makedirs(OUT_DIR, exist_ok=True)
    logo = Image.open(LOGO_PATH).convert('RGBA')
    logo_w, logo_h = logo.size
    logo_aspect = logo_h / logo_w

    meta_lines = []

    for name, cw, ch, media in SIZES:
        # Scale logo to LOGO_WIDTH_RATIO of canvas width, maintaining aspect ratio
        target_logo_w = int(cw * LOGO_WIDTH_RATIO)
        target_logo_h = int(target_logo_w * logo_aspect)

        scaled_logo = logo.resize((target_logo_w, target_logo_h), Image.LANCZOS)

        # Create background canvas
        canvas = Image.new('RGB', (cw, ch), BG_COLOR)

        # Paste logo centred — slightly above centre (visually better than exact centre)
        x = (cw - target_logo_w) // 2
        y = (ch - target_logo_h) // 2 - int(ch * 0.04)  # nudge 4% up

        canvas.paste(scaled_logo, (x, y), scaled_logo)  # use alpha channel as mask

        out_path = os.path.join(OUT_DIR, f'splash-{name}.png')
        canvas.save(out_path, 'PNG', optimize=True)
        print(f'  ✓  {name}  ({cw}×{ch})  →  {os.path.relpath(out_path, REPO_ROOT)}')

        meta_lines.append(
            f'    <link rel="apple-touch-startup-image" '
            f'href="{{{{ url_for(\'static\', filename=\'splash/splash-{name}.png\') }}}}" '
            f'media="{media}">'
        )

    print()
    print('── Paste into layout.html <head> ───────────────────────────────────')
    print('    <!-- iOS splash screens -->')
    for line in meta_lines:
        print(line)
    print('    <!-- end splash screens -->')


if __name__ == '__main__':
    generate()
