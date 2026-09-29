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
        HYPERLIQUID: ["screener.network_hyperliquid", "Hyperliquid Core"],
    };
    let loading = false;
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
            " · " + usagePercent(used, limit).toFixed(1) + "%";
        el("whale-admin-cu-progress").style.width = usagePercent(used, limit) + "%";
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
            const [configData, statsData, keyData] = await Promise.all([
                request("/api/admin/screener/config"),
                request("/api/admin/alchemy/stats"),
                request("/api/admin/alchemy/keys"),
            ]);
            const cfg = configData.config || {};
            const cu = statsData.cu || {};
            const keys = statsData.cu && Array.isArray(statsData.cu.keys)
                ? statsData.cu.keys : keyData.keys || [];
            const unavailable = Boolean(configData.vault_error || keyData.vault_error || !keyData.available);
            el("whale-admin-key").disabled = unavailable;
            el("whale-admin-add-key").disabled = unavailable;
            if (configData.config) {
                if (document.activeElement !== el("whale-admin-interval")) {
                    el("whale-admin-interval").value = String(cfg.interval_sec / 60);
                }
                if (document.activeElement !== el("whale-admin-budget")) {
                    el("whale-admin-budget").value = String(cfg.budget_cu);
                }
                el("whale-admin-mode").value = cfg.mode || "cex_only";
                status.textContent = cfg.phase === "no_key" ? t("adm.alchemy_waiting_for_key") : t("adm.alchemy_config_loaded");
            } else {
                status.textContent = configData.vault_error || t("adm.alchemy_unavailable");
            }
            renderCu(cu);
            renderKeys(keys, cu);
            renderNetworks(statsData.networks || []);
            if (keyData.vault_error) keyStatus.textContent = keyData.vault_error;
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
    enableForAdmin();
})();
