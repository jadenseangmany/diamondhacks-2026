// Service worker — opens the side panel when the toolbar icon is clicked.
chrome.action.onClicked.addListener((tab) => {
  chrome.sidePanel.open({ tabId: tab.id });
});
