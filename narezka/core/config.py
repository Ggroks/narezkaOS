"""Конфигурация: базовые значения, профили исполнения, наложение переопределений.

Соответствует BAZA.md §46 и §62. Профиль — это пресет «скорость против качества»,
а не описание конкретного железа: оба профиля запускаются на любой машине,
отличается только время выполнения.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

Profile = Literal["auto", "dev", "batch"]
DeviceSetting = Literal["auto", "cpu", "cuda"]


class DownloadConfig(BaseModel):
    #: Ограничение высоты кадра. 8 часов 1080p60 это 15–30 ГБ (§65),
    #: и для анализа больше 1080p не нужно.
    max_height: int = 1080
    format: str | None = None  # переопределяет max_height, если задан
    merge_format: str = "mp4"


class AudioConfig(BaseModel):
    #: 16 кГц моно — то, что ожидают модели распознавания.
    sample_rate: int = 16000
    channels: int = 1


class SttConfig(BaseModel):
    model: str = "large-v3"
    compute_type: str = "auto"
    language: str = "auto"
    batch_size: int = 8
    #: Отсев галлюцинаций (§57). Пороги подобраны консервативно:
    #: лучше пометить лишнее, чем пропустить выдуманный текст в отбор.
    vad_filter: bool = True
    max_no_speech_prob: float = 0.6
    min_avg_logprob: float = -1.0
    max_repeat_ratio: float = 0.5


class LlmConfig(BaseModel):
    provider: str = "openrouter"
    #: Имя модели живёт в конфиге, а не в коде: список бесплатных моделей
    #: у провайдера меняется, и захардкоженное имя протухает (§49).
    #: Актуальные варианты показывает `narezka models`.
    model: str | None = None
    #: Куда переключаться, если основная модель исчезла или упёрлась в лимит.
    #: У бесплатных моделей это обычное дело, а не исключение.
    fallback_models: list[str] = Field(default_factory=list)
    batch_mode: bool = True
    cache_prefix: bool = True
    timeout_seconds: float = Field(default=120.0, gt=0)
    #: Повторов на модель до перехода к запасной. Немного намеренно: при
    #: занятом общем пуле смена модели быстрее, чем ожидание очереди.
    max_retries: int = Field(default=2, ge=0)


class DetectorConfig(BaseModel):
    backend: Literal["mediapipe", "yolox", "ultralytics"] = "mediapipe"


class FramingConfig(BaseModel):
    #: single — исходник целиком на подложке; split — лицо стримера сверху,
    #: приближённый контент снизу (§61). Сплит применяется только там, где
    #: стадия facecam нашла вебку наложением; иначе кадрируется как обычно.
    layout: Literal["single", "split"] = "single"

    """Как исходный кадр вписывается в вертикальный (§17, §61).

    Обрезка по бокам и высота содержимого — один параметр: чем уже кадр
    после обрезки, тем выше он выглядит при вписывании по ширине.
    """

    #: full — ничего не терять; balanced — 25% по бокам; focus — 50%;
    #: fill — заполнить кадр целиком; custom — значение из side_crop.
    preset: Literal["full", "balanced", "focus", "fill", "custom"] = "balanced"
    side_crop: float = Field(default=0.25, ge=0.0, le=0.95)
    #: Какую часть кадра оставлять при обрезке. auto появится вместе
    #: с детекцией содержимого на этапе 4.
    anchor: Literal["center", "left", "right"] = "center"
    #: Чем заполняются полосы, когда содержимое не заполняет кадр.
    background: Literal["blur", "color"] = "blur"
    #: 0 отключает размытие — подложка остаётся, но резкая.
    blur_sigma: float = Field(default=28.0, ge=0.0, le=200.0)
    color: str = "0x14171c"


class ShortOutput(BaseModel):
    width: int = 1080
    height: int = 1920
    min_duration: float = 15.0
    optimal_duration: float = 60.0
    max_duration: float = 90.0


class OutputConfig(BaseModel):
    short: ShortOutput = Field(default_factory=ShortOutput)
    framing: FramingConfig = Field(default_factory=FramingConfig)
    #: Что вшивать в готовый ролик. Каждый пункт отключается отдельно:
    #: нарезка нужна и без субтитров (например, под свой монтаж), а
    #: нормализация громкости мешает, если звук уже сведён.
    subtitles_enabled: bool = True
    loudnorm_enabled: bool = True
    loudness_target_lufs: float = -14.0
    #: Кадров в секунду на выходе. Замер на записи 720p60: ограничение 30-ю
    #: снимает 36% времени рендера и 11% веса файла, а вертикальные ролики
    #: платформы и так показывают в 30. None — оставить как в исходнике.
    fps: int | None = 30
    crf: int = 20
    pix_fmt: str = "yuv420p"
    faststart: bool = True


class SubtitlesConfig(BaseModel):
    #: Пресет оформления (§18). Пользовательские стили — в backlog.
    style: str = "STYLE_1"


class CandidatesConfig(BaseModel):
    """Первый проход воронки (§11): дешёвые сигналы по всему материалу."""

    window_seconds: float = 1.0
    #: Вклад сигналов в предварительную оценку. Вес чата заметный: это прямое
    #: свидетельство реакции зрителей, независимое от громкости (§41).
    #: Когда чата нет, его вес перераспределяется между остальными.
    loudness_weight: float = 0.45
    density_weight: float = 0.25
    chat_weight: float = 0.30
    #: Окно реакции чата, считается **вперёд** от момента. Замер на записи
    #: стрима: реакция это не сдвиг на пару секунд, а плато длиной 15–20 с,
    #: начинающееся сразу от события. Симметричное сглаживание размазывало бы
    #: всплеск назад, и кандидат начинался бы раньше события.
    chat_lead_seconds: float = 12.0
    #: Считать ли сообщения ботов. По умолчанию нет: они идут по расписанию.
    chat_include_bots: bool = False
    #: Не доверять чату в начале записи. Зрители здороваются на старте
    #: трансляции, а не в ответ на происходящее, и всплеск приходится
    #: на пустое место. Отключается: у кого-то начало и есть содержание.
    chat_ignore_start: bool = True
    #: Три минуты, а не одна: замер на записи показал, что приветствие звучит
    #: на третьей минуте — ведущий сначала возится с техникой. Окно безопаснее,
    #: чем кажется: глушится только сигнал чата, а громкость и плотность речи
    #: продолжают работать.
    chat_ignore_start_seconds: float = Field(default=180.0, ge=0)
    #: Порог отсечки для локального максимума, в единицах устойчивого z.
    min_peak_score: float = 0.8
    #: Минимальное расстояние между пиками, чтобы один всплеск не породил
    #: десяток кандидатов вокруг одного события.
    min_gap_seconds: float = 20.0
    #: Потолок доли материала, попадающей в кандидаты. Оценка всплеска
    #: считается относительно самого материала, поэтому на однородной записи
    #: «пики» находятся там, где их нет. Потолок оставляет только сильнейшие
    #: и делает видимым, что различающего сигнала не нашлось.
    max_coverage: float = 0.3


class FunnelConfig(BaseModel):
    max_candidates: int = 200
    max_clips: int = 30
    dedup_overlap: float = 0.5
    chunk_seconds: int = 1200
    chunk_overlap_seconds: int = 60


class ScoreConfig(BaseModel):
    schema_version: int = 2
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "semantic": 0.25,
            "emotion": 0.20,
            "context": 0.20,
            "completeness": 0.15,
            "audio": 0.10,
            "novelty": 0.05,
            "visual": 0.05,
        }
    )


class CompilationConfig(BaseModel):
    target_minutes: int = 20


class Config(BaseModel):
    profile: Profile = "auto"
    device: DeviceSetting = "auto"
    degrade_gracefully: bool = True
    storage_root: Path = Path("storage")
    default_project: str = "default"

    download: DownloadConfig = Field(default_factory=DownloadConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    stt: SttConfig = Field(default_factory=SttConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    candidates: CandidatesConfig = Field(default_factory=CandidatesConfig)
    subtitles: SubtitlesConfig = Field(default_factory=SubtitlesConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    funnel: FunnelConfig = Field(default_factory=FunnelConfig)
    score: ScoreConfig = Field(default_factory=ScoreConfig)
    compilation: CompilationConfig = Field(default_factory=CompilationConfig)

    # Разрешённый профиль — вычисляется при загрузке, в yaml не пишется.
    resolved_profile: Literal["dev", "batch"] = "dev"


DEFAULT_CONFIG_PATH = Path("configs/config.yaml")


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивно накладывает overlay на base, не изменяя аргументы."""
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve_profile(requested: Profile, has_accelerator: bool) -> Literal["dev", "batch"]:
    """`auto` выбирает batch только при наличии ускорителя (§62)."""
    if requested in ("dev", "batch"):
        return requested
    return "batch" if has_accelerator else "dev"


def load_config(
    path: Path | None = None,
    *,
    profile_override: Profile | None = None,
    has_accelerator: bool = False,
) -> Config:
    """Читает yaml, накладывает секцию выбранного профиля, валидирует.

    Секция `profiles:` из файла в модель не попадает — она только источник
    переопределений.
    """
    path = path or DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded:
            raw = loaded

    profiles: dict[str, Any] = raw.pop("profiles", {}) or {}
    requested: Profile = profile_override or raw.get("profile", "auto")
    resolved = resolve_profile(requested, has_accelerator)

    merged = _deep_merge(raw, profiles.get(resolved, {}))
    merged["profile"] = requested
    merged["resolved_profile"] = resolved

    return Config.model_validate(merged)
