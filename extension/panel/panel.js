/**
 * Agent UX Chrome Extension — Panel Logic
 */

const API_BASE = 'http://localhost:8000';

// ── SVG Icon Constants ───────────────────────────────────────────────────────
const ICON_GRANDMA = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#66B3FF" stroke-width="2.5" stroke-linecap="round"><circle cx="6" cy="10" r="3"/><circle cx="18" cy="10" r="3"/><path d="M3 10h18"/><path d="M9 10h6"/></svg>';
const ICON_FIRST_TIME = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8h1a4 4 0 0 1 0 8h-1"></path><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"></path><line x1="6" y1="1" x2="6" y2="4"></line><line x1="10" y1="1" x2="10" y2="4"></line><line x1="14" y1="1" x2="14" y2="4"></line></svg>';
const ICON_CUSTOM = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>';

function personaIcon(type, size = 16) {
    if (type === 'elderly') return ICON_GRANDMA.replace(/16/g, size);
    if (type === 'first_time_user') return ICON_FIRST_TIME.replace(/16/g, size);
    return ICON_CUSTOM.replace(/16/g, size);
}

// ── State ────────────────────────────────────────────────────────────────────
let currentUrl = '';
let currentRunId = null;
let pollInterval = null;
let issues = [];
let activeTab = 'setup';
let fullscreenPreviewActive = false;
let lastLivePreviews = [];
let activeTabId = null;
let pageSummary = '';
let generatedTasks = [];
let taskCount = 2;

// ── Tab Navigation ──────────────────────────────────────────────────────────
function switchTab(tabName) {
    activeTab = tabName;

    // Update tab buttons
    document.querySelectorAll('.tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tabName);
    });

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(s => {
        const isTarget = s.dataset.tab === tabName;
        s.style.display = isTarget ? 'block' : 'none';
        s.classList.toggle('active', isTarget);
    });
}

function enableTab(tabName) {
    const tab = document.querySelector(`.tab[data-tab="${tabName}"]`);
    if (tab) tab.disabled = false;
}

// ── Tab helpers ─────────────────────────────────────────────────────────────
async function getActiveTab() {
    try {
        const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
        if (tab && tab.id) {
            activeTabId = tab.id;
            return tab;
        }
    } catch (e) { /* ignore */ }
    try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (tab && tab.id) {
            activeTabId = tab.id;
            return tab;
        }
    } catch (e) { /* ignore */ }
    return null;
}

