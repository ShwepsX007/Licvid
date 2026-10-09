/* Admin-only Alchemy key and quota management. Key values are never rendered. */
(() => {
    "use strict";
    const el = id => document.getElementById(id);
    const card = el("whale-admin-card");
    if (!card) return;
    const status = el("whale-admin-status");
    const keyStatus = el("whale-admin-key-status");
    const NETWORKS = {
        ETH: ["screener.network_eth", "Ethereum"],
        BNB: ["screener.network_bnb", "BNB Chain"],
        POLYGON: ["screener.network_polygon", "Polygon"],
        ARBITRUM: ["screener.network_arbitrum", "Arbitrum"],
        BASE: ["screener.network_base", "Base"],
        SOLANA: ["screener.network_solana", "Solana"],
        TRON: ["screener.network_tron", "TRON"],
        HYPERLIQUID: ["screener.network_hyperliquid", "Hyperliquid Core"],
    };
    let loading = false;
    let editingWalletId = "";
    const t = (key, vars) => {
        try {
            if (window.LiqScopeI18n && window.LiqScopeI18n.t) {
                const value = window.LiqScopeI18n.t(key, vars || {});
                if (value && value !== key) return value;
            }
        } catch (_) {}
        return key;
    };
    async function request(path, options) {
        const response = await fetch(path, {credentials: "same-origin", cache: "no-store", ...options});
        let body = {};
        try { body = await response.json(); } catch (_) {}
        if (!response.ok) {
            const error = new Error(body.reason || body.detail || body.error || "HTTP " + response.status);
            error.code = body.error || "http_error";
            error.status = response.status;
            throw error;
        }
        return body;
    }
    const when = ts => ts ? new Date(Number(ts) * 1000).toLocaleString() : t("adm.alchemy_never");
    const number = value => Number(value || 0).toLocaleString();
    function usagePercent(value, max) {
        return max > 0 ? Math.max(0, Math.min(100, Number(value || 0) / Number(max) * 100)) : 0;
    }
    function make(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }
    function networkLabel(id) {
        const item = NETWORKS[id];
        return item ? t(item[0]) : id;
    }
    function renderKeys(keys, cu) {
        const list = el("whale-admin-keys");
        list.replaceChildren();
        const rows = Array.isArray(keys) ? keys : [];
        el("whale-admin-summary").textContent = t("adm.alchemy_key_summary", {count: rows.length});
        if (!rows.length) {
            list.append(make("p", "meta", t("adm.alchemy_no_keys")));
            return;
        }
        const limit = Number(cu.key_share || cu.limit || 0);
        rows.forEach(key => {
            const row = make("div", "whale-admin-key-row");
            const main = make("div", "whale-admin-key-main");
            main.append(make("strong", "", key.hint || "••••"));
            const source = key.source === "environment" ? t("adm.alchemy_env_key") : t("adm.alchemy_admin_key");
            const stateText = key.state === "active" ? t("adm.alchemy_active")
                : key.state === "cooldown" ? t("adm.alchemy_cooldown")
                : key.state === "exhausted" ? t("adm.alchemy_exhausted")
                : key.state === "reserve" ? t("adm.alchemy_reserve") : t("adm.alchemy_ready");
            main.append(make("small", "", source + " · " + stateText + (key.reason ? " · " + key.reason : "")));
            const usage = make("div", "whale-admin-key-usage");
            const used = Number(key.reserved_cu || 0);
            usage.append(make("span", "", t("adm.alchemy_key_cu", {used: number(used), limit: number(limit)})));
            const track = make("div", "whale-admin-key-progress");
            const fill = make("i");
            fill.style.width = usagePercent(used, limit) + "%";
            track.append(fill);
            usage.append(track);
            const details = make("div", "whale-admin-key-details");
            details.append(make("small", "", "LOCAL ESTIMATE · " +
                number(Number(key.reserved_cu || 0)) + " CU · quota: UNKNOWN"));
            details.append(make("small", "", "Requests today: " +
                (key.requests_today == null ? "UNKNOWN (not tracked yet)" : number(key.requests_today))));
            if (key.last_successful_request) details.append(make("small", "",
                "Last successful request: " + when(key.last_successful_request)));
            if (key.last_provider_error) details.append(make("small", "",
                "Last provider error: " + String(key.last_provider_error).slice(0, 160)));
            if (key.last_http_status != null) details.append(make("small", "",
                "Last HTTP status: " + key.last_http_status));
            if (key.rotation_at) details.append(make("small", "",
                "Last rotation: " + when(key.rotation_at) + (key.rotation_reason ? " · " + key.rotation_reason : "")));
            if (Number(key.cooldown_in_sec || 0) > 0) details.append(make("small", "",
                "Cooldown: " + number(key.cooldown_in_sec) + " s"));
            const test = make("button", "btn btn-ghost btn-compact whale-admin-key-test", "Проверить ключ");
            test.type = "button";
            const testResult = make("small", "whale-admin-key-test-result", "");
            test.addEventListener("click", async () => {
                test.disabled = true;
                testResult.textContent = "Проверка read-only RPC…";
                try {
                    const result = await request("/api/admin/alchemy/keys/" +
                        encodeURIComponent(key.id) + "/test", {method: "POST"});
                    const parts = [];
                    parts.push(result.valid ? "✓ key valid" : "✗ key invalid");
                    parts.push(result.eth_available ? "✓ ETH available" : "✗ ETH RPC unavailable");
                    parts.push("response " + number(result.response_ms) + " ms");
                    parts.push(result.http_status == null ? "HTTP status unknown" : "HTTP " + result.http_status);
                    parts.push(result.quota_status === "PROVIDER-CONFIRMED EXHAUSTION"
                        ? "PROVIDER-CONFIRMED EXHAUSTION" : "quota not reported by provider (UNKNOWN)");
                    if (result.error) parts.push(result.error);
                    testResult.textContent = parts.join(" · ");
                    if (result.provider_message) testResult.title = result.provider_message;
                } catch (error) {
                    testResult.textContent = "Probe failed: " + String(error && error.message || "unknown error");
                } finally {
                    test.disabled = false;
                }
            });
            details.append(test, testResult);
            row.append(main, usage, details);
            if (key.source === "admin") {
                const del = make("button", "btn btn-ghost btn-compact whale-admin-key-delete", t("adm.alchemy_delete"));
                del.type = "button";
                del.addEventListener("click", async () => {
                    if (!window.confirm(t("adm.alchemy_confirm_delete", {key: key.hint}))) return;
                    del.disabled = true;
                    try {
                        await request("/api/admin/alchemy/keys", {
                            method: "DELETE",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({id: key.id}),
                        });
                        keyStatus.textContent = t("adm.alchemy_deleted");
                        await load();
                    } catch (_) {
                        keyStatus.textContent = t("adm.alchemy_delete_error");
                        del.disabled = false;
                    }
                });
                row.append(del);
            } else {
                row.append(make("span", "meta", t("adm.alchemy_env_managed")));
            }
            list.append(row);
        });
    }
    function renderKeyPool(pool) {
        const target = el("whale-admin-key-pool");
        if (!target) return;
        const data = pool || {};
        const total = Number(data.keys_configured || 0);
        if (!total) { target.textContent = t("adm.alchemy_no_keys"); return; }
        const reserve = Array.isArray(data.reserve) ? data.reserve.length : 0;
        const parts = [t("adm.alchemy_pool_active", {key: data.active_hint || "—"})];
        parts.push(reserve
            ? t("adm.alchemy_pool_reserve", {count: reserve, keys: data.reserve.join(", ")})
            : t("adm.alchemy_pool_alone"));
        const exhausted = Array.isArray(data.exhausted) ? data.exhausted.length : 0;
        if (exhausted) parts.push(t("adm.alchemy_pool_exhausted", {count: exhausted}));
        if (Number(data.idle_sec || 0) > Number(data.idle_failover_sec || 0) && total > 1) {
            parts.push(t("adm.alchemy_pool_idle", {seconds: number(data.idle_sec)}));
        }
        const switchInfo = data.switch || {};
        if (switchInfo.at && switchInfo.reason) {
            parts.push(t("adm.alchemy_pool_switched",
                {reason: switchInfo.reason, time: when(switchInfo.at)}));
        }
        target.textContent = parts.join(" · ");
    }

    function renderCu(cu) {
        const used = Number(cu.used || 0), limit = Number(cu.limit || 0);
        el("whale-admin-cu-label").textContent = number(used) + " / " + number(limit) + " CU";
        el("whale-admin-cu-meta").textContent = (cu.month || t("adm.alchemy_current_month")) +
            " · " + usagePercent(used, limit).toFixed(1) + "% · " +
            t("adm.alchemy_monthly_estimate", {amount: number(cu.estimated_monthly || 0)});
        el("whale-admin-cu-progress").style.width = usagePercent(used, limit) + "%";
    }
    function renderTronKeys(keys) {
        const target = el("trongrid-admin-keys");
        target.replaceChildren();
        const rows = Array.isArray(keys) ? keys : [];
        if (!rows.length) {
            target.append(make("p", "meta", t("adm.trongrid_no_key")));
            return;
        }
        rows.forEach(key => {
            const row = make("div", "whale-admin-key-row");
            const main = make("div", "whale-admin-key-main");
            main.append(make("strong", "", key.hint || "••••"));
            main.append(make("small", "", key.source === "environment"
                ? t("adm.alchemy_env_key") : t("adm.alchemy_admin_key")));
            row.append(main);
            if (key.source === "admin") {
                const del = make("button", "btn btn-ghost btn-compact whale-admin-key-delete", t("adm.alchemy_delete"));
                del.type = "button";
                del.addEventListener("click", async () => {
                    try {
                        await request("/api/admin/trongrid/key/" + encodeURIComponent(key.id), {method: "DELETE"});
                        el("trongrid-admin-key-status").textContent = t("adm.trongrid_deleted");
                        await load();
                    } catch (_) {
                        el("trongrid-admin-key-status").textContent = t("adm.trongrid_error");
                    }
                });
                row.append(del);
            } else row.append(make("span", "meta", t("adm.alchemy_env_managed")));
            target.append(row);
        });
    }
    function renderWallets(rows, summary, cleanup) {
        const body = el("whale-admin-wallet-rows");
        body.replaceChildren();
        (rows || []).forEach(wallet => {
            const tr = document.createElement("tr");
            tr.append(make("td", "", networkLabel(wallet.chain)));
            tr.append(make("td", "", wallet.name));
            const address = make("td", "whale-admin-wallet-address-cell");
            address.append(make("code", "", wallet.address));
            tr.append(address);
            const sourceLabel = wallet.source === "manual" ? t("adm.cex_wallet_manual")
                : wallet.source === "etherscan" ? t("adm.cex_wallet_etherscan") : t("adm.cex_wallet_defillama");
            tr.append(make("td", "", sourceLabel));
            const actions = make("td", "");
            if (wallet.source === "manual") {
                const edit = make("button", "btn btn-ghost btn-compact", t("adm.cex_wallet_edit"));
                edit.type = "button";
                edit.addEventListener("click", () => {
                    editingWalletId = wallet.id;
                    el("whale-admin-wallet-name").value = wallet.name;
                    el("whale-admin-wallet-address").value = wallet.address;
                    el("whale-admin-wallet-add-chain").value = wallet.chain;
                    el("whale-admin-wallet-add").textContent = t("adm.cex_wallet_save_changes");
                    el("whale-admin-wallet-cancel").hidden = false;
                    el("whale-admin-wallet-name").focus();
                });
                const del = make("button", "btn btn-ghost btn-compact", t("adm.alchemy_delete"));
                del.type = "button";
                del.addEventListener("click", async () => {
                    if (!window.confirm(t("adm.cex_wallet_confirm_delete", {name: wallet.name, address: wallet.address}))) return;
                    del.disabled = true;
                    try {
                        await request("/api/admin/screener/cex-wallets/" + encodeURIComponent(wallet.id), {method: "DELETE"});
                        if (editingWalletId === wallet.id) cancelWalletEdit();
                        await loadWallets();
                    } catch (_) {
                        el("whale-admin-wallet-status").textContent = t("adm.cex_wallet_error");
                        del.disabled = false;
                    }
                });
                actions.append(edit, del);
            }
            tr.append(actions);
            body.append(tr);
        });
        const counts = summary && summary.by_chain ? Object.entries(summary.by_chain)
            .map(([chain, count]) => networkLabel(chain) + " " + number(count)).join(" · ") : "";
        const refreshed = summary && summary.updated_at ? when(summary.updated_at) : t("adm.alchemy_never");
        el("whale-admin-wallet-summary").textContent = t("adm.cex_wallet_summary", {
            total: number(summary && summary.total || 0), counts, updated: refreshed,
        });
        renderWalletCleanup(cleanup, summary);
        const walletStatus = el("whale-admin-wallet-status");
        if (summary && summary.error) {
            walletStatus.textContent = t("adm.cex_wallet_refresh_error_detail", {reason: summary.error});
            walletStatus.title = summary.error;
        } else {
            walletStatus.title = "";
        }
    }
    function cancelWalletEdit() {
        editingWalletId = "";
        el("whale-admin-wallet-name").value = "";
        el("whale-admin-wallet-address").value = "";
        el("whale-admin-wallet-add-chain").value = "ETH";
        el("whale-admin-wallet-add").textContent = t("adm.cex_wallet_add");
        el("whale-admin-wallet-cancel").hidden = true;
    }
    async function loadWallets() {
        const params = new URLSearchParams({
            chain: el("whale-admin-wallet-chain").value || "ALL",
            exchange: el("whale-admin-wallet-search").value.trim(),
        });
        try {
            const data = await request("/api/admin/screener/cex-wallets?" + params);
            renderWallets(data.wallets || [], data.summary || {}, data.cleanup || {});
        } catch (_) {
            el("whale-admin-wallet-status").textContent = t("adm.cex_wallet_error");
        }
    }
    function renderWalletCleanup(cleanup, summary) {
        const target = el("whale-admin-wallet-cleanup");
        const retentionInput = el("whale-admin-wallet-retention");
        const data = cleanup || {};
        const days = Number(data.retention_days || 0);
        if (retentionInput && document.activeElement !== retentionInput) {
            retentionInput.value = String(days);
        }
        if (!target) return;
        const total = Number((summary || {}).total || 0);
        // «когда чистили в последний раз» идёт блоком в cleanup; summary.last_prune —
        // запасной источник для старых ответов сервера
        const prune = data.last_prune || (summary || {}).last_prune || {};
        if (!days) {
            target.textContent = t("adm.cex_wallet_cleanup_off", {count: number(total)});
            return;
        }
        const parts = [t("adm.cex_wallet_cleanup_on", {
            days: number(days), stale: number(data.stale || 0),
            interval: Math.max(1, Math.round(Number(data.interval_sec || 86400) / 3600)),
            count: number(total),
        })];
        if (prune.at && Number(prune.removed || 0) > 0) {
            parts.push(t("adm.cex_wallet_prune_last", {
                removed: number(prune.removed), time: when(prune.at),
            }));
        } else if (prune.at) {
            parts.push(t("adm.cex_wallet_prune_last_none", {time: when(prune.at)}));
        }
        target.textContent = parts.join(" · ");
        if (prune.reason) target.title = String(prune.reason);
    }

    function renderNetworkSwitches(switchData, networks) {
        const target = el("whale-admin-network-switches");
        if (!target) return;
        target.replaceChildren();
        const rows = (switchData && Array.isArray(switchData.networks))
            ? switchData.networks : [];
        if (!rows.length) {
            target.append(make("p", "meta", t("adm.networks_switch_empty")));
            return;
        }
        const health = {};
        (networks || []).forEach(row => { health[row.chain] = row; });
        rows.forEach(row => {
            const line = make("div", "whale-admin-switch-row" + (row.enabled ? "" : " is-off"));
            const main = make("div", "whale-admin-switch-main");
            main.append(make("strong", "", row.title || row.id));
            const details = row.provider === "trongrid" || row.provider === "native_api"
                ? t("adm.networks_switch_free")
                : t("adm.networks_switch_paid");
            const used = Number((health[row.id] || {}).cu_used || 0);
            main.append(make("small", "", details + (used ? " · " + t("adm.alchemy_network_cu", {amount: number(used)}) : "")));
            const label = make("label", "whale-admin-switch");
            const input = document.createElement("input");
            input.type = "checkbox";
            input.checked = Boolean(row.enabled);
            input.setAttribute("aria-label", (row.title || row.id) + " — " + t("adm.networks_switch_title"));
            input.dataset.chain = row.id;
            const knob = make("i");
            const caption = make("span", "", row.enabled ? t("adm.networks_switch_on") : t("adm.networks_switch_off"));
            input.addEventListener("change", () => setNetwork(row.id, input.checked, caption, line));
            label.append(input, knob, caption);
            line.append(main, label);
            target.append(line);
        });
    }
    async function setNetwork(chain, enabled, caption, line) {
        const status = el("whale-admin-network-status");
        caption.textContent = t("adm.networks_switch_saving");
        try {
            const data = await request("/api/admin/screener/networks", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({chain: chain, enabled: Boolean(enabled)}),
            });
            const applied = (data.applied || [])[0] || {};
            caption.textContent = enabled ? t("adm.networks_switch_on") : t("adm.networks_switch_off");
            if (line) line.classList.toggle("is-off", !applied.enabled);
            if (status) status.textContent = t("adm.networks_switch_saved", {
                network: chain, state: applied.enabled ? t("adm.networks_switch_on") : t("adm.networks_switch_off"),
            });
            await load();
        } catch (error) {
            caption.textContent = t("adm.networks_switch_error");
            caption.title = String(error && error.message || "");
            await load();
        }
    }

    function renderNetworks(networks) {
        const target = el("whale-admin-chains");
        target.replaceChildren();
        (networks || []).forEach(network => {
            const status = network.status || "paused";
            // сеть выключена тумблером — это важнее любой прошлой беды в статусе:
            // иначе админ видит «429» у сети, которую сам же и заглушил
            const disabled = network.enabled === false;
            const warning = network.warning_status || "";
            const rateLimited = !disabled && (status === "rate_limited" || warning === "rate_limited");
            const rateMessage = rateLimited
                ? t("adm.alchemy_rate_limited", {seconds: number(network.retry_in_sec || 0)}) : "";
            let visualStatus = disabled ? "disabled" : rateLimited ? "rate_limited" : status;
            let stateName;
            if (disabled) {
                stateName = t("adm.networks_switch_off_state");
            } else if (rateLimited) {
                stateName = status === "online"
                    ? t("adm.alchemy_online") + " · " + rateMessage : rateMessage;
            } else if (status === "online" && network.waiting_for_filters) {
                stateName = t("adm.alchemy_online_waiting_cex_filters");
            } else if (status === "online") {
                stateName = t("adm.alchemy_online");
            } else if (status === "auth_error") {
                stateName = t("adm.alchemy_auth_error");
            } else if (status === "quota_exhausted") {
                stateName = t("adm.alchemy_quota_exhausted");
            } else if (status === "error") {
                stateName = t("adm.alchemy_error_state");
            } else if (status === "network_error") {
                stateName = t("adm.alchemy_network_error");
            } else if (status === "paused") {
                stateName = t("adm.alchemy_paused");
            } else {
                stateName = t("adm.alchemy_waiting");
            }
            const card = make("div", "whale-admin-network status--" + visualStatus);
            card.append(make("strong", "", networkLabel(network.chain)));
            card.append(make("span", "whale-admin-network-status", stateName));
            const parts = [];
            if (rateMessage) parts.push(rateMessage);
            if (disabled) {
                parts.push(t("adm.networks_switch_off_hint"));
            } else if (network.error && network.error !== "no_key") parts.push(network.error);
            else if (network.error === "no_key") parts.push(t("adm.alchemy_no_key"));
            else if (network.last_success) parts.push(
                t("adm.alchemy_last_success", {time: when(network.last_success)}));
            else if (!rateMessage) parts.push(t("adm.alchemy_never"));
            if (network.rpc_code !== undefined && network.rpc_code !== null) {
                parts.push("JSON-RPC code=" + network.rpc_code);
            }
            if (network.http_status && network.http_status !== 200 && !rateLimited) {
                parts.push("HTTP " + network.http_status);
            }
            if (network.cu_used) parts.push(t("adm.alchemy_network_cu", {amount: number(network.cu_used)}));
            const details = make("div", "whale-admin-network-details", parts.join(" · "));
            details.title = parts.join(" · ");
            card.append(details);
            target.append(card);
        });
    }
    async function load() {
        if (loading) return;
        loading = true;
        try {
            const [configData, statsData, keyData, tronData] = await Promise.all([
                request("/api/admin/screener/config"),
                request("/api/admin/alchemy/stats"),
                request("/api/admin/alchemy/keys"),
                request("/api/admin/trongrid/key").catch(() => ({keys: [], available: false})),
            ]);
            const cfg = configData.config || {};
            const cu = statsData.cu || {};
            const keys = statsData.cu && Array.isArray(statsData.cu.keys)
                ? statsData.cu.keys : keyData.keys || [];
            const unavailable = Boolean(configData.vault_error || keyData.vault_error || !keyData.available);
            el("whale-admin-key").disabled = unavailable;
            el("whale-admin-add-key").disabled = unavailable;
            el("trongrid-admin-key").disabled = Boolean(tronData.vault_error || !tronData.available);
            el("trongrid-admin-add-key").disabled = Boolean(tronData.vault_error || !tronData.available);
            if (configData.config) {
                if (document.activeElement !== el("whale-admin-interval")) {
                    el("whale-admin-interval").value = String(cfg.poll_interval_sec || cfg.interval_sec || 60);
                }
                if (document.activeElement !== el("whale-admin-budget")) {
                    el("whale-admin-budget").value = String(cfg.budget_cu);
                }
                el("whale-admin-mode").value = cfg.mode === "economy" ? "economy" : "realtime";
                status.textContent = cfg.phase === "no_key" ? t("adm.alchemy_waiting_for_key") : t("adm.alchemy_config_loaded");
            } else {
                status.textContent = configData.vault_error || t("adm.alchemy_unavailable");
            }
            renderCu(cu);
            renderKeys(keys, cu);
            renderKeyPool(statsData.key_pool || {});
            renderNetworks(statsData.networks || []);
            renderNetworkSwitches(statsData.network_switch || {}, statsData.networks || []);
            renderTronKeys(tronData.keys || []);
            if (keyData.vault_error) keyStatus.textContent = keyData.vault_error;
            if (tronData.vault_error) el("trongrid-admin-key-status").textContent = tronData.vault_error;
            await loadWallets();
        } catch (_) {
            status.textContent = t("adm.alchemy_unavailable");
        } finally {
            loading = false;
        }
    }
    async function enableForAdmin() {
        try {
            const data = await request("/api/auth/me");
            if (!data.user || !data.user.is_admin) {
                card.remove();
                return;
            }
            card.hidden = false;
            await load();
            window.setInterval(() => { if (!document.hidden) load(); }, 30000);
        } catch (_) {
            card.remove();
        }
    }
    el("whale-admin-add-key").addEventListener("click", async () => {
        const input = el("whale-admin-key");
        const value = input.value.trim();
        if (!value) { keyStatus.textContent = t("adm.alchemy_enter_key"); return; }
        keyStatus.textContent = t("adm.alchemy_saving");
        try {
            await request("/api/admin/alchemy/keys", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({key: value}),
            });
            input.value = "";
            keyStatus.textContent = t("adm.alchemy_saved");
            await load();
            window.setTimeout(load, 7000);
        } catch (error) {
            keyStatus.textContent = t("adm.alchemy_save_error") + " · " + error.message;
        }
    });
    el("trongrid-admin-add-key").addEventListener("click", async () => {
        const input = el("trongrid-admin-key");
        const value = input.value.trim();
        const target = el("trongrid-admin-key-status");
        if (!value) { target.textContent = t("adm.trongrid_enter_key"); return; }
        target.textContent = t("adm.alchemy_saving");
        try {
            await request("/api/admin/trongrid/key", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({key: value}),
            });
            input.value = "";
            target.textContent = t("adm.trongrid_saved");
            await load();
        } catch (_) {
            target.textContent = t("adm.trongrid_error");
        }
    });
    el("whale-admin-save").addEventListener("click", async () => {
        try {
            await request("/api/admin/screener/config", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({mode: el("whale-admin-mode").value,
                    poll_interval_sec: Number(el("whale-admin-interval").value),
                    monthly_cu: Number(el("whale-admin-budget").value)}),
            });
            status.textContent = t("adm.alchemy_settings_saved");
            await load();
        } catch (_) {
            status.textContent = t("adm.alchemy_settings_error");
        }
    });
    el("whale-admin-wallet-chain").addEventListener("change", loadWallets);
    el("whale-admin-wallet-search").addEventListener("input", () => {
        window.clearTimeout(el("whale-admin-wallet-search")._timer);
        el("whale-admin-wallet-search")._timer = window.setTimeout(loadWallets, 250);
    });
    el("whale-admin-wallet-refresh").addEventListener("click", async event => {
        const button = event.currentTarget;
        const target = el("whale-admin-wallet-status");
        button.disabled = true;
        target.textContent = t("adm.cex_wallet_refreshing");
        try {
            const result = await request("/api/admin/screener/cex-wallets/refresh", {method: "POST"});
            const messages = [t("adm.cex_wallet_refreshed", {
                count: number(result.defillama_count || 0),
            })];
            if (number(result.etherscan_count || 0) > 0) {
                messages.push(t("adm.cex_wallet_refreshed_etherscan", {
                    count: number(result.etherscan_count),
                }));
            }
            const warnings = Array.isArray(result.warnings) ? result.warnings.filter(Boolean) : [];
            target.textContent = messages.join(" ");
            target.title = warnings.join("\n");
            await loadWallets();
            target.title = warnings.join("\n");
        } catch (error) {
            const reason = String(error && error.message || "");
            target.textContent = reason
                ? t("adm.cex_wallet_refresh_error_detail", {reason})
                : t("adm.cex_wallet_refresh_error");
            target.title = reason;
        } finally {
            button.disabled = false;
        }
    });
    el("whale-admin-wallet-add").addEventListener("click", async () => {
        const target = el("whale-admin-wallet-status");
        const name = el("whale-admin-wallet-name").value.trim();
        const address = el("whale-admin-wallet-address").value.trim();
        const chain = el("whale-admin-wallet-add-chain").value;
        if (!name || !address) { target.textContent = t("adm.cex_wallet_required"); return; }
        const editing = editingWalletId;
        try {
            await request(editing
                ? "/api/admin/screener/cex-wallets/" + encodeURIComponent(editing)
                : "/api/admin/screener/cex-wallets", {
                method: editing ? "PUT" : "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({name, address, chain}),
            });
            target.textContent = t(editing ? "adm.cex_wallet_updated" : "adm.cex_wallet_added");
            cancelWalletEdit();
            await loadWallets();
        } catch (_) {
            target.textContent = t("adm.cex_wallet_invalid");
        }
    });
    el("whale-admin-wallet-retention-save").addEventListener("click", async () => {
        const target = el("whale-admin-wallet-status");
        const days = Number(el("whale-admin-wallet-retention").value);
        if (!Number.isFinite(days) || days < 0 || days > 365) {
            target.textContent = t("adm.cex_wallet_retention_range"); return;
        }
        target.textContent = t("adm.alchemy_saving");
        try {
            await request("/api/admin/screener/cex-wallets/retention", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({days: Math.round(days)}),
            });
            target.textContent = t("adm.cex_wallet_retention_saved", {days: Math.round(days)});
            await loadWallets();
        } catch (_) {
            target.textContent = t("adm.cex_wallet_retention_error");
        }
    });
    el("whale-admin-wallet-prune").addEventListener("click", async event => {
        const button = event.currentTarget;
        const target = el("whale-admin-wallet-status");
        if (!window.confirm(t("adm.cex_wallet_prune_confirm"))) return;
        button.disabled = true;
        target.textContent = t("adm.cex_wallet_prune_running");
        try {
            const result = await request("/api/admin/screener/cex-wallets/prune", {method: "POST"});
            const removed = Number(result.removed || 0);
            if (removed > 0) {
                target.textContent = t("adm.cex_wallet_pruned", {count: number(removed),
                    total: number((result.summary || {}).total || 0)});
            } else {
                target.textContent = String(result.reason || t("adm.cex_wallet_prune_none"));
            }
            await loadWallets();
        } catch (error) {
            target.textContent = t("adm.cex_wallet_prune_error");
            target.title = String(error && error.message || "");
        } finally {
            button.disabled = false;
        }
    });
    el("whale-admin-wallet-cancel").addEventListener("click", cancelWalletEdit);
    enableForAdmin();
})();
