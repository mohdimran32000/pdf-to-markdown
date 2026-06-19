const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const headerUploadBtn = document.getElementById('header-upload-btn');
const progressOverlay = document.getElementById('progress-overlay');
const progressFill = document.getElementById('progress-fill');
const progressMessage = document.getElementById('progress-message');
const progressPercent = document.getElementById('progress-percent');
const progressDetail = document.getElementById('progress-detail');
const resultContainer = document.getElementById('result-container');
const pdfEmbed = document.getElementById('pdf-embed');
const markdownPreview = document.getElementById('markdown-preview');

const downloadBtn = document.getElementById('download-btn');
const copyBtn = document.getElementById('copy-btn');
const resetBtn = document.getElementById('reset-btn');
const modelSelect = document.getElementById('model-select');
const engineBadge = document.getElementById('engine-badge');

// Update badge when model changes
modelSelect.addEventListener('change', () => {
    const shortNames = {
        'gemini-3.1-pro-preview': 'Gemini 3.1 Pro',
        'gemini-3-flash-preview': 'Gemini 3 Flash'
    };
    engineBadge.textContent = shortNames[modelSelect.value] || modelSelect.value;
});

// Batch elements
const batchContainer = document.getElementById('batch-container');
const batchTitle = document.getElementById('batch-title');
const batchStatusLabel = document.getElementById('batch-status-label');
const batchCountLabel = document.getElementById('batch-count-label');
const batchProgressFill = document.getElementById('batch-progress-fill');
const batchFileList = document.getElementById('batch-file-list');
const batchDownloadBtn = document.getElementById('batch-download-btn');
const batchResetBtn = document.getElementById('batch-reset-btn');
const retryFailedBtn = document.getElementById('retry-failed-btn');

let currentMarkdown = "";
let batchMarkdown = "";
let failedFiles = [];
let batchTotal = 0;
let batchSuccessCount = 0;

// --- Drag & Drop / Upload Logic ---

headerUploadBtn.addEventListener('click', () => fileInput.click());
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
    const files = Array.from(e.dataTransfer.files).filter(f => f.type === 'application/pdf');
    if (!files.length) return;
    if (files.length === 1) {
        handleFile(files[0]);
    } else {
        handleBatch(files);
    }
});

fileInput.addEventListener('change', () => {
    const files = Array.from(fileInput.files);
    if (!files.length) return;
    if (files.length === 1) {
        handleFile(files[0]);
    } else {
        handleBatch(files);
    }
});

// --- Single File Flow ---

const SSE_INACTIVITY_TIMEOUT_MS = 600000; // 10 minutes with no events = abort

async function handleFile(file) {
    if (file.type !== 'application/pdf') {
        alert('Please upload a PDF file.');
        return;
    }

    const fileURL = URL.createObjectURL(file);
    pdfEmbed.src = fileURL;
    showProgress(true, 'Uploading...', 0);
    updateProgressDetail('');

    const formData = new FormData();
    formData.append('file', file);
    formData.append('model', modelSelect.value);

    const startTime = Date.now();
    let elapsedTimer = null;

    // Update elapsed time every second
    elapsedTimer = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        const timeStr = mins > 0 ? `${mins}m ${secs}s elapsed` : `${secs}s elapsed`;
        updateProgressDetail(timeStr);
    }, 1000);

    try {
        const response = await fetch('/convert-stream', { method: 'POST', body: formData });
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Conversion failed');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let receivedResult = false;

        // Inactivity timeout: abort if no SSE event for too long
        let inactivityTimeout = null;

        function resetInactivityTimer() {
            if (inactivityTimeout) clearTimeout(inactivityTimeout);
            inactivityTimeout = setTimeout(() => {
                reader.cancel();
            }, SSE_INACTIVITY_TIMEOUT_MS);
        }
        resetInactivityTimer();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            resetInactivityTimer();
            buffer += decoder.decode(value, { stream: true });
            const parts = buffer.split('\n\n');
            buffer = parts.pop();

            for (const part of parts) {
                const line = part.trim();
                if (!line.startsWith('data: ')) continue;
                const data = JSON.parse(line.slice(6));

                if (data.type === 'stage') {
                    const msg = data.message || '';
                    updateProgressUI(msg, data.progress || 0);

                    if (data.stage === 'retry') {
                        setProgressStyle('retry');
                    } else if (data.stage === 'warning') {
                        setProgressStyle('warning');
                    } else {
                        setProgressStyle('normal');
                    }
                } else if (data.type === 'result') {
                    setProgressStyle('normal');
                    updateProgressUI('Done!', 100);
                    currentMarkdown = data.markdown;
                    receivedResult = true;
                    await new Promise(r => setTimeout(r, 300));
                    renderResult(data.markdown);
                } else if (data.type === 'error') {
                    throw new Error(data.message);
                }
            }
        }

        if (inactivityTimeout) clearTimeout(inactivityTimeout);

        if (!receivedResult) {
            throw new Error('Connection lost before processing completed. Check /dashboard for details.');
        }
    } catch (error) {
        if (error.name === 'AbortError' || error.message.includes('abort')) {
            alert('Error: No response from server for too long. The API call may have timed out.\n\nCheck the Dashboard (/dashboard) for detailed logs.');
        } else {
            alert('Error: ' + error.message);
        }
        resetView();
    } finally {
        if (elapsedTimer) clearInterval(elapsedTimer);
        showProgress(false);
    }
}