// ── Fullscreen Preview (injected directly via chrome.scripting) ─────────────
async function injectFullscreenPreview(previews) {
    if (!activeTabId || !previews || previews.length === 0) return;

    try {
        await chrome.scripting.executeScript({
            target: { tabId: activeTabId },
            args: [previews],
            func: (previews) => {
                const CONTAINER_ID = 'agentux-live-preview-overlay';
                const STYLE_ID = 'agentux-preview-styles';

                const existing = document.getElementById(CONTAINER_ID);
                if (existing) existing.remove();

                const count = previews.length;
                let gridCols, gridRows;
                if (count === 1) { gridCols = '1fr'; gridRows = '1fr'; }
                else if (count === 2) { gridCols = '1fr 1fr'; gridRows = '1fr'; }
                else if (count <= 4) { gridCols = '1fr 1fr'; gridRows = count <= 2 ? '1fr' : '1fr 1fr'; }
                else { gridCols = '1fr 1fr 1fr'; gridRows = `repeat(${Math.ceil(count / 3)}, 1fr)`; }

                const container = document.createElement('div');
                container.id = CONTAINER_ID;
                container.style.cssText = `
                    position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                    z-index: 2147483647; background: #0F1115;
                    display: grid; grid-template-columns: ${gridCols}; grid-template-rows: ${gridRows};
                    gap: 2px; font-family: -apple-system, sans-serif;
                `;

                const COLORS = { elderly: '#0084FF', first_time_user: '#22d3ee' };

                previews.forEach(p => {
                    const color = COLORS[p.persona_type] || '#34d399';
                    const isDone = p.status === 'completed';

                    const cell = document.createElement('div');
                    cell.style.cssText = 'display:flex; flex-direction:column; min-height:0; background:#171A21;';

                    const label = document.createElement('div');
                    label.style.cssText = `display:flex; align-items:center; gap:8px; padding:8px 14px; background:#1E2430; border-bottom:2px solid ${color}; flex-shrink:0;`;

                    const badge = document.createElement('span');
                    badge.style.cssText = `font-size:12px; font-weight:700; color:white; padding:3px 10px; background:${color}; border-radius:9999px;`;
                    badge.textContent = p.persona_name;

                    const dot = document.createElement('span');
                    dot.style.cssText = `width:8px; height:8px; border-radius:50%; background:${isDone ? '#5A6577' : '#34d399'}; margin-left:auto;`;
                    if (!isDone) dot.style.animation = 'agentux-pulse 1.5s ease-in-out infinite';

                    label.appendChild(badge);
                    if (isDone) {
                        const check = document.createElement('span');
                        check.style.cssText = 'font-size:11px; color:#AAB4C0; font-weight:600;';
                        check.textContent = '\u2713 Done';
                        label.appendChild(check);
                    }
                    label.appendChild(dot);

                    const iframe = document.createElement('iframe');
                    iframe.src = p.live_url;
                    iframe.allow = 'autoplay; clipboard-write';
                    iframe.sandbox = 'allow-same-origin allow-scripts allow-popups allow-forms';
                    iframe.style.cssText = 'flex:1; width:100%; border:none; background:white; min-height:0;';

                    cell.appendChild(label);
                    cell.appendChild(iframe);
                    container.appendChild(cell);
                });

                let styleEl = document.getElementById(STYLE_ID);
                if (!styleEl) {
                    styleEl = document.createElement('style');
                    styleEl.id = STYLE_ID;
                    styleEl.textContent = '@keyframes agentux-pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.4;transform:scale(0.8)} }';
                    document.head.appendChild(styleEl);
                }

                document.body.style.overflow = 'hidden';
                document.body.appendChild(container);
            },
        });
    } catch (e) {
        console.error('[AgentUX] Failed to inject fullscreen preview:', e);
    }
}

async function removeFullscreenPreview() {
    if (!activeTabId) return;

    try {
        await chrome.scripting.executeScript({
            target: { tabId: activeTabId },
            func: () => {
                const el = document.getElementById('agentux-live-preview-overlay');
                if (el) {
                    el.remove();
                    document.body.style.overflow = '';
                }
                const style = document.getElementById('agentux-preview-styles');
                if (style) style.remove();
            },
        });
    } catch (e) {
        console.error('[AgentUX] Failed to remove fullscreen preview:', e);
    }
}

// Keep activeTabId in sync when user switches tabs
chrome.tabs.onActivated.addListener(async (activeInfo) => {
    activeTabId = activeInfo.tabId;
    if (fullscreenPreviewActive) {
        fullscreenPreviewActive = false;
        const toggle = document.getElementById('fullscreenToggle');
        if (toggle) toggle.checked = false;
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

    // Tab click handlers
    document.querySelectorAll('.tab').forEach(t => {
        t.addEventListener('click', () => {
            if (!t.disabled) switchTab(t.dataset.tab);
        });
    });

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
            system_prompt: promptInput.value
        };

        const newChip = document.createElement('button');
        newChip.type = 'button';
        newChip.className = 'persona-chip active';
        newChip.dataset.customPayload = JSON.stringify(customPayload);
        newChip.dataset.persona = customPayload.type;
        newChip.innerHTML = `<span class="chip-name">${escapeHtml(nameInput.value)}</span>`;
        document.getElementById('personaChips').appendChild(newChip);

        nameInput.value = '';
        promptInput.value = '';
        document.getElementById('customPersonaForm').style.display = 'none';
        updatePersonaCount();
    });

    // Generate tasks button
    document.getElementById('generateTasksBtn').addEventListener('click', generateTasks);

    // Evaluate button
    document.getElementById('evaluateBtn').addEventListener('click', startEvaluation);

    // Header clear fixes button
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

    // Restart testing button (results page)
    document.getElementById('restartBtnResults').addEventListener('click', resetUI);

    // Task count controls
    document.getElementById('taskCountUp').addEventListener('click', () => {
        if (taskCount < 5) { taskCount++; document.getElementById('taskCountValue').textContent = taskCount; }
    });
    document.getElementById('taskCountDown').addEventListener('click', () => {
        if (taskCount > 1) { taskCount--; document.getElementById('taskCountValue').textContent = taskCount; }
    });

    // Add custom task toggle + submit
    document.getElementById('toggleAddTaskBtn').addEventListener('click', () => {
        const form = document.getElementById('addTaskForm');
        form.style.display = form.style.display === 'none' ? 'flex' : 'none';
    });
    document.getElementById('addCustomTaskBtn').addEventListener('click', addCustomTask);

    // Restart testing button (progress page)
    document.getElementById('restartBtnProgress')?.addEventListener('click', resetUI);

    // Fullscreen live preview toggle
    document.getElementById('fullscreenToggle').addEventListener('change', async (e) => {
        fullscreenPreviewActive = e.target.checked;
        await getActiveTab();

        const liveBrowsers = document.getElementById('liveBrowsers');
        if (fullscreenPreviewActive) {
            if (liveBrowsers) liveBrowsers.style.display = 'none';
            if (lastLivePreviews.length > 0) {
                await injectFullscreenPreview(lastLivePreviews);
            }
        } else {
            if (liveBrowsers) liveBrowsers.style.display = '';
            await removeFullscreenPreview();
        }
    });

    // Restore saved state
    await restoreState();
});

