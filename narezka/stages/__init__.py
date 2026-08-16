"""Реестр стадий и порядок пайплайна.

Новая стадия добавляется здесь — и становится доступна в CLI, в кэше
и в отчётах автоматически.
"""

from __future__ import annotations

from narezka.core.stage import Stage
from narezka.stages.candidates import CandidatesStage
from narezka.stages.chat import ChatStage
from narezka.stages.download import DownloadStage
from narezka.stages.extract_audio import ExtractAudioStage
from narezka.stages.facecam import FacecamStage
from narezka.stages.llm_select import LlmSelectStage
from narezka.stages.metadata import MetadataStage
from narezka.stages.timeline import TimelineStage
from narezka.stages.probe import ProbeStage
from narezka.stages.render import RenderStage
from narezka.stages.subtitles import SubtitlesStage
from narezka.stages.transcribe import TranscribeStage

#: Порядок соответствует BAZA.md §44. Наполняется по мере реализации этапов.
PIPELINE: list[Stage] = [
    DownloadStage(),
    ProbeStage(),
    ExtractAudioStage(),
    # Чат читается до отбора: он один из сигналов воронки (§11, §41).
    ChatStage(),
    TranscribeStage(),
    # Ось времени идёт до отбора: если паузы вырезаются, кандидаты должны
    # искаться уже по правленому времени, иначе их границы указывали бы
    # на места, которых в ролике нет.
    TimelineStage(),
    CandidatesStage(),
    LlmSelectStage(),
    # После отбора: вебка ищется по тем отрезкам, что пойдут в ролики (§61).
    FacecamStage(),
    SubtitlesStage(),
    # Тексты пишутся до рендера: они зависят только от границ и объяснений
    # отбора, а не от готовых файлов. Так заголовки видны через минуты после
    # отбора, а не после получаса рендера — по ним видно, что за ролик, и
    # ненужное можно снять с очереди до траты времени на кодирование.
    MetadataStage(),
    RenderStage(),
]

REGISTRY: dict[str, Stage] = {stage.name: stage for stage in PIPELINE}


def get_stage(name: str) -> Stage:
    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(REGISTRY) or "(пусто)"
        raise KeyError(f"неизвестная стадия '{name}'. Доступны: {known}") from None


__all__ = ["PIPELINE", "REGISTRY", "get_stage"]
