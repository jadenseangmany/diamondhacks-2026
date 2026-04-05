/**
 * AgentUX Chrome Extension — Content Script
 * Handles CSS/JS fix injection and persistence via chrome.storage.
 * Runs on every page load to re-apply saved fixes.
 */

// ── On Page Load: Re-apply saved fixes ──────────────────────────────────────
(async function applySavedFixes() {
    const domain = window.location.hostname;
    try {
        const result = await chrome.storage.local.get('agentux_fixes');
        const allFixes = result.agentux_fixes || {};
        const domainFixes = allFixes[domain] || [];

        domainFixes.forEach(fix => {
            injectFix(fix);
        });

        if (domainFixes.length > 0) {
            console.log(`[AgentUX] Applied ${domainFixes.length} saved fix(es) for ${domain}`);
        }
    } catch (e) {
        console.error('[AgentUX] Failed to load saved fixes:', e);
    }
})();

// ── Listen for messages from popup/background ───────────────────────────────
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'apply_fix') {
        try {
            injectFix(message.fix);
            sendResponse({ success: true });
        } catch (e) {
            sendResponse({ success: false, error: e.message });
        }
    }

    if (message.type === 'save_fix') {
        saveFix(message.fix)
            .then(() => sendResponse({ success: true }))
            .catch(e => sendResponse({ success: false, error: e.message }));
        return true; // async
    }

    if (message.type === 'capture_dom') {
        // Return cleaned page text
        const text = extractPageText();
        sendResponse({ success: true, text });
    }

    if (message.type === 'show_live_previews') {
        showLivePreviews(message.previews);
        sendResponse({ success: true });
    }

    if (message.type === 'hide_live_previews') {
        hideLivePreviews();
        sendResponse({ success: true });
    }
});

// ── Inject a fix (CSS and/or JS) ────────────────────────────────────────────
function injectFix(fix) {
    // Inject CSS
    if (fix.css) {
        const style = document.createElement('style');
        style.dataset.agentuxFix = fix.id;
        style.textContent = fix.css;
        document.head.appendChild(style);
    }

    // Inject JS
    if (fix.js) {
        try {
            const fn = new Function(fix.js);
            fn();
        } catch (e) {
            console.error(`[AgentUX] Fix ${fix.id} JS injection failed:`, e);
        }
    }
}

// ── Save fix to chrome.storage for persistence ──────────────────────────────
async function saveFix(fix) {
    const domain = fix.domain || window.location.hostname;
    const result = await chrome.storage.local.get('agentux_fixes');
    const allFixes = result.agentux_fixes || {};

    if (!allFixes[domain]) {
        allFixes[domain] = [];
    }

    // Avoid duplicates
    const existing = allFixes[domain].findIndex(f => f.id === fix.id);
    if (existing >= 0) {
        allFixes[domain][existing] = fix;
    } else {
        allFixes[domain].push(fix);
    }

    await chrome.storage.local.set({ agentux_fixes: allFixes });
    console.log(`[AgentUX] Saved fix "${fix.description}" for ${domain}`);
}

// ── Extract page text (for DOM capture) ─────────────────────────────────────
function extractPageText() {
    const clone = document.body.cloneNode(true);

    // Remove scripts and styles
    clone.querySelectorAll('script, style, noscript').forEach(el => el.remove());

    // Get text content
    let text = clone.innerText || clone.textContent;
    text = text.replace(/\s+/g, ' ').trim();

    // Truncate to 8000 chars
    return text.substring(0, 8000);
}

// ── Fullscreen Live Preview ─────────────────────────────────────────────────
const PREVIEW_CONTAINER_ID = 'agentux-live-preview-overlay';
let originalOverflow = '';

const PERSONA_COLORS = {
    elderly: '#a78bfa',
    millennial: '#22d3ee',
};

function getPersonaColor(type) {
    return PERSONA_COLORS[type] || '#34d399';
}

