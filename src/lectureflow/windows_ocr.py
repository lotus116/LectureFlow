"""Extract sparse/scanned PDF pages with the installed, offline Windows OCR engine."""

import asyncio

from . import documents
from .runtime import sha256


def extract_pdf(path, force=False):
    digest = sha256(path)
    pages = documents.extract(path)
    needed = [p for p in pages if force or len(p["text"].strip()) < 30]
    if needed:
        asyncio.run(_recognize(path, needed))
    return {"sha256": digest, "pages": pages}


async def _recognize(path, pages):
    import pypdfium2 as pdfium
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise ValueError("请在 Windows 语言设置中安装文字识别语言包")
    document = pdfium.PdfDocument(path)
    try:
        for item in pages:
            page = document[item["page"] - 1]
            try:
                scale = min(2, 2400 / max(page.get_size()))
                raster = page.render(scale=scale, grayscale=True)
                try:
                    img = raster.to_pil().convert("L")
                finally:
                    raster.close()
            finally:
                page.close()
            writer = DataWriter()
            bitmap = SoftwareBitmap(BitmapPixelFormat.GRAY8, img.width, img.height)
            try:
                writer.write_bytes(img.tobytes())
                bitmap.copy_from_buffer(writer.detach_buffer())
                result = await engine.recognize_async(bitmap)
                text = "\n".join(line.text for line in result.lines)
                if text.strip():
                    item.update(text=text, method="windows_ocr")
            finally:
                bitmap.close()
                writer.close()
    finally:
        document.close()
