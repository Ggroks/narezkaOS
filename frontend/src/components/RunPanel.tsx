import { stageTitle } from "../stages";
import type { JobEvent } from "../api";

/**
 * Полоса запуска: что сделает кнопка на этой вкладке и что идёт сейчас.
 *
 * **Почему кнопок несколько, а не одна.** Единая «обработать всё» заставляла
 * платить за то, что человеку не нужно: поиск эпизодов стоит запросов
 * к модели, сборка роликов — минут кодирования. Теперь каждая вкладка
 * запускает свой кусок, а недостающее подтягивается само: разбор входит
 * и в сборку роликов, и в длинную нарезку, а уже посчитанное берётся
 * из кэша за доли секунды. Поэтому порядок нажатий не имеет значения
 * и сломать пайплайн, нажав «не ту» кнопку, нельзя.
 *
 * **Почему нет отдельной кнопки «применить настройки».** Настройка входит
 * в ключ кэша своей стадии: после правки та же кнопка пересчитает только
 * задетое, а остальное возьмёт готовым.
 */

type Props = {
  /** Что это за шаг: «Разбор записи», «Сборка роликов». Существительным. */
  title: string;
  /** Одной строкой: что именно сделает кнопка на этой записи. */
  hint: string;
  /** Надпись на кнопке. Короткая — заголовок уже сказал, о чём речь. */
  label: string;
  /** Она же, когда результат уже есть: нажатие пересоберёт. */
  againLabel: string;
  done: boolean;
  running: boolean;
  /** Место в очереди: 0 — не ждёт. */
  queued?: number;
  /** Во сколько кредитов обойдётся — верхняя граница. null — оплата выключена. */
  price?: number | null;
  /** Ход текущего прогона, если он идёт. */
  activeStage?: string;
  progress?: JobEvent;
  onRun: () => void;
  onStop: () => void;
  /** Почему нажать нельзя. Пустое — можно. */
  blocked?: string;
  /** Прошлый прогон сломался. Показывается всегда: это новость. */
  failure?: string;
  /**
   * Шаг пропущен. Показывается, только когда результата так и нет: на записи
   * без чата «чтение чата пропущено» — обычное дело и не новость, а вот
   * когда моментов не нашлось, это единственное объяснение, почему.
   */
  note?: string;
};

export function RunPanel({
  title, hint, label, againLabel, done, running, queued = 0, price, activeStage, progress,
  onRun, onStop, blocked, failure, note,
}: Props) {
  const waiting = !running && queued > 0;
  return (
    <section className={`runbar${running || waiting ? " running" : ""}`}>
      <div className="runbar-text">
        <b>{title}</b>
        <span className="small dim">
          {running
            ? currentStep(activeStage, progress)
            : waiting
              ? `В очереди, ${queued}-й. Начнём, когда освободится машина — она берёт по одной записи`
              : blocked || hint}
        </span>
        {!running && !waiting && price != null && price > 0 && (
          /* «Не больше»: часть работы возьмётся из кэша и не будет стоить
             ничего. Списание никогда не превышает названного — обратный
             порядок был бы обманом. */
          <span className="small dim tnum">Спишем не больше {price.toFixed(0)} кредитов</span>
        )}
        {!running && failure && <span className="small runbar-fail">{failure}</span>}
        {!running && !failure && !done && note && (
          <span className="small runbar-note">{note}</span>
        )}
      </div>

      {running || waiting ? (
        <button className="ghost" onClick={onStop}>
          {waiting ? "Убрать из очереди" : "Остановить"}
        </button>
      ) : (
        <button className="primary" disabled={Boolean(blocked)} onClick={onRun}>
          {done ? againLabel : label}
        </button>
      )}
    </section>
  );
}

function currentStep(stage?: string, progress?: JobEvent): string {
  const name = stageTitle(stage) || "подготовка";
  if (progress?.total) {
    const note = progress.note ? ` · ${progress.note}` : "";
    return `${name}: ${progress.done} из ${progress.total}${note}`;
  }
  return `${name}…`;
}
