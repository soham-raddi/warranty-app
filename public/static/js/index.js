function byId(id) {
    return document.getElementById(id);
}

const appSettings = {
    timezone: 'UTC',
    currency_symbol: '₹',
    currency_code: 'INR',
    warranty_period_days: '365',
    expiry_alert_days: '30',
    reminders_enabled: '0',
    reminder_days_before: '30',
    reminder_recipients: '',
    theme_mode: 'system',
    accent_color: 'blue',
    date_format: 'auto',
    time_format: '12h',
    reduced_motion: '0',
    density: 'comfortable'
};

const ACCENT_COLORS = {
    blue: ['#2563eb', '#1d4ed8'],
    teal: ['#0f766e', '#115e59'],
    amber: ['#d97706', '#b45309'],
    rose: ['#e11d48', '#be123c'],
    slate: ['#475569', '#334155']
};

// Display symbols only: amounts are stored in the currency printed on each receipt and are never converted.
const CURRENCY_RATES = {
    INR: { symbol: '₹' },
    USD: { symbol: '$' },
    EUR: { symbol: '€' },
    GBP: { symbol: '£' },
    AED: { symbol: 'د.إ' },
    CAD: { symbol: 'C$' },
    AUD: { symbol: 'A$' },
    SGD: { symbol: 'S$' },
    JPY: { symbol: '¥' },
    CNY: { symbol: '¥' }
};

function inferCurrencyCodeFromSymbol(symbol) {
    const value = String(symbol || '').trim();
    const entries = Object.entries(CURRENCY_RATES);
    const match = entries.find(([, meta]) => meta.symbol === value);
    return match ? match[0] : 'INR';
}

function setText(id, value) {
    const el = byId(id);
    if (el) el.innerText = value;
}

// Shared empty, loading and error states so every list looks the same.
function stateBlock(message, isError = false) {
    return `<div class="state-message${isError ? ' is-error' : ''}">${escapeHtml(message)}</div>`;
}

function stateRow(colspan, message, isError = false) {
    return `<tr><td colspan="${Number(colspan)}" class="p-0">${stateBlock(message, isError)}</td></tr>`;
}

function canManageWorkspace() {
    // Mirrors the server's checks; the server still enforces them.
    return ['owner', 'admin'].includes(document.body.dataset.role);
}

function renderMessageContent(content, role) {
    const text = String(content || '');
    // Assistant replies are Markdown; render them only when the sanitizer is available.
    if (role === 'assistant' && window.marked && window.DOMPurify) {
        return DOMPurify.sanitize(marked.parse(text, { breaks: true, gfm: true }));
    }
    return escapeHtml(text).replace(/\n/g, '<br>');
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function parseAmount(value) {
    // Same rule as the server: take the first number, so 'Rs. 1,200' is 1200 rather than 0.12.
    const match = String(value ?? '').match(/-?\d[\d,]*(?:\.\d+)?/);
    return match ? Number(match[0].replace(/,/g, '')) || 0 : 0;
}

function money(value) {
    // Intl picks the right symbol, placement and decimals for each currency (e.g. ₹1,21,713 or ¥5,000).
    const code = CURRENCY_RATES[appSettings.currency_code] ? appSettings.currency_code : 'INR';
    const locale = code === 'INR' ? 'en-IN' : undefined;
    try {
        return new Intl.NumberFormat(locale, {
            style: 'currency',
            currency: code,
            minimumFractionDigits: 0,
            maximumFractionDigits: 2
        }).format(parseAmount(value));
    } catch (e) {
        return `${CURRENCY_RATES[code].symbol}${parseAmount(value).toLocaleString()}`;
    }
}

function formatPlainDate(value) {
    // Purchase and expiry dates are calendar dates (YYYY-MM-DD) with no time zone, so format them in UTC.
    const raw = String(value || '').trim();
    const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return raw;
    const dt = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
    const format = appSettings.date_format || 'auto';
    const options = { timeZone: 'UTC' };
    if (format === 'iso') return raw;
    if (format === 'dmy') return dt.toLocaleDateString('en-GB', { ...options, day: '2-digit', month: '2-digit', year: 'numeric' });
    if (format === 'mdy') return dt.toLocaleDateString('en-US', { ...options, month: '2-digit', day: '2-digit', year: 'numeric' });
    if (format === 'long') return dt.toLocaleDateString([], { ...options, weekday: 'short', month: 'short', day: '2-digit', year: 'numeric' });
    return dt.toLocaleDateString([], { ...options, month: 'short', day: '2-digit', year: 'numeric' });
}

function warrantyBadge(status) {
    const label = status || 'Unknown';
    const classes = {
        'Active': 'bg-success',
        'Expiring Soon': 'bg-warning text-dark',
        'Expired': 'bg-danger',
    };
    const text = label === 'Expiring Soon' ? 'Expiring soon' : label;
    return `<span class="badge ${classes[label] || 'bg-secondary'}">${escapeHtml(text)}</span>`;
}

function describeDaysRemaining(days) {
    if (typeof days !== 'number') return '';
    if (days < 0) return `${Math.abs(days)} day${days === -1 ? '' : 's'} ago`;
    if (days === 0) return 'today';
    return `${days} day${days === 1 ? '' : 's'} left`;
}

function isTruthySetting(value) {
    return ['1', 'true', 'yes', 'on'].includes(String(value || '').trim().toLowerCase());
}

function resolveThemeMode() {
    const mode = appSettings.theme_mode || 'system';
    if (mode === 'dark' || mode === 'light') return mode;
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        return 'dark';
    }
    return 'light';
}

function showToast(message, tone = 'info') {
    // Consistent, non-blocking feedback in place of browser alert() popups.
    let container = byId('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(container);
    }
    const icons = { success: 'bi-check-circle-fill', danger: 'bi-exclamation-octagon-fill', warning: 'bi-exclamation-triangle-fill', info: 'bi-info-circle-fill' };
    const toastEl = document.createElement('div');
    toastEl.className = `toast app-toast app-toast-${tone} border-0`;
    toastEl.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
    toastEl.setAttribute('aria-live', tone === 'danger' ? 'assertive' : 'polite');
    toastEl.innerHTML = `
        <div class="d-flex align-items-start gap-2 p-3">
            <i class="bi ${icons[tone] || icons.info} app-toast-icon"></i>
            <div class="flex-grow-1">${escapeHtml(message)}</div>
            <button type="button" class="btn-close btn-close-sm" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>`;
    container.appendChild(toastEl);
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
    bootstrap.Toast.getOrCreateInstance(toastEl, { delay: tone === 'danger' ? 7000 : 4000 }).show();
}

function applyUserPreferences() {
    const body = document.body;
    if (!body) return;

    const resolvedTheme = resolveThemeMode();

    body.dataset.theme = resolvedTheme;
    body.dataset.density = appSettings.density || 'comfortable';
    body.dataset.reducedMotion = isTruthySetting(appSettings.reduced_motion) ? '1' : '0';

    // Accent colours live in CSS (body[data-accent]); the server renders the same attribute on page load.
    body.dataset.accent = ACCENT_COLORS[appSettings.accent_color] ? appSettings.accent_color : 'blue';
    document.documentElement.style.colorScheme = resolvedTheme === 'dark' ? 'dark' : 'light';
}

function getCurrencyOption(currencyCode) {
    return CURRENCY_RATES[String(currencyCode || '').toUpperCase()] || CURRENCY_RATES.INR;
}

