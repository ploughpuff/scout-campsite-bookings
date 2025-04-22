const logContainer = document.getElementById('log-container');
const levelSelect = document.getElementById('level');

function fetchLogs() {
    const level = levelSelect.value;
    fetch(`/logs/data?level=${level}`)
        .then(response => response.text())
        .then(data => {
            logContainer.textContent = data || '[No logs]';
            logContainer.scrollTop = logContainer.scrollHeight;
        });
}

function downloadLogs() {
    window.location.href = '/logs/download';
}

// Fetch logs when filter changes
levelSelect.addEventListener('change', fetchLogs);

// Initial load
fetchLogs();
