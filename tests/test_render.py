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


def test_pip_places_face_over_content(tmp_path):
    """Врезка: содержимое во весь экран, лицо окошком поверх.

    Отличие от сплита: там кадр делится надвое и обе части равноправны,
    здесь содержимое не теряет половину площади.
    """
    from narezka.core.framing import build_pip_filter

    framing = Framing(layout="pip")
    plan = plan_frame(1280, 720, 1080, 1920, framing)
    chain = build_pip_filter((768, 336, 512, 384), plan, framing, 1080, 1920, fps=30)

    assert "overlay=" in chain, "врезка накладывается, а не встраивается"
    assert chain.count("[0:v]") == 2, "две ветки: содержимое и лицо"
    assert chain.endswith("[v]")


def test_pip_is_skipped_for_full_frame_camera(tmp_path):
    """Камера во весь экран: врезать её саму в себя бессмысленно."""
    from narezka.core.framing import Framing as F
    from narezka.stages.render import RenderStage

    cams = {"0": {"x": 0, "y": 0, "width": 1280, "height": 720, "full_frame": True}}
    assert RenderStage._pip_for({"index": 0}, cams, F(layout="pip"), 1280, 720) is None


# --- раскладки вокруг вебки ------------------------------------------------

CAM = (768, 336, 512, 384)
FACE = (1059, 336, 110, 110)


def test_camera_layout_fills_the_frame_with_the_webcam():
    """«Только вебка»: содержимого не видно вовсе, кадр — один вырез.

    Нужна там, где ролик держится на реакции, а не на том, что на экране.
    """
    from narezka.core.framing import build_camera_filter, plan_camera

    crop = plan_camera(1280, 720, 1080, 1920, CAM, face=FACE)
    assert crop is not None
    x, y, w, h = crop
    assert abs(w / h - 1080 / 1920) < 0.02, "вырез не той пропорции — картинку растянет"
    assert 0 <= x and x + w <= 1280 and 0 <= y and y + h <= 720, "вырез вышел за кадр"

    chain = build_camera_filter(crop, 1080, 1920, fps=30)
    assert chain.count("[0:v]") == 1, "одна ветка: ни подложки, ни второй полосы"
    assert "fps=30,crop=" in chain, "прореживание раньше кропа"
    assert chain.endswith("[v]")


def test_camera_layout_needs_a_webcam():
    """Без найденной вебки раскладка неприменима, и это не ошибка."""
    from narezka.core.framing import Framing as F
    from narezka.stages.render import RenderStage

    class Short:
        width, height = 1080, 1920

    cams = {"0": {"x": 0, "y": 0, "width": 1280, "height": 720, "full_frame": True}}
    assert RenderStage._camera_for({"index": 0}, cams, F(layout="camera"), 1280, 720, Short()) is None
    assert RenderStage._camera_for({"index": 0}, {}, F(layout="camera"), 1280, 720, Short()) is None


def test_split_follows_the_head_when_asked():
    """Слежение двигает верхнюю полосу и оставляет нижнюю на месте.

    Стример за минуту уходит из статичной рамки — она берётся по одному
    кадру клипа. Нижняя полоса при этом дрожать не должна: это содержимое,
    и его движение вслед за головой читается как тряска камеры.
    """
    from narezka.core.framing import build_split_filter, plan_split

    plan = plan_split(1280, 720, 1080, 1920, CAM, face=FACE)
    assert plan is not None

    still = build_split_filter(plan, 1080)
    moving = build_split_filter(plan, 1080, cam_x="120+t*5")

    assert "crop=" in still and "'" not in still, "без слежения граница — число"
    assert "'120+t*5'" in moving, "выражение времени не попало в вырез вебки"
    # Нижняя полоса одинакова в обоих случаях.
    assert moving.split("[cam];")[1] == still.split("[cam];")[1]


def test_follow_face_needs_a_computed_track():
    """Без посчитанной траектории слежение не применяется.

    Траектория считается стадией facecam только когда раскладка её просит,
    и на записях, разобранных раньше, её нет.
    """
    from narezka.core.framing import Framing as F
    from narezka.stages.render import RenderStage

    cams = {"0": {"x": 768, "y": 336, "width": 512, "height": 384}}
    framing = F(layout="split", follow_face=True)
    assert RenderStage._follow_x({"index": 0}, cams, framing, CAM, 1280) is None

    cams["0"]["track"] = {"points": [[0.0, 900.0], [5.0, 1000.0]]}
    expression = RenderStage._follow_x({"index": 0}, cams, framing, CAM, 1280)
    assert expression and "t" in expression, "траектория есть, а выражения нет"

    # Не просили — не следим, даже когда траектория посчитана.
    assert RenderStage._follow_x({"index": 0}, cams, F(layout="split"), CAM, 1280) is None


def test_pip_window_obeys_size_and_corner():
    """Размер и угол врезки — настройка, а не константа в коде."""
    from narezka.core.framing import Framing as F, build_pip_filter

    plan = plan_frame(1280, 720, 1080, 1920, F(layout="pip"))
    small = build_pip_filter(CAM, plan, F(layout="pip", pip_share=0.2), 1080, 1920)
    big = build_pip_filter(CAM, plan, F(layout="pip", pip_share=0.5), 1080, 1920)

    def overlay(chain: str) -> tuple[int, int]:
        x, y = chain.split("overlay=")[1].split("[v]")[0].split(":")
        return int(x), int(y)

    assert "scale=216:" in small and "scale=540:" in big, "размер окошка не поменялся"

    corners = {
        corner: overlay(
            build_pip_filter(CAM, plan, F(layout="pip", pip_corner=corner), 1080, 1920)
        )
        for corner in ("top_left", "top_right", "bottom_left", "bottom_right")
    }
    assert len(set(corners.values())) == 4, "разные углы дали одно место"
    assert corners["top_left"] < corners["bottom_right"]
    # Врезка не должна вылезать за кадр ни в одном углу.
    for corner, (x, y) in corners.items():
        assert 0 <= x <= 1080 and 0 <= y <= 1920, corner


def test_facecam_computes_the_track_only_when_it_is_needed(tmp_path):
    """Траектория стоит прохода детектора по клипу — самой дорогой части.

    Но выбор раскладки со слежением обязан этот проход вызвать: пока ключ
    кэша про него не знал, настройка на разобранной записи не делала ничего.
    """
    from narezka.core import settings
    from narezka.core.config import load_config
    from narezka.core.device import DeviceInfo
    from narezka.core.logging import get_logger
    from narezka.core.paths import video_paths
    from narezka.core.stage import StageContext
    from narezka.stages.facecam import FacecamStage

    def ctx():
        paths = video_paths(tmp_path, "default", "v")
        paths.ensure()
        return StageContext(
            project_id="default", video_id="v", paths=paths, config=load_config(),
            device=DeviceInfo(kind="cpu", name="test"), log=get_logger("test"),
        )

    stage = FacecamStage()
    plain = stage.config_slice(ctx())
    assert plain["track"] is None and not stage._wants_track(ctx())

    settings.update(ctx().paths, {"layout": "split", "follow_face": True})
    assert stage._wants_track(ctx()), "слежение в сплите не просит траекторию"
    assert stage.config_slice(ctx()) != plain, "ключ кэша не поменялся — стадия не пересчитается"
