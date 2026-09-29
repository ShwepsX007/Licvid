/* Alchemy key management: no secrets rendered, cached or included in status URLs. */
(() => {
    "use strict";
    const el = (id) => document.getElementById(id);
    const status = el("whale-admin-status");
    if (!status) return;
    const keyStatus = el("whale-admin-key-status");
    async function request(path, options) {
        const response = await fetch(path, {credentials: "same-origin", ...options});
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || "HTTP " + response.status);
        return body;
    }
    const when = ts => ts ? new Date(ts * 1000).toLocaleString() : "ещё не было";
    async function load() {
        try {
            const data = await request("/api/admin/screener/config");
            const unavailable = Boolean(data.vault_error || !data.config);
            el("whale-admin-key").disabled = unavailable;
            el("whale-admin-add-key").disabled = unavailable;
            if (data.vault_error) keyStatus.textContent = data.vault_error;
            const cfg = data.config;
            if (!cfg) { status.textContent = "Скринер не запущен"; return; }
            el("whale-admin-interval").value = String(cfg.interval_sec / 60);
            el("whale-admin-budget").value = String(cfg.budget_cu);
            status.textContent = (cfg.phase === "no_key" ? "Нужен API-ключ · " : "") +
                "Расход опросчика: " + Number(cfg.reserved_cu).toLocaleString() +
                "/" + Number(cfg.budget_cu).toLocaleString() + " CU за " + (cfg.month || "текущий месяц");
            const list = el("whale-admin-keys");
            list.replaceChildren();
            if (!data.keys.length) list.textContent = "Ключей пока нет. Введите API Key выше.";
            data.keys.forEach((key) => {
                const row = document.createElement("div");
                row.style.margin = "5px 0";
                const text = document.createElement("span");
                text.textContent = key.hint + " · " + key.source + " · " + key.state +
                    " · " + Number(key.reserved_cu).toLocaleString() + " CU" +
                    (key.reason ? " · " + key.reason : "");
                row.appendChild(text);
                if (key.source === "admin") {
                    const del = document.createElement("button");
                    del.className = "btn btn-ghost btn-compact";
                    del.type = "button";
                    del.textContent = "Удалить";
                    del.style.marginLeft = "8px";
                    del.addEventListener("click", async () => {
                        if (!confirm("Удалить ключ " + key.hint + "?")) return;
                        try {
                            await request("/api/admin/screener/keys/" + encodeURIComponent(key.id),
                                {method: "DELETE"});
                            await load();
                        } catch (e) { keyStatus.textContent = "Удаление не удалось"; }
                    });
                    row.appendChild(del);
                }
                list.appendChild(row);
            });
            const chains = el("whale-admin-chains");
            chains.replaceChildren();
            (cfg.supported || []).forEach((chain) => {
                const item = document.createElement("div");
                const problem = (cfg.errors || {})[chain];
                item.textContent = chain + ": " + (problem ? "● " + problem :
                    cfg.last_success && cfg.last_success[chain] ?
                    "✓ последний успешный опрос " + when(cfg.last_success[chain]) +
                    " · блок " + (cfg.cursors || {})[chain] :
                    "ожидание первого опроса") +
                    " · попытка " + when((cfg.last_attempt || {})[chain]);
                chains.appendChild(item);
            });
        } catch (e) { status.textContent = "Настройки скринера недоступны"; }
    }
    el("whale-admin-add-key").addEventListener("click", async () => {
        const input = el("whale-admin-key");
        const value = input.value.trim();
        if (!value) { keyStatus.textContent = "Введите ключ"; return; }
        keyStatus.textContent = "Сохраняю ключ…";
        try {
            await request("/api/admin/screener/keys", {method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({key: value})});
            input.value = "";
            keyStatus.textContent = "Ключ сохранён; проверка сетей началась. Обновление состояния через несколько секунд.";
            await load();
            setTimeout(load, 7000);
        } catch (e) {
            // Server error messages are deliberately secret-free; do not show
            // request payload or the typed credential in the UI.
            keyStatus.textContent = "Ключ не сохранён: " + e.message;
        }
    });
    el("whale-admin-save").addEventListener("click", async () => {
        try {
            await request("/api/admin/screener/config", {method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({mode: el("whale-admin-mode").value,
                    interval_min: Number(el("whale-admin-interval").value),
                    monthly_cu: Number(el("whale-admin-budget").value)})});
            await load();
        } catch (e) { status.textContent = "Не удалось сохранить (проверьте диапазон)"; }
    });
    load();
    setInterval(load, 30000);
})();
