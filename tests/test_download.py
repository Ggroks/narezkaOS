

def test_required_space_scales_with_duration():
    """Место считается по длительности, а не константой.

    Замер: пятичасовой VOD занял 6.59 ГБ — прежний порог в 5 ГБ его пропускал,
    и место кончалось посреди скачивания, то есть после получаса работы.
    """
    from narezka.stages.download import MIN_FREE_GB, required_gb

    assert required_gb(5 * 3600) > 6.59, "должно покрывать замеренный расход"
    assert required_gb(8 * 3600) > required_gb(5 * 3600)
    # Короткое видео не должно требовать больше нижнего порога.
    assert required_gb(10 * 60) == MIN_FREE_GB
    # Длительность ещё неизвестна — требуем хотя бы минимум, а не ноль.
    assert required_gb(None) == MIN_FREE_GB


def test_required_space_scales_with_duration():
    """Место считается по длительности, а не константой.

    Замер: пятичасовой VOD занял 6.59 ГБ — прежний порог в 5 ГБ его пропускал,
    и место кончалось посреди скачивания, то есть после получаса работы.
    """
    from narezka.stages.download import MIN_FREE_GB, required_gb

    assert required_gb(5 * 3600) > 6.59, "должно покрывать замеренный расход"
    assert required_gb(8 * 3600) > required_gb(5 * 3600)
    assert required_gb(10 * 60) == MIN_FREE_GB
    assert required_gb(None) == MIN_FREE_GB
