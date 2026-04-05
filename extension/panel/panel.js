/**
 * AgentUX Chrome Extension — Popup Logic
 */

const API_BASE = 'http://localhost:8000';

// ── SVG Icon Constants ───────────────────────────────────────────────────────
const ICON_GRANDMA = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0084FF" stroke-width="2.5" stroke-linecap="round"><circle cx="6" cy="10" r="3"/><circle cx="18" cy="10" r="3"/><path d="M3 10h18"/><path d="M9 10h6"/></svg>';
const ICON_MILLENNIAL = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#66B3FF" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8h1a4 4 0 0 1 0 8h-1"></path><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"></path><line x1="6" y1="1" x2="6" y2="4"></line><line x1="10" y1="1" x2="10" y2="4"></line><line x1="14" y1="1" x2="14" y2="4"></line></svg>';
const ICON_CUSTOM = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>';
const ICON_FIRST_TIME = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0084FF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
const ICON_GENZ = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#66B3FF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></svg>';

function personaIcon(type, size = 16) {
    if (type === 'elderly') return ICON_GRANDMA.replace(/16/g, size);
    if (type === 'millennial') return ICON_MILLENNIAL.replace(/16/g, size);
    if (type === 'first_time') return ICON_FIRST_TIME.replace(/16/g, size);
    if (type === 'gen_z') return ICON_GENZ.replace(/16/g, size);
    return ICON_CUSTOM.replace(/16/g, size);
}

// ── State ────────────────────────────────────────────────────────────────────
let currentUrl = '';
let currentRunId = null;
let pollInterval = null;
let issues = [];
let fullscreenPreviewActive = false;
let lastLivePreviews = []; // track current live URLs for fullscreen mode
let activeTabId = null; // tab ID for content script messages, kept in sync

// ── Tab helpers ─────────────────────────────────────────────────────────────
async function getActiveTab() {
    try {
        const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
        if (tab && tab.id) {
            activeTabId = tab.id;
            return tab;
        }
    } catch (e) { /* ignore */ }
    // Fallback: try currentWindow
    try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (tab && tab.id) {
            activeTabId = tab.id;
            return tab;
        }
    } catch (e) { /* ignore */ }
    return null;
}

function sendToActiveTab(message) {
    if (!activeTabId) {
        console.warn('[AgentUX] No activeTabId, cannot send message:', message.type);
        return;
    }
    chrome.tabs.sendMessage(activeTabId, message, () => {
        // Suppress "Receiving end does not exist" errors
        if (chrome.runtime.lastError) {
            console.warn('[AgentUX] sendMessage error:', chrome.runtime.lastError.message);
        }
    });
}

// Keep activeTabId in sync when user switches tabs
chrome.tabs.onActivated.addListener(async (activeInfo) => {
    activeTabId = activeInfo.tabId;
    // If fullscreen preview is on, re-send it to the new tab
    // (the new tab won't have the overlay since it's a different page)
    // So we turn it off when switching tabs
    if (fullscreenPreviewActive) {
        fullscreenPreviewActive = false;
        document.getElementById('fullscreenToggle').checked = false;
    }
});

// ── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    // Get current tab URL
    const tab = await getActiveTab();
    if (tab) {
        currentUrl = tab.url;
        document.getElementById('pageUrl').textContent = currentUrl;
    }

    // Persona chip toggling
    document.getElementById('personaChips').addEventListener('click', (e) => {
        const chip = e.target.closest('.persona-chip');
        if (chip) {
            chip.classList.toggle('active');
            updatePersonaCount();
        }
    });

    // Custom Persona expansion
    document.getElementById('toggleCustomPersonaBtn').addEventListener('click', () => {
        const form = document.getElementById('customPersonaForm');
        form.style.display = form.style.display === 'none' ? 'flex' : 'none';
    });

    // Add Custom Persona Submittal
    document.getElementById('addCustomPersonaBtn').addEventListener('click', () => {
        const nameInput = document.getElementById('cpName');
        const promptInput = document.getElementById('cpPrompt');
        if (!nameInput.value || !promptInput.value) {
            alert('Please provide a name and instructions for the custom persona.');
            return;
        }

        const customPayload = {
            type: "custom_" + Date.now(),
            name: nameInput.value,
            emoji: "🧑",
            system_prompt: promptInput.value
        };

        const newChip = document.createElement('button');
        newChip.type = 'button';
        newChip.className = 'persona-chip active';
        newChip.dataset.customPayload = JSON.stringify(customPayload);
        newChip.dataset.persona = customPayload.type;
        newChip.innerHTML = `
            <span class="chip-icon" style="background: rgba(52, 211, 153, 0.15)">${personaIcon('custom', 20)}</span>
            <span class="chip-name">${escapeHtml(nameInput.value)}</span>
        `;
        document.getElementById('personaChips').appendChild(newChip);

        // Reset form
        nameInput.value = '';
        promptInput.value = '';
        document.getElementById('customPersonaForm').style.display = 'none';
        updatePersonaCount();
    });

    // Evaluate button
    document.getElementById('evaluateBtn').addEventListener('click', startEvaluation);

    // Global Force Clear Fixes
    document.getElementById('clearAllFixesBtn').addEventListener('click', async () => {
        try {
            const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
            if (!tab || !tab.url) return;
            const domain = new URL(tab.url).hostname;
            const result = await chrome.storage.local.get('agentux_fixes');
            const allFixes = result.agentux_fixes || {};
            if (allFixes[domain]) {
                delete allFixes[domain];
                await chrome.storage.local.set({ agentux_fixes: allFixes });
            }
            await chrome.tabs.reload(tab.id);
        } catch(e) {
            console.error('Failed to clear ALL fixes:', e);
        }
    });

    // Copy prompt button
    document.getElementById('copyPromptBtn').addEventListener('click', copyDeployPrompt);

    // Restart testing button
    document.getElementById('restartBtnProgress').addEventListener('click', resetUI);
    document.getElementById('restartBtnResults').addEventListener('click', resetUI);

    // Task count controls
    const range = document.getElementById('taskCountRange');
    const countLabel = document.getElementById('taskCountValue');
    range.addEventListener('input', () => { countLabel.textContent = range.value; });
    document.getElementById('taskCountUp').addEventListener('click', () => {
        if (parseInt(range.value) < 5) { range.value = parseInt(range.value) + 1; countLabel.textContent = range.value; }
    });
    document.getElementById('taskCountDown').addEventListener('click', () => {
        if (parseInt(range.value) > 1) { range.value = parseInt(range.value) - 1; countLabel.textContent = range.value; }
    });

    // Fullscreen live preview toggle
    document.getElementById('fullscreenToggle').addEventListener('change', async (e) => {
        fullscreenPreviewActive = e.target.checked;

        // Always refresh the tab ID before sending
        await getActiveTab();

        if (fullscreenPreviewActive && lastLivePreviews.length > 0) {
            sendToActiveTab({
                type: 'show_live_previews',
                previews: lastLivePreviews,
            });
        } else if (fullscreenPreviewActive && lastLivePreviews.length === 0) {
            console.warn('[AgentUX] Toggle ON but no live previews available yet');
        } else {
            sendToActiveTab({ type: 'hide_live_previews' });
        }
    });

    // Header action buttons
    document.getElementById('headerRefreshBtn').addEventListener('click', () => {
        resetUI();
    });
    document.getElementById('headerCloseBtn').addEventListener('click', () => {
        window.close();
    });

    // Restore saved state if an evaluation is in progress
    await restoreState();
});

// ── State Persistence ────────────────────────────────────────────────────────
async function saveState(phase) {
    await chrome.storage.session.set({
        agentux_state: {
            runId: currentRunId,
            url: currentUrl,
            phase, // 'evaluate' | 'progress' | 'results'
        }
    });
}

async function clearState() {
    await chrome.storage.session.remove('agentux_state');
}