function generateConversationId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID();
    }
    return `conv_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function generateLocalMessageId(prefix) {
    return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function truncateText(text, maxLength) {
    const value = String(text || '').trim();
    if (!value) return '';
    if (value.length <= maxLength) return value;
    return `${value.slice(0, maxLength - 1).trim()}…`;
}

let activeServiceHistoryItemId = null;

function renderServiceTimeline(entries) {
    const history = Array.isArray(entries) ? entries : [];
    if (!history.length) {
        return stateBlock('No service history yet for this product.');
    }

    return `<div class="service-timeline">${history.map(entry => `
        <div class="service-timeline-item">
            <div class="service-timeline-dot"></div>
            <div class="service-timeline-card">
                <div class="d-flex justify-content-between flex-wrap gap-2">
                    <strong>${escapeHtml(entry.service_type || 'Service')}</strong>
                    <span class="text-muted small">${escapeHtml(formatPlainDate(entry.service_date) || '')}</span>
                </div>
                <div class="small text-muted mt-1">
                    ${entry.vendor ? `Vendor: ${escapeHtml(entry.vendor)}` : ''}
                    ${entry.cost ? `${entry.vendor ? ' · ' : ''}Cost: ${escapeHtml(entry.cost)}` : ''}
                </div>
                ${entry.notes ? `<p class="mb-0 mt-2">${escapeHtml(entry.notes)}</p>` : ''}
                ${entry.next_service_date ? `<div class="small mt-2">Next service: ${escapeHtml(formatPlainDate(entry.next_service_date))}</div>` : ''}
            </div>
        </div>
    `).join('')}</div>`;
}

async function openServiceHistory(itemId, itemName) {
    const modalEl = byId('serviceHistoryModal');
    if (!modalEl) return;

    activeServiceHistoryItemId = Number(itemId);
    setText('serviceHistoryTitle', itemName || 'Item');
    const statusEl = byId('serviceHistoryStatus');
    if (statusEl) statusEl.className = 'small mt-3 mb-0 text-muted';
    setText('serviceHistoryStatus', 'Loading timeline...');
    const listEl = byId('serviceHistoryList');
    if (listEl) {
        listEl.innerHTML = stateBlock('Loading service history...');
    }

    try {
        const res = await fetch(`/api/inventory/${activeServiceHistoryItemId}/service-history`);
        const data = await res.json();
        if (listEl) {
            listEl.innerHTML = renderServiceTimeline(data.entries || []);
        }
        setText('serviceHistoryStatus', data.entries && data.entries.length ? `${data.entries.length} record(s) found.` : 'No service records yet.');
    } catch (e) {
        if (listEl) {
            listEl.innerHTML = stateBlock('Could not load service history.', true);
        }
        setText('serviceHistoryStatus', 'Could not load timeline.');
    }

    const form = byId('serviceHistoryForm');
    if (form) form.reset();
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
}

async function saveServiceHistory(event) {
    if (event) event.preventDefault();
    if (!activeServiceHistoryItemId) return;

    const serviceDate = byId('serviceDate');
    const serviceType = byId('serviceType');
    const vendor = byId('serviceVendor');
    const cost = byId('serviceCost');
    const notes = byId('serviceNotes');
    const nextServiceDate = byId('serviceNextDate');
    const status = byId('serviceHistoryStatus');

    const payload = {
        service_date: serviceDate ? serviceDate.value : '',
        service_type: serviceType ? serviceType.value.trim() : '',
        vendor: vendor ? vendor.value.trim() : '',
        cost: cost ? cost.value.trim() : '',
        notes: notes ? notes.value.trim() : '',
        next_service_date: nextServiceDate ? nextServiceDate.value : ''
    };

    try {
        const res = await fetch(`/api/inventory/${activeServiceHistoryItemId}/service-history`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (!res.ok) {
            if (status) {
                status.className = 'small mt-3 mb-0 text-danger';
                status.innerText = data.error || 'Could not save the service record.';
            }
            return;
        }

        if (status) {
            status.className = 'small mt-3 mb-0 text-success';
            status.innerText = 'Service record saved.';
        }

        const listEl = byId('serviceHistoryList');
        if (listEl) {
            listEl.innerHTML = renderServiceTimeline(data.entries || []);
        }
        if (serviceType) serviceType.value = '';
        if (vendor) vendor.value = '';
        if (cost) cost.value = '';
        if (notes) notes.value = '';
        if (nextServiceDate) nextServiceDate.value = '';
    } catch (e) {
        if (status) {
            status.className = 'small mt-3 mb-0 text-danger';
            status.innerText = 'Could not save the service record.';
        }
    }
}

// ---- Uploads: shrink large photos in the browser so they fit the server's size limit ----
const MAX_IMAGE_EDGE = 2000;

function maxUploadBytes() {
    return Number(document.body.dataset.maxUpload) || 4 * 1024 * 1024;
}

async function prepareUpload(file) {
    // Returns a file small enough to upload, or throws an Error with a message for the person.
    const isImage = /^image\/(jpeg|png|webp)$/.test(file.type);
    if (isImage && (file.size > maxUploadBytes() * 0.8 || file.size > 1.5 * 1024 * 1024)) {
        file = await shrinkImage(file);
    }
    if (file.size > maxUploadBytes()) {
        const limitMb = Math.floor(maxUploadBytes() / (1024 * 1024));
        throw new Error(isImage
            ? `This photo is still larger than ${limitMb} MB after shrinking. Try a closer, simpler photo.`
            : `This file is larger than ${limitMb} MB. Try a smaller PDF, or upload a photo of the receipt instead.`);
    }
    return file;
}

function shrinkImage(file) {
    return new Promise((resolve) => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
            const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(img.width, img.height));
            const canvas = document.createElement('canvas');
            canvas.width = Math.round(img.width * scale);
            canvas.height = Math.round(img.height * scale);
            canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
            URL.revokeObjectURL(url);
            canvas.toBlob(blob => {
                const name = file.name.replace(/\.[^.]+$/, '') + '.jpg';
                resolve(blob ? new File([blob], name, { type: 'image/jpeg' }) : file);
            }, 'image/jpeg', 0.85);
        };
        img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
        img.src = url;
    });
}

async function readJsonResponse(res) {
    // Parses an API reply, turning hosting errors (too large, timeout) into friendly messages.
    try {
        return await res.json();
    } catch (e) {
        if (res.status === 413) return { error: 'That file is too large to upload.' };
        if (res.status === 504) return { error: 'The server took too long to respond. Please try again.' };
        if (res.status === 429) return { error: 'Too many requests. Please wait a little and try again.' };
        return { error: `Something went wrong (error ${res.status}). Please try again.` };
    }
}

// The scanned file and what the AI read from it, kept until the person saves or cancels the review.
let pendingReceipt = null;

function setUploadBusy(busy) {
    const btn = byId('upBtn');
    if (btn) btn.disabled = busy;
    byId('upLoader')?.classList.toggle('d-none', !busy);
    setText('upBtnLabel', busy ? 'Reading the receipt…' : 'Scan receipt');
}

async function uploadReceipt() {
    const fileInput = byId('receiptFile');
    if (!fileInput) return;

    let file = fileInput.files[0];
    if (!file) return showToast('Choose a receipt image or PDF first.', 'warning');
    try {
        file = await prepareUpload(file);
    } catch (e) {
        return showToast(e.message, 'danger');
    }

    byId('uploadResult')?.classList.add('d-none');
    setUploadBusy(true);
    const formData = new FormData();
    formData.append('receipt_image', file);
    try {
        const res = await fetch('/api/receipts/scan', { method: 'POST', body: formData });
        const data = await readJsonResponse(res);
        if (!res.ok) return showToast(data.error || 'Could not read this receipt.', 'danger');
        pendingReceipt = { file, receipt: data.receipt || {}, items: data.items || [] };
        openReceiptReview();
    } catch (e) {
        showToast('Upload failed. Check your connection and try again.', 'danger');
    } finally {
        setUploadBusy(false);
    }
}

function reviewField(index, name, label, value, attrs = '', col = 'col-6 col-md-3') {
    const id = `review-${index}-${name}`;
    return `<div class="${col}">
        <label class="form-label small mb-1" for="${id}">${label}</label>
        <input id="${id}" data-field="${name}" class="form-control form-control-sm" value="${escapeHtml(value ?? '')}" ${attrs}>
    </div>`;
}

function openReceiptReview() {
    const { receipt, items } = pendingReceipt;
    byId('reviewStore').value = receipt.store_name || '';
    byId('reviewDate').value = receipt.date_of_purchase || '';
    byId('reviewInvoice').value = receipt.invoice_number || '';
    setText('reviewTotal', receipt.total_amount ? `Receipt total: ${money(receipt.total_amount)}` : '');
    setText('reviewItemsTitle', items.length === 1 ? 'Item' : `${items.length} items found`);

    byId('reviewItems').innerHTML = items.map((item, index) => `
        <div class="review-item" data-index="${index}">
            <div class="d-flex align-items-start gap-2 mb-2">
                <input class="form-check-input mt-2 flex-shrink-0" type="checkbox" data-field="include" id="review-${index}-include"
                       ${item.duplicate_item_id ? '' : 'checked'} aria-label="Save this item">
                <div class="flex-grow-1">
                    <input data-field="item_name" class="form-control form-control-sm fw-semibold" value="${escapeHtml(item.item_name || '')}"
                           maxlength="200" aria-label="Item name" required>
                    ${item.duplicate_item_id ? '<span class="badge text-bg-warning mt-1">Already in your inventory</span>' : ''}
                </div>
            </div>
            <div class="row g-2 review-item-fields">
                ${reviewField(index, 'brand', 'Brand', item.brand, 'maxlength="100"')}
                ${reviewField(index, 'category', 'Category', item.category, 'maxlength="100"')}
                ${reviewField(index, 'amount', 'Amount', item.amount, 'inputmode="decimal" maxlength="40"')}
                ${reviewField(index, 'warranty_months', 'Warranty (months)', item.warranty_months, 'type="number" min="1" max="360" placeholder="Default"')}
                ${reviewField(index, 'model_number', 'Model', item.model_number, 'maxlength="100"', 'col-6')}
                ${reviewField(index, 'serial_number', 'Serial number', item.serial_number, 'maxlength="100"', 'col-6')}
            </div>
        </div>`).join('');
    updateReviewSaveLabel();
    bootstrap.Modal.getOrCreateInstance(byId('reviewModal')).show();
}

function selectedReviewItems() {
    return [...document.querySelectorAll('#reviewItems .review-item')].filter(row => row.querySelector('[data-field="include"]').checked);
}

function updateReviewSaveLabel() {
    const count = selectedReviewItems().length;
    setText('reviewSaveLabel', count > 1 ? `Save ${count} items` : 'Save');
    const btn = byId('reviewSaveBtn');
    if (btn) btn.disabled = count === 0;
    document.querySelectorAll('#reviewItems .review-item').forEach(row => {
        row.classList.toggle('is-skipped', !row.querySelector('[data-field="include"]').checked);
    });
}

async function saveReviewedReceipt(event) {
    event.preventDefault();
    if (!pendingReceipt) return;
    const rows = selectedReviewItems();
    const items = rows.map(row => {
        const source = pendingReceipt.items[Number(row.dataset.index)] || {};
        const item = { warranty_info: source.warranty_info || '' };
        row.querySelectorAll('input[data-field]:not([data-field="include"])').forEach(input => { item[input.dataset.field] = input.value.trim(); });
        return item;
    });
    if (items.some(item => !item.item_name)) return showToast('Every item you save needs a name.', 'warning');
    const receipt = {
        ...pendingReceipt.receipt,
        store_name: byId('reviewStore').value.trim(),
        date_of_purchase: byId('reviewDate').value,
        invoice_number: byId('reviewInvoice').value.trim(),
    };

    const formData = new FormData();
    formData.append('receipt_image', pendingReceipt.file);
    formData.append('details', JSON.stringify({ receipt, items }));
    const btn = byId('reviewSaveBtn');
    btn.disabled = true;
    byId('reviewSaveLoader').classList.remove('d-none');
    try {
        const res = await fetch('/api/receipts/save', { method: 'POST', body: formData });
        const data = await readJsonResponse(res);
        if (!res.ok) return showToast(data.error || 'Could not save these items.', 'danger');
        bootstrap.Modal.getOrCreateInstance(byId('reviewModal')).hide();
        pendingReceipt = null;
        const fileInput = byId('receiptFile');
        if (fileInput) fileInput.value = '';
        showSelectedReceipt(null);
        setText('uploadResultText', data.message);
        byId('uploadResult')?.classList.remove('d-none');
    } catch (e) {
        showToast('Could not save these items. Check your connection and try again.', 'danger');
    } finally {
        btn.disabled = false;
        byId('reviewSaveLoader').classList.add('d-none');
    }
}

byId('reviewForm')?.addEventListener('submit', saveReviewedReceipt);
byId('reviewItems')?.addEventListener('change', event => {
    if (event.target.matches('[data-field="include"]')) updateReviewSaveLabel();
});

function showSelectedReceipt(file) {
    const label = byId('dropZoneFile');
    if (!label) return;
    label.textContent = file ? file.name : '';
    label.classList.toggle('d-none', !file);
}

function initDropZone() {
    const zone = byId('dropZone');
    const input = byId('receiptFile');
    if (!zone || !input) return;

    const camera = byId('receiptCamera');
    camera?.addEventListener('change', () => {
        const photo = camera.files[0];
        if (!photo) return;
        const transfer = new DataTransfer();
        transfer.items.add(photo);
        input.files = transfer.files;
        showSelectedReceipt(photo);
    });

    input.addEventListener('change', () => showSelectedReceipt(input.files[0] || null));
    zone.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            input.click();
        }
    });
    ['dragenter', 'dragover'].forEach(type => zone.addEventListener(type, (event) => {
        event.preventDefault();
        zone.classList.add('is-dragover');
    }));
    ['dragleave', 'drop'].forEach(type => zone.addEventListener(type, () => zone.classList.remove('is-dragover')));
    zone.addEventListener('drop', (event) => {
        event.preventDefault();
        const file = event.dataTransfer.files[0];
        if (!file) return;
        const transfer = new DataTransfer();
        transfer.items.add(file);
        input.files = transfer.files;
        showSelectedReceipt(file);
    });
}

let inventoryItems = [];

async function loadInventory() {
    const tbody = byId('invTable');
    if (!tbody) return;

    if (!inventoryItems.length) tbody.innerHTML = stateRow(5, 'Loading inventory…');

    try {
        const res = await fetch('/api/inventory');
        const data = await res.json();

        setText('s-tot', money(data.total_spent));
        setText('s-act', data.active_warranties || 0);
        inventoryItems = Array.isArray(data.items) ? data.items : [];
        fillCategoryFilter();
        renderInventory();
    } catch (e) {
        tbody.innerHTML = stateRow(5, 'Could not load inventory.', true);
    }
}

function fillCategoryFilter() {
    const select = byId('invCategory');
    if (!select) return;
    const current = select.value;
    const categories = [...new Set(inventoryItems.map(i => (i.category || '').trim()).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b));
    select.innerHTML = '<option value="">All categories</option>' +
        categories.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
    select.value = categories.includes(current) ? current : '';
}

const INVENTORY_SORTS = {
    newest: (a, b) => purchaseKey(b).localeCompare(purchaseKey(a)),
    oldest: (a, b) => (purchaseKey(a) || '9999').localeCompare(purchaseKey(b) || '9999'),
    // Still-active warranties ending soonest first, then expired ones, then unknown.
    expiry: (a, b) => expiryRank(a) - expiryRank(b),
    amount: (a, b) => (parseAmount(b.total_amount) || 0) - (parseAmount(a.total_amount) || 0),
    name: (a, b) => String(a.item_name || '').localeCompare(String(b.item_name || '')),
};

function purchaseKey(item) {
    return /^\d{4}-\d{2}-\d{2}$/.test(String(item.date_of_purchase || '')) ? item.date_of_purchase : '';
}

function expiryRank(item) {
    const days = item.days_remaining;
    if (typeof days !== 'number') return 1e9;
    return days >= 0 ? days : 1e6 - days;
}

function filteredInventory() {
    const query = (byId('invSearch')?.value || '').trim().toLowerCase();
    const category = byId('invCategory')?.value || '';
    const status = byId('invStatus')?.value || '';
    const sort = INVENTORY_SORTS[byId('invSort')?.value] || INVENTORY_SORTS.newest;
    const searchable = ['item_name', 'brand', 'model_number', 'serial_number', 'category', 'store_name', 'invoice_number'];
    return inventoryItems
        .filter(i => !category || (i.category || '').trim() === category)
        .filter(i => !status || (i.warranty_status || 'Unknown') === status)
        .filter(i => !query || searchable.some(field => String(i[field] || '').toLowerCase().includes(query)))
        .sort(sort);
}

function renderInventory() {
    const tbody = byId('invTable');
    if (!tbody) return;
    if (!inventoryItems.length) {
        setText('invCount', '');
        tbody.innerHTML = stateRow(5, 'No items yet. Upload a receipt to add your first item.');
        return;
    }
    const items = filteredInventory();
    setText('invCount', items.length === inventoryItems.length
        ? `${items.length} item${items.length === 1 ? '' : 's'}`
        : `Showing ${items.length} of ${inventoryItems.length} items`);
    if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted py-4">No items match your search.
            <button type="button" class="btn btn-link btn-sm p-0 align-baseline" data-action="clear-inventory-filters">Clear filters</button></td></tr>`;
        return;
    }
    tbody.innerHTML = items.map(renderInventoryRow).join('');
}