function showProgress(visible, message = '', percent = 0) {
    progressOverlay.hidden = !visible;
    if (visible) {
        updateProgressUI(message, percent);
        setProgressStyle('normal');
    }
}

function updateProgressUI(message, percent) {
    progressFill.style.width = `${percent}%`;
    progressMessage.textContent = message;
    progressPercent.textContent = `${percent}%`;
}

function updateProgressDetail(text) {
    if (progressDetail) {
        progressDetail.textContent = text;
    }
}

function setProgressStyle(style) {
    progressMessage.classList.remove('progress-retry', 'progress-warning');
    if (style === 'retry') {
        progressMessage.classList.add('progress-retry');
    } else if (style === 'warning') {
        progressMessage.classList.add('progress-warning');
    }
}

function preprocessMarkdown(md) {
    // Strip code fences wrapping HTML tables (Gemini sometimes wraps tables in ```html ... ```)
    md = md.replace(/```(?:html)?\s*\n(<table[\s\S]*?<\/table>)\s*\n```/gi, '\n\n$1\n\n');
    // Ensure blank lines before <table> and after </table> for block-level rendering
    md = md.replace(/([^\n])\n(<table)/gi, '$1\n\n$2');
    md = md.replace(/(<\/table>)\n([^\n])/gi, '$1\n\n$2');
    return md;
}

function renderResult(markdown) {
    progressOverlay.hidden = true;
    dropZone.hidden = false;
    document.querySelector('main').classList.add('has-results');
    resultContainer.hidden = false;
    markdownPreview.innerHTML = marked.parse(preprocessMarkdown(markdown));
}

function resetView() {
    currentMarkdown = "";
    batchMarkdown = "";
    failedFiles = [];
    batchTotal = 0;
    batchSuccessCount = 0;
    document.querySelector('main').classList.remove('has-results');
    resultContainer.hidden = true;
    batchContainer.hidden = true;
    dropZone.hidden = false;
    progressOverlay.hidden = true;
    fileInput.value = '';
    pdfEmbed.src = '';
    batchDownloadBtn.hidden = true;
    retryFailedBtn.hidden = true;
    batchFileList.innerHTML = '';
    batchProgressFill.style.width = '0%';
    batchTitle.textContent = 'Processing PDFs';
    batchStatusLabel.textContent = 'Starting...';
    batchCountLabel.textContent = '0 / 0';
}

// --- Core: process a single file with retry logic ---

async function processOneFile(file, item) {
    const icon = item.querySelector('.bfi-icon');
    const status = item.querySelector('.bfi-status');

    item.dataset.state = 'processing';
    icon.textContent = '\u25CC';
    icon.className = 'bfi-icon processing-icon';
    status.textContent = 'Processing...';
    item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    const formData = new FormData();
    formData.append('file', file);
    formData.append('model', modelSelect.value);

    const MAX_RETRIES = 3;
    const RETRY_DELAY_MS = 10000;
    const FETCH_TIMEOUT_MS = 600000;
    let lastError = null;

    for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        try {
            if (attempt > 1) {
                status.textContent = `Retry ${attempt - 1} of ${MAX_RETRIES - 1}...`;
                await new Promise(r => setTimeout(r, RETRY_DELAY_MS * (attempt - 1)));
            }
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
            let response;
            try {
                response = await fetch('/convert', { method: 'POST', body: formData, signal: controller.signal });
            } finally {
                clearTimeout(timeoutId);
            }
            if (!response.ok) {
                throw new Error((await response.json()).detail || 'Server error');
            }
            const text = await response.text();

            item.dataset.state = 'done';
            icon.textContent = '\u2713';
            icon.className = 'bfi-icon done-icon';
            status.textContent = 'Done';
            return text;
        } catch (err) {
            lastError = err;
        }
    }

    item.dataset.state = 'error';
    icon.textContent = '\u2717';
    icon.className = 'bfi-icon error-icon';
    status.textContent = `Failed: ${lastError.message}`;
    return null;
}

