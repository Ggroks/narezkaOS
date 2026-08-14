"""Реестр стадий и порядок пайплайна.

Новая стадия добавляется здесь — и становится доступна в CLI, в кэше
и в отчётах автоматически.
"""

from __future__ import annotations

from narezka.core.stage import Stage
from narezka.stages.candidates import CandidatesStage
from narezka.stages.download import DownloadStage
from narezka.stages.extract_audio import ExtractAudioStage
from narezka.stages.llm_select import LlmSelectStage
from narezka.stages.metadata import MetadataStage
from narezka.stages.probe import ProbeStage
from narezka.stages.render import RenderStage
from narezka.stages.subtitles import SubtitlesStage
from narezka.stages.transcribe import TranscribeStage

#: Порядок соответствует BAZA.md §44. Наполняется по мере реализации этапов.
PIPELINE: list[Stage] = [
    DownloadStage(),
    ProbeStage(),
    ExtractAudioStage(),
    TranscribeStage(),
    CandidatesStage(),
    LlmSelectStage(),
    SubtitlesStage(),
    RenderStage(),
    MetadataStage(),
]

REGISTRY: dict[str, Stage] = {stage.name: stage for stage in PIPELINE}


def get_stage(name: str) -> Stage:
    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(REGISTRY) or "(пусто)"
        raise KeyError(f"неизвестная стадия '{name}'. Доступны: {known}") from None


__all__ = ["PIPELINE", "REGISTRY", "get_stage"]
