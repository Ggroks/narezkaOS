"""Сквозной прогон пайплайна на синтетическом видео.

Модульные тесты проверяют части, но связку между ними — ничто: ровно там
и пряталась половина найденных ошибок (стадия читала не тот артефакт,
кэш не видел изменившийся вход, границы уезжали за край кадра).

Видео генерируется ffmpeg'ом, транскрипт подставляется готовым. Модель
распознавания сюда не тянется намеренно: она медленная, требует загрузки
весов и проверяет не связку, а сама себя.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from narezka.core.artifacts import Artifact
from narezka.core.config import load_config
from narezka.core.device import DeviceInfo
from narezka.core.logging import get_logger
from narezka.core.paths import video_paths
from narezka.core.runner import Outcome, run_stage
from narezka.core.stage import StageContext
from narezka.stages.candidates import CandidatesStage
from narezka.stages.extract_audio import ExtractAudioStage
from narezka.stages.probe import ProbeStage
from narezka.stages.render import RenderStage
from narezka.stages.subtitles import SubtitlesStage

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нужен ffmpeg")

VIDEO_ID = "e2etest00001"
DURATION = 24


def make_video(path: Path) -> None:
    """Ролик с меняющейся картинкой и звуком, где есть громкие всплески.

    Ровный тон не годится: устойчивая нормализация по медиане и MAD на нём
    не находит пиков, и стадия кандидатов честно возвращает пустоту.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration={DURATION}",
            # Громкость скачет: тихий фон и всплески на 6-й и 16-й секундах.
            "-f", "lavfi", "-i",
            f"sine=frequency=200:duration={DURATION},"
            f"volume='if(between(t,5,8)+between(t,15,18),1.0,0.05)':eval=frame",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True, capture_output=True,
    )


def fake_transcript(paths) -> None:
    """Транскрипт с речью по всей длительности и словами для субтитров."""
    segments = []
    for index in range(DURATION // 2):
        start = index * 2.0
        words = [
            {"word": слово, "start": start + i * 0.4, "end": start + i * 0.4 + 0.35,
             "probability": 0.9}
            for i, слово in enumerate(("сейчас", "будет", "интересный", "момент"))
        ]
        segments.append({
            "id": index, "start": start, "end": start + 1.8,
            "text": "Сейчас будет интересный момент",
            "avg_logprob": -0.2, "no_speech_prob": 0.02, "compression_ratio": 1.2,
            "words": words, "suspect": False,
        })
    Artifact(paths.transcript / "transcript.json").write_json({
        "language": "ru", "language_probability": 0.99, "duration_seconds": float(DURATION),
        "model": "test", "compute_type": "int8", "device": "cpu", "vad_filter": True,
        "segments": segments,
        "stats": {"segments_total": len(segments), "segments_suspect": 0,
                  "words_usable": sum(len(s["words"]) for s in segments)},
    })


@pytest.fixture
def context(tmp_path: Path):
    config = load_config().model_copy(update={"storage_root": tmp_path})
    # Клипы короткие: иначе кандидат не влезает в ограничения площадки
    # и стадия честно ничего не возвращает.
    config.output.short.min_duration = 4
    config.output.short.optimal_duration = 8
    config.output.short.max_duration = 12

    paths = video_paths(tmp_path, "default", VIDEO_ID)
    paths.ensure()
    make_video(paths.source / "source.mp4")

    return StageContext(
        project_id="default", video_id=VIDEO_ID, paths=paths, config=config,
        device=DeviceInfo(kind="cpu", name="test"), log=get_logger("e2e"),
    )


def run(stage, ctx, force=False):
    result = run_stage(stage, ctx, force=force)
    assert result.outcome is not Outcome.FAILED, f"{result.stage}: {result.reason}"
    return result


# --- сквозной прогон -------------------------------------------------------


def test_pipeline_produces_a_playable_short(context) -> None:
    """Главная проверка: из видео получается вертикальный ролик."""
    run(ProbeStage(), context)
    run(ExtractAudioStage(), context)
    fake_transcript(context.paths)
    run(CandidatesStage(), context)
    run(SubtitlesStage(), context)
    run(RenderStage(), context)

    index = Artifact(context.paths.shorts / "index.json").read_json()
    assert index["files"], "не отрендерено ни одного ролика"

    first = context.paths.shorts / index["files"][0]["file"]
    assert first.is_file() and first.stat().st_size > 10_000

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,pix_fmt", "-of", "csv=p=0", str(first)],
        capture_output=True, text=True, check=True,
    )
    # Вертикальный кадр и формат пикселей, который принимают все плееры (§37).
    assert probe.stdout.strip() == "1080,1920,yuv420p"


def test_second_run_uses_the_cache(context) -> None:
    """Повторный прогон не должен пересчитывать ничего: на восьмичасовой
    записи это разница между секундами и часами (§58)."""
    run(ProbeStage(), context)
    run(ExtractAudioStage(), context)
    fake_transcript(context.paths)
    run(CandidatesStage(), context)

    assert run(CandidatesStage(), context).outcome is Outcome.CACHED
    assert run(ProbeStage(), context).outcome is Outcome.CACHED


def test_changed_settings_invalidate_the_cache(context) -> None:
    """Изменил настройку — ролики обязаны пересобраться. Обратное однажды
    уже случилось: стадия брала из кэша тексты для других границ."""
    run(ProbeStage(), context)
    run(ExtractAudioStage(), context)
    fake_transcript(context.paths)
    run(CandidatesStage(), context)
    run(SubtitlesStage(), context)
    run(RenderStage(), context)

    Artifact(context.paths.framing).write_json({"preset": "fill"})
    assert run(RenderStage(), context).outcome is Outcome.DONE


def test_render_without_subtitles(context) -> None:
    """Каждая возможность отключается отдельно, и ролик остаётся собираемым."""
    run(ProbeStage(), context)
    run(ExtractAudioStage(), context)
    fake_transcript(context.paths)
    run(CandidatesStage(), context)
    run(SubtitlesStage(), context)

    Artifact(context.paths.framing).write_json(
        {"subtitles_enabled": False, "loudnorm_enabled": False}
    )
    run(RenderStage(), context)

    index = Artifact(context.paths.shorts / "index.json").read_json()
    assert index["files"]


def test_stage_writes_atomically(context) -> None:
    """После успеха не должно оставаться временных файлов (§58)."""
    run(ProbeStage(), context)
    run(ExtractAudioStage(), context)
    assert not list(context.paths.base.rglob("*.partial"))


def test_candidates_respect_duration_limits(context) -> None:
    run(ProbeStage(), context)
    run(ExtractAudioStage(), context)
    fake_transcript(context.paths)
    run(CandidatesStage(), context)

    short = context.config.output.short
    for clip in Artifact(context.paths.analysis / "candidates.json").read_json()["candidates"]:
        duration = clip["end"] - clip["start"]
        assert short.min_duration <= duration <= short.max_duration * 1.5


def test_subtitles_are_written_for_every_clip(context) -> None:
    run(ProbeStage(), context)
    run(ExtractAudioStage(), context)
    fake_transcript(context.paths)
    run(CandidatesStage(), context)
    run(SubtitlesStage(), context)

    candidates = Artifact(context.paths.analysis / "candidates.json").read_json()["candidates"]
    index = Artifact(context.paths.base / "subtitles" / "index.json").read_json()
    assert len(index["files"]) == len(candidates)
    for entry in index["files"]:
        text = (context.paths.base / "subtitles" / entry["file"]).read_text(encoding="utf-8")
        # Караоке-подсветка слова и кириллица на месте (§18, §60).
        assert "\\kf" in text and "интересный" in text