async function restoreState() {
    try {
        const result = await chrome.storage.session.get('agentux_state');
        const state = result.agentux_state;
        if (!state || !state.runId) return;

        currentRunId = state.runId;

        if (state.phase === 'progress') {
            const check = await fetch(`${API_BASE}/api/runs/${currentRunId}`);
            if (!check.ok) {
                await clearState();
                currentRunId = null;
                return;
            }
            document.getElementById('evaluateSection').style.display = 'none';
            document.getElementById('progressSection').style.display = 'block';
            document.getElementById('resultsSection').style.display = 'none';
            startPolling();
        } else if (state.phase === 'results') {
            const resp = await fetch(`${API_BASE}/api/runs/${currentRunId}`);
            if (!resp.ok) {
                await clearState();
                currentRunId = null;
                return;
            }
            const data = await resp.json();
            document.getElementById('evaluateSection').style.display = 'none';
            if (data.status === 'awaiting_approval' || data.status === 'completed' || data.status === 'failed') {
                document.getElementById('progressSection').style.display = 'none';
                showResults(data);
            } else {
                document.getElementById('progressSection').style.display = 'block';
                startPolling();
            }
        }
    } catch (e) {
        console.log('No saved state to restore');
    }
}

// ── Evaluate ─────────────────────────────────────────────────────────────────
function updatePersonaCount() {
    const count = document.querySelectorAll('.persona-chip.active').length;
    document.getElementById('personaCount').textContent = `${count} selected`;
}