function renderInventoryRow(i) {
    const itemId = Number(i.id);
    const name = escapeHtml(i.item_name || 'Unknown');
    const subline = [i.brand, i.category].filter(Boolean).map(escapeHtml).join(' · ');
    const hasCard = Boolean(Number(i.has_warranty_card || 0));
    const usesDefault = !i.warranty_months;
    const expiry = i.warranty_expires_on
        ? `<small class="text-muted text-nowrap" title="${usesDefault ? 'Using the default warranty period' : `${Number(i.warranty_months)}-month warranty`}">until ${escapeHtml(formatPlainDate(i.warranty_expires_on))}${usesDefault ? ' *' : ''}</small>`
        : '';
    const card = hasCard
        ? `<a href="/files/warranty-card/${itemId}" target="_blank" class="inventory-card-link" title="View warranty card"><i class="bi bi-file-earmark-check"></i> Card</a>`
        : `<button type="button" class="btn btn-link inventory-card-link p-0" data-action="attach-card" data-item-id="${itemId}" title="Attach the warranty card"><i class="bi bi-plus-circle"></i> Card</button>`;
    const cardMenu = hasCard
        ? `<li><a class="dropdown-item" href="/files/warranty-card/${itemId}" target="_blank"><i class="bi bi-file-earmark-check"></i> View warranty card</a></li>
           <li><button type="button" class="dropdown-item" data-action="reread-card" data-item-id="${itemId}"><i class="bi bi-arrow-repeat"></i> Read card again</button></li>
           <li><button type="button" class="dropdown-item" data-action="attach-card" data-item-id="${itemId}"><i class="bi bi-upload"></i> Replace card</button></li>`
        : `<li><button type="button" class="dropdown-item" data-action="attach-card" data-item-id="${itemId}"><i class="bi bi-upload"></i> Attach warranty card</button></li>`;
    return `
    <tr>
        <td class="ps-4">
            <div class="fw-semibold inventory-name" title="${name}">${name}</div>
            ${subline ? `<small class="text-muted">${subline}</small>` : ''}
        </td>
        <td class="d-none d-md-table-cell text-nowrap">
            ${escapeHtml(formatPlainDate(i.date_of_purchase) || 'Unknown')}
            ${i.store_name ? `<small class="text-muted d-block text-truncate inventory-store">${escapeHtml(i.store_name)}</small>` : ''}
        </td>
        <td class="text-end fw-semibold text-nowrap">${money(i.total_amount || 0)}</td>
        <td>
            <div class="d-flex flex-wrap align-items-center column-gap-2">${warrantyBadge(i.warranty_status)} ${expiry}</div>
            ${card}
        </td>
        <td class="text-end pe-3">
            <div class="dropdown">
                <button class="btn btn-sm btn-light inventory-menu-btn" type="button" data-bs-toggle="dropdown" aria-expanded="false"
                        data-bs-popper-config='{"strategy":"fixed"}' aria-label="Actions for ${name}" title="Actions">
                    <i class="bi bi-three-dots"></i>
                </button>
                <ul class="dropdown-menu dropdown-menu-end shadow-sm">
                    <li><button type="button" class="dropdown-item" data-action="edit-item" data-item-id="${itemId}"><i class="bi bi-pencil"></i> Edit details</button></li>
                    <li><button type="button" class="dropdown-item" data-action="service-history" data-item-id="${itemId}" data-item-name="${name}"><i class="bi bi-clock-history"></i> Service history</button></li>
                    <li><hr class="dropdown-divider"></li>
                    <li><a class="dropdown-item" href="/files/receipt/${itemId}" target="_blank"><i class="bi bi-receipt"></i> View receipt</a></li>
                    <li><a class="dropdown-item" href="/files/receipt/${itemId}" download><i class="bi bi-download"></i> Download receipt</a></li>
                    <li><hr class="dropdown-divider"></li>
                    ${cardMenu}
                    ${canManageWorkspace() ? `<li><hr class="dropdown-divider"></li>
                    <li><button type="button" class="dropdown-item text-danger" data-action="delete-item" data-item-id="${itemId}"><i class="bi bi-trash"></i> Delete item</button></li>` : ''}
                </ul>
            </div>
        </td>
    </tr>`;
}

