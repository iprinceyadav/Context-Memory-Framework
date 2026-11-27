document.addEventListener("click", (event) => {
    const target = event.target;

    if (target.closest("[data-toggle-panel]")) {
        document.getElementById("context-panel-wrapper")?.classList.toggle("collapsed");
    }
    if (target.closest("[data-close-panel]")) {
        document.getElementById("context-panel-wrapper")?.classList.add("collapsed");
    }
    if (target.closest("[data-close-modal]") || target.matches("[data-modal]")) {
        target.closest("[data-modal]")?.remove();
    }

    const copyButton = target.closest("[data-copy-target]");
    if (copyButton) {
        const code = document.querySelector(copyButton.dataset.copyTarget);
        if (code && navigator.clipboard) navigator.clipboard.writeText(code.textContent);
    }
});

document.addEventListener("keydown", (event) => {
    if (event.target.id === "query-input" && event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        event.target.form.requestSubmit();
    }
});

document.addEventListener("htmx:beforeRequest", (event) => {
    const form = event.target;
    if (form.id !== "chat-form") return;
    const input = document.getElementById("query-input");
    const pending = document.createElement("div");
    pending.id = "pending-exchange";
    pending.innerHTML = '<div class="message msg-user"><div class="msg-content"></div></div>' +
        '<div class="message msg-bot"><div class="msg-content"><div class="spinner small-spinner"></div></div></div>';
    pending.querySelector(".msg-user .msg-content").textContent = input.value.trim();
    document.getElementById("chat-history").appendChild(pending);
    document.getElementById("workspace-empty")?.remove();
    input.value = "";
});

document.addEventListener("htmx:afterRequest", (event) => {
    if (event.target.id !== "chat-form") return;
    document.getElementById("pending-exchange")?.remove();
    const container = document.getElementById("chat-container");
    container.scrollTop = container.scrollHeight;
});
