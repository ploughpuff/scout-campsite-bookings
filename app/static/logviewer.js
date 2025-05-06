const logContainer = document.getElementById('log-container');
const levelSelect = document.getElementById('level');

function fetchLogs() {
    const level = levelSelect.value;
    fetch(`/logs/data?level=${level}`)
        .then(response => response.text())
        .then(data => {
            const lines = data.split("\n");
            logContainer.innerHTML = ""; // clear it

            lines.forEach(line => {
                if (line.trim() === "") return;

                const div = document.createElement("div");
                div.classList.add("log-line");

                // Detect and tag line by level
                if (line.includes("ERROR")) div.classList.add("log-ERROR");
                else if (line.includes("WARNING")) div.classList.add("log-WARNING");
                else if (line.includes("INFO")) div.classList.add("log-INFO");
                else if (line.includes("DEBUG")) div.classList.add("log-DEBUG");

                div.textContent = line;
                logContainer.appendChild(div);
            });

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