function initInventoryFilters() {
    ['invSearch', 'invCategory', 'invStatus', 'invSort'].forEach(id => {
        byId(id)?.addEventListener(id === 'invSearch' ? 'input' : 'change', renderInventory);
    });
}

function clearInventoryFilters() {
    ['invSearch', 'invCategory', 'invStatus'].forEach(id => { if (byId(id)) byId(id).value = ''; });
    renderInventory();
}

async function rereadWarrantyCard(id) {
    showToast('Reading the warranty card again…', 'info');
    try {
        const res = await fetch(`/api/inventory/${Number(id)}/read-warranty-card`, { method: 'POST' });
        const data = await readJsonResponse(res);
        if (!res.ok) return showToast(data.error || 'Could not read the warranty card.', 'danger');
        showToast(data.message, data.card_read ? 'success' : 'warning');
        await Promise.all([loadInventory(), loadDashboard()]);
    } catch (e) {
        showToast('Could not read the warranty card.', 'danger');
    }
}

async function loadDashboard() {
    const hasDashboard = byId('dash-total') && byId('dash-recent-body') && byId('dash-alerts');
    if (!hasDashboard) return;

    try {
        const res = await fetch('/api/inventory');
        const data = await res.json();
        const items = Array.isArray(data.items) ? data.items.slice() : [];

        setText('dash-total', money(data.total_spent));
        setText('dash-active', data.active_warranties || 0);
        setText('dash-count', items.length);

        // Newest purchases first; items without a valid purchase date go last.
        const sortKey = i => /^\d{4}-\d{2}-\d{2}$/.test(String(i.date_of_purchase || '')) ? i.date_of_purchase : '';
        items.sort((a, b) => sortKey(b).localeCompare(sortKey(a)));

        const recentBody = byId('dash-recent-body');
        if (recentBody) {
            if (!items.length) {
                recentBody.innerHTML = stateRow(4, 'No items yet. Upload a receipt to add your first item.');
            } else {
                recentBody.innerHTML = items.slice(0, 6).map(i => `
                    <tr>
                        <td class="ps-4"><strong>${escapeHtml(i.item_name || 'Unknown')}</strong><br><small class="text-muted">${escapeHtml(i.brand || 'N/A')}</small></td>
                        <td class="text-nowrap">${escapeHtml(formatPlainDate(i.date_of_purchase) || 'Unknown')}</td>
                        <td class="text-nowrap">${money(i.total_amount || 0)}</td>
                        <td class="pe-4">${warrantyBadge(i.warranty_status)}</td>
                    </tr>
                `).join('');
            }
        }

        const alerts = byId('dash-alerts');
        if (alerts) {
            const blocks = [];
            const alertItems = Array.isArray(data.alerts) ? data.alerts : [];
            alertItems.forEach(alert => {
                const expired = alert.status === 'expired';
                const when = alert.expiry_date ? ` on ${escapeHtml(formatPlainDate(alert.expiry_date))}` : '';
                const relative = describeDaysRemaining(alert.days_remaining);
                blocks.push(`
                    <div class="attention-item ${expired ? 'border-danger' : 'border-warning'}">
                        <h6>${escapeHtml(alert.item_name || 'Item')}</h6>
                        <p>${expired ? 'Warranty expired' : 'Warranty expires'}${when}${relative ? ` (${relative})` : ''}.</p>
                    </div>
                `);
            });

            items.filter(i => (i.warranty_status || 'Unknown') === 'Unknown').forEach(i => {
                blocks.push(`
                    <div class="attention-item">
                        <h6>${escapeHtml(i.item_name || 'Item')}</h6>
                        <p>Warranty status unknown because the purchase date is missing.</p>
                    </div>
                `);
            });

            items.filter(i => ['Active', 'Expiring Soon'].includes(i.warranty_status) && !Number(i.has_warranty_card || 0)).forEach(i => {
                blocks.push(`
                    <div class="attention-item">
                        <h6>${escapeHtml(i.item_name || 'Item')}</h6>
                        <p>No warranty card yet. Add one from Inventory so it's handy if you need to make a claim.</p>
                    </div>
                `);
            });

            alerts.innerHTML = blocks.length
                ? `<div class="dashboard-attention-list">${blocks.slice(0, 6).join('')}</div>`
                : stateBlock('Everything looks good. Nothing needs your attention right now.');
        }
    } catch (e) {
        setText('dash-total', money(0));
        setText('dash-active', '0');
        setText('dash-count', '0');

        const recentBody = byId('dash-recent-body');
        if (recentBody) {
            recentBody.innerHTML = stateRow(4, 'Could not load dashboard data.', true);
        }

        const alerts = byId('dash-alerts');
        if (alerts) {
            alerts.innerHTML = stateBlock('Could not load alerts.', true);
        }
    }
}