function showLivePreviews(previews) {
    if (!previews || previews.length === 0) return;

    // Remove existing overlay if present (update case)
    let container = document.getElementById(PREVIEW_CONTAINER_ID);
    if (container) {
        container.remove();
    } else {
        // First time: save scroll state
        originalOverflow = document.body.style.overflow;
    }

    // Prevent page scroll behind the overlay
    document.body.style.overflow = 'hidden';

    container = document.createElement('div');
    container.id = PREVIEW_CONTAINER_ID;

    // Determine grid layout based on count
    const count = previews.length;
    let gridCols, gridRows;
    if (count === 1) {
        gridCols = '1fr';
        gridRows = '1fr';
    } else if (count === 2) {
        gridCols = '1fr 1fr';
        gridRows = '1fr';
    } else if (count <= 4) {
        gridCols = '1fr 1fr';
        gridRows = count <= 2 ? '1fr' : '1fr 1fr';
    } else {
        gridCols = '1fr 1fr 1fr';
        gridRows = `repeat(${Math.ceil(count / 3)}, 1fr)`;
    }

    container.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        z-index: 2147483647;
        background: #0a0a0f;
        display: grid;
        grid-template-columns: ${gridCols};
        grid-template-rows: ${gridRows};
        gap: 2px;
        font-family: 'Inter', -apple-system, sans-serif;
    `;

    previews.forEach(p => {
        const color = getPersonaColor(p.persona_type);
        const isDone = p.status === 'completed';

        const cell = document.createElement('div');
        cell.style.cssText = `
            display: flex;
            flex-direction: column;
            min-height: 0;
            background: #111118;
        `;

        // Label bar
        const label = document.createElement('div');
        label.style.cssText = `
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 14px;
            background: #1a1a24;
            border-bottom: 2px solid ${color};
            flex-shrink: 0;
        `;

        const badge = document.createElement('span');
        badge.style.cssText = `
            font-size: 12px;
            font-weight: 700;
            color: white;
            padding: 3px 10px;
            background: ${color};
            border-radius: 9999px;
            letter-spacing: 0.02em;
        `;
        badge.textContent = p.persona_name;

        const statusDot = document.createElement('span');
        statusDot.style.cssText = `
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: ${isDone ? '#4a4a5a' : '#34d399'};
            margin-left: auto;
            ${isDone ? '' : 'animation: agentux-pulse 1.5s ease-in-out infinite;'}
        `;

        label.appendChild(badge);
        if (isDone) {
            const checkmark = document.createElement('span');
            checkmark.style.cssText = 'font-size: 11px; color: #9898a8; font-weight: 600;';
            checkmark.textContent = '✓ Done';
            label.appendChild(checkmark);
        }
        label.appendChild(statusDot);

        // Iframe
        const iframe = document.createElement('iframe');
        iframe.src = p.live_url;
        iframe.allow = 'autoplay; clipboard-write';
        iframe.sandbox = 'allow-same-origin allow-scripts allow-popups allow-forms';
        iframe.style.cssText = `
            flex: 1;
            width: 100%;
            border: none;
            background: white;
            min-height: 0;
        `;

        cell.appendChild(label);
        cell.appendChild(iframe);
        container.appendChild(cell);
    });

    // Inject the pulse animation
    let styleEl = document.getElementById('agentux-preview-styles');
    if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = 'agentux-preview-styles';
        styleEl.textContent = `
            @keyframes agentux-pulse {
                0%, 100% { opacity: 1; transform: scale(1); }
                50% { opacity: 0.4; transform: scale(0.8); }
            }
        `;
        document.head.appendChild(styleEl);
    }

    document.body.appendChild(container);
    console.log(`[AgentUX] Showing ${previews.length} live preview(s) in fullscreen`);
}

function hideLivePreviews() {
    const container = document.getElementById(PREVIEW_CONTAINER_ID);
    if (container) {
        container.remove();
        document.body.style.overflow = originalOverflow;
        console.log('[AgentUX] Fullscreen live preview hidden');
    }
    const styleEl = document.getElementById('agentux-preview-styles');
    if (styleEl) styleEl.remove();
}
