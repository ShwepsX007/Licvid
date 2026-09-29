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
        if (!response.ok) throw new Error(body.error || "HTTP " + response.status);
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
                : key.state === "cooldown" ? t("adm.alchemy_cooldown") : t("adm.alchemy_ready");
            main.append(make("small", "", source + " · " + stateText + (key.reason ? " · " + key.reason : "")));
            const usage = make("div", "whale-admin-key-usage");
            const used = Number(key.reserved_cu || 0);
            usage.append(make("span", "", t("adm.alchemy_key_cu", {used: number(used), limit: number(limit)})));
            const track = make("div", "whale-admin-key-progress");
            const fill = make("i");
            fill.style.width = usagePercent(used, limit) + "%";
            track.append(fill);
            usage.append(track);
            row.append(main, usage);
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
    function renderWallets(rows, summary) {
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
            renderWallets(data.wallets || [], data.summary || {});
        } catch (_) {
            el("whale-admin-wallet-status").textContent = t("adm.cex_wallet_error");
        }
    }
    function renderNetworks(networks) {
        const target = el("whale-admin-chains");
        target.replaceChildren();
        (networks || []).forEach(network => {
            const stateName = network.status === "online" ? t("adm.alchemy_online")
                : network.status === "error" ? t("adm.alchemy_error_state") : t("adm.alchemy_waiting");
            const card = make("div", "whale-admin-network status--" + (network.status || "waiting"));
            card.append(make("strong", "", networkLabel(network.chain)));
            card.append(make("span", "whale-admin-network-status", stateName));
            const details = network.error && network.error !== "no_key"
                ? network.error : network.last_success
                    ? t("adm.alchemy_last_success", {time: when(network.last_success)})
                    : network.error === "no_key" ? t("adm.alchemy_no_key") : t("adm.alchemy_never");
            card.append(make("div", "", details));
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
                    el("whale-admin-interval").value = String(cfg.history_interval_min || cfg.interval_sec / 60 || 5);
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
            renderNetworks(statsData.networks || []);
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
                    interval_min: Number(el("whale-admin-interval").value),
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
            target.textContent = t("adm.cex_wallet_refreshed", {count: number(result.after || 0)});
            await loadWallets();
        } catch (_) {
            target.textContent = t("adm.cex_wallet_refresh_error");
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
    el("whale-admin-wallet-cancel").addEventListener("click", cancelWalletEdit);
    enableForAdmin();
})();