async function attachWarrantyCard(id) {
    const picker = document.createElement('input');
    picker.type = 'file';
    picker.accept = '.jpg,.jpeg,.png,.webp,.pdf';

    picker.onchange = async () => {
        let file = picker.files && picker.files[0];
        if (!file) return;
        try {
            file = await prepareUpload(file);
        } catch (e) {
            showToast(e.message, 'danger');
            return;
        }

        const formData = new FormData();
        formData.append('warranty_card', file);

        showToast('Uploading and reading the warranty card…', 'info');
        try {
            const res = await fetch(`/api/inventory/${id}/attach-warranty-card`, {
                method: 'POST',
                body: formData
            });
            const data = await readJsonResponse(res);
            if (!res.ok) {
                showToast(data.error || 'Could not attach the warranty card.', 'danger');
                return;
            }
            showToast(data.message || 'Warranty card attached.', data.card_read ? 'success' : 'warning');
            await Promise.all([loadInventory(), loadDashboard()]);
        } catch (e) {
            showToast('Could not upload the warranty card.', 'danger');
        }
    };

    picker.click();
}

async function delItem(id) {
    if (!confirm('Delete this item permanently? This cannot be undone.')) return;
    const res = await fetch(`/api/delete/${id}`, { method: 'DELETE' });
    if (res.ok) {
        showToast('Item deleted.', 'success');
        await Promise.all([loadInventory(), loadDashboard()]);
    } else {
        const data = await res.json().catch(() => ({}));
        showToast(data.error || 'Could not delete this item.', 'danger');
    }
}

function formatTime(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
    const asUtc = /Z$|[+-]\d{2}:\d{2}$/.test(normalized) ? normalized : `${normalized}Z`;
    const dt = new Date(asUtc);
    if (isNaN(dt.getTime())) return '';
    try {
        return dt.toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
            hour12: (appSettings.time_format || '12h') !== '24h',
            timeZone: appSettings.timezone || 'UTC'
        });
    } catch (e) {
        return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
}

function dateKey(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
    const asUtc = /Z$|[+-]\d{2}:\d{2}$/.test(normalized) ? normalized : `${normalized}Z`;
    const dt = new Date(asUtc);
    if (isNaN(dt.getTime())) return '';
    try {
        return dt.toLocaleDateString('sv-SE', {
            timeZone: appSettings.timezone || 'UTC'
        });
    } catch (e) {
        return dt.toISOString().slice(0, 10);
    }
}