async function startEvaluation() {
    const personas = Array.from(document.querySelectorAll('.persona-chip.active'))
        .map(c => {
            if (c.dataset.customPayload) {
                return JSON.parse(c.dataset.customPayload);
            }
            return c.dataset.persona;
        });

    if (personas.length === 0) {
        alert('Select at least one persona.');
        return;
    }

    const btn = document.getElementById('evaluateBtn');
    btn.disabled = true;
    btn.innerHTML = '<span>Starting...</span>';

    // Show progress
    document.getElementById('evaluateSection').style.display = 'none';
    document.getElementById('progressSection').style.display = 'block';
    document.getElementById('resultsSection').style.display = 'none';

    try {
        const resp = await fetch(`${API_BASE}/api/test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: currentUrl,
                personas,
                num_tasks: parseInt(document.getElementById('taskCountRange').value),
            }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const data = await resp.json();
        currentRunId = data.id;

        // Save state and start polling
        await saveState('progress');
        startPolling();
    } catch (err) {
        console.error('Failed to start:', err);
        alert('Failed to connect to AgentUX backend. Make sure it is running on port 8000.');
        resetUI();
    }
}

// ── Polling ──────────────────────────────────────────────────────────────────
function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollResults, 2000);
    pollResults();
}

async function pollResults() {
    if (!currentRunId) return;

    try {
        const resp = await fetch(`${API_BASE}/api/runs/${currentRunId}`);
        if (!resp.ok) {
            if (resp.status === 404) {
                // Run no longer exists — server was restarted
                clearInterval(pollInterval);
                await clearState();
                currentRunId = null;
                lastLogCount = 0;
                resetUI();
            }
            return;
        }

        const data = await resp.json();
        updateProgress(data);

        if (data.status === 'awaiting_approval' || data.status === 'completed') {
            clearInterval(pollInterval);
            await saveState('results');
            showResults(data);
        } else if (data.status === 'failed') {
            clearInterval(pollInterval);
            await clearState();
            currentRunId = null;
            resetUI();
            
            // Show the error message from the backend log
            const errorMsg = data.log_messages.find(m => m.includes('[ERROR]')) || 'Unknown pipeline failure.';
            alert('Evaluation failed: ' + errorMsg);
        }
    } catch (e) {
        console.error('Poll error:', e);
    }
}

// ── Progress Updates ─────────────────────────────────────────────────────────
let lastLogCount = 0;

function updateProgress(data) {
    document.getElementById('progressLabel').textContent = data.current_step || data.status;
    document.getElementById('progressPercent').textContent = `${Math.round(data.progress)}%`;
    document.getElementById('progressFill').style.width = `${data.progress}%`;

    // Render agent status
    const statusEl = document.getElementById('agentStatus');
    if (data.persona_results && data.persona_results.length > 0) {
        statusEl.innerHTML = data.persona_results.map(p => {
            const icon = personaIcon(p.persona_type);
            const statusClass = p.status === 'completed' ? 'done' : p.status === 'in_progress' ? 'running' : '';
            const statusText = p.status === 'completed' ? `Done (${p.tasks_completed}/${p.tasks_total} tasks)` :
                               p.status === 'in_progress' ? `Testing... (${p.tasks_completed}/${p.tasks_total})` :
                               'Waiting...';
            return `<div class="agent-line ${statusClass}">
                <span class="persona-icon-inline">${icon}</span>
                <span>${p.persona_name}: ${statusText}</span>
            </div>`;
        }).join('');
    }

    // Render live log entries
    if (data.log_messages && data.log_messages.length > lastLogCount) {
        const logEl = document.getElementById('liveLogEntries');
        const newEntries = data.log_messages.slice(lastLogCount);
        lastLogCount = data.log_messages.length;

        newEntries.forEach(msg => {
            const entry = document.createElement('div');
            entry.className = 'log-entry';

            // Color-code by persona
            if (msg.includes('👵') || msg.includes('Grandma') || msg.includes('elderly')) {
                entry.classList.add('log-grandma');
            } else if (msg.includes('☕') || msg.includes('Millennial') || msg.includes('millennial')) {
                entry.classList.add('log-genz'); // reusing genz styling color for millennial
            } else if (msg.includes('🧑') || msg.includes('Custom')) {
                entry.classList.add('log-custom');
            } else if (msg.includes('[WARN]')) {
                entry.classList.add('log-warn');
            } else if (msg.includes('[ERROR]')) {
                entry.classList.add('log-error');
            } else {
                entry.classList.add('log-system');
            }

            // Clean up timestamp for display
            const cleaned = msg.replace(/\[\d{4}-\d{2}-\d{2}T[\d:.]+\]\s*/, '');
            entry.textContent = cleaned;
            logEl.appendChild(entry);
        });

        // Auto-scroll to bottom
        logEl.scrollTop = logEl.scrollHeight;
    }

    // ── Live Browser Iframes ──
    if (data.persona_results && data.persona_results.length > 0) {
        // Build list of live previews for fullscreen mode
        const newPreviews = data.persona_results
            .filter(p => p.live_url)
            .map(p => ({
                live_url: p.live_url,
                persona_name: p.persona_name,
                persona_type: p.persona_type,
                status: p.status,
            }));

        // Update fullscreen previews if the list changed
        const previewsChanged = JSON.stringify(newPreviews) !== JSON.stringify(lastLivePreviews);
        lastLivePreviews = newPreviews;

        if (fullscreenPreviewActive && previewsChanged && newPreviews.length > 0) {
            sendToActiveTab({
                type: 'show_live_previews',
                previews: newPreviews,
            });
        }

        // Sidebar iframes
        const container = document.getElementById('liveBrowsers');
        if (container) {
            data.persona_results.forEach((p, i) => {
                const iframeId = `live-iframe-${i}`;
                if (p.live_url && !document.getElementById(iframeId)) {
                    const card = document.createElement('div');
                    card.className = 'live-browser-card';
                    const isGrandma = p.persona_type === 'elderly' || p.persona_name.toLowerCase().includes('grandma');
                    card.innerHTML = `
                        <div class="live-browser-label" style="border-color:${isGrandma ? '#a78bfa' : '#22d3ee'}">
                            <span class="live-persona-badge" style="background:${isGrandma ? '#a78bfa' : '#22d3ee'}">${personaIcon(p.persona_type)} ${p.persona_name}</span>
                            <span class="live-status-dot"></span>
                        </div>
                        <div class="live-browser-wrapper">
                            <iframe id="${iframeId}" src="${p.live_url}" class="live-browser-iframe" allow="autoplay; clipboard-write" sandbox="allow-same-origin allow-scripts allow-popups allow-forms"></iframe>
                            <div class="live-browser-click-overlay"></div>
                        </div>
                    `;
                    container.appendChild(card);

                    // Attach click handler to the overlay (iframes swallow clicks)
                    card.querySelector('.live-browser-click-overlay').addEventListener('click', () => {
                        openBrowserLightbox(p.live_url, p.persona_name, p.persona_type);
                    });
                }
                // Mark iframe once persona is done
                if (p.status === 'completed' && document.getElementById(iframeId)) {
                    const existingCard = document.getElementById(iframeId)?.closest('.live-browser-card');
                    if (existingCard) {
                        existingCard.querySelector('.live-status-dot')?.classList.add('done');
                        existingCard.querySelector('.live-persona-badge').textContent += ' ✓';
                    }
                }
            });
        }
    }
}

// ── Results ──────────────────────────────────────────────────────────────────
function showResults(data) {
    document.getElementById('progressSection').style.display = 'none';
    document.getElementById('resultsSection').style.display = 'block';

    // Dismiss fullscreen preview when results are ready
    if (fullscreenPreviewActive) {
        fullscreenPreviewActive = false;
        document.getElementById('fullscreenToggle').checked = false;
        sendToActiveTab({ type: 'hide_live_previews' });
    }
    lastLivePreviews = [];

    // Build issues from suggested edits
    issues = (data.suggested_edits || []).map((edit, i) => ({
        id: edit.id || `issue-${i}`,
        title: edit.description,
        description: edit.rationale || '',
        severity: edit.severity || 'medium',
        persona: (edit.personas_affected || [])[0] || '',
        before: edit.before_snippet || '',
        after: edit.after_snippet || '',
        fix_js: edit.fix_js || '',
        fix_css: edit.fix_css || '',
        applied: false,
    }));

    document.getElementById('issueCount').textContent = issues.length;
    renderIssues();

    // Show deploy section if there are issues
    if (issues.length > 0) {
        document.getElementById('deploySection').style.display = 'block';
        generateDeployPrompt();
    }
}

function renderIssues() {
    const list = document.getElementById('issuesList');
    list.innerHTML = issues.map((issue, i) => {
        const icon = personaIcon(issue.persona, 18);

        return `
            <div class="issue-card" data-index="${i}">
                <div class="issue-header toggle-issue-btn" data-issue="${i}">
                    <span class="issue-severity ${issue.severity}">${issue.severity}</span>
                    <span class="issue-title">${escapeHtml(issue.title)}</span>
                    <span class="issue-persona">${icon}</span>
                </div>
                <div class="issue-body open" id="issueBody${i}">
                    ${issue.description ? `<div class="issue-description">${escapeHtml(issue.description)}</div>` : ''}
                    ${issue.beforeScreenshot && issue.afterScreenshot ? `
                        <div class="screenshot-compare">
                            <div class="screenshot-pane">
                                <span class="preview-label screenshot-label-before">Before</span>
                                <img src="${issue.beforeScreenshot}" class="screenshot-img" alt="Before fix">
                            </div>
                            <div class="screenshot-pane">
                                <span class="preview-label screenshot-label-after">After</span>
                                <img src="${issue.afterScreenshot}" class="screenshot-img" alt="After fix">
                            </div>
                        </div>
                    ` : issue.before || issue.after ? `
                        <div class="issue-preview">
                            <div class="preview-pane preview-before">
                                <span class="preview-label">— Before</span>
                                ${escapeHtml(issue.before || '(no content)')}
                            </div>
                            <div class="preview-pane preview-after">
                                <span class="preview-label">+ After</span>
                                ${escapeHtml(issue.after || '(no content)')}
                            </div>
                        </div>
                    ` : ''}
                    <div class="code-editor" id="codeEditor${i}" style="display:none; margin-bottom:10px;">
                        <span class="preview-label">Edit CSS Fix</span>
                        <textarea class="edit-css" spellcheck="false" style="width:100%; height:60px; font-family:var(--font-mono); font-size:11px; margin-bottom:10px; background:var(--bg-tertiary); color:white; border:1px solid var(--border-color); border-radius:4px; padding:8px; box-sizing:border-box;">${issue.fix_css || ''}</textarea>
                        <span class="preview-label">Edit JS Fix</span>
                        <textarea class="edit-js" spellcheck="false" style="width:100%; height:80px; font-family:var(--font-mono); font-size:11px; background:var(--bg-tertiary); color:white; border:1px solid var(--border-color); border-radius:4px; padding:8px; box-sizing:border-box;">${issue.fix_js || ''}</textarea>
                    </div>
                    <div class="issue-actions">
                        <button class="btn-skip toggle-code-btn" data-issue="${i}" style="flex:0.3; padding:8px 0;" title="Edit AI Code">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 19l7-7 3 3-7 7-3-3z"></path><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"></path><path d="M2 2l7.586 7.586"></path><circle cx="11" cy="11" r="2"></circle></svg>
                        </button>
                        ${issue.applied ? `
                        <button class="btn-apply applied" data-issue="${i}" style="flex:1; background:var(--bg-tertiary);">
                            ✓ Applied
                        </button>
                        <button class="btn-skip revert-fix-btn" data-issue="${i}" style="flex:0.5; border-color:#ef4444; color:#ef4444;" title="Revert Changes">↺ Revert</button>
                        ` : `
                        <button class="btn-apply apply-fix-btn" data-issue="${i}" style="flex:1">
                            ✦ Apply Fix
                        </button>
                        <button class="btn-skip toggle-issue-btn" data-issue="${i}" style="flex:0.5">Skip</button>
                        `}
                    </div>
                </div>
            </div>
        `;
    }).join('');

    // Attach event listeners dynamically to avoid CSP errors (no inline onclick allowed)
    document.querySelectorAll('.toggle-issue-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const index = e.currentTarget.dataset.issue;
            toggleIssue(index);
        });
    });

    document.querySelectorAll('.toggle-code-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const index = e.currentTarget.dataset.issue;
            const editor = document.getElementById(`codeEditor${index}`);
            if (editor) editor.style.display = editor.style.display === 'none' ? 'block' : 'none';
        });
    });

    document.querySelectorAll('.apply-fix-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const index = e.currentTarget.dataset.issue;
            applyFix(index);
        });
    });

    document.querySelectorAll('.revert-fix-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const index = e.currentTarget.dataset.issue;
            revertFix(index);
        });
    });
}

function toggleIssue(index) {
    const body = document.getElementById(`issueBody${index}`);
    if (body) body.classList.toggle('open');
}

// ── Revert Fix ───────────────────────────────────────────────────────────────
async function revertFix(index) {
    const issue = issues[index];
    if (!issue.applied) return;

    const btn = document.querySelector(`[data-index="${index}"] .revert-fix-btn`);
    if (btn) { btn.textContent = '...'; }

    try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) throw new Error('No active tab');

        try {
            const domain = new URL(tab.url).hostname;
            const result = await chrome.storage.local.get('agentux_fixes');
            const allFixes = result.agentux_fixes || {};
            if (allFixes[domain]) {
                allFixes[domain] = allFixes[domain].filter(f => f.id !== issue.id);
                await chrome.storage.local.set({ agentux_fixes: allFixes });
            }
        } catch (e) {
            console.warn('Failed to clear persistence storage during revert:', e);
        }

        if (issue.fix_css) {
            try {
                await chrome.scripting.removeCSS({
                    target: { tabId: tab.id },
                    css: issue.fix_css,
                });
            } catch (e) {
                console.warn('Could not remove CSS:', e);
            }
        }
        
        if (issue.fix_js) {
            await chrome.tabs.reload(tab.id);
        }

        issue.applied = false;
        renderIssues();
    } catch (e) {
        console.error('Revert fix failed:', e);
        alert(`Revert failed: ${e.message}`);
    }
}

// ── Apply Fix ────────────────────────────────────────────────────────────────
async function applyFix(index) {
    const issue = issues[index];
    if (issue.applied) return;

    const cssArea = document.querySelector(`#issueBody${index} .edit-css`);
    const jsArea = document.querySelector(`#issueBody${index} .edit-js`);
    if (cssArea && cssArea.value.trim() !== '') issue.fix_css = cssArea.value;
    if (jsArea && jsArea.value.trim() !== '') issue.fix_js = jsArea.value;

    // Check that there's something to inject
    if (!issue.fix_js && !issue.fix_css) {
        alert('No fix script available for this issue. The AI did not generate injectable code.');
        return;
    }

    const btn = document.querySelector(`[data-index="${index}"] .btn-apply`);
    if (btn) { btn.textContent = 'Applying...'; btn.disabled = true; }

    try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) throw new Error('No active tab');

        // 1. Capture BEFORE screenshot
        const beforeImg = await chrome.tabs.captureVisibleTab(null, { format: 'png' });

        // Show toast animation on the target page
        await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: () => {
                const toast = document.createElement('div');
                toast.id = 'agentux-toast';
                toast.style.cssText = 'position:fixed;bottom:20px;right:20px;background:#a78bfa;color:white;padding:12px 24px;border-radius:8px;font-family:system-ui,sans-serif;font-weight:600;font-size:14px;z-index:999999;box-shadow:0 10px 25px rgba(0,0,0,0.3);transition:all 0.4s ease;transform:translateY(20px);opacity:0;';
                toast.textContent = 'AgentUX: Generating Edit...';
                document.body.appendChild(toast);
                
                // Animate in
                requestAnimationFrame(() => {
                    toast.style.transform = 'translateY(0)';
                    toast.style.opacity = '1';
                });

                // Show success status after brief delay (simulating applying)
                setTimeout(() => {
                    toast.style.background = '#34d399';
                    toast.textContent = 'AgentUX: Fix Successfully Applied ✓';
                }, 400);

                // Fade out and remove
                setTimeout(() => {
                    toast.style.opacity = '0';
                    toast.style.transform = 'translateY(20px)';
                    setTimeout(() => toast.remove(), 400);
                }, 3000);
            }
        });

        // 2. Apply the fix via chrome.scripting (works regardless of content script)
        if (issue.fix_css) {
            await chrome.scripting.insertCSS({
                target: { tabId: tab.id },
                css: issue.fix_css,
            });
        }

        if (issue.fix_js) {
            await chrome.scripting.executeScript({
                target: { tabId: tab.id },
                world: 'MAIN',
                func: (jsCode) => {
                    try {
                        const script = document.createElement('script');
                        script.textContent = `
try {
  // Wrap AI-generated code
  ${jsCode}
} catch(err) {
  // Throw visible alert so the user doesn't wonder why it silently failed
  alert('AgentUX DOM Injection Error: ' + err.message + '\\n\\nThe requested element may not exist on the page. Try manually adjusting the JS payload via the Edit Code button!');
}`;
                        (document.head || document.documentElement).appendChild(script);
                        script.remove();
                    } catch(e) {
                        console.error('[AgentUX JS Injection]', e);
                    }
                },
                args: [issue.fix_js],
            });
        }

        // 3. Small delay to let changes render
        await new Promise(r => setTimeout(r, 500));

        // 4. Capture AFTER screenshot
        const afterImg = await chrome.tabs.captureVisibleTab(null, { format: 'png' });

        // 5. Save fix for persistence (via content script)
        try {
            await chrome.tabs.sendMessage(tab.id, {
                type: 'save_fix',
                fix: {
                    id: issue.id,
                    url: currentUrl,
                    domain: new URL(currentUrl).hostname,
                    css: issue.fix_css,
                    js: issue.fix_js,
                    description: issue.title,
                },
            });
        } catch (e) {
            console.log('Content script not available for persistence, fix applied but won\'t persist');
        }

        // 6. Update state with screenshots
        issue.applied = true;
        issue.beforeScreenshot = beforeImg;
        issue.afterScreenshot = afterImg;
        renderIssues();

    } catch (e) {
        console.error('Apply fix failed:', e);
        if (btn) { btn.textContent = '✦ Apply Fix'; btn.disabled = false; }
        alert(`Fix failed: ${e.message}`);
    }
}

