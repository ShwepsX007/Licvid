(() => {
    "use strict";
    const $ = id => document.getElementById(id);
    const scan = {ETH: "https://etherscan.io/tx/", BNB: "https://bscscan.com/tx/",
        POLYGON: "https://polygonscan.com/tx/", ARBITRUM: "https://arbiscan.io/tx/",
        BASE: "https://basescan.org/tx/", HYPERLIQUID: "https://hypurrscan.io/tx/"};
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
        const native = data.native || {};
        const badge = $("screener-state");
        const errors = p.errors || {};
        const successes = p.last_success || {};
        if (data.unavailable_reason) {
            state(badge, "● Whale-скринер недоступен: " + data.unavailable_reason, "bad");
        } else if (native.connected) {
            if (!data.enabled) {
                const evm = data.alchemy_unavailable_reason ?
                    "Alchemy EVM недоступен: " + data.alchemy_unavailable_reason : "Alchemy EVM без ключа";
                state(badge, "● Hyperliquid работает через native API · " + evm,
                    data.alchemy_unavailable_reason ? "warn" : "ok");
            } else if (Object.keys(errors).length || data.alchemy_unavailable_reason) {
                state(badge, "● Hyperliquid работает · ошибка части EVM-сетей", "warn");
            } else {
                state(badge, "● Hyperliquid native API и EVM-скринер подключены", "ok");
            }
        } else if (!data.enabled) {
            const detail = data.alchemy_unavailable_reason || native.error || "";
            state(badge, "◌ Hyperliquid " + (native.enabled ? "подключается" : "выключен") +
                " · для EVM-сетей нужен ключ Alchemy" + (detail ? " · " + detail : ""), "warn");
        } else if (p.phase === "budget_exhausted") {
            state(badge, "● Месячный бюджет опроса Alchemy исчерпан", "bad");
        } else if (Object.keys(errors).length) {
            state(badge, "● Ошибка части сетей — подробности ниже", "bad");
        } else if (!Object.keys(successes).length) {
            state(badge, "◌ Первый опрос / подключение…", "warn");
        } else {
            state(badge, "● Опрос Alchemy работает · проверка раз в " +
                Math.round((p.interval_sec || 3600) / 60) + " мин", "ok");
        }
        $("screener-usage").textContent = "Alchemy: " + (p.keys_configured || 0) +
            " ключей · локальный расход " + format(p.reserved_cu || 0) + "/" +
            format(p.budget_cu || 0) + " CU (" + (p.month || "новый месяц") +
            "). Hyperliquid использует публичный native API и не расходует CU Alchemy.";
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
                " · блок " + (p.cursors || {})[chain] : "Ожидает ключ Alchemy / первой проверки");
            item.append(heading, detail);
            chains.appendChild(item);
        });
        const nativeItem = document.createElement("div");
        nativeItem.className = "chain " + (native.connected ? "" : native.error ? "error" : "unknown");
        const nativeHeading = document.createElement("strong");
        nativeHeading.textContent = "HYPERLIQUID · native API " +
            (native.connected ? "✓" : native.enabled ? "◌" : "✕");
        const nativeDetail = document.createElement("small");
        const nativeCounts = (native.market_count || 0) + " рынков · подписки " +
            (native.subscriptions_acked || 0) + "/" + (native.market_count || 0) +
            " · сделок " + format(native.trades_seen || 0) +
            " · whale-событий " + format(native.events || 0);
        nativeDetail.textContent = native.error || (native.connected ? nativeCounts :
            (native.state || "ожидает подключения") + " · api.hyperliquid.xyz");
        nativeItem.append(nativeHeading, nativeDetail);
        chains.appendChild(nativeItem);

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
            const from = row.from_label || row.from || "участник";
            const to = row.to_label || row.to || "участник";
            route.textContent = row.direction === "trade" ?
                "Покупатель " + from + " ↔ продавец " + to : from + " → " + to;
            const direction = document.createElement("span");
            direction.className = row.direction === "inflow" || row.direction === "outflow" ?
                row.direction : row.direction === "trade" ? "trade" : "";
            direction.textContent = row.direction === "trade" ? "Сделка · " + (row.side || "") :
                row.direction;
            card.append(a, amount, route, direction);
            target.appendChild(card);
        });
        const empty = $("screener-empty");
        empty.hidden = target.childElementCount > 0;
        empty.textContent = (data.unavailable_reason ? "Скринер отключён до устранения причины выше." :
            !data.enabled ? "Для EVM-сетей добавьте ключ Alchemy. Hyperliquid работает отдельно через native API." :
            "Событий по выбранным фильтрам пока нет.");
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