function separatorLabel(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
    const asUtc = /Z$|[+-]\d{2}:\d{2}$/.test(normalized) ? normalized : `${normalized}Z`;
    const dt = new Date(asUtc);
    if (isNaN(dt.getTime())) return '';

    const now = new Date();
    const todayKey = dateKey(now.toISOString());
    const targetKey = dateKey(dt.toISOString());

    const today = new Date(`${todayKey}T00:00:00Z`);
    const target = new Date(`${targetKey}T00:00:00Z`);
    const diffDays = Math.round((today - target) / 86400000);

    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (appSettings.date_format === 'iso') {
        return dt.toLocaleDateString('sv-SE', { timeZone: appSettings.timezone || 'UTC' });
    }
    try {
        return dt.toLocaleDateString([], {
            day: '2-digit',
            month: 'short',
            year: 'numeric',
            timeZone: appSettings.timezone || 'UTC'
        });
    } catch (e) {
        return dt.toLocaleDateString([], { day: '2-digit', month: 'short', year: 'numeric' });
    }
}

const CHAT_SUGGESTIONS = [
    { icon: 'bi-wallet2', text: 'How much have I spent this year?' },
    { icon: 'bi-hourglass-split', text: 'Which warranties end in the next 3 months?' },
    { icon: 'bi-file-earmark-x', text: "Which items don't have a warranty card yet?" },
    { icon: 'bi-bag', text: 'What did I buy most recently?' },
];

function renderAssistantAction(action) {
    if (!action || action.type !== 'attach_warranty_card' || !action.item_id) return '';
    return `
        <button type="button" class="btn btn-sm btn-outline-primary ai-action-btn" data-action="attach-card" data-item-id="${Number(action.item_id)}">
            <i class="bi bi-paperclip"></i> Attach the warranty card for ${escapeHtml(action.item_name || 'this item')}
        </button>`;
}

function renderChatMessage(message) {
    const role = message.role === 'user' ? 'user' : 'assistant';
    const when = formatTime(message.created_at);
    const body = renderMessageContent(message.content, role);
    if (role === 'user') {
        return `
        <div class="ai-msg ai-msg-user">
            <div class="ai-bubble">${body}</div>
            ${when ? `<div class="ai-msg-time">${when}</div>` : ''}
        </div>`;
    }
    return `
        <div class="ai-msg ai-msg-assistant${message.error ? ' is-error' : ''}">
            <div class="ai-avatar" aria-hidden="true"><i class="bi ${message.error ? 'bi-exclamation-lg' : 'bi-stars'}"></i></div>
            <div class="ai-msg-body">
                <div class="ai-bubble ai-markdown">${body}</div>
                ${renderAssistantAction(message.action)}
                ${when ? `<div class="ai-msg-time">${when}</div>` : ''}
            </div>
        </div>`;
}

function renderChatWelcome() {
    return `
        <div class="ai-welcome">
            <div class="ai-welcome-icon" aria-hidden="true"><i class="bi bi-stars"></i></div>
            <h2 class="ai-welcome-title">How can I help?</h2>
            <p class="ai-welcome-text">Ask about anything in your inventory: what you've spent, when warranties end, or details from a receipt.</p>
            <div class="ai-suggestions">
                ${CHAT_SUGGESTIONS.map(s => `
                    <button type="button" class="ai-suggestion" data-action="chat-suggestion" data-prompt="${escapeHtml(s.text)}">
                        <i class="bi ${s.icon}" aria-hidden="true"></i><span>${escapeHtml(s.text)}</span>
                    </button>`).join('')}
            </div>
        </div>`;
}

let currentConversationId = generateConversationId();
let activeConversationMessages = [];
let lastConversationList = [];
let activeConversationFilter = '';
let chatBusy = false;

function renderRecentConversationsSidebar(conversations) {
    const container = byId('recentConversations');
    if (!container) return;

    const items = Array.isArray(conversations) ? conversations : [];
    if (!items.length) {
        container.innerHTML = `<div class="ai-conv-empty">${activeConversationFilter ? 'No chats match your search.' : 'Your chats will appear here.'}</div>`;
        updateChatTitle();
        return;
    }

    const groups = {};
    items.forEach(conversation => {
        const stamp = conversation.updated_at || conversation.created_at;
        const key = dateKey(stamp) || 'unknown';
        if (!groups[key]) groups[key] = { label: separatorLabel(stamp) || 'Earlier', items: [] };
        groups[key].items.push(conversation);
    });

    container.innerHTML = Object.keys(groups).sort((a, b) => (a < b ? 1 : -1)).map(key => `
        <div class="ai-conv-group">
            <div class="ai-conv-label">${escapeHtml(groups[key].label)}</div>
            ${groups[key].items.map(item => `
                <button type="button" class="ai-conv ${item.conversation_id === currentConversationId ? 'active' : ''}"
                        data-action="open-conversation" data-conversation-id="${escapeHtml(String(item.conversation_id || ''))}">
                    <span class="ai-conv-title">${escapeHtml(truncateText(item.title || 'New chat', 200))}</span>
                    <span class="ai-conv-time">${formatTime(item.updated_at || item.created_at) || ''}</span>
                </button>`).join('')}
        </div>`).join('');
    updateChatTitle();
}

function updateChatTitle() {
    const title = byId('chatTitle');
    if (!title) return;
    const saved = lastConversationList.find(c => c.conversation_id === currentConversationId);
    const firstQuestion = activeConversationMessages.find(m => m.role === 'user');
    title.textContent = (saved && saved.title) || (firstQuestion && truncateText(firstQuestion.content, 80)) || 'New chat';
}

function renderChatThread(messages, { typing = false } = {}) {
    const box = byId('chatThread');
    if (!box) return;

    if ((!messages || !messages.length) && !typing) {
        box.innerHTML = renderChatWelcome();
        updateChatTitle();
        return;
    }

    let html = '';
    let lastDate = '';
    messages.forEach(m => {
        const key = dateKey(m.created_at);
        if (key && key !== lastDate) {
            html += `<div class="ai-date"><span>${separatorLabel(m.created_at)}</span></div>`;
            lastDate = key;
        }
        html += renderChatMessage(m);
    });
    if (typing) {
        html += `
        <div class="ai-msg ai-msg-assistant">
            <div class="ai-avatar" aria-hidden="true"><i class="bi bi-stars"></i></div>
            <div class="ai-bubble ai-typing" aria-label="The assistant is typing"><span></span><span></span><span></span></div>
        </div>`;
    }
    box.innerHTML = `<div class="ai-thread-inner">${html}</div>`;
    box.scrollTop = box.scrollHeight;
    updateChatTitle();
}

function setCurrentConversation(conversationId) {
    currentConversationId = conversationId || generateConversationId();
}

function toggleChatSidebar(open) {
    const shell = byId('aiShell');
    if (!shell) return;
    shell.classList.toggle('sidebar-open', typeof open === 'boolean' ? open : !shell.classList.contains('sidebar-open'));
}

function startNewConversation() {
    setCurrentConversation(generateConversationId());
    activeConversationMessages = [];
    renderChatThread(activeConversationMessages);
    renderRecentConversationsSidebar(lastConversationList);
    toggleChatSidebar(false);
    byId('chatInput')?.focus();
}

