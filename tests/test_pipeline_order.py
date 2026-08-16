"""Порядок стадий: что от чего зависит по существу, а не по привычке."""

from narezka.stages import PIPELINE

ORDER = [stage.name for stage in PIPELINE]


def test_texts_are_written_before_rendering():
    """Тексты идут до рендера.

    Заголовки зависят только от границ клипа и объяснения отбора, а не от
    готовых файлов. Написанные заранее, они видны через минуты после отбора,
    а не после получаса кодирования, — и по ним видно, что за ролик, ещё до
    того, как на него потрачено время.
    """
    assert ORDER.index("metadata") < ORDER.index("render")


def test_vision_runs_after_selection():
    """Вебка ищется по отобранным отрезкам, а не по всей записи (§11, §61)."""
    assert ORDER.index("llm_select") < ORDER.index("facecam")


def test_chat_precedes_candidates():
    """Чат — сигнал воронки, значит нужен до отбора кандидатов (§41)."""
    assert ORDER.index("chat") < ORDER.index("candidates")
