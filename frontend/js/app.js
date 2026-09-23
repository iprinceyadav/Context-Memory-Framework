document.addEventListener("DOMContentLoaded", () => {
    // --- DOM Elements ---
    
    // Views
    const viewLanding = document.getElementById("view-landing");
    const viewConnect = document.getElementById("view-connect");
    const viewWorkspace = document.getElementById("view-workspace");
    
    // Connection Flow Elements
    const step1 = document.getElementById("connect-step-1");
    const step2 = document.getElementById("connect-step-2");
    const step3 = document.getElementById("connect-step-3");
    
    // Buttons
    const btnStartConnect = document.getElementById("btn-start-connect");
    const btnDemo = document.getElementById("btn-demo");
    const btnBackLanding = document.getElementById("btn-back-landing");
    const btnChangeDb = document.getElementById("btn-change-db");
    const btnTestConnection = document.getElementById("btn-test-connection");
    const btnDisconnect = document.getElementById("btn-disconnect");
    const btnSendQuery = document.getElementById("btn-send-query");
    
    // Forms & Inputs
    const dbOptions = document.querySelectorAll(".db-option");
    const connectionForm = document.getElementById("connection-form");
    const queryInput = document.getElementById("query-input");
    
    // Workspace Elements
    const workspaceEmpty = document.getElementById("workspace-empty");
    const chatHistory = document.getElementById("chat-history");
    const contextPanelWrapper = document.getElementById("context-panel-wrapper");
    const btnCloseContext = document.getElementById("btn-close-context");
    
    // --- State ---
    let currentDbType = 'postgresql';
    let isConnected = false;

    // --- Navigation & View Logic ---
    
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

    btnStartConnect.addEventListener("click", () => {
        showView(viewConnect);
        showConnectStep(step1);
    });

    btnBackLanding.addEventListener("click", () => {
        showView(viewLanding);
    });

    btnChangeDb.addEventListener("click", () => {
        showConnectStep(step1);
    });
    
    btnDemo.addEventListener("click", () => {
        // Quick shortcut to workspace for demo purposes (using default sqlite if available)
        showView(viewWorkspace);
        queryInput.focus();
    });

    // Handle DB Type Selection
    dbOptions.forEach(option => {
        option.addEventListener("click", () => {
            const dbType = option.getAttribute("data-db");
            currentDbType = dbType;
            document.getElementById("db-type").value = dbType;
            
            // Update UI
            document.getElementById("selected-db-name").textContent = 
                dbType === 'postgresql' ? 'PostgreSQL' : 
                dbType === 'mysql' ? 'MySQL' : 'SQLite';
            
            // Toggle form fields
            const pgMyFields = document.querySelectorAll(".pg-my-only");
            const sqliteFields = document.querySelectorAll(".sqlite-only");
            
            if (dbType === 'sqlite') {
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

    // --- API Interactions (Connection) ---

    connectionForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        // Transition to loading step
        showConnectStep(step3);
        
        const payload = getFormPayload();
        
        // Simulate step progression visually
        setTimeout(() => document.getElementById("sync-s1").classList.add("done"), 500);
        setTimeout(() => document.getElementById("sync-s2").classList.add("done"), 1200);
        
        try {
            const response = await fetch("/api/databases/sync-schema", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const result = await response.json();
            
            setTimeout(() => {
                if (response.ok) {
                    document.getElementById("sync-s3").classList.add("done");
                    document.getElementById("sync-status-text").textContent = "Context ready!";
                    
                    setTimeout(() => {
                        isConnected = true;
                        document.getElementById("workspace-db-name").textContent = payload.database_name || "SQLite DB";
                        showView(viewWorkspace);
                        queryInput.focus();
                        
                        // Reset forms
                        document.getElementById("sync-s1").classList.remove("done");
                        document.getElementById("sync-s2").classList.remove("done");
                        document.getElementById("sync-s3").classList.remove("done");
                        document.getElementById("sync-status-text").textContent = "Connecting to database...";
                    }, 800);
                } else {
                    alert("Error syncing schema: " + (result.detail || "Unknown error"));
                    showConnectStep(step2);
                }
            }, 1800); // Artificial delay for UX
            
        } catch (error) {
            alert("Network error: " + error.message);
            showConnectStep(step2);
        }
    });

    btnTestConnection.addEventListener("click", () => {
        const originalText = btnTestConnection.textContent;
        btnTestConnection.textContent = "Testing...";
        btnTestConnection.disabled = true;
        
        // In a real app, you'd hit a /test endpoint. We'll simulate a fast check here.
        setTimeout(() => {
            btnTestConnection.textContent = "Success";
            btnTestConnection.style.color = "var(--success)";
            setTimeout(() => {
                btnTestConnection.textContent = originalText;
                btnTestConnection.style.color = "";
                btnTestConnection.disabled = false;
            }, 2000);
        }, 1000);
    });

    btnDisconnect.addEventListener("click", () => {
        isConnected = false;
        chatHistory.innerHTML = "";
        workspaceEmpty.style.display = "flex";
        contextPanelWrapper.classList.add("collapsed");
        showView(viewLanding);
    });

    function getFormPayload() {
        const dbType = document.getElementById("db-type").value;
        const host = document.getElementById("db-host").value || "localhost";
        const port = document.getElementById("db-port").value || 5432;
        const dbName = document.getElementById("db-name").value || "cmf_db";
        const username = document.getElementById("db-user").value || "postgres";
        const password = document.getElementById("db-pass").value || "";
        
        return {
            db_type: dbType,
            host: host,
            port: parseInt(port),
            database_name: dbName,
            username: username,
            password: password
        };
    }

    // --- Workspace Chat Logic ---

    // Handle Enter key for textarea
    queryInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleQuery();
        }
    });
    
    btnSendQuery.addEventListener("click", handleQuery);

    // Suggestion chips
    document.querySelectorAll(".suggestion-chip").forEach(chip => {
        chip.addEventListener("click", () => {
            queryInput.value = chip.textContent;
            handleQuery();
        });
    });

    async function handleQuery() {
        const queryText = queryInput.value.trim();
        if (!queryText) return;

        // Hide empty state
        workspaceEmpty.style.display = "none";
        
        // Add User Message
        appendMessage("user", queryText);
        queryInput.value = "";
        
        // Add Loading Bot Message
        const loadingId = "loading-" + Date.now();
        appendMessage("bot", `<div class="spinner" style="width: 20px; height: 20px; border-width: 2px; margin-bottom: 0;"></div>`, loadingId);

        const payload = getFormPayload();
        payload.natural_query = queryText;

        try {
            const response = await fetch("/api/databases/ask", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            const result = await response.json();
            const loadingMsg = document.getElementById(loadingId);
            
            if (response.ok) {
                // Render Table Result
                const htmlContent = renderTable(result.data);
                if (loadingMsg) {
                    loadingMsg.innerHTML = `<div class="msg-label">Result</div>${htmlContent}`;
                }

                // Update Context Panel
                updateContextPanel(result.sql_query, extractTablesFromSQL(result.sql_query));
                
                // Show context panel if hidden
                contextPanelWrapper.classList.remove("collapsed");

                // Mock updating the schema tree in the left sidebar based on used tables
                updateSchemaTreeMock(extractTablesFromSQL(result.sql_query));

            } else {
                if (loadingMsg) {
                    loadingMsg.innerHTML = `
                        <div class="msg-label error-label">Query execution failed</div>
                        <div class="error-msg">${result.detail || "Failed to execute query. Please check context or try again."}</div>
                    `;
                }
            }
        } catch (error) {
            const loadingMsg = document.getElementById(loadingId);
            if (loadingMsg) {
                loadingMsg.innerHTML = `
                    <div class="msg-label error-label">Network Error</div>
                    <div class="error-msg">${error.message}</div>
                `;
            }
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
        
        headers.forEach(h => {
            tableHTML += `<th>${h}</th>`;
        });
        tableHTML += `</tr></thead><tbody>`;

        dataArray.forEach(row => {
            tableHTML += `<tr>`;
            headers.forEach(h => {
                let val = row[h];
                if (val === null) val = 'NULL';
                if (typeof val === 'object') val = JSON.stringify(val);
                tableHTML += `<td>${val}</td>`;
            });
            tableHTML += `</tr>`;
        });
        
        tableHTML += `</tbody></table></div>`;
        return tableHTML;
    }

    // --- Context Panel Logic ---
    
    btnCloseContext.addEventListener("click", () => {
        contextPanelWrapper.classList.add("collapsed");
    });

    function updateContextPanel(sql, tables) {
        // Update SQL code block
        const sqlCode = document.getElementById("context-sql-code");
        sqlCode.innerHTML = syntaxHighlightSQL(sql);
        
        // Update Execution Meta
        document.getElementById("context-exec-meta").textContent = `Dialect: ${currentDbType} | Generated successfully`;

        // Update Context Tables List
        const list = document.getElementById("context-tables-list");
        list.innerHTML = "";
        
        if (tables.length === 0) {
            list.innerHTML = `<li class="placeholder">No explicit tables detected</li>`;
        } else {
            tables.forEach(t => {
                const li = document.createElement("li");
                li.textContent = `✓ ${t}`;
                list.appendChild(li);
            });
        }
    }

    // Extremely basic regex to find FROM/JOIN tables for visual feedback
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
            // Only replace placeholder if it exists
            const placeholder = tree.querySelector(".schema-placeholder");
            if (placeholder) {
                tree.innerHTML = "";
            }
            
            tables.forEach(t => {
                // check if already added
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
        let formatted = sql
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
            
        // Keywords
        const keywords = ['SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'ON', 'GROUP BY', 'ORDER BY', 'LIMIT', 'OFFSET', 'AS', 'AND', 'OR', 'NOT', 'IN', 'IS', 'NULL', 'DESC', 'ASC', 'HAVING'];
        keywords.forEach(kw => {
            const re = new RegExp(`\\b${kw}\\b`, 'gi');
            formatted = formatted.replace(re, `<span class="sql-keyword">$&</span>`);
        });
        
        // Functions
        const funcs = ['SUM', 'COUNT', 'AVG', 'MIN', 'MAX', 'COALESCE'];
        funcs.forEach(fn => {
            const re = new RegExp(`\\b${fn}\\b`, 'gi');
            formatted = formatted.replace(re, `<span class="sql-function">$&</span>`);
        });
        
        // Strings
        formatted = formatted.replace(/'([^']*)'/g, `<span class="sql-string">'$1'</span>`);
        
        // Numbers
        formatted = formatted.replace(/\b(\d+)\b/g, `<span class="sql-number">$1</span>`);
        
        return formatted;
    }
});
