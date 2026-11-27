document.addEventListener("DOMContentLoaded", () => {
    const ORG_ID = "default-org";
    const DEFAULT_USER_ID = "default-user";
    let currentUserId = DEFAULT_USER_ID;
    let currentUser = null;
    let members = [];

    const viewLanding = document.getElementById("view-landing");
    const viewConnect = document.getElementById("view-connect");
    const viewWorkspace = document.getElementById("view-workspace");

    const step1 = document.getElementById("connect-step-1");
    const step2 = document.getElementById("connect-step-2");
    const step3 = document.getElementById("connect-step-3");

    const btnStartConnect = document.getElementById("btn-start-connect");
    const btnDemo = document.getElementById("btn-demo");
    const btnBackLanding = document.getElementById("btn-back-landing");
    const btnChangeDb = document.getElementById("btn-change-db");
    const btnTestConnection = document.getElementById("btn-test-connection");
    const btnDisconnect = document.getElementById("btn-disconnect");
    const btnSendQuery = document.getElementById("btn-send-query");
    const btnNewChat = document.getElementById("btn-new-chat");
    const btnToggleRightPanel = document.getElementById("btn-toggle-right-panel");
    const btnCopySql = document.getElementById("btn-copy-sql");

    const dbOptions = document.querySelectorAll(".db-option");
    const connectionForm = document.getElementById("connection-form");
    const queryInput = document.getElementById("query-input");

    const workspaceEmpty = document.getElementById("workspace-empty");
    const chatHistory = document.getElementById("chat-history");
    const contextPanelWrapper = document.getElementById("context-panel-wrapper");
    const btnCloseContext = document.getElementById("btn-close-context");

    const navTabs = document.querySelectorAll(".nav-tab");
    const workspaceTabs = document.querySelectorAll(".workspace-tab");
    const pendingBanner = document.getElementById("pending-memory-banner");
    const btnReviewPending = document.getElementById("btn-review-pending");
    const btnRefreshMemories = document.getElementById("btn-refresh-memories");
    const btnSearchMemories = document.getElementById("btn-search-memories");
    const memorySearchInput = document.getElementById("memory-search-input");
    const memoryFilterType = document.getElementById("memory-filter-type");
    const memoryFilterStatus = document.getElementById("memory-filter-status");
    const knowledgeForm = document.getElementById("knowledge-form");
    const detailModal = document.getElementById("memory-detail-modal");
    const btnCloseDetailModal = document.getElementById("btn-close-detail-modal");

    let currentDbType = "postgresql";
    let isConnected = false;
    let currentConversationId = null;
    let currentConnectionId = null;
    let lastSqlQuery = "";
    let lastContextDebug = null;

    function showView(viewElement) {
        document.querySelectorAll(".view-section").forEach(v => {
            v.classList.remove("active");
            v.classList.add("hidden");
        });
        viewElement.classList.remove("hidden");
        viewElement.classList.add("active");
    }

    function showConnectStep(stepElement) {
        document.querySelectorAll(".connect-step").forEach(s => {
            s.classList.remove("active");
            s.classList.add("hidden");
        });
        stepElement.classList.remove("hidden");
        stepElement.classList.add("active");
    }

    function switchWorkspaceTab(tabName) {
        navTabs.forEach(t => t.classList.toggle("active", t.dataset.tab === tabName));
        workspaceTabs.forEach(w => {
            const match = w.id === `tab-${tabName}`;
            w.classList.toggle("active", match);
            w.classList.toggle("hidden", !match);
        });
        if (tabName === "memories") {
            loadMemoriesDashboard();
        } else if (tabName === "inspector") {
            renderContextInspector(lastContextDebug);
        } else if (tabName === "evaluation") {
            loadEvaluationDashboard();
        } else if (tabName === "approvals") {
            loadChanges();
        } else if (tabName === "team") {
            loadTeam();
        }
    }

    function enterWorkspace() {
        showView(viewWorkspace);
        loadMembers().then(() => {
            loadConversations();
            refreshMemoryStats();
            refreshApprovalsBadge();
        });
        queryInput.focus();
    }

    navTabs.forEach(tab => {
        tab.addEventListener("click", () => switchWorkspaceTab(tab.dataset.tab));
    });

    btnStartConnect.addEventListener("click", () => {
        showView(viewConnect);
        showConnectStep(step1);
    });

    btnBackLanding.addEventListener("click", () => showView(viewLanding));
    btnChangeDb.addEventListener("click", () => showConnectStep(step1));

    btnDemo.addEventListener("click", enterWorkspace);

    btnNewChat.addEventListener("click", () => {
        currentConversationId = null;
        chatHistory.innerHTML = "";
        workspaceEmpty.style.display = "flex";
        switchWorkspaceTab("chat");
        loadConversations();
    });

    btnToggleRightPanel.addEventListener("click", () => {
        contextPanelWrapper.classList.toggle("collapsed");
    });

    btnReviewPending.addEventListener("click", () => {
        pendingBanner.classList.add("hidden");
        memoryFilterStatus.value = "pending";
        switchWorkspaceTab("memories");
    });

    if (btnCopySql) {
        btnCopySql.addEventListener("click", () => {
            if (lastSqlQuery) {
                navigator.clipboard.writeText(lastSqlQuery);
                btnCopySql.textContent = "Copied!";
                setTimeout(() => (btnCopySql.textContent = "Copy"), 1500);
            }
        });
    }

    if (btnCloseDetailModal) {
        btnCloseDetailModal.addEventListener("click", () => detailModal.classList.add("hidden"));
    }

    dbOptions.forEach(option => {
        option.addEventListener("click", () => {
            const dbType = option.getAttribute("data-db");
            currentDbType = dbType;
            document.getElementById("db-type").value = dbType;

            document.getElementById("selected-db-name").textContent =
                dbType === "postgresql" ? "PostgreSQL" : dbType === "mysql" ? "MySQL" : "SQLite";

            const pgMyFields = document.querySelectorAll(".pg-my-only");
            const sqliteFields = document.querySelectorAll(".sqlite-only");

            if (dbType === "sqlite") {
                pgMyFields.forEach(f => f.classList.add("hidden"));
                sqliteFields.forEach(f => f.classList.remove("hidden"));
                document.getElementById("sqlite-path").required = true;
                document.getElementById("db-host").required = false;
                document.getElementById("db-name").required = false;
                document.getElementById("db-user").required = false;
                document.getElementById("db-pass").required = false;
            } else {
                pgMyFields.forEach(f => f.classList.remove("hidden"));
                sqliteFields.forEach(f => f.classList.add("hidden"));
                document.getElementById("sqlite-path").required = false;
                document.getElementById("db-host").required = true;
                document.getElementById("db-name").required = true;
                document.getElementById("db-user").required = true;
                document.getElementById("db-pass").required = true;
            }
            showConnectStep(step2);
        });
    });

    function getFormPayload() {
        const dbType = document.getElementById("db-type").value;
        const isSqlite = dbType === "sqlite";
        const sqlitePath = document.getElementById("sqlite-path").value;
        const host = isSqlite ? "localhost" : (document.getElementById("db-host").value || "localhost");
        const port = isSqlite ? 0 : parseInt(document.getElementById("db-port").value || "5432", 10);
        const dbName = isSqlite ? (sqlitePath || "cmf_platform.db") : (document.getElementById("db-name").value || "cmf_db");
        const username = isSqlite ? "sqlite" : (document.getElementById("db-user").value || "postgres");
        const password = isSqlite ? "" : (document.getElementById("db-pass").value || "");

        return {
            db_type: dbType,
            host,
            port,
            database_name: dbName,
            username,
            password,
        };
    }

    async function saveConnection(payload) {
        try {
            const res = await fetch("/api/databases/connect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ...payload, name: payload.database_name, organization_id: ORG_ID }),
            });
            if (!res.ok) return null;
            const saved = await res.json();
            return saved.id;
        } catch (_) {
            return null;
        }
    }

    connectionForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        showConnectStep(step3);

        const payload = getFormPayload();
        setTimeout(() => document.getElementById("sync-s1").classList.add("done"), 300);
        setTimeout(() => document.getElementById("sync-s2").classList.add("done"), 700);

        try {
            const response = await fetch("/api/databases/sync-schema", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const result = await response.json();

            if (response.ok) {
                document.getElementById("sync-s3").classList.add("done");
                document.getElementById("sync-status-text").textContent = "Context ready!";
                currentConnectionId = await saveConnection(payload);

                setTimeout(() => {
                    isConnected = true;
                    document.getElementById("workspace-db-name").textContent = payload.database_name;
                    document.getElementById("workspace-db-badge").textContent =
                        payload.db_type === "postgresql" ? "PG" : payload.db_type === "mysql" ? "MY" : "SQ";
                    enterWorkspace();

                    ["sync-s1", "sync-s2", "sync-s3"].forEach(id =>
                        document.getElementById(id).classList.remove("done")
                    );
                    document.getElementById("sync-status-text").textContent = "Connecting to database...";
                }, 500);
            } else {
                alert("Error syncing schema: " + (result.detail || "Unknown error"));
                showConnectStep(step2);
            }
        } catch (error) {
            alert("Network error: " + error.message);
            showConnectStep(step2);
        }
    });

    btnTestConnection.addEventListener("click", async () => {
        const originalText = btnTestConnection.textContent;
        btnTestConnection.textContent = "Testing...";
        btnTestConnection.disabled = true;
        try {
            const resp = await fetch("/api/databases/test", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(getFormPayload()),
            });
            if (resp.ok) {
                btnTestConnection.textContent = "Connected ✓";
                btnTestConnection.style.color = "var(--success)";
            } else {
                const err = await resp.json();
                btnTestConnection.textContent = "Failed";
                btnTestConnection.style.color = "var(--error)";
                alert("Test failed: " + (err.detail || "Unable to connect"));
            }
        } catch (e) {
            btnTestConnection.textContent = "Error";
        } finally {
            setTimeout(() => {
                btnTestConnection.textContent = originalText;
                btnTestConnection.style.color = "";
                btnTestConnection.disabled = false;
            }, 2000);
        }
    });

    btnDisconnect.addEventListener("click", () => {
        isConnected = false;
        currentConversationId = null;
        currentConnectionId = null;
        chatHistory.innerHTML = "";
        workspaceEmpty.style.display = "flex";
        contextPanelWrapper.classList.add("collapsed");
        showView(viewLanding);
    });

    queryInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleQuery();
        }
    });

    btnSendQuery.addEventListener("click", handleQuery);

    document.querySelectorAll(".suggestion-chip").forEach(chip => {
        chip.addEventListener("click", () => {
            queryInput.value = chip.textContent;
            handleQuery();
        });
    });

    async function handleQuery() {
        const queryText = queryInput.value.trim();
        if (!queryText) return;

        workspaceEmpty.style.display = "none";
        appendMessage("user", escapeHtml(queryText));
        queryInput.value = "";

        const loadingId = "loading-" + Date.now();
        appendMessage(
            "bot",
            `<div class="spinner" style="width: 20px; height: 20px; border-width: 2px; margin-bottom: 0;"></div>`,
            loadingId
        );

        const payload = {
            ...getFormPayload(),
            natural_query: queryText,
            conversation_id: currentConversationId,
            connection_id: currentConnectionId,
            organization_id: ORG_ID,
            user_id: currentUserId,
        };

        try {
            const response = await fetch("/api/chat/ask", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            const result = await response.json();
            const loadingMsg = document.getElementById(loadingId);

            if (response.ok) {
                currentConversationId = result.conversation_id;
                lastSqlQuery = result.sql_query || "";
                lastContextDebug = result.context_debug || null;

                if (loadingMsg) {
                    const content = loadingMsg.querySelector(".msg-content");
                    content.innerHTML = renderAssistantReply(result);
                    attachCollapsibleListeners(loadingMsg);
                    bindChangeActions(content);
                }
                if (result.outcome === "change_requested") refreshApprovalsBadge();

                const tables = (result.context_used && result.context_used.schema_tables && result.context_used.schema_tables.length)
                    ? extractTablesFromSQL(result.sql_query)
                    : extractTablesFromSQL(result.sql_query);

                updateContextPanel(result.sql_query, tables, result.context_used);
                contextPanelWrapper.classList.remove("collapsed");
                updateSchemaTreeMock(tables);
                loadConversations();

                setTimeout(checkPendingMemoriesBanner, 1200);
            } else {
                if (loadingMsg) {
                    loadingMsg.querySelector(".msg-content").innerHTML = `
                        <div class="msg-label error-label">Query execution failed</div>
                        <div class="error-msg">${escapeHtml(result.detail || "Failed to execute query.")}</div>
                    `;
                }
            }
        } catch (error) {
            const loadingMsg = document.getElementById(loadingId);
            if (loadingMsg) {
                loadingMsg.querySelector(".msg-content").innerHTML = `
                    <div class="msg-label error-label">Network Error</div>
                    <div class="error-msg">${escapeHtml(error.message)}</div>
                `;
            }
        }
    }

    function renderContextUsedCollapsible(contextUsed) {
        if (!contextUsed) return "";
        const mems = contextUsed.memories || [];
        const memCount = mems.length;
        const turns = contextUsed.conversation_turns || 0;
        const memItems = memCount
            ? mems.map(m => `
                <div class="cu-mem-item">
                    <span class="badge badge-${escapeHtml(m.type)}">${escapeHtml(m.type)}</span>
                    <span class="cu-mem-text">${escapeHtml(m.content)}</span>
                    <span class="cu-mem-score">score: ${Number(m.score).toFixed(2)}</span>
                </div>
            `).join("")
            : `<div class="cu-empty">No persistent memories matched this query (used live schema + ${turns} recent turn(s)).</div>`;

        return `
            <div class="context-used-box">
                <button type="button" class="context-used-toggle">
                    <span>Context Used: ${memCount} memor${memCount === 1 ? "y" : "ies"} • ${turns} turn(s) • ${contextUsed.tokens_used || 0} tokens</span>
                    <span class="toggle-arrow">▸</span>
                </button>
                <div class="context-used-body hidden">
                    ${memItems}
                </div>
            </div>
        `;
    }

    function attachCollapsibleListeners(container) {
        const toggle = container.querySelector(".context-used-toggle");
        const body = container.querySelector(".context-used-body");
        if (toggle && body) {
            toggle.addEventListener("click", () => {
                body.classList.toggle("hidden");
                const arrow = toggle.querySelector(".toggle-arrow");
                if (arrow) arrow.textContent = body.classList.contains("hidden") ? "▸" : "▾";
            });
        }
    }

    function appendMessage(sender, htmlContent, id = null) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `message msg-${sender}`;
        if (id) msgDiv.id = id;

        const contentDiv = document.createElement("div");
        contentDiv.className = "msg-content";
        contentDiv.innerHTML = htmlContent;

        msgDiv.appendChild(contentDiv);
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function renderTable(dataArray) {
        if (!dataArray || dataArray.length === 0) {
            return `<div class="error-msg" style="color: var(--text-secondary); background: transparent; border-color: var(--border-color);">0 rows returned.</div>`;
        }

        const headers = Object.keys(dataArray[0]);
        let tableHTML = `<div class="result-table-wrapper"><table class="result-table"><thead><tr>`;
        headers.forEach(h => (tableHTML += `<th>${escapeHtml(h)}</th>`));
        tableHTML += `</tr></thead><tbody>`;

        dataArray.forEach(row => {
            tableHTML += `<tr>`;
            headers.forEach(h => {
                let val = row[h];
                if (val === null || val === undefined) val = "NULL";
                if (typeof val === "object") val = JSON.stringify(val);
                tableHTML += `<td>${escapeHtml(String(val))}</td>`;
            });
            tableHTML += `</tr>`;
        });

        tableHTML += `</tbody></table></div>`;
        return tableHTML;
    }

    btnCloseContext.addEventListener("click", () => {
        contextPanelWrapper.classList.add("collapsed");
    });

    function updateContextPanel(sql, tables, contextUsed) {
        const sqlCode = document.getElementById("context-sql-code");
        sqlCode.innerHTML = syntaxHighlightSQL(sql || "");
        document.getElementById("context-exec-meta").textContent = `Dialect: ${currentDbType} | Generated & validated`;

        const list = document.getElementById("context-tables-list");
        list.innerHTML = "";
        if (!tables || tables.length === 0) {
            list.innerHTML = `<li class="placeholder">No explicit tables detected</li>`;
        } else {
            tables.forEach(t => {
                const li = document.createElement("li");
                li.textContent = `✓ ${t}`;
                list.appendChild(li);
            });
        }

        const memList = document.getElementById("context-memories-list");
        memList.innerHTML = "";
        const mems = (contextUsed && contextUsed.memories) || [];
        if (mems.length === 0) {
            memList.innerHTML = `<li class="placeholder">None injected</li>`;
        } else {
            mems.forEach(m => {
                const li = document.createElement("li");
                li.className = "mini-mem-item";
                li.innerHTML = `<span class="badge badge-${escapeHtml(m.type)}">${escapeHtml(m.type)}</span> ${escapeHtml(m.content)}`;
                memList.appendChild(li);
            });
        }
    }

    function extractTablesFromSQL(sql) {
        if (!sql) return [];
        const tables = new Set();
        const regex = /(?:FROM|JOIN)\s+([a-zA-Z0-9_]+)/gi;
        let match;
        while ((match = regex.exec(sql)) !== null) {
            tables.add(match[1].toLowerCase());
        }
        return Array.from(tables);
    }

    function updateSchemaTreeMock(tables) {
        const tree = document.getElementById("schema-tree");
        if (tables.length > 0) {
            const placeholder = tree.querySelector(".schema-placeholder");
            if (placeholder) tree.innerHTML = "";
            tables.forEach(t => {
                if (!Array.from(tree.children).some(c => c.textContent === t)) {
                    const el = document.createElement("div");
                    el.className = "schema-item";
                    el.textContent = t;
                    tree.appendChild(el);
                }
            });
        }
    }

    function syntaxHighlightSQL(sql) {
        if (!sql) return "";
        let formatted = sql.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        const keywords = [
            "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "ON",
            "GROUP BY", "ORDER BY", "LIMIT", "OFFSET", "AS", "AND", "OR", "NOT",
            "IN", "IS", "NULL", "DESC", "ASC", "HAVING", "WITH", "DISTINCT"
        ];
        keywords.forEach(kw => {
            const re = new RegExp(`\\b${kw}\\b`, "gi");
            formatted = formatted.replace(re, `<span class="sql-keyword">$&</span>`);
        });
        const funcs = ["SUM", "COUNT", "AVG", "MIN", "MAX", "COALESCE", "DATE_TRUNC", "STRFTIME"];
        funcs.forEach(fn => {
            const re = new RegExp(`\\b${fn}\\b`, "gi");
            formatted = formatted.replace(re, `<span class="sql-function">$&</span>`);
        });
        formatted = formatted.replace(/'([^']*)'/g, `<span class="sql-string">'$1'</span>`);
        formatted = formatted.replace(/\b(\d+)\b/g, `<span class="sql-number">$1</span>`);
        return formatted;
    }

    async function loadConversations() {
        const listEl = document.getElementById("conversations-list");
        try {
            const res = await fetch(`/api/chat/conversations?organization_id=${ORG_ID}&user_id=${currentUserId}`);
            if (!res.ok) return;
            const convs = await res.json();
            if (!convs.length) {
                listEl.innerHTML = `<div class="schema-placeholder">No conversations yet</div>`;
                return;
            }
            listEl.innerHTML = convs.slice(0, 12).map(c => `
                <div class="conv-item ${c.id === currentConversationId ? "active" : ""}" data-id="${c.id}">
                    ${escapeHtml(c.title || "Conversation")}
                </div>
            `).join("");
            listEl.querySelectorAll(".conv-item").forEach(el => {
                el.addEventListener("click", () => loadConversationMessages(el.dataset.id));
            });
        } catch (_) {}
    }

    async function loadConversationMessages(convId) {
        currentConversationId = convId;
        switchWorkspaceTab("chat");
        try {
            const res = await fetch(`/api/chat/conversations/${convId}/messages?organization_id=${ORG_ID}&user_id=${currentUserId}`);
            if (!res.ok) return;
            const msgs = await res.json();
            chatHistory.innerHTML = "";
            workspaceEmpty.style.display = msgs.length ? "none" : "flex";
            msgs.forEach(m => {
                if (m.role === "user") {
                    appendMessage("user", escapeHtml(m.content));
                } else {
                    const sqlPart = m.sql_query
                        ? `<pre class="inline-sql"><code>${syntaxHighlightSQL(m.sql_query)}</code></pre>`
                        : "";
                    appendMessage("bot", `<div class="msg-label">Assistant</div><div class="nl-answer">${escapeHtml(m.content)}</div>${sqlPart}`);
                }
            });
            loadConversations();
        } catch (_) {}
    }

    async function refreshMemoryStats() {
        try {
            const res = await fetch(`/api/memories/statistics?organization_id=${ORG_ID}&user_id=${currentUserId}`);
            if (!res.ok) return;
            const stats = await res.json();
            document.getElementById("stat-total").textContent = stats.total;
            document.getElementById("stat-active").textContent = stats.active;
            document.getElementById("stat-pending").textContent = stats.pending;
            document.getElementById("stat-superseded").textContent = stats.superseded;

            const badge = document.getElementById("nav-pending-badge");
            if (stats.pending > 0) {
                badge.textContent = stats.pending;
                badge.classList.remove("hidden");
            } else {
                badge.classList.add("hidden");
            }
            return stats;
        } catch (_) {}
    }

    async function checkPendingMemoriesBanner() {
        const stats = await refreshMemoryStats();
        if (stats && stats.pending > 0) {
            document.getElementById("pending-banner-text").textContent =
                `You have ${stats.pending} pending memor${stats.pending === 1 ? "y" : "ies"} awaiting review & approval.`;
            pendingBanner.classList.remove("hidden");
        }
    }

    async function loadMemoriesDashboard() {
        await refreshMemoryStats();
        const listEl = document.getElementById("memory-list");
        const mType = memoryFilterType.value;
        const mStatus = memoryFilterStatus.value;
        let url = `/api/memories/?organization_id=${ORG_ID}&user_id=${currentUserId}&limit=100`;
        if (mType) url += `&memory_type=${encodeURIComponent(mType)}`;
        if (mStatus) url += `&status=${encodeURIComponent(mStatus)}`;

        try {
            const res = await fetch(url);
            const items = await res.json();
            renderMemoryCards(items, false);
        } catch (err) {
            listEl.innerHTML = `<div class="error-msg">Failed to load memories: ${escapeHtml(err.message)}</div>`;
        }
    }

    async function searchMemoriesDashboard() {
        const q = memorySearchInput.value.trim();
        if (!q) return loadMemoriesDashboard();

        const res = await fetch("/api/memories/search", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                query: q,
                organization_id: ORG_ID,
                user_id: currentUserId,
                memory_types: memoryFilterType.value ? [memoryFilterType.value] : null,
                include_superseded: true,
                only_approved: false,
                limit: 30,
            }),
        });
        const results = await res.json();
        renderMemoryCards(results, true);
    }

    function renderMemoryCards(items, isSearch) {
        const listEl = document.getElementById("memory-list");
        if (!items || items.length === 0) {
            listEl.innerHTML = `<div class="schema-placeholder">No memories found matching the current filters.</div>`;
            return;
        }

        listEl.innerHTML = items.map(m => {
            const id = m.id || m.memory_id;
            const scoreBadge = isSearch && m.combined_score !== undefined
                ? `<span class="badge badge-score">score: ${Number(m.combined_score).toFixed(2)}</span>`
                : "";
            const approveRejectBtns = m.status === "pending"
                ? `<button class="btn-approve" data-id="${id}">Approve</button>
                   <button class="btn-reject" data-id="${id}">Reject</button>`
                : "";

            return `
                <div class="memory-card">
                    <div class="memory-card-header">
                        <div class="memory-badges">
                            <span class="badge badge-${escapeHtml(m.memory_type)}">${escapeHtml(m.memory_type)}</span>
                            <span class="badge badge-status-${escapeHtml(m.status)}">${escapeHtml(m.status)}</span>
                            <span class="badge badge-scope">${escapeHtml(m.scope)} / ${escapeHtml(m.visibility)}</span>
                            ${m.subject ? `<span class="badge badge-subject">${escapeHtml(m.subject)}</span>` : ""}
                            ${scoreBadge}
                        </div>
                        <div class="memory-actions">
                            <button class="btn-text small btn-view-mem" data-id="${id}">Details</button>
                            ${approveRejectBtns}
                            <button class="btn-text small btn-edit-mem" data-id="${id}">Edit</button>
                            <button class="btn-text small text-danger btn-del-mem" data-id="${id}">Delete</button>
                        </div>
                    </div>
                    <div class="memory-card-content">${escapeHtml(m.content)}</div>
                </div>
            `;
        }).join("");

        listEl.querySelectorAll(".btn-approve").forEach(b => b.addEventListener("click", () => actOnMemory(b.dataset.id, "approve")));
        listEl.querySelectorAll(".btn-reject").forEach(b => b.addEventListener("click", () => actOnMemory(b.dataset.id, "reject")));
        listEl.querySelectorAll(".btn-del-mem").forEach(b => b.addEventListener("click", () => deleteMemory(b.dataset.id)));
        listEl.querySelectorAll(".btn-view-mem").forEach(b => b.addEventListener("click", () => openMemoryDetail(b.dataset.id)));
        listEl.querySelectorAll(".btn-edit-mem").forEach(b => b.addEventListener("click", () => editMemoryPrompt(b.dataset.id)));
    }

    async function actOnMemory(id, action) {
        await fetch(`/api/memories/${id}/${action}?organization_id=${ORG_ID}&user_id=${currentUserId}`, { method: "POST" });
        loadMemoriesDashboard();
    }

    async function deleteMemory(id) {
        if (!confirm("Delete this memory and clear its derived embedding?")) return;
        await fetch(`/api/memories/${id}?organization_id=${ORG_ID}&user_id=${currentUserId}`, { method: "DELETE" });
        loadMemoriesDashboard();
    }

    async function editMemoryPrompt(id) {
        const res = await fetch(`/api/memories/${id}?organization_id=${ORG_ID}&user_id=${currentUserId}`);
        if (!res.ok) return;
        const mem = await res.json();
        const updated = prompt("Edit memory content (a new version will be preserved):", mem.content);
        if (updated && updated.trim() && updated.trim() !== mem.content) {
            await fetch(`/api/memories/${id}?organization_id=${ORG_ID}&user_id=${currentUserId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ content: updated.trim(), change_reason: "Edited from Memory Dashboard" }),
            });
            loadMemoriesDashboard();
        }
    }

    async function openMemoryDetail(id) {
        const [memRes, verRes, audRes] = await Promise.all([
            fetch(`/api/memories/${id}?organization_id=${ORG_ID}&user_id=${currentUserId}`),
            fetch(`/api/memories/${id}/versions?organization_id=${ORG_ID}&user_id=${currentUserId}`),
            fetch(`/api/memories/${id}/audit?organization_id=${ORG_ID}&user_id=${currentUserId}`),
        ]);
        if (!memRes.ok) return;
        const mem = await memRes.json();
        const versions = verRes.ok ? await verRes.json() : [];
        const audits = audRes.ok ? await audRes.json() : [];

        document.getElementById("memory-detail-body").innerHTML = `
            <div class="detail-grid">
                <div><strong>Type:</strong> ${escapeHtml(mem.memory_type)}</div>
                <div><strong>Status:</strong> ${escapeHtml(mem.status)}</div>
                <div><strong>Scope:</strong> ${escapeHtml(mem.scope)} (${escapeHtml(mem.visibility)})</div>
                <div><strong>Subject:</strong> ${escapeHtml(mem.subject || "—")}</div>
                <div><strong>Confidence:</strong> ${mem.confidence} | <strong>Importance:</strong> ${mem.importance}/10</div>
                <div><strong>Connection ID:</strong> ${escapeHtml(mem.connection_id || "Global/Personal")}</div>
                <div><strong>Source Conversation:</strong> ${escapeHtml(mem.source_conversation_id || "Manual")}</div>
                <div><strong>Source Message:</strong> ${escapeHtml(mem.source_message_id || "—")}</div>
                <div><strong>Created:</strong> ${escapeHtml(mem.created_at)}</div>
                <div><strong>Valid To:</strong> ${escapeHtml(mem.valid_to || "Active")}</div>
            </div>
            <div class="detail-section">
                <div class="block-label">Content</div>
                <div class="detail-content-box">${escapeHtml(mem.content)}</div>
            </div>
            <div class="detail-section">
                <div class="block-label">Version History (${versions.length})</div>
                ${versions.map(v => `
                    <div class="version-row">
                        <span class="badge">v${v.version_number}</span>
                        <span>${escapeHtml(v.updated_content)}</span>
                        <small class="text-muted">${escapeHtml(v.change_reason || "")}</small>
                    </div>
                `).join("") || "<div class='schema-placeholder'>No versions</div>"}
            </div>
            <div class="detail-section">
                <div class="block-label">Audit Log (${audits.length})</div>
                ${audits.map(a => `
                    <div class="version-row">
                        <span class="badge">${escapeHtml(a.action)}</span>
                        <span>${escapeHtml(a.details || "")}</span>
                    </div>
                `).join("") || "<div class='schema-placeholder'>No audit events</div>"}
            </div>
        `;
        detailModal.classList.remove("hidden");
    }

    if (btnRefreshMemories) btnRefreshMemories.addEventListener("click", loadMemoriesDashboard);
    if (btnSearchMemories) btnSearchMemories.addEventListener("click", searchMemoriesDashboard);
    if (memoryFilterType) memoryFilterType.addEventListener("change", loadMemoriesDashboard);
    if (memoryFilterStatus) memoryFilterStatus.addEventListener("change", loadMemoriesDashboard);

    function renderContextInspector(debugInfo) {
        const container = document.getElementById("inspector-body");
        if (!debugInfo) {
            container.innerHTML = `<div class="schema-placeholder">Run a query in SQL Assistant to inspect retrieved memories, scores, and token budget usage.</div>`;
            return;
        }
        const retrieved = debugInfo.retrieved_memories || [];
        const excluded = debugInfo.excluded_memories || [];

        container.innerHTML = `
            <div class="stats-grid">
                <div class="stat-card"><div class="stat-label">Retrieved</div><div class="stat-value text-success">${retrieved.length}</div></div>
                <div class="stat-card"><div class="stat-label">Excluded</div><div class="stat-value">${excluded.length}</div></div>
                <div class="stat-card"><div class="stat-label">Tokens Used</div><div class="stat-value">${debugInfo.tokens_used || 0} / ${debugInfo.token_budget || 0}</div></div>
                <div class="stat-card"><div class="stat-label">Recent Turns</div><div class="stat-value">${debugInfo.conversation_turns || 0}</div></div>
            </div>
            <div class="detail-section">
                <div class="block-label">Query Inspected</div>
                <div class="detail-content-box">${escapeHtml(debugInfo.query || "")}</div>
            </div>
            <div class="detail-section">
                <div class="block-label">Retrieved Memories & Hybrid Scores (S = 0.45V + 0.20K + 0.15R + 0.10I + 0.10T)</div>
                ${retrieved.length ? `
                    <div class="result-table-wrapper">
                        <table class="result-table">
                            <thead><tr><th>Type</th><th>Content</th><th>Vec(V)</th><th>Key(K)</th><th>Rec(R)</th><th>Imp(I)</th><th>Type(T)</th><th>Total(S)</th></tr></thead>
                            <tbody>
                                ${retrieved.map(m => `
                                    <tr>
                                        <td>${escapeHtml(m.memory_type)}</td>
                                        <td style="white-space:normal;max-width:340px;">${escapeHtml(m.content)}</td>
                                        <td>${m.vector_similarity}</td>
                                        <td>${m.keyword_relevance}</td>
                                        <td>${m.recency_score}</td>
                                        <td>${m.importance_score}</td>
                                        <td>${m.type_relevance}</td>
                                        <td><strong>${m.combined_score}</strong></td>
                                    </tr>
                                `).join("")}
                            </tbody>
                        </table>
                    </div>
                ` : `<div class="schema-placeholder">No memories selected for this query.</div>`}
            </div>
            <div class="detail-section">
                <div class="block-label">Excluded Memories (${excluded.length})</div>
                ${excluded.length ? excluded.map(e => `
                    <div class="version-row"><span class="badge">${escapeHtml(e.reason)}</span><span>${escapeHtml(e.content)}</span></div>
                `).join("") : `<div class="schema-placeholder">None excluded.</div>`}
            </div>
            <div class="detail-section">
                <div class="block-label">Final Assembled Prompt Preview</div>
                <div class="sql-viewer"><pre><code>${escapeHtml(debugInfo.final_prompt_preview || "")}</code></pre></div>
            </div>
        `;
    }

    if (knowledgeForm) {
        knowledgeForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const payload = {
                memory_type: document.getElementById("bk-type").value,
                subject: document.getElementById("bk-subject").value.trim(),
                content: document.getElementById("bk-content").value.trim(),
                scope: document.getElementById("bk-scope").value,
                visibility: document.getElementById("bk-visibility").value,
                importance: parseInt(document.getElementById("bk-importance").value || "8", 10),
                confidence: 0.98,
                connection_id: currentConnectionId,
                auto_approve: document.getElementById("bk-auto-approve").checked,
            };

            const res = await fetch(`/api/memories/?organization_id=${ORG_ID}&user_id=${currentUserId}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            if (res.ok) {
                knowledgeForm.reset();
                await refreshMemoryStats();
                alert(payload.auto_approve
                    ? "Business knowledge saved & activated!"
                    : "Business knowledge saved to pending review queue!");
                switchWorkspaceTab("memories");
            } else {
                const err = await res.json();
                alert("Failed to save business knowledge: " + (err.detail || "Unknown error"));
            }
        });
    }

    const ROLE_LABELS = { owner: "Owner", admin: "Admin", analyst: "Analyst", viewer: "Employee" };
    const ACTION_LABELS = { update: "Update", insert: "Add", delete: "Delete" };
    const STATUS_LABELS = { pending: "Waiting for approval", executed: "Applied", rejected: "Rejected", failed: "Failed" };
    const actingUserSelect = document.getElementById("acting-user");
    const changeFilter = document.getElementById("change-filter");
    const memberForm = document.getElementById("member-form");

    function isAdminUser() {
        return Boolean(currentUser && (currentUser.role === "owner" || currentUser.role === "admin"));
    }

    function userQuery() {
        return `organization_id=${encodeURIComponent(ORG_ID)}&user_id=${encodeURIComponent(currentUserId)}`;
    }

    function errorText(body) {
        if (body && Array.isArray(body.detail)) return body.detail.map(d => d.msg).join("; ");
        return (body && body.detail) || "Something went wrong.";
    }

    function storedUserId() {
        try {
            return localStorage.getItem("cmf_acting_user");
        } catch (_) {
            return null;
        }
    }

    function rememberUserId(id) {
        try {
            localStorage.setItem("cmf_acting_user", id);
        } catch (_) {}
    }

    async function loadMembers() {
        try {
            const res = await fetch(`/api/team/members?organization_id=${ORG_ID}&user_id=${DEFAULT_USER_ID}`);
            if (!res.ok) return;
            members = await res.json();
        } catch (_) {
            return;
        }
        const preferred = storedUserId();
        if (preferred && members.some(m => m.id === preferred)) currentUserId = preferred;
        if (!members.some(m => m.id === currentUserId) && members.length) currentUserId = members[0].id;
        actingUserSelect.innerHTML = members
            .map(m => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.full_name)}</option>`)
            .join("");
        actingUserSelect.value = currentUserId;
        applyIdentity();
    }

    function applyIdentity() {
        currentUser = members.find(m => m.id === currentUserId) || null;
        document.getElementById("acting-role").textContent =
            currentUser ? (ROLE_LABELS[currentUser.role] || currentUser.role) : "";
        document.querySelectorAll(".admin-only").forEach(el => el.classList.toggle("hidden", !isAdminUser()));
        document.getElementById("approvals-subtitle").textContent = isAdminUser()
            ? "Changes your team asked for in chat. Nothing changes until you approve."
            : "Changes you asked for in chat. An admin approves them before anything changes.";
    }

    actingUserSelect.addEventListener("change", () => {
        currentUserId = actingUserSelect.value;
        rememberUserId(currentUserId);
        applyIdentity();
        currentConversationId = null;
        chatHistory.innerHTML = "";
        workspaceEmpty.style.display = "flex";
        const activeTab = document.querySelector(".nav-tab.active");
        const tab = activeTab ? activeTab.dataset.tab : "chat";
        switchWorkspaceTab(tab === "team" && !isAdminUser() ? "chat" : tab);
        loadConversations();
        refreshMemoryStats();
        refreshApprovalsBadge();
    });

    function renderAssistantReply(result) {
        const answer = `<div class="nl-answer">${escapeHtml(result.natural_response || "")}</div>`;
        if (result.outcome === "change_requested" && result.change_request) {
            return `<div class="msg-label">Change request</div>${answer}${renderChangeCard(result.change_request)}`;
        }
        if (result.outcome === "not_allowed" || result.outcome === "no_match") {
            const label = result.outcome === "no_match" ? "Nothing to change" : "Not allowed";
            return `<div class="msg-label">${label}</div><div class="notice-msg">${escapeHtml(result.natural_response || "")}</div>`;
        }
        const notes = [];
        if (result.truncated) notes.push(`Showing the first ${(result.data || []).length} rows only.`);
        if (result.hidden_columns && result.hidden_columns.length) {
            notes.push(`Hidden for privacy: ${result.hidden_columns.join(", ")}.`);
        }
        const noteHtml = notes.length ? `<div class="result-note">${escapeHtml(notes.join(" "))}</div>` : "";
        return `
            <div class="msg-label">Answer</div>
            ${answer}
            ${renderContextUsedCollapsible(result.context_used)}
            ${renderTable(result.data)}
            ${noteHtml}
        `;
    }

    function renderChangeCard(cr) {
        const preview = cr.preview || {};
        const changes = (preview.changes || [])
            .map(c => `<li><span class="mono">${escapeHtml(c.column)}</span> → <span class="mono">${escapeHtml(c.value)}</span></li>`)
            .join("");
        const current = preview.rows && preview.rows.length
            ? `<div class="block-label">Rows that will change (current values)</div>${renderTable(preview.rows)}`
            : "";
        const added = preview.new_rows && preview.new_rows.length
            ? `<div class="block-label">New rows</div>${renderTable(preview.new_rows)}`
            : "";
        const status = STATUS_LABELS[cr.status] || cr.status;
        const meta = [
            cr.requester ? `Asked by ${escapeHtml(cr.requester)}` : "",
            cr.reviewer ? `${escapeHtml(status)} by ${escapeHtml(cr.reviewer)}` : "",
        ].filter(Boolean).join(" · ");
        const actions = cr.status === "pending" && isAdminUser()
            ? `<div class="memory-actions">
                   <button class="btn-approve" data-change="${escapeHtml(cr.id)}" data-decision="approve">Approve</button>
                   <button class="btn-reject" data-change="${escapeHtml(cr.id)}" data-decision="reject">Reject</button>
               </div>`
            : "";
        return `<div class="change-card" data-id="${escapeHtml(cr.id)}">
                <div class="memory-card-header">
                    <div class="memory-badges">
                        <span class="badge badge-action-${escapeHtml(cr.action)}">${escapeHtml(ACTION_LABELS[cr.action] || cr.action)}</span>
                        <strong class="change-title">${escapeHtml(cr.summary)}</strong>
                    </div>
                    <span class="badge badge-status-${escapeHtml(cr.status)}">${escapeHtml(status)}</span>
                </div>
                <div class="change-question">“${escapeHtml(cr.question)}”</div>
                ${meta ? `<div class="change-meta">${meta}</div>` : ""}
                ${changes ? `<ul class="change-list">${changes}</ul>` : ""}
                ${current}
                ${added}
                ${cr.review_note ? `<div class="change-meta">Note: ${escapeHtml(cr.review_note)}</div>` : ""}
                ${cr.error ? `<div class="error-msg">${escapeHtml(cr.error)}</div>` : ""}
                <details class="change-sql"><summary>Statement</summary><pre><code>${syntaxHighlightSQL(cr.sql)}</code></pre></details>
                ${actions}
            </div>`;
    }

    function bindChangeActions(container) {
        container.querySelectorAll("[data-change]").forEach(btn => {
            btn.addEventListener("click", () => reviewChange(btn.dataset.change, btn.dataset.decision, btn.closest(".change-card")));
        });
    }

    async function reviewChange(id, decision, card) {
        let note = null;
        if (decision === "reject") {
            note = prompt("Reason for rejecting (optional):");
            if (note === null) return;
        } else if (!confirm("Apply this change to the database now?")) {
            return;
        }
        const buttons = card.querySelectorAll("[data-change]");
        buttons.forEach(b => (b.disabled = true));
        try {
            const res = await fetch(`/api/changes/${encodeURIComponent(id)}/${decision}?${userQuery()}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ note }),
            });
            const body = await res.json();
            if (!res.ok) {
                alert(errorText(body));
                buttons.forEach(b => (b.disabled = false));
                return;
            }
            const holder = document.createElement("div");
            holder.innerHTML = renderChangeCard(body);
            card.replaceWith(holder.firstElementChild);
        } catch (err) {
            alert("Network error: " + err.message);
            buttons.forEach(b => (b.disabled = false));
        }
        refreshApprovalsBadge();
    }

    async function loadChanges() {
        const list = document.getElementById("change-list");
        const status = changeFilter.value;
        list.innerHTML = `<div class="schema-placeholder">Loading...</div>`;
        try {
            const res = await fetch(`/api/changes?${userQuery()}${status ? `&status=${encodeURIComponent(status)}` : ""}`);
            const items = res.ok ? await res.json() : [];
            list.innerHTML = items.length
                ? items.map(item => renderChangeCard(item)).join("")
                : `<div class="schema-placeholder">${status === "pending" ? "Nothing is waiting for approval." : "No change requests yet."}</div>`;
            bindChangeActions(list);
        } catch (err) {
            list.innerHTML = `<div class="error-msg">Failed to load: ${escapeHtml(err.message)}</div>`;
        }
        refreshApprovalsBadge();
    }

    async function refreshApprovalsBadge() {
        const badge = document.getElementById("nav-approvals-badge");
        try {
            const res = await fetch(`/api/changes?${userQuery()}&status=pending`);
            if (!res.ok) return;
            const items = await res.json();
            badge.textContent = items.length;
            badge.classList.toggle("hidden", items.length === 0);
        } catch (_) {}
    }

    changeFilter.addEventListener("change", loadChanges);
    document.getElementById("btn-refresh-changes").addEventListener("click", loadChanges);

    function renderMemberCard(member, grants) {
        const id = escapeHtml(member.id);
        const name = escapeHtml(member.full_name);
        const isAdminRole = member.role === "owner" || member.role === "admin";
        const roleControl = member.role === "owner"
            ? `<span class="badge">Owner</span>`
            : `<select class="member-role" data-member="${id}" aria-label="Role for ${name}">
                   ${["viewer", "analyst", "admin"].map(r => `<option value="${r}" ${r === member.role ? "selected" : ""}>${ROLE_LABELS[r]}</option>`).join("")}
               </select>`;
        const chips = grants.length
            ? grants.map(g => `
                <span class="chip">
                    ${escapeHtml(ACTION_LABELS[g.action] || g.action)} · ${g.table_name === "*" ? "all tables" : escapeHtml(g.table_name)}
                    <button type="button" class="chip-remove" data-revoke="${escapeHtml(g.id)}" aria-label="Remove permission">×</button>
                </span>`).join("")
            : `<span class="change-meta">Read only</span>`;
        const access = isAdminRole
            ? `<div class="change-meta">Admins can ask for any change and approve requests.</div>`
            : `<div class="grant-chips">${chips}</div>
               <form class="grant-form" data-member="${id}">
                   <select name="action" aria-label="Action">
                       <option value="update">Update</option>
                       <option value="insert">Add</option>
                       <option value="delete">Delete</option>
                   </select>
                   <input name="table" placeholder="Table, or * for all" required aria-label="Table">
                   <button type="submit" class="btn-secondary small-btn">Allow</button>
               </form>`;
        return `
            <div class="memory-card member-card">
                <div class="memory-card-header">
                    <div>
                        <strong>${name}</strong>
                        <div class="change-meta">${escapeHtml(member.email)}</div>
                    </div>
                    ${roleControl}
                </div>
                ${access}
            </div>`;
    }

    async function loadTeam() {
        const list = document.getElementById("member-list");
        if (!isAdminUser()) {
            list.innerHTML = "";
            return;
        }
        await loadMembers();
        let grants = [];
        try {
            const res = await fetch(`/api/team/grants?${userQuery()}`);
            if (res.ok) grants = await res.json();
        } catch (_) {}
        list.innerHTML = members.map(m => renderMemberCard(m, grants.filter(g => g.user_id === m.id))).join("");

        list.querySelectorAll(".member-role").forEach(select => {
            select.addEventListener("change", async () => {
                const res = await fetch(`/api/team/members/${encodeURIComponent(select.dataset.member)}?${userQuery()}`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ role: select.value }),
                });
                if (!res.ok) alert(errorText(await res.json()));
                loadTeam();
            });
        });

        list.querySelectorAll(".grant-form").forEach(form => {
            form.addEventListener("submit", async (e) => {
                e.preventDefault();
                const res = await fetch(`/api/team/grants?${userQuery()}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        user_id: form.dataset.member,
                        action: form.elements.action.value,
                        table_name: form.elements.table.value.trim() || "*",
                    }),
                });
                if (!res.ok) alert(errorText(await res.json()));
                loadTeam();
            });
        });

        list.querySelectorAll("[data-revoke]").forEach(btn => {
            btn.addEventListener("click", async () => {
                await fetch(`/api/team/grants/${encodeURIComponent(btn.dataset.revoke)}?${userQuery()}`, { method: "DELETE" });
                loadTeam();
            });
        });
    }

    memberForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const res = await fetch(`/api/team/members?${userQuery()}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                full_name: document.getElementById("member-name").value.trim(),
                email: document.getElementById("member-email").value.trim(),
                role: document.getElementById("member-role").value,
            }),
        });
        if (!res.ok) {
            alert(errorText(await res.json()));
            return;
        }
        memberForm.reset();
        loadTeam();
    });

    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    const btnRefreshEvaluation = document.getElementById("btn-refresh-evaluation");
    const btnRunEvaluation = document.getElementById("btn-run-evaluation");

    async function loadEvaluationDashboard(selectedCategory = "") {
        const container = document.getElementById("evaluation-body");
        if (!container) return;
        container.innerHTML = `<div class="schema-placeholder">Loading evaluation results...</div>`;

        const qs = selectedCategory ? `?category=${encodeURIComponent(selectedCategory)}` : "";
        const res = await fetch(`/api/evaluation/results${qs}`);
        if (!res.ok) {
            container.innerHTML = `<div class="schema-placeholder">Failed to load evaluation results.</div>`;
            return;
        }
        const data = await res.json();
        renderEvaluationDashboard(data, selectedCategory);
    }

    function renderEvaluationDashboard(data, selectedCategory = "") {
        const container = document.getElementById("evaluation-body");
        if (!container) return;

        if (!data.results_exists && !data.dataset_exists) {
            container.innerHTML = `
                <div class="schema-placeholder">
                    No <code>evaluation_results.json</code> or <code>evaluation_dataset.json</code> found yet.
                    Click <strong>Run Benchmark</strong> above to generate them.
                </div>
            `;
            return;
        }

        const results = data.results || {};
        const retrieval = results.retrieval_metrics || {};
        const comp = results.comparison || {};
        const baseline = comp.baseline_nl2sql_without_memory || {};
        const contextAware = comp.context_aware_nl2sql_with_memory || {};
        const categories = data.categories || [];
        const dataset = data.dataset || [];

        const pct = (v) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—");

        container.innerHTML = `
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">Dataset Cases</div>
                    <div class="stat-value">${results.dataset_size ?? data.total_dataset_count ?? 0}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Context-Aware Accuracy</div>
                    <div class="stat-value text-success">${pct(contextAware.rule_aware_sql_accuracy)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Baseline Accuracy</div>
                    <div class="stat-value text-warning">${pct(baseline.rule_aware_sql_accuracy)}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Isolation Pass Rate</div>
                    <div class="stat-value text-success">${pct(contextAware.isolation_pass_rate)}</div>
                </div>
            </div>

            <div class="detail-section">
                <div class="block-label">Retrieval & Comparison Metrics (evaluation_results.json)</div>
                <div class="result-table-wrapper">
                    <table class="result-table">
                        <thead>
                            <tr>
                                <th>Precision@3</th>
                                <th>Recall@3</th>
                                <th>MRR</th>
                                <th>Avg Latency</th>
                                <th>Update Accuracy</th>
                                <th>Avg Tokens (Base / Ctx)</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td>${pct(retrieval.precision_at_3)}</td>
                                <td>${pct(retrieval.recall_at_3)}</td>
                                <td>${retrieval.mrr ?? "—"}</td>
                                <td>${retrieval.avg_retrieval_latency_ms ?? "—"} ms</td>
                                <td>${pct(contextAware.memory_update_accuracy)}</td>
                                <td>${baseline.avg_prompt_tokens ?? "—"} / ${contextAware.avg_prompt_tokens ?? "—"}</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="detail-section">
                <div class="memory-toolbar" style="margin-bottom:12px;">
                    <select id="eval-category-filter">
                        <option value="">All Categories (${data.total_dataset_count || 0})</option>
                        ${categories.map(c => `<option value="${escapeHtml(c)}" ${c === selectedCategory ? "selected" : ""}>${escapeHtml(c)}</option>`).join("")}
                    </select>
                    <span class="text-muted" style="font-size:12px;align-self:center;">Showing ${dataset.length} cases from evaluation_dataset.json</span>
                </div>
                <div class="result-table-wrapper" style="max-height:380px;overflow:auto;">
                    <table class="result-table">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Category</th>
                                <th>Query</th>
                                <th>Memory / Setup</th>
                                <th>Expected</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${dataset.map(item => `
                                <tr>
                                    <td>${escapeHtml(item.id || "")}</td>
                                    <td><span class="badge">${escapeHtml(item.category || "")}</span></td>
                                    <td style="white-space:normal;max-width:260px;">${escapeHtml(item.query || item.followup_query || "")}</td>
                                    <td style="white-space:normal;max-width:260px;">${escapeHtml(item.memory || item.updated_memory || (item.memories ? item.memories.join(" | ") : "") || "")}</td>
                                    <td style="white-space:normal;max-width:220px;">${escapeHtml(item.expected_sql_pattern || item.expected_memory || item.expected_topic || item.isolation_type || "")}</td>
                                </tr>
                            `).join("")}
                        </tbody>
                    </table>
                </div>
            </div>
        `;

        const catSelect = document.getElementById("eval-category-filter");
        if (catSelect) {
            catSelect.addEventListener("change", () => loadEvaluationDashboard(catSelect.value));
        }
    }

    if (btnRefreshEvaluation) {
        btnRefreshEvaluation.addEventListener("click", () => {
            const cat = document.getElementById("eval-category-filter")?.value || "";
            loadEvaluationDashboard(cat);
        });
    }

    if (btnRunEvaluation) {
        btnRunEvaluation.addEventListener("click", async () => {
            const orig = btnRunEvaluation.textContent;
            btnRunEvaluation.textContent = "Running...";
            btnRunEvaluation.disabled = true;
            try {
                const res = await fetch("/api/evaluation/run", { method: "POST" });
                if (res.ok) {
                    const data = await res.json();
                    renderEvaluationDashboard(data, "");
                } else {
                    const err = await res.json();
                    alert("Benchmark failed: " + (err.detail || "Unknown error"));
                }
            } catch (e) {
                alert("Network error running benchmark: " + e.message);
            } finally {
                btnRunEvaluation.textContent = orig;
                btnRunEvaluation.disabled = false;
            }
        });
    }
});
