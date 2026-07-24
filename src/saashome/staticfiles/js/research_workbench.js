(function () {
    "use strict";

    const liveRegion = document.getElementById("review-live-region");

    function showNotice(message, kind) {
        if (!liveRegion) return;
        const notice = document.createElement("div");
        const isError = kind === "error";
        notice.className = [
            "pointer-events-auto", "mb-2", "rounded-xl", "border", "px-4", "py-3",
            "text-sm", "font-bold", "shadow-lg", "transition",
            isError ? "border-red-200" : "border-emerald-200",
            isError ? "bg-red-50" : "bg-emerald-50",
            isError ? "text-red-900" : "text-emerald-900",
        ].join(" ");
        notice.textContent = message;
        liveRegion.appendChild(notice);
        window.setTimeout(function () {
            notice.remove();
        }, isError ? 7000 : 3200);
    }

    function updateCounters(counts) {
        ["pending", "accepted", "edited", "rejected", "gaps"].forEach(function (key) {
            const node = document.getElementById("review-count-" + key);
            if (node) node.textContent = counts[key];
        });
        const value = document.getElementById("review-progress-value");
        const summary = document.getElementById("review-progress-summary");
        const progress = document.getElementById("review-progress");
        const bar = document.getElementById("review-progress-bar");
        if (value) value.textContent = counts.progress + "%";
        if (summary) summary.textContent = counts.reviewed + " z " + counts.total + " pól sprawdzonych";
        if (progress) progress.setAttribute("aria-valuenow", counts.progress);
        if (bar) bar.style.width = counts.progress + "%";
    }

    function updateDecisionButtons(article, decision) {
        const selectedAction = {
            accepted: "accept",
            accepted_edited: "accept",
            rejected: "reject",
            documented_gap: "gap",
        }[decision];
        article.querySelectorAll("[data-review-action-form]").forEach(function (form) {
            const button = form.querySelector("button");
            if (!button) return;
            const selected = form.dataset.action === selectedAction;
            button.setAttribute("aria-pressed", selected ? "true" : "false");
            button.classList.remove("ring-2", "ring-emerald-400", "ring-red-400", "ring-slate-400");
            if (selected) {
                button.classList.add("ring-2");
                button.classList.add(
                    form.dataset.action === "accept" ? "ring-emerald-400" :
                    form.dataset.action === "reject" ? "ring-red-400" : "ring-slate-400"
                );
            }
        });
        const reset = article.querySelector("[data-review-reset-form]");
        if (reset) reset.classList.toggle("hidden", decision === "pending");
    }

    function updateFieldState(article, field, edited) {
        article.dataset.decision = field.decision;
        article.classList.remove("bg-emerald-50/30", "bg-red-50/30", "bg-slate-50");
        if (field.decision === "accepted" || field.decision === "accepted_edited") {
            article.classList.add("bg-emerald-50/30");
        } else if (field.decision === "rejected") {
            article.classList.add("bg-red-50/30");
        } else if (field.decision === "documented_gap") {
            article.classList.add("bg-slate-50");
        }

        const label = article.querySelector('[data-role="decision-label"]');
        if (label) {
            label.textContent = field.decision_label;
            label.classList.remove("text-emerald-700", "text-red-700", "text-slate-500");
            label.classList.add(
                field.decision === "accepted" || field.decision === "accepted_edited"
                    ? "text-emerald-700"
                    : field.decision === "rejected" ? "text-red-700" : "text-slate-500"
            );
        }

        article.querySelectorAll('input[name="field_version"]').forEach(function (input) {
            input.value = field.updated_at;
        });
        updateDecisionButtons(article, field.decision);

        if (edited) {
            const valueContainer = article.querySelector('[data-role="effective-value"]');
            if (valueContainer) {
                valueContainer.replaceChildren();
                const value = document.createElement("p");
                value.className = "mt-2 whitespace-pre-line text-base font-bold text-blue-950";
                value.textContent = field.effective_value;
                const caption = document.createElement("p");
                caption.className = "mt-1 text-xs font-bold text-blue-700";
                caption.textContent = "Wartość wpisana przez researchera";
                valueContainer.append(value, caption);
            }
            const note = article.querySelector('[data-role="reviewer-note"]');
            if (note) {
                const noteValue = note.querySelector("span");
                if (noteValue) noteValue.textContent = field.reviewer_note;
                note.classList.toggle("hidden", !field.reviewer_note);
            }
            const editor = article.querySelector('[id^="edit-"]');
            if (editor) editor.classList.add("hidden");
        }
    }

    function prependEvent(event) {
        const list = document.getElementById("review-event-list");
        if (!list || !event) return;
        const empty = list.querySelector("[data-empty-event]");
        if (empty) empty.remove();
        const item = document.createElement("li");
        item.className = "border-l-2 border-emerald-300 pl-3";
        const message = document.createElement("p");
        message.className = "text-xs font-semibold text-slate-800";
        message.textContent = event.message;
        const meta = document.createElement("p");
        meta.className = "mt-1 text-[10px] text-slate-500";
        meta.textContent = event.created_at + (event.actor ? " · " + event.actor : "");
        item.append(message, meta);
        list.prepend(item);
        while (list.children.length > 20) list.lastElementChild.remove();
    }

    async function submitReviewForm(form) {
        if (form.dataset.submitting === "true") return;
        const article = form.closest("[data-review-field]");
        if (!article) return;
        form.dataset.submitting = "true";
        article.setAttribute("aria-busy", "true");
        const buttons = Array.from(article.querySelectorAll("button"));
        buttons.forEach(function (button) { button.disabled = true; });

        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: new FormData(form),
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
            });
            let data;
            try {
                data = await response.json();
            } catch (error) {
                throw new Error("Serwer zwrócił nieoczekiwaną odpowiedź.");
            }
            if (!response.ok || !data.ok) {
                throw new Error(data.error || "Nie udało się zapisać decyzji.");
            }
            updateFieldState(article, data.field, form.matches("[data-review-edit-form]"));
            updateCounters(data.counts);
            prependEvent(data.event);
            showNotice(data.message, "success");
        } catch (error) {
            showNotice(error.message || "Nie udało się połączyć z serwerem. Spróbuj ponownie.", "error");
        } finally {
            delete form.dataset.submitting;
            article.removeAttribute("aria-busy");
            buttons.forEach(function (button) { button.disabled = false; });
        }
    }

    document.addEventListener("submit", function (event) {
        const form = event.target.closest("[data-review-action-form], [data-review-edit-form]");
        if (!form) return;
        event.preventDefault();
        submitReviewForm(form);
    });
})();
