"""Доля реакций в чате — признак, отдельный от плотности.

Замер на пятичасовой записи (37 828 сообщений): связь с плотностью 0.11,
то есть сигнал самостоятельный, а не пересказ уже имеющегося. Средняя доля
реакций 2.4%, на пиках до 50%.
"""

import numpy as np

from narezka.core.signals import _is_reaction, chat_reaction_share


def test_reaction_is_told_from_conversation():
    """Короткий смайл — реакция, развёрнутая реплика — нет."""
    assert _is_reaction("KEKW")
    assert _is_reaction("ахах")
    assert not _is_reaction("я думаю он прав потому что так удобнее")
    assert not _is_reaction("")


def test_repeated_words_count_as_reaction():
    """Повтор одного слова — реакция, даже если слова нет в словаре."""
    assert _is_reaction("клатч клатч клатч")


def test_wave_of_identical_messages_counts():
    """Волна одинаковых сообщений — реакция целиком.

    Когда зал повторяет одно и то же, это и есть реакция, даже если само
    слово в словарь не попало.
    """
    messages = [{"at": 1.0, "text": "вот это да"} for _ in range(4)]
    share = chat_reaction_share(messages, 2, 10.0)
    assert share[0] > 0.4


def test_share_not_count():
    """Возвращается доля, а не количество.

    Количество уже учтено плотностью, и складывать два признака, меняющихся
    вместе, значит считать один дважды.
    """
    # Реплики намеренно разные: одинаковые подряд — это волна, и правило
    # повтора справедливо считает её реакцией, что здесь исказило бы проверку.
    few = [{"at": 1.0, "text": "KEKW"}, {"at": 1.0, "text": "он тут неправ конечно"}]
    many = [{"at": 1.0, "text": "KEKW"}, {"at": 1.0, "text": "KEKW"}]
    many += [{"at": 1.0, "text": f"реплика номер {i} по существу"} for i in range(2)]
    assert chat_reaction_share(few, 2, 10.0)[0] == chat_reaction_share(many, 2, 10.0)[0] == 0.5


def test_bots_are_skipped():
    """Боты пишут по расписанию, а не в ответ на происходящее."""
    messages = [{"at": 1.0, "text": "KEKW", "is_bot": True}, {"at": 1.0, "text": "разговор длинный"}]
    assert chat_reaction_share(messages, 2, 10.0)[0] == 0.0


def test_empty_input_is_safe():
    assert np.all(chat_reaction_share([], 3, 10.0) == 0.0)
