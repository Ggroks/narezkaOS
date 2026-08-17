import { useState } from "react";
import { api, type Whoami } from "../api";

/**
 * Вход. Показывается только тогда, когда он включён на сервере: на своей
 * машине программа остаётся инструментом без паролей.
 *
 * Одно поле пароля и никакого подтверждения при регистрации: повтор пароля
 * ловит опечатку ценой лишнего поля у всех, а починить опечатку всё равно
 * можно входом заново. Ошибка приходит одна и та же на неверный логин и на
 * неверный пароль — разные ответы выдали бы список зарегистрированных.
 */

type Props = { state: Whoami; onEntered: (state: Whoami) => void };

export function LoginScreen({ state, onEntered }: Props) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const signing = mode === "signup";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const next = signing
        ? await api.signup(login.trim(), password)
        : await api.login(login.trim(), password);
      onEntered(next);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setBusy(false);
    }
  }

  return (
    <section className="invite login">
      <h3>{signing ? "Новая учётка" : "Вход"}</h3>
      <p className="dim">
        {signing
          ? "Логин и пароль от восьми знаков. Записи каждого лежат отдельно."
          : "Narezka OS — нарезка стримов на короткие ролики."}
      </p>

      <form className="login-form" onSubmit={submit}>
        <label>
          <span className="sheet-label">Логин</span>
          <input
            autoFocus
            autoComplete="username"
            value={login}
            onChange={(event) => setLogin(event.target.value)}
          />
        </label>
        <label>
          <span className="sheet-label">Пароль</span>
          <input
            type="password"
            autoComplete={signing ? "new-password" : "current-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>

        {error && (
          <div className="error" role="alert" style={{ margin: 0 }}>
            {error}
          </div>
        )}

        <button className="primary" type="submit" disabled={busy || !login.trim() || !password}>
          {busy ? "Проверяем…" : signing ? "Завести" : "Войти"}
        </button>
      </form>

      {state.allow_signup && (
        <button
          className="ghost invite-alt"
          onClick={() => {
            setMode(signing ? "login" : "signup");
            setError(null);
          }}
        >
          {signing ? "У меня уже есть учётка" : "Завести учётку"}
        </button>
      )}
    </section>
  );
}
