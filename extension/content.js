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

