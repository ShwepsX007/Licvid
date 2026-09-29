(() => {
    "use strict";
    const $ = id => document.getElementById(id);
    const scan = {ETH: "https://etherscan.io/tx/", BNB: "https://bscscan.com/tx/",
        POLYGON: "https://polygonscan.com/tx/", ARBITRUM: "https://arbiscan.io/tx/",
        BASE: "https://basescan.org/tx/", HYPERLIQUID: "https://hyperevmscan.io/tx/"};
    let data = null;
    const time = ts => ts ? new Date(Number(ts) * 1000).toLocaleString() : "ещё не было";
    const format = n => Number(n).toLocaleString(undefined, {maximumFractionDigits: 4});
    function state(badge, text, kind) {
        badge.className = "badge " + kind;
        badge.textContent = text;
    }
    function render() {
        if (!data) return;
        const p = data.poller || {};
        const badge = $("screener-state");
        const errors = p.errors || {};
        const successes = p.last_success || {};
        if (data.unavailable_reason) state(badge, "● Скринер недоступен: " + data.unavailable_reason, "bad");
        else if (!data.enabled || p.phase === "no_key") state(badge, "● Нет ключа Alchemy — добавьте его в админке", "bad");
        else if (p.phase === "budget_exhausted") state(badge, "● Месячный бюджет опроса исчерпан", "bad");
        else if (Object.keys(errors).length) state(badge, "● Ошибка части сетей — подробности ниже", "bad");
        else if (!Object.keys(successes).length) state(badge, "◌ Первый опрос / подключение…", "warn");
        else state(badge, "● Опрос работает · проверка раз в " + Math.round(p.interval_sec / 60) + " мин", "ok");
        $("screener-usage").textContent = "Ключей: " + (p.keys_configured || 0) +
            " · локальный расход " + format(p.reserved_cu || 0) + "/" +
            format(p.budget_cu || 0) + " CU (" + (p.month || "новый месяц") +
            "). Ключи не увеличивают общий лимит Alchemy Free.";
        const chains = $("screener-chains");
        chains.replaceChildren();
        (p.supported || []).forEach(chain => {
            const item = document.createElement("div");
            item.className = "chain " + (errors[chain] ? "error" : successes[chain] ? "" : "unknown");
            const heading = document.createElement("strong");
            heading.textContent = chain + " " + (errors[chain] ? "✕" : successes[chain] ? "✓" : "◌");
            const detail = document.createElement("small");
            detail.textContent = errors[chain] || (successes[chain] ?
                "Последний успешный опрос: " + time(successes[chain]) +
                " · блок " + (p.cursors || {})[chain] : "Ожидает первой проверки");
            item.append(heading, detail);
            chains.appendChild(item);
        });
        const min = Number($("whale-min").value), dir = $("whale-direction").value;
        const target = $("screener-events");
        target.replaceChildren();
        const events = (data.events || []).filter(row => Number(row.usd) >= min &&
            (dir === "ALL" || row.direction === dir));
        events.forEach(row => {
            if (!scan[row.chain] || !/^0x[0-9a-f]{64}$/i.test(row.hash || "")) return;
            const card = document.createElement("div");
            card.className = "row";
            const a = document.createElement("a");
            a.href = scan[row.chain] + row.hash;
            a.rel = "noopener noreferrer";
            a.target = "_blank";
            a.textContent = row.chain + " · " + time(row.timestamp) + " ↗";
            const amount = document.createElement("strong");
            amount.textContent = format(row.amount) + " " + row.symbol + " · $" + format(row.usd);
            const route = document.createElement("span");
            route.className = "dim";
            route.textContent = (row.from_label || row.from) + " → " + (row.to_label || row.to);
            const direction = document.createElement("span");
            direction.className = row.direction === "inflow" || row.direction === "outflow" ? row.direction : "";
            direction.textContent = row.direction;
            card.append(a, amount, route, direction);
            target.appendChild(card);
        });
        const empty = $("screener-empty");
        empty.hidden = target.childElementCount > 0;
        empty.textContent = (data.unavailable_reason ? "Скринер отключён до устранения причины выше." :
            !data.enabled ? "Сначала добавьте ключ в админке." :
            "Событий по фильтрам пока нет. Первый запуск запоминает текущий блок; старая история не загружается.");
    }
    async function load() {
        try {
            const params = new URLSearchParams({chain: $("whale-chain").value, min_usd: "0", limit: "100"});
            const response = await fetch("/api/screener/whales?" + params, {cache: "no-store"});
            if (!response.ok) throw new Error();
            data = await response.json();
            render();
        } catch (e) { state($("screener-state"), "● Не удалось получить состояние сервиса", "bad"); }
    }
    ["whale-chain", "whale-min", "whale-direction"].forEach(id => $(id).addEventListener("change",
        id === "whale-chain" ? load : render));
    load();
    setInterval(load, 30000);
})();
