/**
 * AgentUX Chrome Extension — Popup Logic
 */

const API_BASE = 'http://localhost:8000';

// ── SVG Icon Constants ───────────────────────────────────────────────────────
const ICON_GRANDMA = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#a78bfa" stroke-width="2.5" stroke-linecap="round"><circle cx="6" cy="10" r="3"/><circle cx="18" cy="10" r="3"/><path d="M3 10h18"/><path d="M9 10h6"/></svg>';
const ICON_GENZ = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>';

function personaIcon(type, size = 16) {
    if (type === 'elderly') return ICON_GRANDMA.replace(/16/g, size);
    return ICON_GENZ.replace(/16/g, size);
}

// ── State ────────────────────────────────────────────────────────────────────
let currentUrl = '';
let currentRunId = null;
let pollInterval = null;
let issues = [];

// ── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    // Get current tab URL
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) {
        currentUrl = tab.url;
        document.getElementById('pageUrl').textContent = currentUrl;
    }

    // Persona chip toggling
    document.querySelectorAll('.persona-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            chip.classList.toggle('active');
            const count = document.querySelectorAll('.persona-chip.active').length;
            document.getElementById('personaCount').textContent = `${count} selected`;
        });
    });

    // Evaluate button
    document.getElementById('evaluateBtn').addEventListener('click', startEvaluation);

    // Copy prompt button
    document.getElementById('copyPromptBtn').addEventListener('click', copyDeployPrompt);

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
async function startEvaluation() {
    const personas = Array.from(document.querySelectorAll('.persona-chip.active'))
        .map(c => c.dataset.persona);

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
            if (msg.includes('👵') || msg.includes('Grandma')) {
                entry.classList.add('log-grandma');
            } else if (msg.includes('⚡') || msg.includes('Gen-Z')) {
                entry.classList.add('log-genz');
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
}

// ── Results ──────────────────────────────────────────────────────────────────
function showResults(data) {
    document.getElementById('progressSection').style.display = 'none';
    document.getElementById('resultsSection').style.display = 'block';

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
                    <div class="issue-actions">
                        <button class="btn-apply apply-fix-btn ${issue.applied ? 'applied' : ''}" data-issue="${i}">
                            ${issue.applied ? '✓ Applied' : '✦ Apply Fix'}
                        </button>
                        <button class="btn-skip toggle-issue-btn" data-issue="${i}">Skip</button>
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

    document.querySelectorAll('.apply-fix-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const index = e.currentTarget.dataset.issue;
            applyFix(index);
        });
    });
}

function toggleIssue(index) {
    const body = document.getElementById(`issueBody${index}`);
    if (body) body.classList.toggle('open');
}

// ── Apply Fix ────────────────────────────────────────────────────────────────
async function applyFix(index) {
    const issue = issues[index];
    if (issue.applied) return;

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
                func: (jsCode) => {
                    try { new Function(jsCode)(); } catch(e) { console.error('[AgentUX]', e); }
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
    document.getElementById('evaluateSection').style.display = 'block';
    document.getElementById('progressSection').style.display = 'none';
    document.getElementById('resultsSection').style.display = 'none';
    const btn = document.getElementById('evaluateBtn');
    btn.disabled = false;
    btn.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        <span>Evaluate This Page</span>
    `;
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
