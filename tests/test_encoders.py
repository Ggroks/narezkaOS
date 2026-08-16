"""Выбор кодировщика: процессор или блок видеокарты."""

from pathlib import Path

import pytest

from narezka.core import encoders
from narezka.core.framing import Framing, plan_frame
from narezka.stages.render import RenderStage


def _args(encoder: str) -> list[str]:
    framing = Framing()
    return RenderStage()._command(
        source=Path("/s.mp4"), start=0.0, duration=10.0, subtitle_name=None,
        output=Path("/o.mp4"), has_video=True, framing=framing,
        plan=plan_frame(1280, 720, 1080, 1920, framing),
        width=1080, height=1920, crf=20, fps=30, encoder=encoder,
        pix_fmt="yuv420p", faststart=True, lufs=-14.0,
    )


def test_cpu_is_the_default_path():
    """По умолчанию качество: §53 ставит его выше скорости."""
    assert "libx264" in _args("cpu")
    assert not any("vaapi" in a for a in _args("cpu"))


@pytest.mark.skipif(encoders.available("gpu") is not None, reason="нет видеокарты")
def test_gpu_path_is_complete():
    """Аппаратный путь собирается целиком, а не наполовину.

    Нужны все три части: устройство до входного файла, загрузка кадров в
    память видеокарты последним фильтром и сам кодировщик. Без любой из них
    ffmpeg падает уже во время рендера, то есть после всей подготовки.
    """
    args = _args("gpu")
    assert "-vaapi_device" in args
    assert "h264_vaapi" in args
    chain = args[args.index("-filter_complex") + 1]
    assert chain.endswith("hwupload[v]"), "метка выхода должна остаться последней"


def test_unavailable_gpu_falls_back_honestly(monkeypatch):
    """Недоступная видеокарта откатывается на процессор, а не роняет рендер."""
    monkeypatch.setattr(encoders, "available", lambda e="cpu": "нет узла видеокарты")
    device, codec, tail = encoders.video_args("gpu", crf=20, pix_fmt="yuv420p")
    assert "libx264" in codec and device == [] and tail == ""


def test_both_sides_are_described():
    """У каждого варианта названы и плюсы, и минусы — выбор не вслепую."""
    for entry in encoders.describe_encoders():
        assert entry["pros"] and entry["cons"]
