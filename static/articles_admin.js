/**
 * LiqScope — 📰 «Статьи» в админке: список материалов и редактор.
 *
 * Работает с /api/admin/articles. Что важно знать про этот раздел:
 *
 *   * английская версия — не украшение: без неё статья не публикуется.
 *     Перевод делает ИИ («🌐 Перевести ИИ»), но если сервис молчит, панель
 *     говорит об этом прямо, и английский текст можно вписать руками;
 *   * обложка одна: одно фото показывается и на русской, и на английской
 *     странице;
 *   * расписание: дата и время в поле «Опубликовать» — статья выйдет сама
 *     (в каналы при этом ничего не уйдёт);
 *   * в Telegram каналы статья уходит только по кнопке: русская версия в
 *     русский канал, английская — в английский. Повторная отправка возможна,
 *     панель предупредит, что уже уходило.
 *
 * Разметка текста — облегчённый markdown: `## заголовок`, `**жирный**`,
 * `*курсив*`, `[ссылка](адрес)`, `- список`, `> цитата`, `---` разделитель.
 * Предпросмотр рисуется здесь же, чтобы не бегать на сервер за каждой буквой.
 */
(function () {
    "use strict";

    var API = "/api/admin/articles";
    var LIST_LIMIT = 9;                 // сколько статей показываем сразу (потом «показать ещё»)

    var host = null;
    var state = { items: [], channels: {}, ai: {}, limits: {}, bot: false, site: "" };
    var current = null;                 // выбранная статья (данные с сервера)
    var photo = null;                   // новое фото: {data, name} — уйдёт вместе с сохранением
    var removePhoto = false;
    var busy = false;
    var shown = LIST_LIMIT;

    function el(id) { return document.getElementById(id); }

    function esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function api(url, opts) {
        return fetch(url, Object.assign({ credentials: "same-origin" }, opts || {}))
            .then(function (r) {
                return r.json().catch(function () { return {}; })
                    .then(function (j) { return { ok: r.ok, status: r.status, body: j }; });
            });
    }

    function post(url, body) {
        return api(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body || {})
        });
    }

    function timeLabel(ts) {
        if (!ts) return "";
        try {
            return new Intl.DateTimeFormat("ru-RU", {
                day: "2-digit", month: "2-digit", year: "2-digit",
                hour: "2-digit", minute: "2-digit"
            }).format(new Date(ts * 1000));
        } catch (e) { return ""; }
    }

    /** Дата и время для <input type="datetime-local"> — в поясе браузера. */
    function localInput(ts) {
        if (!ts) return "";
        var d = new Date(ts * 1000);
        function p(n) { return (n < 10 ? "0" : "") + n; }
        return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate())
            + "T" + p(d.getHours()) + ":" + p(d.getMinutes());
    }

    /* ---------- предпросмотр markdown (те же правила, что на сервере) ---------- */

    function inlineMd(text) {
        var out = esc(text);
        out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (m, t, u) {
            return '<a href="' + u.replace(/"/g, "%22") + '" target="_blank" rel="noopener nofollow">'
                + t + "</a>";
        });
        out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        out = out.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
        out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
        return out;
    }

    function mdHtml(text) {
        var lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
        var out = [], para = [], list = null, quote = [];

        function flushPara() {
            if (para.length) { out.push("<p>" + inlineMd(para.join("<br>")) + "</p>"); para = []; }
        }
        function flushList() {
            if (list) { out.push("<ul>" + list.join("") + "</ul>"); list = null; }
        }
        function flushQuote() {
            if (quote.length) { out.push("<blockquote>" + inlineMd(quote.join("<br>")) + "</blockquote>"); quote = []; }
        }
        function flushAll() { flushPara(); flushList(); flushQuote(); }

        for (var i = 0; i < lines.length; i++) {
            var line = lines[i], t = line.trim();
            if (!t) { flushAll(); continue; }
            if (/^(---+|\*\*\*+)$/.test(t)) { flushAll(); out.push("<hr>"); continue; }
            var h = /^(#{1,3})\s+(.*)$/.exec(t);
            if (h) { flushAll(); out.push("<h3>" + inlineMd(h[2]) + "</h3>"); continue; }
            if (/^[-*]\s+/.test(t)) { flushPara(); flushQuote(); if (!list) list = []; list.push("<li>" + inlineMd(t.replace(/^[-*]\s+/, "")) + "</li>"); continue; }
            if (t.charAt(0) === ">") { flushPara(); flushList(); quote.push(t.replace(/^>\s?/, "")); continue; }
            flushList(); flushQuote(); para.push(t);
        }
        flushAll();
        return out.join("");
    }

    /* ---------- список статей ---------- */

    function statusBadge(it) {
        if (it.status === "published") return '<span class="art-badge pub">опубликована</span>';
        if (it.status === "scheduled") return '<span class="art-badge plan">по расписанию</span>';
        return '<span class="art-badge">черновик</span>';
    }

    function sentBadge(it) {
        var sent = it.sent || {};
        var langs = [];
        if (sent.ru) langs.push("RU");
        if (sent.en) langs.push("EN");
        if (!langs.length) return "";
        return '<span class="art-badge tg">в TG: ' + langs.join("+") + "</span>";
    }

    function renderList() {
        var box = el("art-list");
        if (!box) return;
        var items = state.items || [];
        if (!items.length) {
            box.innerHTML = '<p class="meta">Статей пока нет. Нажмите «✏️ Новая статья», '
                + "напишите текст и опубликуйте — материал появится в разделе «Статьи».</p>";
            return;
        }
        var html = items.slice(0, shown).map(function (it) {
            var title = (it.titles && it.titles.ru) || (it.titles && it.titles.en) || "(без заголовка)";
            var when = it.published_at ? "опубликована " + timeLabel(it.published_at)
                : (it.publish_at ? "выйдет " + timeLabel(it.publish_at) : "черновик");
            var act = current && current.id === it.id ? " on" : "";
            return '<div class="art-row' + act + '" data-id="' + esc(it.id) + '">'
                + '<span class="ar-title">' + esc(title) + "</span>"
                + statusBadge(it) + sentBadge(it)
                + '<span class="ar-sub">' + esc(when)
                + ((it.titles && it.titles.en) ? " · есть английская версия" : " · английской версии нет")
                + "</span></div>";
        }).join("");
        if (items.length > shown) {
            html += '<button class="btn btn-small" type="button" id="art-more">Показать ещё ('
                + (items.length - shown) + ")</button>";
        }
        box.innerHTML = html;
        var more = el("art-more");
        if (more) more.addEventListener("click", function () { shown += LIST_LIMIT; renderList(); });
        Array.prototype.forEach.call(box.querySelectorAll(".art-row"), function (row) {
            row.addEventListener("click", function () { select(row.getAttribute("data-id")); });
        });
    }

    /* ---------- редактор ---------- */

    function editorHtml() {
        var it = current || { titles: {}, texts: {}, sent: {} };
        var titles = it.titles || {}, texts = it.texts || {};
        var photoUrl = photo ? photo.data
            : (!removePhoto && it.photo && it.photo.url ? it.photo.url : "");
        var published = it.status === "published";
        var scheduled = it.status === "scheduled";
        return ''
            + '<p class="meta" style="margin-bottom:8px">'
            + (it.id ? "Адрес статьи: <b>/articles/" + esc(it.id) + "</b>" : "Новая статья — адрес получит имя из заголовка")
            + "</p>"
            + '<label>Заголовок — русский</label>'
            + '<input class="search" id="art-title-ru" maxlength="160" autocomplete="off" value="' + esc(titles.ru || "") + '" placeholder="О чём статья, одной строкой">'
            + '<label>Заголовок — English</label>'
            + '<input class="search" id="art-title-en" maxlength="160" autocomplete="off" value="' + esc(titles.en || "") + '" placeholder="Появится сам после перевода ИИ — или впишите вручную">'
            + '<label>Текст статьи — русский</label>'
            + '<div class="art-md-bar" data-md="ru">'
            + '<button type="button" data-ins="## " data-line="1">H2</button>'
            + '<button type="button" data-ins="**" data-wrap="1"><b>B</b></button>'
            + '<button type="button" data-ins="*" data-wrap="1"><i>I</i></button>'
            + '<button type="button" data-ins="[текст](https://)" data-sel="1">Ссылка</button>'
            + '<button type="button" data-ins="- " data-line="1">Список</button>'
            + '<button type="button" data-ins="> " data-line="1">Цитата</button>'
            + '<button type="button" data-ins="\n---\n" data-line="1">Разделитель</button>'
            + "</div>"
            + '<textarea id="art-text-ru" rows="14" placeholder="Абзацы разделяются пустой строкой. ## — подзаголовок, **жирный**, *курсив*, [ссылка](адрес), - пункт списка, > цитата, --- разделитель.">' + esc(texts.ru || "") + "</textarea>"
            + '<p class="meta" id="art-len-ru"></p>'
            + '<div class="art-prev" id="art-prev-ru"></div>'
            + '<label style="margin-top:12px">Текст статьи — English</label>'
            + '<div class="row-actions" style="margin-top:6px">'
            + '<button class="btn btn-small" type="button" id="art-translate">🌐 Перевести ИИ</button>'
            + '<span class="meta" id="art-ai-state"></span>'
            + "</div>"
            + '<textarea id="art-text-en" rows="12" placeholder="Here goes the English version. Пусто — статья не опубликуется, пока перевод не появится.">' + esc(texts.en || "") + "</textarea>"
            + '<p class="meta" id="art-len-en"></p>'
            + '<div class="art-prev" id="art-prev-en"></div>'
            + '<label style="margin-top:12px">Обложка (одна на обе версии)</label>'
            + '<div class="row-actions" style="margin-top:6px">'
            + '<label class="btn btn-primary file-btn">Загрузить фото'
            + '<input id="art-photo" type="file" accept="image/jpeg,image/jpg,image/png,image/webp,image/*"></label>'
            + '<button class="btn" type="button" id="art-photo-clear">Убрать фото</button>'
            + "</div>"
            + '<p class="meta">jpg, png, webp — до 12 МБ. Фото показывается и в русской, и в английской версии статьи.</p>'
            + '<div class="art-photo-prev" id="art-photo-prev">'
            + (photoUrl ? '<img src="' + esc(photoUrl) + '" alt="обложка статьи">' : '<p class="meta">фото не выбрано</p>')
            + "</div>"
            + '<label style="margin-top:12px">Публикация на сайте</label>'
            + '<div class="row-actions" style="margin-top:6px">'
            + '<input class="search" id="art-when" type="datetime-local" style="max-width:230px" value="' + esc(localInput(it.publish_at || 0)) + '">'
            + '<button class="btn btn-small" type="button" id="art-now">Сейчас</button>'
            + '<button class="btn btn-small" type="button" id="art-clear-when">Без расписания</button>'
            + "</div>"
            + '<p class="meta">Пусто или «Сейчас» — статья выйдет сразу при публикации. '
            + "Дата в будущем — выйдет сама, и только на сайте: в каналы ничего не уйдёт, это всегда кнопка.</p>"
            + '<div class="row-actions" style="margin-top:10px">'
            + '<button class="btn btn-primary" type="button" id="art-save">💾 Сохранить</button>'
            + '<button class="btn btn-primary" type="button" id="art-publish">✅ Опубликовать</button>'
            + (published ? '<button class="btn" type="button" id="art-unpublish">⬇ Снять с сайта</button>' : "")
            + '<button class="btn" type="button" id="art-send">📤 Отправить в Telegram</button>'
            + (it.id ? '<button class="btn" type="button" id="art-delete">🗑 Удалить</button>' : "")
            + "</div>"
            + '<p class="meta" id="art-sent"></p>'
            + '<p class="meta">«Отправить в Telegram» отправит русскую версию в русский канал, '
            + "английскую — в английский. Можно нажать ещё раз, если что-то пошло не так.</p>"
            + (scheduled ? '<p class="meta">⏳ Статья ждёт своего времени.</p>' : "");
    }

    function renderEditor() {
        var box = el("art-editor");
        if (!box) return;
        box.innerHTML = editorHtml();
        var ru = el("art-text-ru"), en = el("art-text-en");
        wireMarkdownBar(ru);
        bind("input", ru, function () { preview(ru, "art-prev-ru", "art-len-ru"); });
        bind("input", en, function () { preview(en, "art-prev-en", "art-len-en"); });
        preview(ru, "art-prev-ru", "art-len-ru");
        preview(en, "art-prev-en", "art-len-en");
        bind("change", el("art-photo"), onPhoto);
        on("art-photo-clear", clearPhoto);
        on("art-now", function () { el("art-when").value = localInput(Date.now() / 1000); });
        on("art-clear-when", function () { el("art-when").value = ""; });
        on("art-save", function () { save(false); });
        on("art-publish", function () { save(true); });
        on("art-translate", doTranslate);
        on("art-send", doSend);
        on("art-unpublish", doUnpublish);
        on("art-delete", doDelete);
        var sent = current && current.sent || {};
        var parts = [];
        if (sent.ru) parts.push("русская версия в канал " + timeLabel(sent.ru));
        if (sent.en) parts.push("английская " + timeLabel(sent.en));
        var line = el("art-sent");
        if (line) {
            line.textContent = parts.length
                ? "Уже отправлено: " + parts.join(", ") + ". Повторная отправка — только по кнопке."
                : "";
        }
        var ai = el("art-ai-state");
        if (ai) {
            if (state.ai && state.ai.enabled) {
                ai.textContent = "ИИ на связи — перевод придёт в правое окно.";
            } else {
                ai.textContent = "ИИ недоступен: без английской версии публикации не будет — впишите её сами.";
            }
        }
    }

    function bind(ev, node, fn) { if (node) node.addEventListener(ev, fn); }

    function on(id, fn) { var node = el(id); if (node) node.addEventListener("click", fn); }

    function preview(node, outId, lenId) {
        var out = el(outId);
        var text = node ? node.value : "";
        if (out) out.innerHTML = mdHtml(text) || '<span class="meta">предпросмотр появится здесь</span>';
        var len = el(lenId);
        if (len) len.textContent = text.length + " знаков";
    }

    function wireMarkdownBar(node) {
        var bar = document.querySelector('[data-md="ru"]');
        if (!bar || !node) return;
        bar.addEventListener("click", function (ev) {
            var btn = ev.target.closest("button[data-ins]");
            if (!btn) return;
            ev.preventDefault();
            insert(node, btn.getAttribute("data-ins"), btn.hasAttribute("data-wrap"),
                btn.hasAttribute("data-line"), btn.hasAttribute("data-sel"));
        });
    }

    /** Вставка разметки в текст: обёртка для жирного, префикс строки для списка. */
    function insert(node, ins, wrap, line, sel) {
        var start = node.selectionStart || 0, end = node.selectionEnd || 0;
        var before = node.value.slice(0, start), chosen = node.value.slice(start, end);
        var after = node.value.slice(end);
        if (wrap) {
            node.value = before + ins + chosen + ins + after;
            node.selectionStart = start + ins.length;
            node.selectionEnd = start + ins.length + chosen.length;
        } else if (line) {
            var atLine = before.lastIndexOf("\n") === before.length - 1 || before === "";
            var prefix = atLine ? ins : "\n" + ins;
            node.value = before + prefix + chosen + after;
            node.selectionStart = node.selectionEnd = start + prefix.length + chosen.length;
        } else if (sel && chosen) {
            node.value = before + "[" + chosen + "](https://)" + after;
            node.selectionStart = node.selectionEnd = start + chosen.length + 3;
        } else {
            node.value = before + ins + chosen + after;
            node.selectionStart = node.selectionEnd = start + ins.length;
        }
        node.dispatchEvent(new Event("input"));
        node.focus();
    }

    /* ---------- действия ---------- */

    function form() {
        return {
            id: current && current.id ? current.id : "",
            title_ru: (el("art-title-ru") || {}).value || "",
            title_en: (el("art-title-en") || {}).value || "",
            text_ru: (el("art-text-ru") || {}).value || "",
            text_en: (el("art-text-en") || {}).value || "",
            publish_at: (el("art-when") || {}).value || "",
            photo: photo ? photo.data : "",
            photo_name: photo ? photo.name : "",
            remove_photo: removePhoto
        };
    }

    function say(text, kind) {
        var line = el("art-hint");
        if (!line) return;
        line.innerHTML = text || "";
        line.className = "meta";
        if (kind) line.classList.add("art-note", kind);
    }

    function lock(flag) {
        busy = flag;
        var ids = ["art-save", "art-publish", "art-translate", "art-send",
            "art-unpublish", "art-delete", "art-new"];
        ids.forEach(function (id) { var n = el(id); if (n) n.disabled = flag; });
    }

    function save(publish) {
        if (busy) return;
        var data = form();
        data.publish = !!publish;
        if (!data.title_ru && !data.title_en) { say("Нужен хотя бы русский заголовок.", "warn"); return; }
        lock(true);
        say(publish ? "Публикую…" : "Сохраняю…");
        post(API + "/save", data).then(function (r) {
            lock(false);
            var b = r.body || {};
            if (!b.ok) {
                // Статья могла создаться, а сорваться уже публикация: тогда
                // берём её себе, иначе следующее «Сохранить» наплодит дубликатов
                if (b.item) {
                    current = b.item;
                    photo = null;
                    removePhoto = false;
                    load(true);
                }
                say(esc(b.hint || "Не получилось сохранить статью."), "err");
                return;
            }
            photo = null;
            removePhoto = false;
            current = b.item || null;
            say(publish
                ? (b.ai === "translated"
                    ? "Опубликовано, английскую версию сделал ИИ."
                    : "Статья опубликована на сайте.")
                : "Сохранено. Английская версия: "
                    + ((current && current.titles && current.titles.en)
                        ? "есть." : "пока нет — без неё публикация не пройдёт."), "ok");
            load(true);
        });
    }

    function doTranslate() {
        if (busy) return;
        var titleRu = (el("art-title-ru") || {}).value || "";
        var textRu = (el("art-text-ru") || {}).value || "";
        if (!textRu.trim()) { say("Сначала напишите русский текст.", "warn"); return; }
        lock(true);
        say("ИИ переводит статью…");
        function run(id) {
            post(API + "/" + encodeURIComponent(id) + "/translate",
                { title_ru: titleRu, text_ru: textRu })
                .then(function (r) {
                    lock(false);
                    var b = r.body || {};
                    if (!b.ok) {
                        say(esc(b.hint || "ИИ не справился с переводом."), "err");
                        return;
                    }
                    current = b.item || current;
                    say("Готово: английская версия в правом окне. Проверьте заголовок и текст.", "ok");
                    load(true);
                });
        }
        if (current && current.id) { run(current.id); return; }
        // Новой статье перевода не сделать, пока её нет: сначала сохраняем
        post(API + "/save", form()).then(function (r) {
            if (!r.body || !r.body.ok) {
                lock(false);
                say(esc((r.body || {}).hint || "Сначала сохраните статью."), "err");
                return;
            }
            current = r.body.item;
            run(current.id);
        });
    }

    function doSend() {
        if (busy) return;
        if (!current || !current.id) { say("Сначала сохраните статью.", "warn"); return; }
        var already = current.sent && (current.sent.ru || current.sent.en);
        var note = already ? "Эта статья уже уходила в канал. Отправить ещё раз? " : "";
        if (note && !window.confirm(note)) return;
        lock(true);
        say("Отправляю в каналы…");
        post(API + "/" + encodeURIComponent(current.id) + "/send", { langs: ["ru", "en"] })
            .then(function (r) {
                lock(false);
                var b = r.body || {};
                if (!b.ok) {
                    var why = (b.errors || []).join("; ") || b.hint || "Telegram не принял статью.";
                    say(esc(why), "err");
                    if (b.item) { current = b.item; renderEditor(); }
                    load(true);
                    return;
                }
                var res = b.results || {};
                var parts = [];
                if (res.ru && res.ru.ok) parts.push("русская версия → канал" + (res.ru.again ? " (повторно)" : ""));
                if (res.en && res.en.ok) parts.push("английская → английский канал" + (res.en.again ? " (повторно)" : ""));
                say("Отправлено: " + parts.join(", ") + ".", "ok");
                if (b.item) current = b.item;
                load(true);
            });
    }

    function doUnpublish() {
        if (busy || !current || !current.id) return;
        if (!window.confirm("Снять статью с сайта? Страница станет недоступна, в каналах посты останутся.")) return;
        lock(true);
        post(API + "/" + encodeURIComponent(current.id) + "/unpublish", {})
            .then(function (r) {
                lock(false);
                if (!r.body || !r.body.ok) { say("Не получилось снять статью.", "err"); return; }
                current = r.body.item;
                say("Статья снята с сайта. В каналах её посты не трогаем.", "ok");
                load(true);
            });
    }

    function doDelete() {
        if (busy || !current || !current.id) return;
        if (!window.confirm("Удалить статью совсем? Текст и фото пропадут, вернуть их будет нельзя.")) return;
        lock(true);
        post(API + "/" + encodeURIComponent(current.id) + "/delete", {})
            .then(function (r) {
                lock(false);
                if (!r.body || !r.body.ok) { say("Не получилось удалить статью.", "err"); return; }
                say("Статья удалена.", "ok");
                current = null; photo = null; removePhoto = false;
                load(true);
                renderEditor();
            });
    }

    function onPhoto(ev) {
        var file = ev.target.files && ev.target.files[0];
        if (!file) return;
        if (file.size > 12 * 1024 * 1024) { say("Фото больше 12 МБ — уменьшите его.", "err"); return; }
        var reader = new FileReader();
        reader.onload = function () {
            photo = { data: String(reader.result || ""), name: file.name || "article.jpg" };
            removePhoto = false;
            var box = el("art-photo-prev");
            if (box) box.innerHTML = '<img src="' + esc(photo.data) + '" alt="обложка статьи">';
            say("Фото выбрано. Нажмите «Сохранить» — и оно появится у статьи.", "ok");
        };
        reader.readAsDataURL(file);
    }

    function clearPhoto() {
        photo = null;
        removePhoto = true;
        var box = el("art-photo-prev");
        if (box) box.innerHTML = '<p class="meta">фото убрано (сохранится после «Сохранить»)</p>';
    }

    function select(id) {
        if (busy) return;
        var item = (state.items || []).filter(function (x) { return x.id === id; })[0];
        if (!item) return;
        current = item;
        photo = null;
        removePhoto = false;
        renderList();
        renderEditor();
        say("");
    }

    /* ---------- загрузка ---------- */

    function load(keepCurrent) {
        return api(API).then(function (r) {
            var b = r.body || {};
            if (!b.ok) {
                if (el("art-list")) {
                    el("art-list").innerHTML = '<p class="meta">'
                        + (r.status === 401 ? "Нужен вход в админку." : "Список статей не загрузился.")
                        + "</p>";
                }
                return;
            }
            state = b;
            if (keepCurrent && current && current.id) {
                var fresh = (b.items || []).filter(function (x) { return x.id === current.id; })[0];
                if (fresh) current = fresh;
            }
            var line = el("art-state");
            if (line) {
                var st = b.stats || {};
                var bits = [];
                bits.push("всего: " + (st.count || 0));
                bits.push("опубликовано: " + (st.published || 0));
                if (st.scheduled) bits.push("по расписанию: " + st.scheduled);
                if (st.draft) bits.push("черновиков: " + st.draft);
                if (st.with_en !== undefined) bits.push("с переводом: " + st.with_en);
                var ch = [];
                if (b.channels && b.channels.ru && b.channels.ru.ready) ch.push("RU-канал");
                if (b.channels && b.channels.en && b.channels.en.ready) ch.push("EN-канал");
                bits.push(ch.length ? "каналы: " + ch.join(" + ") : "каналы не привязаны");
                if (b.ai && b.ai.enabled) bits.push("ИИ на связи");
                else bits.push("ИИ недоступен (перевод вручную)");
                line.textContent = bits.join(" · ");
            }
            renderList();
            if (!current) {
                var box = el("art-editor");
                if (box) box.innerHTML = '<p class="meta">Выберите статью слева или создайте новую — '
                    + "текст, фото и расписание живут в редакторе.</p>";
            }
        });
    }

    function boot() {
        host = el("articles-admin");
        if (!host) return;
        on("art-new", function () {
            if (busy) return;
            current = { titles: {}, texts: {}, sent: {}, status: "draft", publish_at: 0 };
            photo = null;
            removePhoto = false;
            shown = LIST_LIMIT;
            renderList();
            renderEditor();
            say("Новая статья: заполните русский текст, затем «Перевести ИИ» и «Опубликовать».");
        });
        on("art-reload", function () { if (!busy) { current = null; load(); } });
        load();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
