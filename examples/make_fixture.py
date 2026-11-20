"""Generate a synthetic OCR test card; this is not a real XHS note."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).parent
font_file = Path("C:/Windows/Fonts/msyh.ttc")
if not font_file.exists():
    font_file = Path("C:/Windows/Fonts/arial.ttf")
image = Image.new("RGB", (1300, 650), "white")
draw = ImageDraw.Draw(image)
font = ImageFont.truetype(str(font_file), 42)
small = ImageFont.truetype(str(font_file), 30)
draw.text((55, 40), "合成 OCR 测试图片 / Synthetic fixture", font=font, fill="black")
draw.text((55, 150), "银河土豆饼", font=font, fill="black")
draw.text((55, 245), "ORBITAL-TAPAS-42", font=font, fill="black")
draw.text((55, 340), "Example only. Do not save on maps.", font=small, fill="black")
draw.text((55, 430), "中文、English、Café、k8s", font=font, fill="black")
draw.text((55, 530), "这不是实际收藏，也不是实际店铺。", font=small, fill="black")
image.save(root / "ocr-card.png")

