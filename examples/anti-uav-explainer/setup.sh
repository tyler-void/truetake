#!/usr/bin/env bash
# Set up the anti-UAV explainer: a project folder with the scripts, the DUT Anti-UAV tracking set, the public
# SiamFC weights and the fonts. Nothing is trained.
#
#   examples/anti-uav-explainer/setup.sh [PROJECT_DIR]      default: ./anti-uav-demo
#
# Downloads about 3.7 GB (the tracking set) plus PyTorch. Keep the project folder outside any repository you
# publish: the dataset is for academic research (see the authors' repository).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
PROJECT=${1:-./anti-uav-demo}
SIAMFC_COMMIT=39572051d7c3d9850ca6100efa04091e18c2f706

if [ -e "$PROJECT/data" ]; then
  echo "$PROJECT/data exists; choose another folder or remove it" >&2
  exit 1
fi
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }
mkdir -p "$PROJECT"/{data/raw,fonts,scout}
cp -r "$HERE"/tools "$HERE"/film "$HERE"/paper "$PROJECT"/
cd "$PROJECT"

echo "== the models' environment (PyTorch, torchvision, OpenCV)"
uv venv -q --python 3.11 tools/.venv
uv pip install -q --python tools/.venv/bin/python torch torchvision opencv-python-headless numpy pillow

echo "== SiamFC (huanglianghua/siamfc-pytorch, MIT) and its public weights"
git clone -q https://github.com/huanglianghua/siamfc-pytorch.git tools/siamfc-pytorch
git -C tools/siamfc-pytorch checkout -q "$SIAMFC_COMMIT"
mkdir -p tools/pretrained
uvx gdown -q 1UdxuBQ1qtisoWYFZxLgMFJ9mJtGVw6n4 -O tools/pretrained/siamfc_alexnet_e50.pth

echo "== DUT Anti-UAV tracking set (github.com/wangdongdut/DUT-Anti-UAV)"
uvx gdown -q 16PE3tBhT0lUGZLA8-zIRYvNUvxfhFZJq -O data/raw/Anti-UAV-Tracking-V0GT.zip
uvx gdown -q 1dlSPDggg6TRFMcC1jlYIJxxzUQS1mIh9 -O data/raw/Anti-UAV-Tracking-V0.zip
unzip -q data/raw/Anti-UAV-Tracking-V0GT.zip -d data/gt
unzip -q data/raw/Anti-UAV-Tracking-V0.zip -d data/images

echo "== fonts (Google Fonts, OFL), pinned to static weights"
G=https://github.com/google/fonts/raw/main/ofl
curl -sSL -o fonts/SpaceGrotesk-VF.ttf "$G/spacegrotesk/SpaceGrotesk%5Bwght%5D.ttf"
curl -sSL -o fonts/Inter-VF.ttf "$G/inter/Inter%5Bopsz,wght%5D.ttf"
curl -sSL -o fonts/JetBrainsMono-VF.ttf "$G/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf"
uvx --with fonttools python - <<'PY'
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
for src, dst, axes in [("SpaceGrotesk-VF", "SpaceGrotesk-Bold", {"wght": 700}),
                       ("Inter-VF", "Inter-Regular", {"wght": 400, "opsz": 14}),
                       ("Inter-VF", "Inter-SemiBold", {"wght": 600, "opsz": 14}),
                       ("JetBrainsMono-VF", "JetBrainsMono-Regular", {"wght": 400})]:
    font = TTFont(f"fonts/{src}.ttf")
    axes = {k: v for k, v in axes.items() if k in {a.axisTag for a in font["fvar"].axes}}
    instancer.instantiateVariableFont(font, axes).save(f"fonts/{dst}.ttf")
PY
rm fonts/*-VF.ttf

cat <<NEXT

Ready in $(pwd). Next (from this folder):
  tools/.venv/bin/python tools/scout.py data/images data/gt scout video05        # tracker alone vs. Algorithm 1
  # pick the handover frame from scout/video05.frames.json (source == "detector"), e.g. 41
  tools/.venv/bin/python tools/extract.py data/images data/gt film video05 41 41
  python film/film.py --cues                                                     # with truetake installed (film extra)
  truetake-score render film/cues.json -o film/score.wav --style arranged
  python film/film.py --score film/score.wav
  python -m truetake.flash film/anti-uav-explainer.mp4
NEXT
