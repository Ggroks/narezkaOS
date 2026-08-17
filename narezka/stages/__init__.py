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
from narezka.stages.audiotags import AudioTagsStage
from narezka.stages.compilation import CompilationStage
from narezka.stages.episodes import EpisodesStage
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
    # Теги звука до отбора: смех работает признаком, музыка — штрафом,
    # и оба нужны уже на стадии оценки кандидатов.
    AudioTagsStage(),
    TimelineStage(),
    CandidatesStage(),
    LlmSelectStage(),
    # Эпизоды ищутся по расшифровке и не зависят от отбора моментов, но
    # стоят запросов к модели — поэтому после него, чтобы отказ провайдера
    # не оставил пайплайн без главного.
    EpisodesStage(),
    # После отбора: вебка ищется по тем отрезкам, что пойдут в ролики (§61).
    FacecamStage(),
    SubtitlesStage(),
    # Тексты пишутся до рендера: они зависят только от границ и объяснений
    # отбора, а не от готовых файлов. Так заголовки видны через минуты после
    # отбора, а не после получаса рендера — по ним видно, что за ролик, и
    # ненужное можно снять с очереди до траты времени на кодирование.
    MetadataStage(),
    RenderStage(),
    # Компиляция после шортсов: она тяжелее всего в пайплайне, и её отказ
    # не должен оставлять человека без готовых роликов.
    CompilationStage(),
]

REGISTRY: dict[str, Stage] = {stage.name: stage for stage in PIPELINE}

#: Куски работы, которые человек запускает по отдельности. Названия — для
#: сообщений; порядок — от того, что делается раньше.
GROUPS: dict[str, str] = {
    "analysis": "разбор записи",
    "shorts": "короткие ролики",
    "long": "длинная нарезка",
}


def stages_for(group: str) -> list[Stage]:
    """Стадии, которые нужно выполнить ради этого куска работы.

    Разбор записи входит в любой запуск, потому что и ролики, и нарезка
    делаются из его результатов. Уже посчитанное берётся из кэша за доли
    секунды, поэтому «лишними» эти стадии не бывают — зато сборка не может
    оказаться запущенной без того, на чём стоит.

    Обратное неверно: поиск эпизодов не попадает в сборку роликов, хотя
    в общем порядке стоит раньше неё. Он нужен только длинной нарезке
    и стоит запросов к модели — платить за него тем, кому он не нужен,
    было бы обманом.
    """
    if group not in GROUPS:
        known = ", ".join(GROUPS)
        raise KeyError(f"неизвестный кусок работы '{group}'. Доступны: {known}")
    wanted = {"analysis", group}
    return [stage for stage in PIPELINE if stage.group in wanted]


def get_stage(name: str) -> Stage:
    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(REGISTRY) or "(пусто)"
        raise KeyError(f"неизвестная стадия '{name}'. Доступны: {known}") from None


__all__ = ["GROUPS", "PIPELINE", "REGISTRY", "get_stage", "stages_for"]
