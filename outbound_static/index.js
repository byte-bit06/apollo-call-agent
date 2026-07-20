/**
 * index.js — AI Sales Outbound Dialer Frontend
 * Manages all state, API calls, tab switching, lead cards, discovery log,
 * call modal, settings, and toast notifications using professional Lucide icons.
 */

const API = ""; // same origin

// ─────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────

function $(id) { return document.getElementById(id); }

function toast(msg, type = "info", duration = 4000) {
  const c = $("toast-container");
  if (!c) return;
  const el = document.createElement("div");
  const icons = { success: "check-circle", error: "alert-circle", info: "info", warning: "alert-triangle" };
  el.className = `toast ${type}`;
  el.innerHTML = `<span><i data-lucide="${icons[type] || "info"}" style="width:16px;height:16px;"></i></span><span>${msg}</span>`;
  c.appendChild(el);
  
  // Render new Lucide icon in toast
  if (window.lucide) lucide.createIcons();

  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateX(30px)";
    el.style.transition = "0.3s ease";
    setTimeout(() => el.remove(), 320);
  }, duration);
}

function timeAgo(iso) {
  if (!iso) return "Never";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "Just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function formatDuration(sec) {
  if (!sec) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function escHtml(str) {
  return String(str || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function initials(name) {
  const words = (name || "?").trim().split(/\s+/);
  if (words.length === 1) return words[0][0].toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

function statusLabel(status) {
  const map = {
    new: "Pending",
    approved: "Approved",
    rejected: "Rejected",
    no_answer: "Callback",
    contacted: "Contacted",
    do_not_call: "DNC",
  };
  return map[status] || status;
}

function statusClass(status) {
  return `status-${status || "new"}`;
}

async function apiGet(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return r.json();
}

async function apiPost(path, body) {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || data.error || `POST ${path} → ${r.status}`);
  return data;
}

async function apiPut(path, body) {
  const r = await fetch(API + path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || data.error || `PUT ${path} → ${r.status}`);
  return data;
}

async function apiPatch(path, body) {
  const r = await fetch(API + path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || data.error || `PATCH ${path} → ${r.status}`);
  return data;
}

// ─────────────────────────────────────────────
// App State
// ─────────────────────────────────────────────

const State = {
  leads: [],
  calls: [],
  discovery: { jobId: null, polling: null },
  modal: { leadId: null },
  searchQuery: "",
  authenticated: false,
};

// ─────────────────────────────────────────────
// App Object
// ─────────────────────────────────────────────

const App = {

  // ── Tabs ──────────────────────────────────
  tabs: {
    show(tabId) {
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      const panel = $(tabId);
      if (panel) panel.classList.add("active");
      const btn = document.querySelector(`[data-tab="${tabId}"]`);
      if (btn) btn.classList.add("active");

      // Dynamic titles (emojis removed)
      const titles = {
        "tab-discover": ["Dashboard", "Plan, prioritize, and accomplish your lead discovery with ease."],
        "tab-new": ["New Leads", "Manage and dial your fresh leads currently in queue."],
        "tab-callback": ["Call Back Later", "Monitor leads tagged for callback retry attempts."],
        "tab-contacted": ["Contacted", "View successfully contacted business prospects."],
        "tab-autopilot": ["Auto-Pilot", "Run and configure autonomous leads discovery and auto-calling."],
        "tab-calls": ["Call Logs", "Review history, duration, and outcomes of outbound dial attempts."],
        "tab-settings": ["Settings", "Configure Vapi credentials and Australia targeting parameters."]
      };
      const [title, desc] = titles[tabId] || ["Dashboard", ""];
      if ($("page-display-title")) $("page-display-title").textContent = title;
      if ($("page-display-desc")) $("page-display-desc").textContent = desc;

      // Lazy load data
      if (tabId === "tab-new" || tabId === "tab-callback" || tabId === "tab-contacted") App.leads.refresh();
      if (tabId === "tab-calls") App.calls.refresh();
      if (tabId === "tab-settings") App.settings.load();
    },
  },

  // ── Discovery ────────────────────────────
  discovery: {
    async start() {
      const queriesRaw = $("query-input").value.trim();
      const urlsRaw = $("url-input").value.trim();

      const queries = queriesRaw ? queriesRaw.split("\n").map(s => s.trim()).filter(Boolean) : [];
      const urls = urlsRaw ? urlsRaw.split("\n").map(s => s.trim()).filter(Boolean) : [];

      if (!queries.length && !urls.length) {
        toast("Please enter at least one search query or URL.", "warning");
        return;
      }

      const resultsPerQuery = parseInt($("results-per-query").value) || 8;

      $("log-panel").style.display = "block";
      $("log-terminal").innerHTML = "";
      $("discover-btn").disabled = true;
      $("cancel-btn").style.display = "inline-flex";
      $("discover-progress").style.display = "block";

      try {
        const data = await apiPost("/api/leads/discover", { queries, urls, results_per_query: resultsPerQuery });
        State.discovery.jobId = data.job_id;
        App.discovery.logLine("Discovery job started...", "info");
        App.discovery.poll();
      } catch (e) {
        toast(`Discovery failed: ${e.message}`, "error");
        App.discovery.reset();
      }
    },

    poll() {
      if (State.discovery.polling) clearInterval(State.discovery.polling);
      let lastLogCount = 0;
      State.discovery.polling = setInterval(async () => {
        if (!State.discovery.jobId) { App.discovery.reset(); return; }
        try {
          const job = await apiGet(`/api/leads/status/${State.discovery.jobId}`);
          const logs = job.logs || [];
          for (let i = lastLogCount; i < logs.length; i++) {
            const entry = logs[i];
            const msg = typeof entry === "string" ? entry : entry.msg;
            const cls = msg.includes("[ERROR]") || msg.includes("[!]") ? "error"
              : msg.includes("✓") || msg.includes("completed") ? "success" : "";
            App.discovery.logLine(msg, cls);
          }
          lastLogCount = logs.length;

          if (job.status === "completed") {
            App.discovery.logLine(`Done. ${job.leads_found} new leads added.`, "success");
            toast(`Discovery complete! ${job.leads_found} new leads found.`, "success");
            App.discovery.reset();
            App.leads.refresh();
            App.tabs.show("tab-new");
          } else if (job.status === "failed") {
            App.discovery.logLine(`Job failed: ${job.error}`, "error");
            toast(`Discovery failed: ${job.error}`, "error");
            App.discovery.reset();
          }
        } catch (e) {
          console.error("Poll error:", e);
        }
      }, 1200);
    },

    async cancel() {
      if (!State.discovery.jobId) return;
      try {
        await apiPost(`/api/leads/status/${State.discovery.jobId}/cancel`, {});
        App.discovery.logLine("Cancellation requested...", "error");
        toast("Discovery cancelled.", "warning");
      } catch (e) {}
      App.discovery.reset();
    },

    logLine(msg, cls = "") {
      const terminal = $("log-terminal");
      const placeholder = terminal.querySelector("span[style]");
      if (placeholder) placeholder.remove();

      const line = document.createElement("div");
      line.className = `log-line ${cls}`;
      line.textContent = msg;
      terminal.appendChild(line);
      terminal.scrollTop = terminal.scrollHeight;
    },

    clearLog() {
      $("log-terminal").innerHTML = '<span style="color:#4a5568;">Log cleared.</span>';
    },

    reset() {
      if (State.discovery.polling) { clearInterval(State.discovery.polling); State.discovery.polling = null; }
      State.discovery.jobId = null;
      $("discover-btn").disabled = false;
      $("cancel-btn").style.display = "none";
      $("discover-progress").style.display = "none";
    },
  },

  // ── Leads ────────────────────────────────
  leads: {
    async refresh() {
      if (!State.authenticated) return;
      try {
        const leads = await apiGet("/api/leads");
        State.leads = leads;
        App.leads.render();
      } catch (e) {
        if (e.message.includes("401")) {
          App.auth.showLogin();
        } else {
          toast(`Failed to load leads: ${e.message}`, "error");
        }
      }
    },

    search(query) {
      State.searchQuery = query;
      App.leads.render();
    },

    render() {
      const allNewLeads = State.leads.filter(l => l.status === "new" || l.status === "approved");
      const allCallbackLeads = State.leads.filter(l => l.status === "no_answer");
      const allContactedLeads = State.leads.filter(l => l.status === "contacted" || l.status === "do_not_call");

      // Update metrics
      if ($("stat-total-leads")) $("stat-total-leads").textContent = State.leads.length;
      if ($("stat-new-leads")) $("stat-new-leads").textContent = allNewLeads.length;
      if ($("stat-callback-leads")) $("stat-callback-leads").textContent = allCallbackLeads.length;
      if ($("stat-calls-placed")) {
        $("stat-calls-placed").textContent = State.leads.reduce((sum, lead) => sum + (lead.callCount || 0), 0);
      }

      // Update sidebar nav badges
      if ($("badge-new")) $("badge-new").textContent = allNewLeads.length;
      if ($("badge-callback")) $("badge-callback").textContent = allCallbackLeads.length;
      if ($("badge-contacted")) $("badge-contacted").textContent = allContactedLeads.length;

      // Filter by search text
      const query = (State.searchQuery || "").toLowerCase().trim();
      let displayedLeads = State.leads;
      if (query) {
        displayedLeads = State.leads.filter(l =>
          (l.businessName || "").toLowerCase().includes(query) ||
          (l.phoneNumber || "").toLowerCase().includes(query) ||
          (l.industry || "").toLowerCase().includes(query) ||
          (l.domain || "").toLowerCase().includes(query)
        );
      }

      const newLeads = displayedLeads.filter(l => l.status === "new" || l.status === "approved");
      const callbackLeads = displayedLeads.filter(l => l.status === "no_answer");
      const contactedLeads = displayedLeads.filter(l => l.status === "contacted" || l.status === "do_not_call");

      App.leads.renderGrid("new-leads-grid", newLeads, "new");
      App.leads.renderGrid("callback-leads-grid", callbackLeads, "callback");
      App.leads.renderGrid("contacted-leads-grid", contactedLeads, "contacted");

      // Compile newly generated vector icons
      if (window.lucide) lucide.createIcons();
    },

    renderGrid(gridId, leads, type) {
      const grid = $(gridId);
      if (!grid) return;
      if (!leads.length) {
        const emptyMsgs = {
          new: ["search", "No new leads yet", "Head to the Discover tab to start finding Australian businesses."],
          callback: ["refresh-cw", "No pending callbacks", "Leads that don't answer will automatically appear here after a call attempt."],
          contacted: ["check-circle", "No contacted leads yet", "After a successful call, leads will appear here and won't be re-called."],
        };
        const [icon, title, desc] = emptyMsgs[type] || ["file-text", "No leads", ""];
        grid.innerHTML = `
          <div class="empty-state" style="grid-column:1/-1;">
            <div class="empty-icon"><i data-lucide="${icon}"></i></div>
            <div class="empty-title">${title}</div>
            <div class="empty-desc">${desc}</div>
          </div>`;
        return;
      }
      grid.innerHTML = leads.map(l => App.leads.cardHtml(l)).join("");
    },

    cardHtml(lead) {
      const init = initials(lead.businessName);
      const callCountInfo = lead.callCount > 0
        ? `${lead.callCount} attempt${lead.callCount > 1 ? "s" : ""} · Last ${timeAgo(lead.lastCalledAt)}`
        : "Not called yet";
      
      const callBtnLabel = lead.status === "no_answer" 
        ? `<i data-lucide="refresh-cw" style="width:12px;height:12px;margin-right:4px;"></i> Retry` 
        : `<i data-lucide="phone" style="width:12px;height:12px;margin-right:4px;"></i> Call`;
        
      const callBtnClass = lead.status === "no_answer" ? "btn-warning" : "btn-success";
      const isDNC = lead.status === "do_not_call";

      let footerActions = "";
      if (lead.status === "new") {
        footerActions = `
          <button class="btn btn-sm" style="background:#d1e7dd; color:#0f5132; border-color:rgba(25, 135, 84, 0.2);" onclick="event.stopPropagation(); App.leads.approve('${lead.id}')">
            <i data-lucide="check" style="width:12px;height:12px;margin-right:2px;"></i> Approve
          </button>
          <button class="btn btn-sm" style="background:#f8d7da; color:#842029; border-color:rgba(220, 53, 69, 0.2);" onclick="event.stopPropagation(); App.leads.reject('${lead.id}')">
            <i data-lucide="x" style="width:12px;height:12px;margin-right:2px;"></i> Reject
          </button>
        `;
      } else if (lead.status === "approved" || lead.status === "no_answer") {
        if (lead.phoneNumber) {
          footerActions = `<button class="btn ${callBtnClass} btn-sm" onclick="event.stopPropagation(); App.leads.quickCall('${lead.id}')">${callBtnLabel}</button>`;
        }
      }

      return `
        <div class="glass-card lead-card" onclick="App.modal.open('${lead.id}')">
          <div class="lead-card-header">
            <div style="display:flex;align-items:flex-start;gap:12px;flex:1;min-width:0;">
              <div class="lead-avatar"><i data-lucide="building-2"></i></div>
              <div style="min-width:0;">
                <div class="lead-name">${escHtml(lead.businessName)}</div>
                <div class="lead-domain">
                  <a href="${lead.domain.startsWith('http') ? lead.domain : 'https://' + lead.domain}" target="_blank" onclick="event.stopPropagation();" class="lead-link">
                    ${escHtml(lead.domain)}
                  </a>
                </div>
              </div>
            </div>
            <span class="status-badge ${statusClass(lead.status)}" style="flex-shrink:0;">${statusLabel(lead.status)}</span>
          </div>
          ${lead.industry ? `<div style="margin-bottom:8px;"><span class="industry-badge">${escHtml(lead.industry)}</span></div>` : ""}
          ${lead.services ? `<p class="lead-services">${escHtml(lead.services)}</p>` : ""}
          <div class="lead-phone">
            <i data-lucide="phone" style="width:12px;height:12px;color:var(--text-muted);"></i>
            <span>Phone:</span> ${escHtml(lead.phoneNumber || "No number")}
          </div>
          <div class="lead-footer">
            <span class="call-count-info">${callCountInfo}</span>
            <div style="display:flex;gap:6px;">
              ${footerActions}
            </div>
          </div>
        </div>`;
    },

    async quickCall(leadId) {
      const lead = State.leads.find(l => l.id === leadId);
      if (!lead) return;
      toast(`Calling ${lead.businessName}...`, "info");
      try {
        const result = await apiPost(`/api/leads/${leadId}/call`, {});
        if (result.success) {
          toast(`Call initiated to ${lead.businessName}!`, "success");
          setTimeout(() => App.leads.refresh(), 1500);
        } else {
          toast(`Call failed: ${result.error}`, "error");
        }
      } catch (e) {
        toast(`${e.message}`, "error");
      }
    },

    async approve(leadId) {
      const lead = State.leads.find(l => l.id === leadId);
      if (!lead) return;
      try {
        await apiPatch(`/api/leads/${leadId}/status`, { status: "approved" });
        toast(`Lead "${lead.businessName}" approved for calling.`, "success");
        App.leads.refresh();
      } catch (e) {
        toast(`Failed to approve lead: ${e.message}`, "error");
      }
    },

    async reject(leadId) {
      const lead = State.leads.find(l => l.id === leadId);
      if (!lead) return;
      try {
        await apiPatch(`/api/leads/${leadId}/status`, { status: "rejected" });
        toast(`Lead "${lead.businessName}" rejected.`, "warning");
        App.leads.refresh();
      } catch (e) {
        toast(`Failed to reject lead: ${e.message}`, "error");
      }
    },

    addManualOpen() {
      $("am-name").value = "";
      $("am-phone").value = "";
      $("am-industry").value = "";
      $("am-url").value = "";
      $("am-services").value = "";
      $("am-pain").value = "";
      $("add-modal-overlay").classList.add("open");
      if (window.lucide) lucide.createIcons();
    },

    addManualClose() {
      $("add-modal-overlay").classList.remove("open");
    },

    addManualCloseOnOverlay(e) {
      if (e.target === $("add-modal-overlay") || e.target.classList.contains("modal-overlay")) {
        App.leads.addManualClose();
      }
    },

    async addManualSubmit() {
      const name = $("am-name").value.trim();
      const phone = $("am-phone").value.trim();
      if (!name) { toast("Business name is required.", "warning"); return; }
      if (!phone) { toast("Phone number is required.", "warning"); return; }
      try {
        await apiPost("/api/leads/add", {
          businessName: name,
          phoneNumber: phone,
          industry: $("am-industry").value.trim(),
          url: $("am-url").value.trim(),
          services: $("am-services").value.trim(),
          painPoints: $("am-pain").value.trim(),
        });
        toast(`Lead "${name}" added!`, "success");
        App.leads.addManualClose();
        App.leads.refresh();
      } catch (e) {
        toast(`${e.message}`, "error");
      }
    },
  },

  // ── Lead Edit/Call Modal ─────────────────
  modal: {
    open(leadId) {
      const lead = State.leads.find(l => l.id === leadId);
      if (!lead) return;
      State.modal.leadId = leadId;

      $("modal-title").textContent = lead.businessName || "Lead Details";
      $("m-name").value = lead.businessName || "";
      $("m-industry").value = lead.industry || "";
      $("m-phone").value = lead.phoneNumber || "";
      $("m-services").value = lead.services || "";
      $("m-pain").value = lead.painPoints || "";
      $("m-status").value = lead.status || "new";

      // Call button state
      const callBtn = $("modal-call-btn");
      if (!lead.phoneNumber) {
        callBtn.disabled = true;
        callBtn.innerHTML = `<i data-lucide="phone-off"></i> No Phone Number`;
      } else if (lead.status === "do_not_call") {
        callBtn.disabled = true;
        callBtn.innerHTML = `<i data-lucide="phone-off"></i> Do Not Call`;
      } else if (lead.status === "rejected") {
        callBtn.disabled = true;
        callBtn.innerHTML = `<i data-lucide="phone-off"></i> Rejected`;
      } else if (lead.status === "new") {
        callBtn.disabled = true;
        callBtn.innerHTML = `<i data-lucide="phone"></i> Pending Approval`;
      } else if (lead.status === "no_answer") {
        callBtn.disabled = false;
        callBtn.innerHTML = `<i data-lucide="refresh-cw"></i> Retry Call`;
      } else if (lead.status === "approved") {
        callBtn.disabled = false;
        callBtn.innerHTML = `<i data-lucide="phone-call"></i> Call Now`;
      } else {
        callBtn.disabled = false;
        callBtn.innerHTML = `<i data-lucide="phone-call"></i> Call Now`;
      }

      // Call history
      const histEl = $("modal-call-history");
      if (lead.callHistory && lead.callHistory.length > 0) {
        histEl.innerHTML = `
          <div style="font-size:0.78rem;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;">Call History</div>
          ${lead.callHistory.map(h => `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:rgba(255,255,255,0.03);border-radius:6px;margin-bottom:6px;font-size:0.78rem;">
              <span>${timeAgo(h.calledAt)}</span>
              <span style="color:var(--text-muted);">${h.endedReason || h.outcome || "pending"}</span>
              ${h.duration ? `<span style="color:var(--text-dim);">${formatDuration(h.duration)}</span>` : ""}
            </div>`).join("")}`;
      } else {
        histEl.innerHTML = "";
      }

      // Populate prompt variables preview
      const dateOptions = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
      const dateStr = new Date().toLocaleDateString('en-US', dateOptions);

      const updatePreview = () => {
        const previewText = `Today’s date is ${dateStr}. Only suggest or accept booking dates on or after today. When calling check_calendar_availability.

Business Name: ${$("m-name").value.trim()}
Industry: ${$("m-industry").value.trim()}

Services:
${$("m-services").value.trim()}

Common Business Challenges:
${$("m-pain").value.trim()}`;

        $("m-prompt-preview").textContent = previewText;
      };

      // Set initial preview
      updatePreview();

      // Bind events to update preview live on user edits
      $("m-name").oninput = updatePreview;
      $("m-industry").oninput = updatePreview;
      $("m-services").oninput = updatePreview;
      $("m-pain").oninput = updatePreview;

      $("lead-modal-overlay").classList.add("open");
      if (window.lucide) lucide.createIcons();
    },

    close() {
      $("lead-modal-overlay").classList.remove("open");
      State.modal.leadId = null;
    },

    closeOnOverlay(e) {
      if (e.target === $("lead-modal-overlay")) App.modal.close();
    },

    async save() {
      const id = State.modal.leadId;
      if (!id) return;
      try {
        await apiPut(`/api/leads/${id}`, {
          businessName: $("m-name").value.trim(),
          industry: $("m-industry").value.trim(),
          phoneNumber: $("m-phone").value.trim(),
          services: $("m-services").value.trim(),
          painPoints: $("m-pain").value.trim(),
        });
        await apiPatch(`/api/leads/${id}/status`, { status: $("m-status").value });
        toast("Lead saved.", "success");
        App.modal.close();
        App.leads.refresh();
      } catch (e) {
        toast(`${e.message}`, "error");
      }
    },

    async call() {
      const id = State.modal.leadId;
      if (!id) return;
      const lead = State.leads.find(l => l.id === id);
      toast(`Calling ${lead?.businessName || "lead"}...`, "info");
      $("modal-call-btn").disabled = true;
      try {
        const result = await apiPost(`/api/leads/${id}/call`, {});
        if (result.success) {
          toast(`Call initiated!`, "success");
          App.modal.close();
          setTimeout(() => { App.leads.refresh(); App.calls.refresh(); }, 1500);
        } else {
          toast(`Call failed: ${result.error}`, "error");
          $("modal-call-btn").disabled = false;
        }
      } catch (e) {
        toast(`${e.message}`, "error");
        $("modal-call-btn").disabled = false;
      }
    },

    async markDNC() {
      const id = State.modal.leadId;
      if (!id) return;
      if (!confirm("Mark this lead as Do Not Call? They won't be called again.")) return;
      try {
        await apiPatch(`/api/leads/${id}/status`, { status: "do_not_call" });
        toast("Lead marked as Do Not Call.", "warning");
        App.modal.close();
        App.leads.refresh();
      } catch (e) {
        toast(`${e.message}`, "error");
      }
    },

    async deleteLead() {
      const id = State.modal.leadId;
      if (!id) return;
      const lead = State.leads.find(l => l.id === id);
      if (!confirm(`Are you sure you want to delete lead "${lead?.businessName || 'this lead'}"?`)) return;
      try {
        await fetch(`/api/leads/${id}`, { method: "DELETE" });
        toast("Lead deleted successfully.", "success");
        App.modal.close();
        App.leads.refresh();
      } catch (e) {
        toast(`Error deleting lead: ${e.message}`, "error");
      }
    },
  },

  // ── Calls ────────────────────────────────
  calls: {
    async refresh() {
      try {
        const calls = await apiGet("/api/calls?limit=100");
        State.calls = calls;
        App.calls.render();
      } catch (e) {
        toast(`Failed to load call log: ${e.message}`, "error");
      }
    },

    render() {
      const el = $("call-log-list");
      if (!State.calls.length) {
        el.innerHTML = `
          <div class="empty-state">
            <div class="empty-icon"><i data-lucide="phone-off"></i></div>
            <div class="empty-title">No calls placed yet</div>
            <div class="empty-desc">Call history will appear here after you trigger your first call.</div>
          </div>`;
        if (window.lucide) lucide.createIcons();
        return;
      }

      el.innerHTML = State.calls.map(c => {
        const outcomeMap = {
          contacted: ["check-circle", "var(--green)"],
          no_answer: ["refresh-cw", "var(--yellow)"],
          initiated: ["radio", "var(--blue)"],
          failed: ["alert-circle", "var(--red)"],
          pending: ["clock", "var(--text-muted)"],
        };
        const [icon, color] = outcomeMap[c.outcome || c.status] || ["phone", "var(--text-muted)"];
        return `
          <div class="call-log-item">
            <div style="display:flex;align-items:center;justify-content:center;background:var(--bg);border-radius:50%;width:38px;height:38px;color:${color};">
              <i data-lucide="${icon}"></i>
            </div>
            <div style="flex:1;min-width:0;">
              <div class="call-log-biz">${escHtml(c.businessName || "Unknown")}</div>
              <div class="call-log-phone">${escHtml(c.phoneNumber || "")}</div>
            </div>
            <div style="text-align:right;flex-shrink:0;">
              <div style="font-size:0.82rem;font-weight:600;color:${color};text-transform:capitalize;">${c.outcome || c.status || "pending"}</div>
              ${c.duration ? `<div class="call-log-duration">${formatDuration(c.duration)}</div>` : ""}
              <div class="call-log-time">${timeAgo(c.startedAt)}</div>
            </div>
          </div>`;
      }).join("");

      if (window.lucide) lucide.createIcons();
    },
  },

  // ── Settings ─────────────────────────────
  settings: {
    async load() {
      try {
        const s = await apiGet("/api/settings/raw");
        $("s-vapi-key").value = s.VAPI_API_KEY || "";
        $("s-assistant-id").value = s.VAPI_ASSISTANT_ID || "";
        $("s-phone-id").value = s.VAPI_PHONE_NUMBER_ID || "";
        $("s-gemini-key").value = s.GEMINI_API_KEY || "";
        $("s-override-prompt").checked = (s.OVERRIDE_SYSTEM_PROMPT || "").toLowerCase() === "true";
        $("s-cal-id").value = s.GOOGLE_CALENDAR_ID || "primary";
        $("s-cal-timezone").value = s.TIMEZONE || "Australia/Melbourne";
        $("s-cal-json").value = s.GOOGLE_SERVICE_ACCOUNT_JSON || "";

        $("webhook-url-display").textContent = `${window.location.origin}/webhook`;
      } catch (e) {
        toast(`Failed to load settings: ${e.message}`, "error");
      }
    },

    async save() {
      try {
        await apiPost("/api/settings", {
          VAPI_API_KEY: $("s-vapi-key").value.trim(),
          VAPI_ASSISTANT_ID: $("s-assistant-id").value.trim(),
          VAPI_PHONE_NUMBER_ID: $("s-phone-id").value.trim(),
          GEMINI_API_KEY: $("s-gemini-key").value.trim(),
          OVERRIDE_SYSTEM_PROMPT: $("s-override-prompt").checked ? "true" : "false",
          GOOGLE_CALENDAR_ID: $("s-cal-id").value.trim(),
          TIMEZONE: $("s-cal-timezone").value.trim(),
          GOOGLE_SERVICE_ACCOUNT_JSON: $("s-cal-json").value.trim(),
        });
        toast("Settings saved!", "success");
      } catch (e) {
        toast(`${e.message}`, "error");
      }
    },
  },

  // ── Auto-Pilot ─────────────────────────────
  autopilot: {
    _polling: null,
    _lastLogCount: 0,

    async start() {
      await App.autopilot.saveConfig();
      try {
        await apiPost("/api/autopilot/start", {});
        toast("Auto-Pilot started!", "success");
        App.autopilot.startPolling();
      } catch (e) {
        toast(`Failed to start: ${e.message}`, "error");
      }
    },

    async stop() {
      try {
        await apiPost("/api/autopilot/stop", {});
        toast("Auto-Pilot stopped.", "warning");
        App.autopilot.stopPolling();
        App.autopilot.renderState({ status: "stopped", current_action: "Idle", calls_today: 0, calls_today_limit: 50, total_calls_session: 0, total_leads_discovered_session: 0, next_discovery_at: null, recent_logs: [] });
      } catch (e) {
        toast(`${e.message}`, "error");
      }
    },

    async saveConfig() {
      const queriesRaw = $("ap-queries").value.trim();
      const queries = queriesRaw ? queriesRaw.split("\n").map(s => s.trim()).filter(Boolean) : [];
      try {
        await apiPost("/api/autopilot/config", {
          search_queries: queries,
          results_per_query: parseInt($("ap-results-per-q").value) || 5,
          discover_interval_hours: parseInt($("ap-discover-interval").value) || 2,
          call_delay_seconds: parseInt($("ap-call-delay").value) || 45,
          daily_call_limit: parseInt($("ap-daily-limit").value) || 50,
          retry_after_hours: parseInt($("ap-retry-hours").value) || 24,
          calling_hours_start: parseInt($("ap-hours-start").value) || 9,
          calling_hours_end: parseInt($("ap-hours-end").value) || 17,
        });
        toast("Config saved.", "success");
      } catch (e) {
        toast(`${e.message}`, "error");
      }
    },

    async loadConfig() {
      try {
        const cfg = await apiGet("/api/autopilot/config");
        $("ap-queries").value = (cfg.search_queries || []).join("\n");
        $("ap-results-per-q").value = cfg.results_per_query || 5;
        $("ap-discover-interval").value = cfg.discover_interval_hours || 2;
        $("ap-call-delay").value = cfg.call_delay_seconds || 45;
        $("ap-daily-limit").value = cfg.daily_call_limit || 50;
        $("ap-retry-hours").value = cfg.retry_after_hours || 24;
        $("ap-hours-start").value = cfg.calling_hours_start ?? 9;
        $("ap-hours-end").value = cfg.calling_hours_end ?? 17;
      } catch (e) {
        console.error("Failed to load AP config:", e);
      }
    },

    startPolling() {
      if (App.autopilot._polling) return;
      App.autopilot._lastLogCount = 0;
      App.autopilot._polling = setInterval(async () => {
        try {
          const state = await apiGet("/api/autopilot/state");
          App.autopilot.renderState(state);
          App.autopilot.appendNewLogs(state.recent_logs || []);
        } catch (e) {
          console.error("AP poll error:", e);
        }
      }, 2500);
    },

    stopPolling() {
      if (App.autopilot._polling) { clearInterval(App.autopilot._polling); App.autopilot._polling = null; }
    },

    renderState(state) {
      const isRunning = state.status !== "stopped";

      // Big icon & label using Lucide
      const apIcon = state.status === "stopped" ? "square" : state.status === "discovering" ? "search" : state.status === "calling" ? "phone-call" : "play-circle";
      const iconColor = state.status === "stopped" ? "var(--red)" : "var(--green)";
      $("ap-toggle-icon").innerHTML = `<i data-lucide="${apIcon}" style="width:30px;height:30px;color:${iconColor};"></i>`;
      
      $("ap-status-label").textContent = isRunning
        ? `Auto-Pilot is ${state.status === "discovering" ? "DISCOVERING" : state.status === "calling" ? "CALLING" : "RUNNING"}...`
        : "Auto-Pilot is OFF";
      $("ap-current-action").textContent = state.current_action || "Idle";

      // Buttons
      $("ap-start-btn").style.display = isRunning ? "none" : "";
      $("ap-stop-btn").style.display = isRunning ? "" : "none";

      // Nav badge
      $("ap-nav-badge").style.display = isRunning ? "inline-block" : "none";

      // Stats
      $("ap-stat-calls-today").textContent = `${state.calls_today || 0}/${state.calls_today_limit || 50}`;
      $("ap-stat-calls-session").textContent = state.total_calls_session || 0;
      $("ap-stat-leads-found").textContent = state.total_leads_discovered_session || 0;
      if (state.next_discovery_at) {
        const d = new Date(state.next_discovery_at);
        $("ap-stat-next-discovery").textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } else {
        $("ap-stat-next-discovery").textContent = "—";
      }

      if (window.lucide) lucide.createIcons();
    },

    appendNewLogs(logs) {
      const terminal = $("ap-log-terminal");
      const placeholder = terminal.querySelector("span[style]");
      if (placeholder && logs.length > 0) placeholder.remove();

      const newEntries = logs.slice(App.autopilot._lastLogCount);
      App.autopilot._lastLogCount = logs.length;

      newEntries.forEach(entry => {
        const msg = typeof entry === "string" ? entry : entry.msg;
        const level = (typeof entry === "object" ? entry.level : "") || "";
        const line = document.createElement("div");
        line.className = `log-line ${level === "error" ? "error" : level === "success" ? "success" : level === "warning" ? "" : "info"}`;
        const ts = entry.ts ? new Date(entry.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
        line.textContent = ts ? `[${ts}] ${msg}` : msg;
        terminal.appendChild(line);
      });
      if (newEntries.length) terminal.scrollTop = terminal.scrollHeight;
    },

    clearLog() {
      $("ap-log-terminal").innerHTML = '<span style="color:#4a5568;">Log cleared.</span>';
      App.autopilot._lastLogCount = 0;
    },
  },

  // ── Auth ────────────────────────────────────────
  auth: {
    async check() {
      try {
        const r = await fetch("/api/auth/check");
        if (r.ok) {
          App.auth.showDashboard();
        } else {
          App.auth.showLogin();
        }
      } catch {
        App.auth.showLogin();
      }
    },

    showLogin() {
      State.authenticated = false;
      $("login-screen").style.display = "flex";
      $("app-wrapper").style.display = "none";
      // Clear fields
      if ($("login-username")) $("login-username").value = "";
      if ($("login-password")) $("login-password").value = "";
      if ($("login-error")) $("login-error").style.display = "none";
    },

    showDashboard() {
      State.authenticated = true;
      $("login-screen").style.display = "none";
      $("app-wrapper").style.display = "flex";
      // Load data once authenticated
      App.leads.refresh();
      App.autopilot.loadConfig();
      apiGet("/api/autopilot/state").then(state => {
        if (state.status !== "stopped") {
          App.autopilot.renderState(state);
          App.autopilot._lastLogCount = (state.recent_logs || []).length;
          App.autopilot.appendNewLogs(state.recent_logs || []);
          App.autopilot.startPolling();
        }
      }).catch(() => {});
      if (window.lucide) lucide.createIcons();
    },

    async login() {
      const username = $("login-username").value.trim();
      const password = $("login-password").value;
      const errEl = $("login-error");
      const btn = $("login-btn");

      if (!username || !password) {
        errEl.textContent = "Please enter your username and password.";
        errEl.style.display = "block";
        return;
      }

      btn.disabled = true;
      btn.textContent = "Signing in...";
      errEl.style.display = "none";

      try {
        const r = await fetch("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password }),
        });
        const data = await r.json().catch(() => ({}));
        if (r.ok && data.success) {
          App.auth.showDashboard();
        } else {
          errEl.textContent = data.detail || "Invalid username or password.";
          errEl.style.display = "block";
        }
      } catch (e) {
        errEl.textContent = "Connection error. Is the server running?";
        errEl.style.display = "block";
      } finally {
        btn.disabled = false;
        btn.textContent = "Sign In";
      }
    },

    async logout() {
      try {
        await fetch("/api/auth/logout", { method: "POST" });
      } catch {}
      App.auth.showLogin();
    },
  },

  // ── Quick Call ──────────────────────────────────
  quickCall: {
    open() {
      $("qc-name").value = "";
      $("qc-industry").value = "";
      $("qc-phone").value = "";
      $("qc-services").value = "";
      $("qc-pain").value = "";

      const dateOptions = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
      const dateStr = new Date().toLocaleDateString('en-US', dateOptions);

      const updatePreview = () => {
        const previewText = `Today’s date is ${dateStr}. Only suggest or accept booking dates on or after today. When calling check_calendar_availability.

Business Name: ${$("qc-name").value.trim()}
Industry: ${$("qc-industry").value.trim()}

Services:
${$("qc-services").value.trim()}

Common Business Challenges:
${$("qc-pain").value.trim()}`;

        $("qc-prompt-preview").textContent = previewText;
      };

      updatePreview();

      $("qc-name").oninput = updatePreview;
      $("qc-industry").oninput = updatePreview;
      $("qc-services").oninput = updatePreview;
      $("qc-pain").oninput = updatePreview;

      $("quickcall-modal-overlay").classList.add("open");
      if (window.lucide) lucide.createIcons();
    },

    close() {
      $("quickcall-modal-overlay").classList.remove("open");
    },

    closeOnOverlay(e) {
      if (e.target === $("quickcall-modal-overlay")) App.quickCall.close();
    },

    async submit() {
      const name = $("qc-name").value.trim();
      const phone = $("qc-phone").value.trim();
      const industry = $("qc-industry").value.trim();
      const services = $("qc-services").value.trim();
      const pain = $("qc-pain").value.trim();
      const btn = $("quickcall-btn");

      if (!name || !phone) {
        toast("Business Name and Phone Number are required.", "error");
        return;
      }

      btn.disabled = true;
      btn.innerHTML = `<i data-lucide="loader" class="spin" style="width:14px;height:14px;margin-right:4px;"></i> Calling...`;
      if (window.lucide) lucide.createIcons();

      try {
        const result = await apiPost("/api/leads/quick-call", {
          businessName: name,
          phoneNumber: phone,
          industry: industry,
          services: services,
          painPoints: pain
        });

        if (result.success) {
          toast(`Call initiated to ${name}!`, "success");
          App.quickCall.close();
          setTimeout(() => { App.calls.refresh(); }, 1500);
        } else {
          toast(`Call failed: ${result.error}`, "error");
        }
      } catch (e) {
        toast(`Error: ${e.message}`, "error");
      } finally {
        btn.disabled = false;
        btn.innerHTML = `<i data-lucide="phone-call" style="width:14px;height:14px;margin-right:4px;"></i> Start Call Now`;
        if (window.lucide) lucide.createIcons();
      }
    }
  },
};

// ─────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────

window.addEventListener("DOMContentLoaded", () => {
  // Check auth first before loading anything
  App.auth.check();

  setInterval(App.leads.refresh, 15000);

  if ($("webhook-url-display")) {
    $("webhook-url-display").textContent = `${window.location.origin}/webhook`;
  }

  // Initialize Lucide icons on page load
  if (window.lucide) lucide.createIcons();
});
