"""Local OCR extraction (tesseract). Runs on aws-01, on-fleet, before any
off-fleet call. This is stage (a) of the ingest pipeline (plan §4.2)."""
from __future__ import annotations

import io

import pytesseract
from PIL import Image


def extract_text(image_bytes: bytes) -> str:
    """Run local tesseract OCR over raw screenshot bytes.

    Never call anything network-facing from here - this function must stay
    fully offline so raw frame bytes never leave the process, let alone the
    fleet.
    """
    image = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(image)
