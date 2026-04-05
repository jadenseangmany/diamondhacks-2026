/**
 * AgentUX Chrome Extension — Background Service Worker
 * Relays messages between popup and content scripts.
 */

const API_BASE = 'http://localhost:8000';

// Listen for messages from popup
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'evaluate') {
        // Start evaluation via backend
        fetch(`${API_BASE}/api/test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: message.url,
                personas: message.personas,
            }),
        })
            .then(resp => resp.json())
            .then(data => sendResponse({ success: true, data }))
            .catch(err => sendResponse({ success: false, error: err.message }));
        return true; // Keep channel open for async response
    }

    if (message.type === 'get_results') {
        fetch(`${API_BASE}/api/runs/${message.runId}`)
            .then(resp => resp.json())
            .then(data => sendResponse({ success: true, data }))
            .catch(err => sendResponse({ success: false, error: err.message }));
        return true;
    }
});
