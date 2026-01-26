const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const headerUploadBtn = document.getElementById('header-upload-btn');
const loadingOverlay = document.getElementById('loading-overlay');
const resultContainer = document.getElementById('result-container');
const pdfEmbed = document.getElementById('pdf-embed');
const markdownPreview = document.getElementById('markdown-preview');

const downloadBtn = document.getElementById('download-btn');
const copyBtn = document.getElementById('copy-btn');
const resetBtn = document.getElementById('reset-btn');

let currentMarkdown = "";

// --- Drag & Drop / Upload Logic ---

// Trigger file input when clicking header button
headerUploadBtn.addEventListener('click', () => {
    fileInput.click();
});

// Also trigger on dropzone click for convenience
dropZone.addEventListener('click', () => fileInput.click());

dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
});

dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragover');
});

dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length) {
        handleFile(e.dataTransfer.files[0]);
    }
});

fileInput.addEventListener('change', () => {
    if (fileInput.files.length) {
        handleFile(fileInput.files[0]);
    }
});

async function handleFile(file) {
    if (file.type !== 'application/pdf') {
        alert('Please upload a PDF file.');
        return;
    }

    // 1. Show PDF Preview immediately
    const fileURL = URL.createObjectURL(file);
    pdfEmbed.src = fileURL;

    // 2. Show Loading State
    showLoading(true);

    // 3. Prepare Upload
    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch('/convert', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Conversion failed');
        }

        const text = await response.text();
        currentMarkdown = text;
        renderResult(text);

    } catch (error) {
        alert('Error: ' + error.message);
        // Reset view on error
        resetView();
    } finally {
        showLoading(false);
    }
}

function showLoading(isLoading) {
    loadingOverlay.hidden = !isLoading;
    if (isLoading) {
        // Keep result container hidden while loading initial state,
        // OR show it with overlay if re-processing.
        // For now, simpler to toggle main views.
        // But we want to transition FROM dropZone TO splitView.
        // If loading, we are transitioning.
    }
}

function renderResult(markdown) {
    loadingOverlay.hidden = true;
    // Don't hide dropZone completely, just enter "compact mode" via CSS
    dropZone.hidden = false;
    document.querySelector('main').classList.add('has-results');
    resultContainer.hidden = false;

    // Render Markdown
    markdownPreview.innerHTML = marked.parse(markdown);
}

function resetView() {
    currentMarkdown = "";
    document.querySelector('main').classList.remove('has-results');
    resultContainer.hidden = true;
    dropZone.hidden = false;
    loadingOverlay.hidden = true;
    fileInput.value = '';
    pdfEmbed.src = '';
}

// --- Action Buttons ---

resetBtn.addEventListener('click', resetView);

copyBtn.addEventListener('click', () => {
    navigator.clipboard.writeText(currentMarkdown);
    const originalText = copyBtn.innerText;
    copyBtn.innerText = "Copied!";
    setTimeout(() => copyBtn.innerText = originalText, 2000);
});

downloadBtn.addEventListener('click', () => {
    const blob = new Blob([currentMarkdown], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'output.md';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
});
