"""Тесты чата как сигнала отбора (BAZA.md §41, §11).

Смысл сигнала: всплеск сообщений — прямое свидетельство того, что зрители
сочли момент важным, и он **не зависит от громкости**. Тихая, но неожиданная
сцена даёт всплеск чата при ровном звуке.
"""

from __future__ import annotations

import numpy as np

from narezka.core.signals import chat_rate, robust_z, smooth


def messages(*times: float, bot_at: float | None = None) -> list[dict]:
    result = [{"at": t, "is_bot": False} for t in times]
    if bot_at is not None:
        result.append({"at": bot_at, "is_bot": True})
    return result


def test_rate_counts_messages_per_window() -> None:
    rate = chat_rate(messages(1.0, 1.2, 1.8, 5.0), window_count=10, window_seconds=1.0)
    assert rate[1] == 3.0
    assert rate[5] == 1.0
    assert rate[0] == 0.0


def test_bots_are_excluded_by_default() -> None:
    """Реклама раз в десять минут выглядела бы всплеском на пустом месте."""
    rate = chat_rate(messages(1.0, bot_at=7.0), window_count=10, window_seconds=1.0)
    assert rate[7] == 0.0


def test_bots_can_be_included_deliberately() -> None:
    rate = chat_rate(messages(1.0, bot_at=7.0), window_count=10, window_seconds=1.0, skip_bots=False)
    assert rate[7] == 1.0


def test_messages_outside_the_recording_are_ignored() -> None:
    """Чат отдаёт сообщения и за пределами окна анализа — они не должны
    попадать в чужие окна и раздувать край."""
    rate = chat_rate(messages(-5.0, 999.0, 2.0), window_count=10, window_seconds=1.0)
    assert rate.sum() == 1.0


def test_longer_window_lowers_the_rate() -> None:
    """Плотность — сообщения в секунду, а не за окно: иначе значение зависит
    от настройки окна и несравнимо между прогонами."""
    rate = chat_rate(messages(1.0, 1.5), window_count=5, window_seconds=2.0)
    assert rate[0] == 1.0


def test_empty_chat_gives_zeros() -> None:
    assert chat_rate([], window_count=5, window_seconds=1.0).tolist() == [0.0] * 5


def test_broken_message_does_not_crash() -> None:
    assert chat_rate([{"at": None}, {"is_bot": False}], 5, 1.0).sum() == 0.0


# --- сглаживание -----------------------------------------------------------


def test_smoothing_spreads_a_burst() -> None:
    """Зрители реагируют с запозданием и вразнобой: без сглаживания всплеск
    рассыпается на дрожь вокруг фона."""
    raw = np.array([0.0, 0.0, 9.0, 0.0, 0.0])
    result = smooth(raw, 3)
    assert result[1] > 0 and result[3] > 0
    assert result[2] < 9.0


def test_smoothing_preserves_total_energy() -> None:
    raw = np.array([0.0, 0.0, 3.0, 0.0, 0.0])
    assert smooth(raw, 3).sum() == np.float64(3.0)


def test_smoothing_is_a_noop_for_window_one() -> None:
    raw = np.array([1.0, 2.0, 3.0])
    assert smooth(raw, 1).tolist() == raw.tolist()


def test_chat_burst_is_visible_after_normalization() -> None:
    """Сквозная проверка: всплеск в тихом чате должен дать высокий z."""
    times = [float(i) for i in range(0, 300, 10)]          # ровный фон
    times += [150.0 + i * 0.1 for i in range(40)]          # всплеск на 150-й с
    rate = smooth(chat_rate(messages(*times), 300, 1.0), 5)
    z = robust_z(rate)
    assert z[150] > 3.0
    assert abs(z[10]) < 3.0


# --- окно реакции вперёд ---------------------------------------------------


def test_forward_window_attributes_reaction_to_its_cause() -> None:
    """Замер на записи стрима (усреднение по 10 всплескам громкости) показал:
    реакция чата — не сдвиг на пару секунд, а плато длиной 15–20 с,
    начинающееся сразу от события.

    Значит, оценка момента t должна учитывать чат в [t, t+окно]: всплеск
    через несколько секунд после события вызван именно им.
    """
    from narezka.core.signals import forward_average

    burst = np.zeros(20)
    burst[10:15] = 5.0                      # реакция началась на 10-й секунде
    result = forward_average(burst, 6)
    # Момент события виден раньше пика самой реакции.
    assert result[6] > 0
    assert int(np.argmax(result)) <= 10


def test_forward_window_does_not_look_back() -> None:
    """Чат до события к нему не относится: симметричное сглаживание
    размазывало бы всплеск назад, и кандидат начинался бы раньше события."""
    from narezka.core.signals import forward_average

    values = np.array([9.0, 0.0, 0.0, 0.0, 0.0])
    result = forward_average(values, 3)
    assert result[1] == 0.0
    assert result[2] == 0.0


def test_forward_window_handles_the_tail() -> None:
    """У конца записи окно короче — делить надо на фактическое число точек."""
    from narezka.core.signals import forward_average

    values = np.array([0.0, 0.0, 3.0])
    assert forward_average(values, 5)[2] == 3.0


def test_forward_window_of_one_changes_nothing() -> None:
    from narezka.core.signals import forward_average

    values = np.array([1.0, 2.0, 3.0])
    assert forward_average(values, 1).tolist() == values.tolist()
