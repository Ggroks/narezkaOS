"""Сборка вызова ffmpeg на стадии рендера."""

from narezka.core.framing import Framing, plan_frame
from narezka.stages.render import RenderStage


def _args(tmp_path, **overrides):
    framing = Framing()
    plan = plan_frame(1280, 720, 1080, 1920, framing)
    kwargs = dict(
        source=tmp_path / "s.mp4", start=0.0, duration=10.0, subtitle_name=None,
        output=tmp_path / "o.mp4", has_video=True, framing=framing, plan=plan,
        width=1080, height=1920, crf=20, pix_fmt="yuv420p",
        faststart=True, lufs=-14.0,
    )
    kwargs.update(overrides)
    return RenderStage()._command(**kwargs)


def test_fps_decimation_is_first_in_chain(tmp_path):
    """Прореживание идёт первым фильтром, до масштабирования и размытия.

    Замер на записи 720p60: в начале цепочки 30 fps снимают треть времени
    рендера, а тем же ограничением на выходе (`-r`) прогон стал медленнее —
    вся тяжёлая обработка всё равно считалась по 60 кадрам.
    """
    chain = _args(tmp_path, fps=30)[_args(tmp_path, fps=30).index("-filter_complex") + 1]
    assert "fps=30" in chain
    head = chain[: chain.index("fps=30")]
    assert "scale" not in head and "blur" not in head, (
        "прореживание должно стоять раньше тяжёлых фильтров, иначе смысла нет"
    )


def test_fps_absent_keeps_source_rate(tmp_path):
    """Без настройки частота исходника не трогается — это осознанный выбор."""
    chain = _args(tmp_path, fps=None)
    assert not any("fps=" in a for a in chain)


def test_fast_seek_precedes_input(tmp_path):
    """`-ss` до `-i`: иначе ffmpeg декодирует запись от начала до нужного места."""
    args = _args(tmp_path, start=9000.0, fps=30)
    assert args.index("-ss") < args.index("-i")