// --- Batch Flow ---

async function handleBatch(files) {
    const total = files.length;
    batchTotal = total;
    batchSuccessCount = 0;
    batchMarkdown = '';
    failedFiles = [];

    dropZone.hidden = true;
    batchContainer.hidden = false;
    batchDownloadBtn.hidden = true;
    retryFailedBtn.hidden = true;
    batchFileList.innerHTML = '';
    batchTitle.textContent = `Processing ${total} PDFs`;

    const fileItems = files.map((file) => {
        const item = document.createElement('div');
        item.className = 'batch-file-item';
        item.innerHTML = `
            <span class="bfi-icon pending-icon">\u25CB</span>
            <span class="bfi-name" title="${file.name}">${file.name}</span>
            <span class="bfi-status">Waiting</span>
        `;
        batchFileList.appendChild(item);
        return item;
    });

    updateBatchProgress(0, total);

    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const item = fileItems[i];
        updateBatchStatus(`Processing ${i + 1} of ${total}: ${file.name}`);

        const text = await processOneFile(file, item);
        if (text !== null) {
            batchMarkdown += `\n\n---\n\n# ${file.name}\n\n${text}`;
            batchSuccessCount++;
        } else {
            failedFiles.push({ file, item });
        }

        updateBatchProgress(i + 1, total);
    }

    batchTitle.textContent = `Done \u2014 ${batchSuccessCount} of ${batchTotal} converted`;
    batchMarkdown = batchMarkdown.trim();
    batchDownloadBtn.hidden = batchMarkdown.length === 0;

    if (failedFiles.length > 0) {
        updateBatchStatus(`${failedFiles.length} file(s) failed. Click "Retry Failed" to try again.`);
        retryFailedBtn.textContent = `Retry Failed (${failedFiles.length})`;
        retryFailedBtn.hidden = false;
    } else {
        updateBatchStatus('All files processed. Download your combined markdown below.');
    }
}

// --- Retry Failed Flow ---

async function handleRetryFailed() {
    const toRetry = [...failedFiles];
    failedFiles = [];
    retryFailedBtn.hidden = true;

    const total = toRetry.length;
    batchTitle.textContent = `Retrying ${total} failed file${total > 1 ? 's' : ''}`;
    updateBatchStatus(`Starting retry of ${total} file${total > 1 ? 's' : ''}...`);

    for (let i = 0; i < toRetry.length; i++) {
        const { file, item } = toRetry[i];
        updateBatchStatus(`Retrying ${i + 1} of ${total}: ${file.name}`);

        const text = await processOneFile(file, item);
        if (text !== null) {
            batchMarkdown += `\n\n---\n\n# ${file.name}\n\n${text}`;
            batchSuccessCount++;
        } else {
            failedFiles.push({ file, item });
        }
    }

    batchTitle.textContent = `Done \u2014 ${batchSuccessCount} of ${batchTotal} converted`;
    batchMarkdown = batchMarkdown.trim();
    batchDownloadBtn.hidden = batchMarkdown.length === 0;

    if (failedFiles.length > 0) {
        updateBatchStatus(`${failedFiles.length} file(s) still failed. Click "Retry Failed" to try again.`);
        retryFailedBtn.textContent = `Retry Failed (${failedFiles.length})`;
        retryFailedBtn.hidden = false;
    } else {
        updateBatchStatus('All files converted. Download your combined markdown below.');
    }
}

function updateBatchProgress(done, total) {
    const pct = total > 0 ? (done / total) * 100 : 0;
    batchProgressFill.style.width = `${pct}%`;
    batchCountLabel.textContent = `${done} / ${total}`;
}

function updateBatchStatus(text) {
    batchStatusLabel.textContent = text;
}

// --- Action Buttons ---

resetBtn.addEventListener('click', resetView);
batchResetBtn.addEventListener('click', resetView);
retryFailedBtn.addEventListener('click', handleRetryFailed);

copyBtn.addEventListener('click', () => {
    navigator.clipboard.writeText(currentMarkdown);
    const orig = copyBtn.innerText;
    copyBtn.innerText = 'Copied!';
    setTimeout(() => copyBtn.innerText = orig, 2000);
});

downloadBtn.addEventListener('click', () => {
    triggerDownload(currentMarkdown, 'output.md');
});

batchDownloadBtn.addEventListener('click', () => {
    triggerDownload(batchMarkdown, 'combined_output.md');
});

function triggerDownload(content, filename) {
    const blob = new Blob([content], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
