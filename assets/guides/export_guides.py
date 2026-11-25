"""Cut the HyperFrames master into small GIFs, pausable MP4s and step stills."""
import argparse
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--ffmpeg', required=True)
parser.add_argument('--source', type=Path, default=ROOT / 'var/guides-master.mp4')
args = parser.parse_args()

def run(*parts):
    subprocess.run([args.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', *map(str, parts)], check=True)

(HERE / 'stills').mkdir(exist_ok=True)
segments = json.loads((HERE / 'segments.json').read_text(encoding='utf-8'))
for segment in segments:
    name = segment['name']
    # Start after the title entrance, so the looping GIF has a readable first frame.
    start, duration = segment['start'] + .6, segment['duration'] - .6
    run('-ss', start, '-t', duration, '-i', args.source,
        '-vf', 'fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle',
        '-loop', 0, HERE / f'{name}.gif')
    run('-ss', start, '-t', duration, '-i', args.source,
        '-an', '-c:v', 'libx264', '-crf', 19, '-pix_fmt', 'yuv420p', '-movflags', '+faststart', HERE / f'{name}.mp4')
    for i, time in enumerate(segment['hero_times'], 1):
        run('-ss', time, '-i', args.source, '-frames:v', 1, '-vf', 'scale=960:-1:flags=lanczos', HERE / 'stills' / f'{name}-{i}.png')
    print(f'Exported {name}: {duration:g}s GIF + MP4 + 3 stills', flush=True)
