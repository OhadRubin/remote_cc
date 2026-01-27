export const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;

export const panes = [];
export let activePane = null;
export let tabCounter = 0;

export function setActivePane(pane) {
    activePane = pane;
}

export function incrementTabCounter() {
    tabCounter++;
    return tabCounter;
}
