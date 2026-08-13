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
    provider: str = "api"
    model: str | None = None
    batch_mode: bool = True
    cache_prefix: bool = True


class DetectorConfig(BaseModel):
    backend: Literal["mediapipe", "yolox", "ultralytics"] = "mediapipe"


class ShortOutput(BaseModel):
    width: int = 1080
    height: int = 1920
    min_duration: float = 15.0
    optimal_duration: float = 60.0
    max_duration: float = 90.0


class OutputConfig(BaseModel):
    short: ShortOutput = Field(default_factory=ShortOutput)
    loudness_target_lufs: float = -14.0
    crf: int = 20
    pix_fmt: str = "yuv420p"
    faststart: bool = True


class CandidatesConfig(BaseModel):
    """Первый проход воронки (§11): дешёвые сигналы по всему материалу."""

    window_seconds: float = 1.0
    #: Вклад всплеска громкости и плотности речи в предварительную оценку.
    loudness_weight: float = 0.6
    density_weight: float = 0.4
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