// ── State Persistence ────────────────────────────────────────────────────────
async function saveState(phase) {
    await chrome.storage.session.set({
        agentux_state: {
            runId: currentRunId,
            url: currentUrl,
            phase,
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
            enableTab('progress');
            switchTab('progress');
            startPolling();
        } else if (state.phase === 'results') {
            const resp = await fetch(`${API_BASE}/api/runs/${currentRunId}`);
            if (!resp.ok) {
                await clearState();
                currentRunId = null;
                return;
            }
            const data = await resp.json();
            enableTab('progress');
            if (data.status === 'awaiting_approval' || data.status === 'completed' || data.status === 'failed') {
                enableTab('results');
                switchTab('results');
                showResults(data);
            } else {
                switchTab('progress');
                startPolling();
            }
        }
    } catch (e) {
        console.log('No saved state to restore');
    }
}

// ── Setup Flow ──────────────────────────────────────────────────────────────
function updatePersonaCount() {
    const count = document.querySelectorAll('.persona-chip.active').length;
    document.getElementById('personaCount').textContent = `${count} selected`;
}

async function generateTasks() {
    const btn = document.getElementById('generateTasksBtn');
    const mascot = document.getElementById('taskGenMascot');
    btn.disabled = true;
    btn.textContent = 'Generating...';
    mascot.style.display = '';

    try {
        // Summarize in background if not already done
        if (!pageSummary) {
            const sumResp = await fetch(`${API_BASE}/api/summarize`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: currentUrl }),
            });
            if (!sumResp.ok) throw new Error(`Summarize failed: HTTP ${sumResp.status}`);
            const sumData = await sumResp.json();
            pageSummary = sumData.summary;
        }

        const resp = await fetch(`${API_BASE}/api/generate-tasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: currentUrl,
                summary: pageSummary,
                num_tasks: taskCount,
            }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const data = await resp.json();
        generatedTasks = data.tasks;
        renderTaskCards();
    } catch (err) {
        console.error('Task generation failed:', err);
        alert('Failed to generate tasks. Make sure the backend is running on port 8000.');
    } finally {
        mascot.style.display = 'none';
        btn.disabled = false;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48l2.83-2.83"/></svg> Generate Tasks';
    }
}

let editingTaskIndex = null;

function updateEvalButton() {
    const btn = document.getElementById('evaluateBtn');
    if (generatedTasks.length > 0) {
        btn.disabled = false;
        btn.classList.remove('btn-evaluate-disabled');
    } else {
        btn.disabled = true;
        btn.classList.add('btn-evaluate-disabled');
    }
}

function renderTaskCards() {
    const container = document.getElementById('taskCards');
    editingTaskIndex = null;

    if (generatedTasks.length === 0) {
        container.innerHTML = '<div class="task-cards-empty" id="taskCardsEmpty">Click "Generate Tasks" to create test tasks from the page summary</div>';
        document.getElementById('toggleAddTaskBtn').style.display = 'none';
        updateEvalButton();
        return;
    }

    document.getElementById('toggleAddTaskBtn').style.display = '';

    container.innerHTML = generatedTasks.map((task, i) => `
        <div class="task-card" data-index="${i}">
            <span class="task-card-number">${i + 1}</span>
            <div class="task-card-content" data-index="${i}">
                <div class="task-card-title">${escapeHtml(task.title)}</div>
                <div class="task-card-desc">${escapeHtml(task.description)}</div>
            </div>
            <button class="task-card-delete" data-index="${i}" title="Remove task">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
        </div>
    `).join('');

    // Attach delete handlers
    container.querySelectorAll('.task-card-delete').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const idx = parseInt(e.currentTarget.dataset.index);
            generatedTasks.splice(idx, 1);
            renderTaskCards();
        });
    });

    // Attach click-to-edit handlers
    container.querySelectorAll('.task-card-content').forEach(el => {
        el.addEventListener('click', (e) => {
            const idx = parseInt(e.currentTarget.dataset.index);
            openTaskEditor(idx);
        });
    });

    updateEvalButton();
}

function openTaskEditor(index) {
    // If already editing this one, do nothing
    if (editingTaskIndex === index) return;

    // Commit any open editor first (without re-rendering)
    commitOpenEditor();

    editingTaskIndex = index;
    const task = generatedTasks[index];
    const card = document.querySelector(`.task-card[data-index="${index}"]`);
    if (!card) return;

    card.classList.add('task-card-editing');
    const content = card.querySelector('.task-card-content');

    content.innerHTML = `
        <input type="text" class="task-edit-title" value="${escapeHtml(task.title)}">
        <textarea class="task-edit-desc">${escapeHtml(task.description)}</textarea>
        <button type="button" class="task-edit-save">Done</button>
    `;

    const titleInput = content.querySelector('.task-edit-title');
    const descInput = content.querySelector('.task-edit-desc');

    // Auto-size textarea to fit content
    requestAnimationFrame(() => {
        descInput.style.height = 'auto';
        descInput.style.height = Math.max(60, descInput.scrollHeight) + 'px';
    });

    titleInput.focus();

    // Stop clicks inside the editor from bubbling to the content click handler
    content.addEventListener('click', (e) => e.stopPropagation());

    content.querySelector('.task-edit-save').addEventListener('click', (e) => {
        e.stopPropagation();
        commitOpenEditor();
        renderTaskCards();
    });

    titleInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            commitOpenEditor();
            renderTaskCards();
        }
    });

    descInput.addEventListener('input', () => {
        descInput.style.height = 'auto';
        descInput.style.height = descInput.scrollHeight + 'px';
    });
}

function commitOpenEditor() {
    if (editingTaskIndex === null) return;
    const card = document.querySelector(`.task-card[data-index="${editingTaskIndex}"]`);
    if (!card) { editingTaskIndex = null; return; }
    const titleInput = card.querySelector('.task-edit-title');
    const descInput = card.querySelector('.task-edit-desc');
    if (titleInput && descInput) {
        generatedTasks[editingTaskIndex].title = titleInput.value.trim() || generatedTasks[editingTaskIndex].title;
        generatedTasks[editingTaskIndex].description = descInput.value.trim() || generatedTasks[editingTaskIndex].description;
    }
    editingTaskIndex = null;
}

function addCustomTask() {
    const titleInput = document.getElementById('customTaskTitle');
    const descInput = document.getElementById('customTaskDesc');

    if (!titleInput.value.trim()) {
        alert('Please provide a task title.');
        return;
    }

    generatedTasks.push({
        id: 'custom-' + Date.now(),
        title: titleInput.value.trim(),
        description: descInput.value.trim() || titleInput.value.trim(),
        expected_outcome: '',
        priority: 'medium',
    });

    titleInput.value = '';
    descInput.value = '';
    document.getElementById('addTaskForm').style.display = 'none';
    renderTaskCards();
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

    if (generatedTasks.length === 0) {
        // Flash the generate button to guide user
        const genBtn = document.getElementById('generateTasksBtn');
        genBtn.classList.remove('flash');
        void genBtn.offsetWidth; // force reflow
        genBtn.classList.add('flash');
        return;
    }

    const btn = document.getElementById('evaluateBtn');
    btn.disabled = true;
    btn.innerHTML = '<span>Starting...</span>';

    // Switch to progress tab
    enableTab('progress');
    switchTab('progress');

    try {
        const resp = await fetch(`${API_BASE}/api/execute`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: currentUrl,
                personas,
                tasks: generatedTasks,
            }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const data = await resp.json();
        currentRunId = data.id;

        await saveState('progress');
        startPolling();
    } catch (err) {
        console.error('Failed to start:', err);
        alert('Failed to connect to Agent UX backend. Make sure it is running on port 8000.');
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

            // Force progress to 100%
            document.getElementById('progressLabel').textContent = 'Analysis complete!';
            document.getElementById('progressPercent').textContent = '100%';
            document.getElementById('progressFill').style.width = '100%';
            const mascotStatus = document.getElementById('mascotStatus');
            if (mascotStatus) mascotStatus.textContent = 'All done! Generating results...';
            const mascotTitle = document.querySelector('.mascot-progress-title');
            if (mascotTitle) mascotTitle.textContent = 'Complete!';

            await saveState('results');
            enableTab('results');
            switchTab('results');
            showResults(data);
        } else if (data.status === 'failed') {
            clearInterval(pollInterval);
            await clearState();
            currentRunId = null;
            resetUI();
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

    // Update mascot image + status text
    const mascotImg = document.querySelector('.mascot-progress-img');
    const mascotStatus = document.getElementById('mascotStatus');
    if (mascotStatus) {
        const step = data.current_step || data.status || '';
        const thinkingFaces = ['../icons/hmmm.svg', '../icons/oh well...svg'];
        if (mascotImg) {
            // Cycle between thinking/hmmm during progress
            const cycleIdx = Math.floor(Date.now() / 3000) % thinkingFaces.length;
            mascotImg.src = thinkingFaces[cycleIdx];
        }
        if (step.includes('EXECUTING') || step.includes('executing')) {
            mascotStatus.textContent = 'Agents are browsing your page...';
        } else if (step.includes('ANALYZING') || step.includes('analyzing')) {
            mascotStatus.textContent = 'Analyzing agent feedback...';
        } else if (step.includes('SUGGESTING') || step.includes('suggesting')) {
            mascotStatus.textContent = 'Generating improvement suggestions...';
        } else if (step.includes('SUMMARIZING') || step.includes('summarizing')) {
            mascotStatus.textContent = 'Summarizing your page...';
        } else if (step.includes('GENERATING') || step.includes('generating')) {
            mascotStatus.textContent = 'Creating test tasks...';
        } else {
            mascotStatus.textContent = step || 'Initializing agents...';
        }
    }

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
            if (msg.includes('Grandma') || msg.includes('elderly')) {
                entry.classList.add('log-grandma');
            } else if (msg.includes('First Time User') || msg.includes('first_time_user')) {
                entry.classList.add('log-genz');
            } else if (msg.includes('Custom')) {
                entry.classList.add('log-custom');
            } else if (msg.includes('[WARN]')) {
                entry.classList.add('log-warn');
            } else if (msg.includes('[ERROR]')) {
                entry.classList.add('log-error');
            } else {
                entry.classList.add('log-system');
            }

            // Tag-based styling
            if (msg.includes('[TASK]')) entry.classList.add('log-task');
            if (msg.includes('[slow]')) entry.classList.add('log-slow');
            if (msg.includes('[done]')) entry.classList.add('log-done');
            if (msg.includes('[thinking]')) entry.classList.add('log-thinking');
            if (msg.includes('confusion') || msg.includes('backtracking')) entry.classList.add('log-confusion');
            if (msg.includes('Zoomed in')) entry.classList.add('log-zoom');

            // Extract timestamp and format as HH:MM:SS
            let cleaned = msg;
            const tsMatch = msg.match(/\[(\d{4}-\d{2}-\d{2}T[\d:.]+)\]\s*/);
            let timeStr = '';
            if (tsMatch) {
                const d = new Date(tsMatch[1]);
                timeStr = d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
                cleaned = msg.replace(tsMatch[0], '');
            }
            cleaned = cleaned.replace('[TASK] ', '');
            cleaned = cleaned.replace(' [slow]', '');
            cleaned = cleaned.replace('[done] ', '');
            cleaned = cleaned.replace(' [thinking]', '').replace('[thinking]: ', '');

            if (timeStr) {
                const ts = document.createElement('span');
                ts.className = 'log-timestamp';
                ts.textContent = timeStr;
                entry.appendChild(ts);

                const text = document.createElement('span');
                text.textContent = ' ' + cleaned;
                entry.appendChild(text);
            } else {
                entry.textContent = cleaned;
            }
            logEl.appendChild(entry);
        });

        logEl.scrollTop = logEl.scrollHeight;
    }

    // ── Live Browser Iframes ──
    if (data.persona_results && data.persona_results.length > 0) {
        // Track live previews for fullscreen mode
        const newPreviews = data.persona_results
            .filter(p => p.live_url)
            .map(p => ({
                live_url: p.live_url,
                persona_name: p.persona_name,
                persona_type: p.persona_type,
                status: p.status,
            }));

        const previewsChanged = JSON.stringify(newPreviews) !== JSON.stringify(lastLivePreviews);
        lastLivePreviews = newPreviews;

        if (fullscreenPreviewActive && previewsChanged && newPreviews.length > 0) {
            injectFullscreenPreview(newPreviews);
        }

        const container = document.getElementById('liveBrowsers');
        if (container) {
            data.persona_results.forEach((p, i) => {
                const iframeId = `live-iframe-${i}`;
                if (p.live_url && !document.getElementById(iframeId)) {
                    const card = document.createElement('div');
                    card.className = 'live-browser-card';
                    card.innerHTML = `
                        <div class="live-browser-label" style="border-color:var(--primary)">
                            <span class="live-persona-badge" style="background:var(--primary)">${personaIcon(p.persona_type)} ${p.persona_name}</span>
                            <span class="live-status-dot"></span>
                        </div>
                        <div class="live-browser-wrapper">
                            <iframe id="${iframeId}" src="${p.live_url}" class="live-browser-iframe" allow="autoplay; clipboard-write" sandbox="allow-same-origin allow-scripts allow-popups allow-forms"></iframe>
                            <div class="live-browser-click-overlay"></div>
                        </div>
                    `;
                    container.appendChild(card);

                    // Show fullscreen toggle once first live browser appears
                    const toggleWrap = document.getElementById('fullscreenToggleWrap');
                    if (toggleWrap) toggleWrap.style.display = '';

                    card.querySelector('.live-browser-click-overlay').addEventListener('click', () => {
                        openBrowserLightbox(p.live_url, p.persona_name, p.persona_type);
                    });
                }
                if (p.status === 'completed' && document.getElementById(iframeId)) {
                    const existingCard = document.getElementById(iframeId)?.closest('.live-browser-card');
                    if (existingCard) {
                        existingCard.querySelector('.live-status-dot')?.classList.add('done');
                    }
                }
            });
        }
    }
}

// ── Results ──────────────────────────────────────────────────────────────────
function showResults(data) {
    // Set results mascot to happy/yay randomly
    const celebImg = document.querySelector('.mascot-celebrate');
    if (celebImg) {
        celebImg.src = Math.random() < 0.5 ? '../icons/yay!!.svg' : '../icons/happy hi.svg';
    }
    // Dismiss fullscreen preview
    if (fullscreenPreviewActive) {
        fullscreenPreviewActive = false;
        const toggle = document.getElementById('fullscreenToggle');
        if (toggle) toggle.checked = false;
        removeFullscreenPreview();
    }
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
                                <span class="preview-label">- Before</span>
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
                        <textarea class="edit-css" spellcheck="false" style="width:100%; height:60px; font-family:var(--font-mono); font-size:11px; margin-bottom:10px; background:var(--bg-elevated); color:var(--text-primary); border:1px solid var(--border-color); border-radius:4px; padding:8px; box-sizing:border-box;">${issue.fix_css || ''}</textarea>
                        <span class="preview-label">Edit JS Fix</span>
                        <textarea class="edit-js" spellcheck="false" style="width:100%; height:80px; font-family:var(--font-mono); font-size:11px; background:var(--bg-elevated); color:var(--text-primary); border:1px solid var(--border-color); border-radius:4px; padding:8px; box-sizing:border-box;">${issue.fix_js || ''}</textarea>
                    </div>
                    <div class="issue-actions">
                        <button class="btn-skip toggle-code-btn" data-issue="${i}" style="flex:0.3; padding:8px 0;" title="Edit AI Code">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 19l7-7 3 3-7 7-3-3z"></path><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"></path><path d="M2 2l7.586 7.586"></path><circle cx="11" cy="11" r="2"></circle></svg>
                        </button>
                        ${issue.applied ? `
                        <button class="btn-apply applied" data-issue="${i}" style="flex:1;">
                            Applied
                        </button>
                        <button class="btn-skip revert-fix-btn" data-issue="${i}" style="flex:0.5; border-color:var(--accent-red); color:var(--accent-red);" title="Revert Changes">Revert</button>
                        ` : `
                        <button class="btn-apply apply-fix-btn" data-issue="${i}" style="flex:1">
                            Apply Fix
                        </button>
                        <button class="btn-skip toggle-issue-btn" data-issue="${i}" style="flex:0.5">Skip</button>
                        `}
                    </div>
                </div>
            </div>
        `;
    }).join('');

    // Attach event listeners
    document.querySelectorAll('.toggle-issue-btn').forEach(btn => {
        btn.addEventListener('click', (e) => toggleIssue(e.currentTarget.dataset.issue));
    });
    document.querySelectorAll('.toggle-code-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const editor = document.getElementById(`codeEditor${e.currentTarget.dataset.issue}`);
            if (editor) editor.style.display = editor.style.display === 'none' ? 'block' : 'none';
        });
    });
    document.querySelectorAll('.apply-fix-btn').forEach(btn => {
        btn.addEventListener('click', (e) => applyFix(e.currentTarget.dataset.issue));
    });
    document.querySelectorAll('.revert-fix-btn').forEach(btn => {
        btn.addEventListener('click', (e) => revertFix(e.currentTarget.dataset.issue));
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
                await chrome.scripting.removeCSS({ target: { tabId: tab.id }, css: issue.fix_css });
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

    if (!issue.fix_js && !issue.fix_css) {
        alert('No fix script available for this issue.');
        return;
    }

    const btn = document.querySelector(`[data-index="${index}"] .btn-apply`);
    if (btn) { btn.textContent = 'Applying...'; btn.disabled = true; }

    try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) throw new Error('No active tab');

        const beforeImg = await chrome.tabs.captureVisibleTab(null, { format: 'png' });

        // Show toast
        await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: () => {
                const toast = document.createElement('div');
                toast.id = 'agentux-toast';
                toast.style.cssText = 'position:fixed;bottom:20px;right:20px;background:#0084FF;color:white;padding:12px 24px;border-radius:8px;font-family:system-ui,sans-serif;font-weight:600;font-size:14px;z-index:999999;box-shadow:0 10px 25px rgba(0,0,0,0.3);transition:all 0.4s ease;transform:translateY(20px);opacity:0;';
                toast.textContent = 'Agent UX: Applying fix...';
                document.body.appendChild(toast);
                requestAnimationFrame(() => { toast.style.transform = 'translateY(0)'; toast.style.opacity = '1'; });
                setTimeout(() => { toast.style.background = '#34d399'; toast.textContent = 'Agent UX: Fix applied'; }, 400);
                setTimeout(() => { toast.style.opacity = '0'; toast.style.transform = 'translateY(20px)'; setTimeout(() => toast.remove(), 400); }, 3000);
            }
        });

        if (issue.fix_css) {
            await chrome.scripting.insertCSS({ target: { tabId: tab.id }, css: issue.fix_css });
        }

        if (issue.fix_js) {
            await chrome.scripting.executeScript({
                target: { tabId: tab.id },
                world: 'MAIN',
                func: (jsCode) => {
                    try {
                        const script = document.createElement('script');
                        script.textContent = `try { ${jsCode} } catch(err) { alert('Agent UX DOM Error: ' + err.message); }`;
                        (document.head || document.documentElement).appendChild(script);
                        script.remove();
                    } catch(e) { console.error('[Agent UX JS Injection]', e); }
                },
                args: [issue.fix_js],
            });
        }

        await new Promise(r => setTimeout(r, 500));
        const afterImg = await chrome.tabs.captureVisibleTab(null, { format: 'png' });

        try {
            await chrome.tabs.sendMessage(tab.id, {
                type: 'save_fix',
                fix: { id: issue.id, url: currentUrl, domain: new URL(currentUrl).hostname, css: issue.fix_css, js: issue.fix_js, description: issue.title },
            });
        } catch (e) {
            console.log('Content script not available for persistence');
        }

        issue.applied = true;
        issue.beforeScreenshot = beforeImg;
        issue.afterScreenshot = afterImg;
        renderIssues();
    } catch (e) {
        console.error('Apply fix failed:', e);
        if (btn) { btn.textContent = 'Apply Fix'; btn.disabled = false; }
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
    btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg> Copied!`;
    setTimeout(() => {
        btn.classList.remove('copied');
        btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> Copy Prompt`;
    }, 2000);
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function resetUI() {
    // Dismiss fullscreen preview
    if (fullscreenPreviewActive) {
        fullscreenPreviewActive = false;
        const toggle = document.getElementById('fullscreenToggle');
        if (toggle) toggle.checked = false;
        removeFullscreenPreview();
    }

    if (pollInterval) clearInterval(pollInterval);
    pollInterval = null;
    currentRunId = null;
    lastLogCount = 0;
    lastLivePreviews = [];
    issues = [];
    pageSummary = '';
    generatedTasks = [];
    taskCount = 2;
    editingTaskIndex = null;

    // Reset tab states
    document.getElementById('tabProgress').disabled = true;
    document.getElementById('tabResults').disabled = true;
    switchTab('setup');

    // Reset setup steps
    document.getElementById('toggleAddTaskBtn').style.display = 'none';
    document.getElementById('addTaskForm').style.display = 'none';
    document.getElementById('taskCards').innerHTML = '<div class="task-cards-empty" id="taskCardsEmpty">Click "Generate Tasks" to create test tasks from the page summary</div>';
    document.getElementById('taskCountValue').textContent = '2';
    updateEvalButton();

    const liveBrowsers = document.getElementById('liveBrowsers');
    if (liveBrowsers) { liveBrowsers.innerHTML = ''; liveBrowsers.style.display = ''; }
    const toggleWrap = document.getElementById('fullscreenToggleWrap');
    if (toggleWrap) toggleWrap.style.display = 'none';
    document.getElementById('liveLogEntries').innerHTML = '';
    const btn = document.getElementById('evaluateBtn');
    btn.disabled = false;
    btn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        <span>Run Evaluation</span>
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
        document.getElementById('lightboxImg').src = e.target.src;
        lightbox.style.display = 'flex';
    }
});

document.getElementById('imageLightbox').addEventListener('click', () => {
    document.getElementById('imageLightbox').style.display = 'none';
});

// ── Browser Lightbox Logic ───────────────────────────────────────────────────
function openBrowserLightbox(liveUrl, personaName, personaType) {
    const lightbox = document.getElementById('browserLightbox');
    document.getElementById('lightboxBrowserIframe').src = liveUrl;
    document.getElementById('lightboxBrowserLabel').innerHTML = `${personaIcon(personaType, 20)} <span>${personaName}</span>`;
    lightbox.style.display = 'flex';
}

function closeBrowserLightbox() {
    document.getElementById('browserLightbox').style.display = 'none';
    setTimeout(() => { document.getElementById('lightboxBrowserIframe').src = 'about:blank'; }, 300);
}

document.getElementById('closeBrowserLightbox').addEventListener('click', closeBrowserLightbox);
document.getElementById('browserLightbox').addEventListener('click', (e) => {
    if (e.target.id === 'browserLightbox') closeBrowserLightbox();
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (document.getElementById('browserLightbox').style.display === 'flex') closeBrowserLightbox();
        else if (document.getElementById('imageLightbox').style.display === 'flex') document.getElementById('imageLightbox').style.display = 'none';
    }
});
