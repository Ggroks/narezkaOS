import { useEffect, useState } from "react";
import { api, type PublishEntry, type PublishTexts } from "../api";

type Props = { videoId: string };

/**
 * Тексты для публикации ([§22](BAZA.md#22), [§23](BAZA.md#23), [§24](BAZA.md#24)).
 *
 * Экран нужен для одного действия — скопировать и вставить в форму загрузки.
 * Поэтому кнопка копирования есть у каждого блока по отдельности и у всего
 * описания целиком: заголовок и описание вставляются в разные поля.
 *
 * Отвергнутые варианты заголовка показываются намеренно. §22 требует, чтобы
 * выбирала система, а не модель, и решение должно быть объяснимым: видно,
 * что именно отброшено и по какой причине.
 */
export function PublishView({ videoId }: Props) {
  const [texts, setTexts] = useState<PublishTexts | null>(null);
  const [missing, setMissing] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .publish(videoId)
      .then((data) => alive && setTexts(data))
      .catch(() => alive && setMissing(true));
    return () => {
      alive = false;
    };
  }, [videoId]);

  async function copy(key: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(key);
      window.setTimeout(() => setCopied((current) => (current === key ? null : current)), 1600);
    } catch {
      /* браузер может запретить доступ к буферу — текст всегда можно выделить */
    }
  }

  if (missing || !texts) return null;

  return (
    <section className="card" aria-labelledby="publish-heading">
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <h2 id="publish-heading" style={{ margin: 0 }}>
          Тексты для публикации
        </h2>
        <span className="small dim grow">
          {texts.stats.written} из {texts.stats.clips}
          {texts.stats.titles_rejected > 0 &&
            ` · отбраковано вариантов заголовка: ${texts.stats.titles_rejected}`}
        </span>
      </div>

      <div className="publish-list">
        {texts.clips.map((entry) => (
          <Entry
            key={entry.index}
            entry={entry}
            copied={copied}
            onCopy={copy}
          />
        ))}
      </div>
    </section>
  );
}

type EntryProps = {
  entry: PublishEntry;
  copied: string | null;
  onCopy: (key: string, value: string) => void;
};

function Entry({ entry, copied, onCopy }: EntryProps) {
  const rejected = entry.title_variants.filter((v) => v.problems.length > 0);

  return (
    <article className="publish-item">
      <div className="publish-row">
        <h3 className="publish-title">{entry.title}</h3>
        <button
          className="small"
          onClick={() => onCopy(`t${entry.index}`, entry.title)}
          aria-label={`Скопировать заголовок клипа ${entry.index + 1}`}
        >
          {copied === `t${entry.index}` ? "скопировано" : "копировать"}
        </button>
      </div>

      <p className="publish-description">{entry.description}</p>

      {entry.hashtags.length > 0 && (
        <p className="publish-tags small">{entry.hashtags.join(" ")}</p>
      )}

      <div className="row wrap small">
        <button onClick={() => onCopy(`d${entry.index}`, entry.ready)}>
          {copied === `d${entry.index}` ? "скопировано" : "копировать описание с тегами"}
        </button>
        <span className="dim tnum">клип {entry.index + 1}</span>
      </div>

      {rejected.length > 0 && (
        <details className="publish-rejected small dim">
          <summary>Отброшено вариантов: {rejected.length}</summary>
          <ul>
            {rejected.map((variant) => (
              <li key={variant.title}>
                «{variant.title}» — {variant.problems.join(", ")}
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}