async function loadConversationList(query = '') {
    activeConversationFilter = query || '';
    try {
        const url = query
            ? `/api/chat/conversations?limit=100&query=${encodeURIComponent(query)}`
            : '/api/chat/conversations?limit=100';
        const res = await fetch(url);
        const data = await res.json();
        lastConversationList = Array.isArray(data.conversations) ? data.conversations : [];
    } catch (e) {
        lastConversationList = [];
    }
    renderRecentConversationsSidebar(lastConversationList);
}

async function openConversation(conversationId) {
    if (!conversationId) return;

    setCurrentConversation(conversationId);
    toggleChatSidebar(false);
    renderRecentConversationsSidebar(lastConversationList);
    const box = byId('chatThread');
    if (box) box.innerHTML = '<div class="ai-thread-status">Loading conversation…</div>';

    try {
        const res = await fetch(`/api/chat/conversations/${encodeURIComponent(conversationId)}/messages?limit=200`);
        const data = await res.json();
        activeConversationMessages = (data.messages || []).map(m => ({
            id: m.id || generateLocalMessageId('db'),
            role: m.role,
            content: m.content || '',
            created_at: m.created_at || new Date().toISOString()
        }));
        renderChatThread(activeConversationMessages);
    } catch (e) {
        if (box) box.innerHTML = '<div class="ai-thread-status text-danger">Could not load this conversation.</div>';
    }
}

async function clearChatHistory() {
    if (!confirm('Delete all your saved chats? This cannot be undone.')) return;

    try {
        const res = await fetch('/api/chat/history', { method: 'DELETE' });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            showToast(data.error || 'Could not clear the chat history.', 'danger');
            return;
        }
        lastConversationList = [];
        startNewConversation();
        await loadConversationList('');
    } catch (e) {
        showToast('Could not clear the chat history.', 'danger');
    }
}

function resizeChatInput() {
    const input = byId('chatInput');
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
    input.style.overflowY = input.scrollHeight > 180 ? 'auto' : 'hidden';
    const send = byId('chatSendBtn');
    if (send) send.disabled = chatBusy || !input.value.trim();
}

async function sendMessage(text) {
    const input = byId('chatInput');
    if (!input || chatBusy) return;

    const msg = (typeof text === 'string' ? text : input.value).trim();
    if (!msg) return;

    activeConversationMessages.push({ id: generateLocalMessageId('user'), role: 'user', content: msg, created_at: new Date().toISOString() });
    input.value = '';
    chatBusy = true;
    resizeChatInput();
    renderChatThread(activeConversationMessages, { typing: true });

    let reply;
    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg, conversation_id: currentConversationId })
        });
        const data = await readJsonResponse(res);
        reply = res.ok && data.reply
            ? { content: data.reply, action: data.action || null, error: Boolean(data.error) }
            : { content: data.error || data.reply || 'Something went wrong. Please try again.', error: true };
    } catch (e) {
        reply = { content: 'Could not reach the server. Check your connection and try again.', error: true };
    }

    activeConversationMessages.push({ id: generateLocalMessageId('assistant'), role: 'assistant', created_at: new Date().toISOString(), ...reply });
    chatBusy = false;
    renderChatThread(activeConversationMessages);
    resizeChatInput();
    input.focus();
    await loadConversationList(activeConversationFilter);
}

function initAssistant() {
    const input = byId('chatInput');
    if (!input) return;
    byId('chatComposer')?.addEventListener('submit', event => {
        event.preventDefault();
        sendMessage();
    });
    input.addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            sendMessage();
        }
    });
    input.addEventListener('input', resizeChatInput);
    // On phones the chat list slides over the chat; tapping the chat closes it.
    document.querySelector('.ai-main')?.addEventListener('click', event => {
        if (byId('aiShell')?.classList.contains('sidebar-open') && !event.target.closest('.ai-icon-btn')) toggleChatSidebar(false);
    });

    let searchTimer;
    byId('chatSearchInput')?.addEventListener('input', event => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => loadConversationList(event.target.value.trim()), 250);
    });
}

async function startAssistantSession() {
    initAssistant();
    startNewConversation();
    await loadConversationList('');
}

async function loadSettings() {
    try {
        const res = await fetch('/api/settings');
        if (!res.ok) return;
        const data = await res.json();
        const settings = data.settings || {};
        appSettings.timezone = settings.timezone || appSettings.timezone;
        const resolvedCurrencyCode = (settings.currency_code || inferCurrencyCodeFromSymbol(settings.currency_symbol) || appSettings.currency_code || 'INR').toUpperCase();
        appSettings.currency_code = resolvedCurrencyCode;
        appSettings.currency_symbol = settings.currency_symbol || getCurrencyOption(appSettings.currency_code).symbol;
        appSettings.warranty_period_days = settings.warranty_period_days || appSettings.warranty_period_days;
        appSettings.expiry_alert_days = settings.expiry_alert_days || appSettings.expiry_alert_days;
        appSettings.reminders_enabled = settings.reminders_enabled || appSettings.reminders_enabled;
        appSettings.reminder_days_before = settings.reminder_days_before || appSettings.reminder_days_before;
        appSettings.reminder_recipients = settings.reminder_recipients || appSettings.reminder_recipients;
        appSettings.theme_mode = settings.theme_mode || appSettings.theme_mode;
        appSettings.accent_color = settings.accent_color || appSettings.accent_color;
        appSettings.date_format = settings.date_format || appSettings.date_format;
        appSettings.time_format = settings.time_format || appSettings.time_format;
        appSettings.reduced_motion = settings.reduced_motion || appSettings.reduced_motion;
        appSettings.density = settings.density || appSettings.density;
        applyUserPreferences();
    } catch (e) {
        // keep defaults
    }
}

async function loadSettingsIntoForm() {
    if (!byId('settingsForm')) return;

    await loadSettings();
    const timezoneInput = byId('settingsTimezone');
    const currencyInput = byId('settingsCurrency');
    const warrantyInput = byId('settingsWarrantyDays');
    const expiryAlertInput = byId('settingsExpiryAlertDays');
    const remindersEnabledInput = byId('settingsRemindersEnabled');
    const reminderDaysInput = byId('settingsReminderDaysBefore');
    const reminderRecipientsInput = byId('settingsReminderRecipients');
    const themeInput = byId('settingsThemeMode');
    const accentInput = byId('settingsAccentColor');
    const dateFormatInput = byId('settingsDateFormat');
    const timeFormatInput = byId('settingsTimeFormat');
    const motionInput = byId('settingsReducedMotion');
    const densityInput = byId('settingsDensity');

    if (timezoneInput) timezoneInput.value = appSettings.timezone || 'UTC';
    if (currencyInput) currencyInput.value = appSettings.currency_code || 'INR';
    if (warrantyInput) warrantyInput.value = appSettings.warranty_period_days || '365';
    if (expiryAlertInput) expiryAlertInput.value = appSettings.expiry_alert_days || '30';
    if (remindersEnabledInput) remindersEnabledInput.checked = isTruthySetting(appSettings.reminders_enabled);
    if (reminderDaysInput) reminderDaysInput.value = appSettings.reminder_days_before || '30';
    if (reminderRecipientsInput) reminderRecipientsInput.value = appSettings.reminder_recipients || '';
    if (themeInput) themeInput.value = appSettings.theme_mode || 'system';
    if (accentInput) accentInput.value = appSettings.accent_color || 'blue';
    if (dateFormatInput) dateFormatInput.value = appSettings.date_format || 'auto';
    if (timeFormatInput) timeFormatInput.value = appSettings.time_format || '12h';
    if (motionInput) motionInput.checked = isTruthySetting(appSettings.reduced_motion);
    if (densityInput) densityInput.value = appSettings.density || 'comfortable';
    // The settings page tracks unsaved changes from this point.
    document.dispatchEvent(new CustomEvent('settings:loaded'));
}