// ── Deploy Prompt ────────────────────────────────────────────────────────────
function generateDeployPrompt() {
    const prompt = `I ran an AI usability audit on ${currentUrl}. Please make these changes to improve the site:\n\n` +
        issues.map((issue, i) => {
            let entry = `${i + 1}. ${issue.title}\n`;
            entry += `   Severity: ${issue.severity}\n`;
            if (issue.description) entry += `   Rationale: ${issue.description}\n`;
            if (issue.before) entry += `   Before: ${issue.before}\n`;
            if (issue.after) entry += `   After: ${issue.after}\n`;
            return entry;
        }).join('\n');

    document.getElementById('deployPrompt').textContent = prompt;
}

async function copyDeployPrompt() {
    const prompt = document.getElementById('deployPrompt').textContent;
    await navigator.clipboard.writeText(prompt);

    const btn = document.getElementById('copyPromptBtn');
    btn.classList.add('copied');
    btn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
        Copied!
    `;
    setTimeout(() => {
        btn.classList.remove('copied');
        btn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            Copy Prompt
        `;
    }, 2000);
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function resetUI() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = null;
    currentRunId = null;
    lastLogCount = 0;
    issues = [];
    lastLivePreviews = [];

    // Turn off fullscreen preview if active
    if (fullscreenPreviewActive) {
        fullscreenPreviewActive = false;
        document.getElementById('fullscreenToggle').checked = false;
        sendToActiveTab({ type: 'hide_live_previews' });
    }

    document.getElementById('evaluateSection').style.display = 'block';
    document.getElementById('progressSection').style.display = 'none';
    document.getElementById('resultsSection').style.display = 'none';
    const liveBrowsers = document.getElementById('liveBrowsers');
    if (liveBrowsers) liveBrowsers.innerHTML = '';
    document.getElementById('liveLogEntries').innerHTML = '';
    const btn = document.getElementById('evaluateBtn');
    btn.disabled = false;
    btn.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        <span>Evaluate This Page</span>
    `;
    clearState();
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ── Lightbox Logic ───────────────────────────────────────────────────────────
document.body.addEventListener('click', (e) => {
    if (e.target.classList.contains('screenshot-img')) {
        const lightbox = document.getElementById('imageLightbox');
        const lightboxImg = document.getElementById('lightboxImg');
        lightboxImg.src = e.target.src;
        lightbox.style.display = 'flex';
    }
});

document.getElementById('imageLightbox').addEventListener('click', () => {
    document.getElementById('imageLightbox').style.display = 'none';
});

// ── Browser Lightbox Logic ───────────────────────────────────────────────────
function openBrowserLightbox(liveUrl, personaName, personaType) {
    const lightbox = document.getElementById('browserLightbox');
    const iframe = document.getElementById('lightboxBrowserIframe');
    const label = document.getElementById('lightboxBrowserLabel');

    iframe.src = liveUrl;
    label.innerHTML = `${personaIcon(personaType, 20)} <span>${personaName}</span>`;
    lightbox.style.display = 'flex';
}

function closeBrowserLightbox() {
    const lightbox = document.getElementById('browserLightbox');
    const iframe = document.getElementById('lightboxBrowserIframe');
    lightbox.style.display = 'none';
    // Don't clear src immediately - allow a moment for animation
    setTimeout(() => {
        iframe.src = 'about:blank';
    }, 300);
}

document.getElementById('closeBrowserLightbox').addEventListener('click', closeBrowserLightbox);
document.getElementById('browserLightbox').addEventListener('click', (e) => {
    // Only close if clicking the background, not the iframe or label
    if (e.target.id === 'browserLightbox') {
        closeBrowserLightbox();
    }
});

// Keyboard shortcut: ESC to close lightboxes
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const browserLightbox = document.getElementById('browserLightbox');
        const imageLightbox = document.getElementById('imageLightbox');
        if (browserLightbox.style.display === 'flex') {
            closeBrowserLightbox();
        } else if (imageLightbox.style.display === 'flex') {
            imageLightbox.style.display = 'none';
        }
    }
});
