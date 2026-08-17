/**
 * Человеческие названия стадий.
 *
 * Вынесены из страницы записи, потому что их показывает ещё и каталог:
 * два списка неизбежно разошлись бы, и одна и та же стадия называлась бы
 * на соседних экранах по-разному.
 */
export const STAGE_TITLES: Record<string, string> = {
  download: "Загрузка записи",
  probe: "Проверка файла",
  extract_audio: "Извлечение звука",
  chat: "Чтение чата",
  transcribe: "Распознавание речи",
  audiotags: "Разбор звука",
  timeline: "Удаление пауз",
  candidates: "Поиск моментов",
  llm_select: "Оценка моментов",
  episodes: "Поиск эпизодов",
  facecam: "Поиск лица",
  subtitles: "Субтитры",
  metadata: "Тексты для публикации",
  render: "Сборка роликов",
  compilation: "Длинная нарезка",
};

export function stageTitle(name: string | null | undefined): string {
  if (!name) return "";
  return STAGE_TITLES[name] ?? name;
}