async function saveSettings(event) {
    if (event) event.preventDefault();

    const timezoneInput = byId('settingsTimezone');
    const currencyInput = byId('settingsCurrency');
    const warrantyInput = byId('settingsWarrantyDays');
    const expiryAlertInput = byId('settingsExpiryAlertDays');
    const remindersEnabledInput = byId('settingsRemindersEnabled');
    const reminderDaysInput = byId('settingsReminderDaysBefore');
    const reminderRecipientsInput = byId('settingsReminderRecipients');
    const themeInput = byId('settingsThemeMode');
    const accentInput = byId('settingsAccentColor');
    const dateFormatInput = byId('settingsDateFormat');
    const timeFormatInput = byId('settingsTimeFormat');
    const motionInput = byId('settingsReducedMotion');
    const densityInput = byId('settingsDensity');

    const payload = {
        timezone: timezoneInput ? timezoneInput.value.trim() : 'UTC',
        currency_code: currencyInput ? currencyInput.value.trim() : 'INR',
        currency_symbol: getCurrencyOption(currencyInput ? currencyInput.value.trim() : 'INR').symbol,
        warranty_period_days: warrantyInput ? Number(warrantyInput.value || 365) : 365,
        expiry_alert_days: expiryAlertInput ? Number(expiryAlertInput.value || 30) : 30,
        reminders_enabled: remindersEnabledInput && remindersEnabledInput.checked ? '1' : '0',
        reminder_days_before: reminderDaysInput ? Number(reminderDaysInput.value || 30) : 30,
        reminder_recipients: reminderRecipientsInput ? reminderRecipientsInput.value.trim() : '',
        theme_mode: themeInput ? themeInput.value : 'system',
        accent_color: accentInput ? accentInput.value : 'blue',
        date_format: dateFormatInput ? dateFormatInput.value : 'auto',
        time_format: timeFormatInput ? timeFormatInput.value : '12h',
        reduced_motion: motionInput && motionInput.checked ? '1' : '0',
        density: densityInput ? densityInput.value : 'comfortable'
    };

    try {
        const res = await fetch('/api/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (!res.ok) {
            showToast(data.error || 'Could not save the settings.', 'danger');
            return;
        }

        // Take the saved values as-is, so a field that was cleared stays cleared.
        Object.assign(appSettings, data.settings || payload);
        applyUserPreferences();
        document.dispatchEvent(new CustomEvent('settings:saved'));
        showToast('Settings saved.', 'success');

        await Promise.all([loadDashboard(), loadInventory()]);
        if (byId('chatThread')) {
            renderChatThread(activeConversationMessages);
            renderRecentConversationsSidebar(lastConversationList);
        }
    } catch (e) {
        showToast('Could not save the settings.', 'danger');
    }
}

async function sendReminderEmailsNow() {
    showToast('Sending reminder emails…', 'info');
    try {
        const res = await fetch('/api/reminders/send', { method: 'POST' });
        const data = await readJsonResponse(res);
        if (!res.ok) {
            showToast(data.error || 'Could not send the reminder emails.', 'danger');
            return;
        }
        showToast(data.message || 'Reminder emails sent.', data.sent ? 'success' : 'info');
    } catch (e) {
        showToast('Could not send the reminder emails.', 'danger');
    }
}

// One delegated handler for buttons rendered from data, so names and ids never end up inside inline JS.
document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-action]');
    if (!target) return;

    const itemId = Number(target.dataset.itemId);
    switch (target.dataset.action) {
        case 'attach-card':
            attachWarrantyCard(itemId);
            break;
        case 'service-history':
            openServiceHistory(itemId, target.dataset.itemName);
            break;
        case 'delete-item':
            delItem(itemId);
            break;
        case 'edit-item':
            openEditItem(itemId);
            break;
        case 'reread-card':
            rereadWarrantyCard(itemId);
            break;
        case 'clear-inventory-filters':
            clearInventoryFilters();
            break;
        case 'open-conversation':
            openConversation(target.dataset.conversationId);
            break;
        case 'chat-suggestion':
            sendMessage(target.dataset.prompt);
            break;
        case 'switch-workspace':
            switchWorkspace(Number(target.dataset.workspaceId));
            break;
        case 'create-workspace':
            openCreateWorkspace();
            break;
    }
});

async function switchWorkspace(workspaceId) {
    try {
        const res = await fetch('/api/workspaces/switch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workspace_id: workspaceId })
        });
        const data = await res.json();
        if (!res.ok) {
            showToast(data.error || 'Could not switch workspace.', 'danger');
            return;
        }
        // Everything on the page belongs to the old workspace, so start fresh on the dashboard.
        window.location.href = '/';
    } catch (e) {
        showToast('Could not switch workspace.', 'danger');
    }
}

function openCreateWorkspace() {
    const modalEl = byId('createWorkspaceModal');
    if (!modalEl) return;
    const input = byId('newWorkspaceName');
    if (input) input.value = '';
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
    modalEl.addEventListener('shown.bs.modal', () => input && input.focus(), { once: true });
}

async function createWorkspace(name) {
    const res = await fetch('/api/workspaces', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Could not create the workspace.');
    return data.workspace;
}

document.addEventListener('submit', async (event) => {
    if (event.target.id !== 'createWorkspaceForm') return;
    event.preventDefault();
    const input = byId('newWorkspaceName');
    try {
        await createWorkspace(input ? input.value.trim() : '');
        window.location.href = '/';
    } catch (e) {
        showToast(e.message, 'danger');
    }
});

window.onload = async () => {
    initDropZone();
    initInventoryFilters();
    await loadSettings();
    await Promise.all([loadDashboard(), loadInventory()]);
    await loadSettingsIntoForm();
    if (byId('chatThread')) {
        await startAssistantSession();
    }
};

// ---- Edit item ----
let editingItemId = null;

async function openEditItem(itemId) {
    const modalEl = byId('editItemModal');
    if (!modalEl) return;
    try {
        const res = await fetch(`/api/inventory/${Number(itemId)}`);
        const data = await readJsonResponse(res);
        if (!res.ok) throw new Error(data.error || 'Could not load this item.');
        editingItemId = Number(itemId);
        const form = byId('editItemForm');
        [...form.elements].forEach(field => {
            if (field.name) field.value = data.item[field.name] ?? '';
        });
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
    } catch (e) {
        showToast(e.message, 'danger');
    }
}

document.addEventListener('submit', async (event) => {
    if (event.target.id !== 'editItemForm') return;
    event.preventDefault();
    const payload = {};
    [...event.target.elements].forEach(field => { if (field.name) payload[field.name] = field.value.trim(); });
    try {
        const res = await fetch(`/api/inventory/${editingItemId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await readJsonResponse(res);
        if (!res.ok) throw new Error(data.error || 'Could not save the changes.');
        bootstrap.Modal.getOrCreateInstance(byId('editItemModal')).hide();
        showToast('Item updated.', 'success');
        await Promise.all([loadInventory(), loadDashboard()]);
    } catch (e) {
        showToast(e.message, 'danger');
    }
});
