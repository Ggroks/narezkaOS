"""Куски работы: что именно запускается по одной кнопке.

Единой кнопки «сделать всё» в интерфейсе нет — человек запускает разбор
записи, сборку роликов или длинную нарезку по отдельности. Здесь проверяется
то, на чём это держится: недостающее подтягивается само, а лишнее не
подтягивается.
"""

from __future__ import annotations

import pytest

from narezka.stages import GROUPS, PIPELINE, stages_for


def names(group: str) -> list[str]:
    return [stage.name for stage in stages_for(group)]


def test_every_stage_belongs_to_a_known_group() -> None:
    """Стадия без группы не попала бы ни в одну кнопку и стала бы
    недостижимой из интерфейса."""
    assert {stage.group for stage in PIPELINE} <= set(GROUPS)


def test_assembly_pulls_the_analysis_in() -> None:
    """Нажатие «собрать ролики» на необработанной записи обязано работать.

    Иначе пайплайн ломается ровно там, где человек не угадал порядок кнопок.
    """
    assert "transcribe" in names("shorts")
    assert "candidates" in names("shorts")
    assert "render" in names("shorts")


def test_shorts_do_not_pay_for_episode_search() -> None:
    """Поиск эпизодов стоит запросов к модели и нужен только длинной нарезке.

    В общем порядке он стоит раньше сборки роликов, и прогон «до рендера»
    захватил бы его заодно — за чужой счёт.
    """
    assert "episodes" not in names("shorts")
    assert "episodes" in names("long")


def test_long_cut_does_not_render_shorts() -> None:
    assert "render" not in names("long")
    assert "compilation" in names("long")


def test_analysis_stops_at_the_moments() -> None:
    assert names("analysis")[-1] == "llm_select"


def test_group_keeps_the_pipeline_order() -> None:
    """Порядок внутри куска — общий порядок пайплайна: стадии зависят
    от результатов предыдущих, и переставить их значит сломать."""
    for group in GROUPS:
        chosen = names(group)
        assert chosen == [s.name for s in PIPELINE if s.name in chosen]


def test_unknown_group_is_refused_by_name() -> None:
    with pytest.raises(KeyError, match="неизвестный кусок работы"):
        stages_for("шортсы")
