/* Budget controls for the exchange-only polling source (no API key in browser). */
(() => {
    "use strict";
    const el = (id) => document.getElementById(id);
    const status = el("whale-admin-status");
    if (!status) return;
    async function load() {
        try {
            const resp = await fetch("/api/admin/screener/config", {credentials: "same-origin"});
            if (!resp.ok) throw new Error("HTTP " + resp.status);
            const data = await resp.json();
            if (!data.enabled) { status.textContent = "Нет ALCHEMY_API_KEY"; return; }
            el("whale-admin-interval").value = String(data.config.interval_sec / 60);
            el("whale-admin-budget").value = String(data.config.budget_cu);
            status.textContent = "Израсходовано этим скринером: " + data.config.reserved_cu.toLocaleString() +
                " CU в " + data.config.month;
        } catch (e) { status.textContent = "Настройки недоступны"; }
    }
    el("whale-admin-save").addEventListener("click", async () => {
        try {
            const resp = await fetch("/api/admin/screener/config", {
                method: "POST", credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({mode: el("whale-admin-mode").value,
                    interval_min: Number(el("whale-admin-interval").value),
                    monthly_cu: Number(el("whale-admin-budget").value)})
            });
            if (!resp.ok) throw new Error("HTTP " + resp.status);
            await load();
        } catch (e) { status.textContent = "Не удалось сохранить (проверьте диапазон)"; }
    });
    load();
})();
