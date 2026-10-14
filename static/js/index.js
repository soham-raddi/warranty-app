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
    reminder_subject: 'Warranty expiry reminder',
    reminder_body: 'Your warranty for {{item_name}} expires on {{expiry_date}}. Days left: {{days_remaining}}.',
    smtp_host: '',
    smtp_port: '587',
    smtp_username: '',
    smtp_sender_email: '',
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
    return `<span class="badge ${classes[label] || 'bg-secondary'}">${escapeHtml(label)}</span>`;
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

function formatDateValue(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
    const asUtc = /Z$|[+-]\d{2}:\d{2}$/.test(normalized) ? normalized : `${normalized}Z`;
    const dt = new Date(asUtc);
    if (isNaN(dt.getTime())) return raw;

    try {
        const timeZone = appSettings.timezone || 'UTC';
        const format = appSettings.date_format || 'auto';
        if (format === 'iso') {
            return dt.toLocaleDateString('sv-SE', { timeZone });
        }
        if (format === 'dmy') {
            return dt.toLocaleDateString([], { day: '2-digit', month: '2-digit', year: 'numeric', timeZone });
        }
        if (format === 'mdy') {
            return dt.toLocaleDateString([], { month: '2-digit', day: '2-digit', year: 'numeric', timeZone });
        }
        if (format === 'long') {
            return dt.toLocaleDateString([], { weekday: 'short', month: 'short', day: '2-digit', year: 'numeric', timeZone });
        }
        return dt.toLocaleDateString([], { month: 'short', day: '2-digit', year: 'numeric', timeZone });
    } catch (e) {
        return dt.toISOString().slice(0, 10);
    }
}

function formatDateTimeValue(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
    const asUtc = /Z$|[+-]\d{2}:\d{2}$/.test(normalized) ? normalized : `${normalized}Z`;
    const dt = new Date(asUtc);
    if (isNaN(dt.getTime())) return raw;

    try {
        return dt.toLocaleDateString([], {
            year: 'numeric',
            month: 'short',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hour12: (appSettings.time_format || '12h') !== '24h',
            timeZone: appSettings.timezone || 'UTC'
        });
    } catch (e) {
        return raw;
    }
}

function getCurrencyOption(currencyCode) {
    return CURRENCY_RATES[String(currencyCode || '').toUpperCase()] || CURRENCY_RATES.INR;
}

function parseReminderRecipients(value) {
    return String(value || '')
        .replace(/;/g, ',')
        .split(',')
        .map(entry => entry.trim())
        .filter(Boolean);
}

function formatTimeValue(value) {
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
                status.innerText = data.error || 'Failed to save service history.';
            }
            return;
        }

        if (status) {
            status.className = 'small mt-3 mb-0 text-success';
            status.innerText = 'Service history saved.';
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
            status.innerText = 'Failed to save service history.';
        }
    }
}

async function uploadReceipt() {
    const fileInput = byId('receiptFile');
    if (!fileInput) return;

    const file = fileInput.files[0];
    if (!file) return showToast('Choose a receipt image or PDF first.', 'warning');

    const btn = byId('upBtn');
    const loader = byId('upLoader');
    if (btn) btn.disabled = true;
    if (loader) loader.classList.remove('d-none');
    setText('upBtnLabel', 'Scanning...');

    const formData = new FormData();
    formData.append('receipt_image', file);

    try {
        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();

        if (res.ok) {
            setText('modalItemName', data.item_name || 'N/A');
            setText('modalCategory', data.category || 'N/A');
            setText('modalStore', data.store_name || 'N/A');
            setText('modalDate', formatPlainDate(data.date_of_purchase) || 'Not found');
            setText('modalInvoice', data.invoice_number || 'N/A');
            setText('modalWarranty', data.warranty_info || 'Not specified on receipt');
            setText('modalPrice', data.total_amount ? money(data.total_amount) : 'Not found');

            const modalEl = byId('summaryModal');
            if (modalEl) {
                bootstrap.Modal.getOrCreateInstance(modalEl).show();
            }

            fileInput.value = '';
            showSelectedReceipt(null);
        } else {
            showToast(data.error || 'Upload failed.', 'danger');
        }
    } catch (e) {
        showToast('Upload failed. Make sure the server is running.', 'danger');
    } finally {
        if (btn) btn.disabled = false;
        if (loader) loader.classList.add('d-none');
        setText('upBtnLabel', 'Scan and Save');
    }
}

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

async function loadInventory() {
    const tbody = byId('invTable');
    if (!tbody) return;

    tbody.innerHTML = stateRow(6, 'Loading inventory...');

    try {
        const res = await fetch('/api/inventory');
        const data = await res.json();

        setText('s-tot', money(data.total_spent));
        setText('s-act', data.active_warranties || 0);

        if (!data.items || data.items.length === 0) {
            tbody.innerHTML = stateRow(6, 'No items yet. Upload a receipt to add your first item.');
            return;
        }

        tbody.innerHTML = data.items.map(i => {
            const itemId = Number(i.id);
            const expiry = i.warranty_expires_on
                ? `<small class="text-muted d-block">Warranty until ${escapeHtml(formatPlainDate(i.warranty_expires_on))}</small>`
                : '';
            return `
            <tr>
                <td class="ps-4">
                    <a href="/files/receipt/${itemId}" target="_blank" class="item-link" title="Click to view receipt">
                        <strong>${escapeHtml(i.item_name || 'Unknown')}</strong><br>
                        <small class="text-muted">${escapeHtml(i.brand || 'N/A')}</small>
                    </a>
                </td>
                <td><span class="badge bg-light text-dark border">${escapeHtml(i.category || 'N/A')}</span></td>
                <td class="text-nowrap">${escapeHtml(formatPlainDate(i.date_of_purchase) || 'Unknown')}${expiry}</td>
                <td class="fw-bold text-nowrap">${money(i.total_amount || 0)}</td>
                <td>
                    <div class="d-flex flex-wrap gap-1 align-items-center">
                        ${warrantyBadge(i.warranty_status)}
                        ${Number(i.has_warranty_card || 0)
                            ? `<a href="/files/warranty-card/${itemId}" target="_blank" class="badge text-bg-info text-decoration-none" title="View attached warranty card">Card Attached</a>`
                            : `<button type="button" data-action="attach-card" data-item-id="${itemId}" class="btn btn-sm btn-outline-warning py-0 px-2" title="Attach warranty card">Attach Card</button>`}
                    </div>
                </td>
                <td class="text-end pe-4">
                    <div class="btn-group">
                        <button type="button" class="btn btn-sm btn-outline-info" data-action="service-history" data-item-id="${itemId}" data-item-name="${escapeHtml(i.item_name || 'Item')}" title="View service history"><i class="bi bi-clock-history"></i></button>
                        <a href="/files/receipt/${itemId}" download class="btn btn-sm btn-outline-secondary" title="Download receipt"><i class="bi bi-download"></i></a>
                        ${canManageWorkspace() ? `<button type="button" data-action="delete-item" data-item-id="${itemId}" class="btn btn-sm btn-outline-danger" title="Delete Record"><i class="bi bi-trash"></i></button>` : ''}
                    </div>
                </td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = stateRow(6, 'Could not load inventory.', true);
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
                        <p>Warranty card missing. Add it from Inventory for faster claim support.</p>
                    </div>
                `);
            });

            alerts.innerHTML = blocks.length
                ? `<div class="dashboard-attention-list">${blocks.slice(0, 6).join('')}</div>`
                : stateBlock('All items look healthy. No immediate follow-ups.');
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
        const file = picker.files && picker.files[0];
        if (!file) return;

        const formData = new FormData();
        formData.append('warranty_card', file);

        try {
            const res = await fetch(`/api/inventory/${id}/attach-warranty-card`, {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            if (!res.ok) {
                showToast(data.error || 'Failed to attach warranty card.', 'danger');
                return;
            }
            showToast('Warranty card attached.', 'success');
            await Promise.all([loadInventory(), loadDashboard()]);
        } catch (e) {
            showToast('Failed to upload warranty card.', 'danger');
        }
    };

    picker.click();
}

async function delItem(id) {
    if (!confirm('Delete this record permanently?')) return;
    const res = await fetch(`/api/delete/${id}`, { method: 'DELETE' });
    if (res.ok) {
        showToast('Item deleted.', 'success');
        await Promise.all([loadInventory(), loadDashboard()]);
    } else {
        const data = await res.json().catch(() => ({}));
        showToast(data.error || 'Failed to delete this record.', 'danger');
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

function renderAssistantAction(action) {
    if (!action || action.type !== 'attach_warranty_card' || !action.item_id) return '';
    const label = escapeHtml(action.item_name || 'item');
    return `
        <div class="mt-2">
            <button onclick="attachWarrantyCard(${Number(action.item_id)})" class="btn btn-sm btn-warning">
                <i class="bi bi-paperclip"></i> Attach Warranty Card for ${label}
            </button>
        </div>
    `;
}

function renderChatMessage(message) {
    const role = message.role === 'user' ? 'user' : 'assistant';
    const safeContent = renderMessageContent(message.content, role);
    const bubbleClass = role === 'user' ? 'msg-user' : 'msg-ai';
    const when = formatTime(message.created_at);
    const actionHtml = role === 'assistant' ? renderAssistantAction(message.action) : '';
    const rawId = String(message.id || generateLocalMessageId('msg'));
    const domId = `msg_${rawId.replace(/[^a-zA-Z0-9_-]/g, '_')}`;

    return `
        <div class="msg-wrap ${role}" id="${domId}" data-message-id="${rawId}">
            <div class="${bubbleClass}">
                <div>${safeContent}</div>
                ${actionHtml}
                ${when ? `<div class="msg-time">${when}</div>` : ''}
            </div>
        </div>
    `;
}

let currentConversationId = generateConversationId();
let activeConversationMessages = [];
let lastConversationList = [];
let activeConversationFilter = '';

function renderRecentConversationsSidebar(conversations) {
    const container = byId('recentConversations');
    if (!container) return;

    const items = Array.isArray(conversations) ? conversations : [];
    if (!items.length) {
        container.innerHTML = stateBlock('No conversations yet.');
        return;
    }

    const groups = {};
    items.forEach(conversation => {
        const key = dateKey(conversation.updated_at || conversation.created_at) || 'unknown';
        if (!groups[key]) {
            groups[key] = {
                label: separatorLabel(conversation.updated_at || conversation.created_at) || 'Unknown date',
                items: []
            };
        }
        groups[key].items.push(conversation);
    });

    const sortedKeys = Object.keys(groups).sort((a, b) => (a < b ? 1 : -1));
    let html = '';

    sortedKeys.forEach(key => {
        html += `<div class="conv-date-group"><div class="conv-date-label">${escapeHtml(groups[key].label)}</div>`;
        groups[key].items.forEach(item => {
            const convId = escapeHtml(String(item.conversation_id || ''));
            // Long text is cut with a CSS ellipsis, so keep a generous limit here.
            const title = truncateText(item.title || 'New conversation', 200);
            const preview = truncateText(item.preview || '', 200);
            const isActive = item.conversation_id === currentConversationId ? 'active' : '';
            html += `
                <button type="button" class="conv-item w-100 text-start ${isActive}" data-action="open-conversation" data-conversation-id="${convId}">
                    <div class="conv-preview"><strong class="conv-line">${escapeHtml(title)}</strong>${preview ? `<span class="conv-line">${escapeHtml(preview)}</span>` : ''}</div>
                    <div class="conv-time">${formatTime(item.updated_at || item.created_at) || ''}</div>
                </button>
            `;
        });
        html += '</div>';
    });

    container.innerHTML = html;
}

function renderChatThread(messages) {
    const box = byId('chat-box');
    if (!box) return;

    if (!messages || messages.length === 0) {
        box.innerHTML = '<div class="msg-wrap assistant"><div class="msg-ai">New conversation started. Ask me anything about your inventory.</div></div>';
        box.scrollTop = box.scrollHeight;
        return;
    }

    let html = '';
    let lastDate = '';

    messages.forEach(m => {
        const key = dateKey(m.created_at);
        if (key && key !== lastDate) {
            html += `<div class="date-sep"><span>${separatorLabel(m.created_at)}</span></div>`;
            lastDate = key;
        }
        html += renderChatMessage(m);
    });

    box.innerHTML = html;
    box.scrollTop = box.scrollHeight;
}

function setCurrentConversation(conversationId) {
    currentConversationId = conversationId || generateConversationId();
}

function startNewConversation() {
    setCurrentConversation(generateConversationId());
    activeConversationMessages = [];
    renderChatThread(activeConversationMessages);
    renderRecentConversationsSidebar(lastConversationList);
}

async function loadConversationList(query = '') {
    activeConversationFilter = query || '';

    const container = byId('recentConversations');
    if (container) {
        container.innerHTML = stateBlock('Loading conversations...');
    }

    try {
        const url = query
            ? `/api/chat/conversations?limit=100&query=${encodeURIComponent(query)}`
            : '/api/chat/conversations?limit=100';

        const res = await fetch(url);
        const data = await res.json();
        lastConversationList = Array.isArray(data.conversations) ? data.conversations : [];
        renderRecentConversationsSidebar(lastConversationList);
    } catch (e) {
        lastConversationList = [];
        renderRecentConversationsSidebar([]);
    }
}

async function openConversation(conversationId) {
    if (!conversationId) return;

    setCurrentConversation(conversationId);
    const box = byId('chat-box');
    if (box) {
        box.innerHTML = '<div class="msg-wrap assistant"><div class="msg-ai">Loading conversation...</div></div>';
    }

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
        renderRecentConversationsSidebar(lastConversationList);
    } catch (e) {
        if (box) {
            box.innerHTML = '<div class="msg-wrap assistant"><div class="msg-ai text-danger">Could not load conversation.</div></div>';
        }
    }
}

async function searchChatHistory() {
    const input = byId('chatSearchInput');
    if (!input) return;

    const query = (input.value || '').trim();
    await loadConversationList(query);
}

async function resetChatSearch() {
    const input = byId('chatSearchInput');
    if (input) input.value = '';
    await loadConversationList('');
}

async function clearChatHistory() {
    if (!confirm('Clear all saved chat history? This cannot be undone.')) return;

    try {
        const res = await fetch('/api/chat/history', { method: 'DELETE' });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            showToast(data.error || 'Failed to clear chat history.', 'danger');
            return;
        }

        lastConversationList = [];
        startNewConversation();
        await loadConversationList('');
    } catch (e) {
        showToast('Failed to clear chat history.', 'danger');
    }
}

async function sendMessage() {
    const input = byId('chatInput');
    if (!input) return;

    const msg = input.value.trim();
    if (!msg) return;

    const nowIso = new Date().toISOString();
    const userMessageObj = {
        id: generateLocalMessageId('user'),
        role: 'user',
        content: msg,
        created_at: nowIso
    };

    activeConversationMessages.push(userMessageObj);
    renderChatThread(activeConversationMessages);
    input.value = '';

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: msg,
                conversation_id: currentConversationId
            })
        });
        const data = await res.json();

        const assistantMessageObj = {
            id: generateLocalMessageId('assistant'),
            role: 'assistant',
            content: data.reply || '',
            created_at: new Date().toISOString(),
            action: data.action || null
        };

        activeConversationMessages.push(assistantMessageObj);
        renderChatThread(activeConversationMessages);
        await loadConversationList(activeConversationFilter);
    } catch (e) {
        activeConversationMessages.push({
            id: generateLocalMessageId('assistant'),
            role: 'assistant',
            content: 'Connection error.',
            created_at: new Date().toISOString()
        });
        renderChatThread(activeConversationMessages);
    }
}

async function startAssistantSession() {
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
        appSettings.reminder_subject = settings.reminder_subject || appSettings.reminder_subject;
        appSettings.reminder_body = settings.reminder_body || appSettings.reminder_body;
        appSettings.smtp_host = settings.smtp_host || appSettings.smtp_host;
        appSettings.smtp_port = settings.smtp_port || appSettings.smtp_port;
        appSettings.smtp_username = settings.smtp_username || appSettings.smtp_username;
        appSettings.smtp_sender_email = settings.smtp_sender_email || appSettings.smtp_sender_email;
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
    const reminderSubjectInput = byId('settingsReminderSubject');
    const reminderBodyInput = byId('settingsReminderBody');
    const smtpHostInput = byId('settingsSmtpHost');
    const smtpPortInput = byId('settingsSmtpPort');
    const smtpUsernameInput = byId('settingsSmtpUsername');
    const smtpSenderInput = byId('settingsSmtpSenderEmail');
    const themeInput = byId('settingsThemeMode');
    const accentInput = byId('settingsAccentColor');
    const dateFormatInput = byId('settingsDateFormat');
    const timeFormatInput = byId('settingsTimeFormat');
    const motionInput = byId('settingsReducedMotion');
    const densityInput = byId('settingsDensity');
    const status = byId('settingsStatus');

    if (timezoneInput) timezoneInput.value = appSettings.timezone || 'UTC';
    if (currencyInput) currencyInput.value = appSettings.currency_code || 'INR';
    if (warrantyInput) warrantyInput.value = appSettings.warranty_period_days || '365';
    if (expiryAlertInput) expiryAlertInput.value = appSettings.expiry_alert_days || '30';
    if (remindersEnabledInput) remindersEnabledInput.checked = isTruthySetting(appSettings.reminders_enabled);
    if (reminderDaysInput) reminderDaysInput.value = appSettings.reminder_days_before || '30';
    if (reminderRecipientsInput) reminderRecipientsInput.value = appSettings.reminder_recipients || '';
    if (reminderSubjectInput) reminderSubjectInput.value = appSettings.reminder_subject || 'Warranty expiry reminder';
    if (reminderBodyInput) reminderBodyInput.value = appSettings.reminder_body || '';
    if (smtpHostInput) smtpHostInput.value = appSettings.smtp_host || '';
    if (smtpPortInput) smtpPortInput.value = appSettings.smtp_port || '587';
    if (smtpUsernameInput) smtpUsernameInput.value = appSettings.smtp_username || '';
    if (smtpSenderInput) smtpSenderInput.value = appSettings.smtp_sender_email || '';
    if (themeInput) themeInput.value = appSettings.theme_mode || 'system';
    if (accentInput) accentInput.value = appSettings.accent_color || 'blue';
    if (dateFormatInput) dateFormatInput.value = appSettings.date_format || 'auto';
    if (timeFormatInput) timeFormatInput.value = appSettings.time_format || '12h';
    if (motionInput) motionInput.checked = isTruthySetting(appSettings.reduced_motion);
    if (densityInput) densityInput.value = appSettings.density || 'comfortable';
    if (status) {
        status.className = 'small mt-3 mb-0 text-muted';
        status.innerText = `Theme: ${appSettings.theme_mode || 'system'} · Timezone: ${appSettings.timezone}`;
    }
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
    const reminderSubjectInput = byId('settingsReminderSubject');
    const reminderBodyInput = byId('settingsReminderBody');
    const smtpHostInput = byId('settingsSmtpHost');
    const smtpPortInput = byId('settingsSmtpPort');
    const smtpUsernameInput = byId('settingsSmtpUsername');
    const smtpSenderInput = byId('settingsSmtpSenderEmail');
    const themeInput = byId('settingsThemeMode');
    const accentInput = byId('settingsAccentColor');
    const dateFormatInput = byId('settingsDateFormat');
    const timeFormatInput = byId('settingsTimeFormat');
    const motionInput = byId('settingsReducedMotion');
    const densityInput = byId('settingsDensity');
    const status = byId('settingsStatus');

    const payload = {
        timezone: timezoneInput ? timezoneInput.value.trim() : 'UTC',
        currency_code: currencyInput ? currencyInput.value.trim() : 'INR',
        currency_symbol: getCurrencyOption(currencyInput ? currencyInput.value.trim() : 'INR').symbol,
        warranty_period_days: warrantyInput ? Number(warrantyInput.value || 365) : 365,
        expiry_alert_days: expiryAlertInput ? Number(expiryAlertInput.value || 30) : 30,
        reminders_enabled: remindersEnabledInput && remindersEnabledInput.checked ? '1' : '0',
        reminder_days_before: reminderDaysInput ? Number(reminderDaysInput.value || 30) : 30,
        reminder_recipients: reminderRecipientsInput ? reminderRecipientsInput.value.trim() : '',
        reminder_subject: reminderSubjectInput ? reminderSubjectInput.value.trim() : 'Warranty expiry reminder',
        reminder_body: reminderBodyInput ? reminderBodyInput.value.trim() : '',
        smtp_host: smtpHostInput ? smtpHostInput.value.trim() : '',
        smtp_port: smtpPortInput ? smtpPortInput.value.trim() : '587',
        smtp_username: smtpUsernameInput ? smtpUsernameInput.value.trim() : '',
        smtp_sender_email: smtpSenderInput ? smtpSenderInput.value.trim() : '',
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
            if (status) {
                status.className = 'small mt-3 mb-0 text-danger';
                status.innerText = data.error || 'Failed to save settings.';
            }
            return;
        }

        appSettings.timezone = payload.timezone || appSettings.timezone;
        appSettings.currency_code = payload.currency_code || appSettings.currency_code;
        appSettings.currency_symbol = payload.currency_symbol || appSettings.currency_symbol;
        appSettings.warranty_period_days = String(payload.warranty_period_days || appSettings.warranty_period_days);
        appSettings.expiry_alert_days = String(payload.expiry_alert_days || appSettings.expiry_alert_days);
        appSettings.reminders_enabled = payload.reminders_enabled || appSettings.reminders_enabled;
        appSettings.reminder_days_before = String(payload.reminder_days_before || appSettings.reminder_days_before);
        appSettings.reminder_recipients = payload.reminder_recipients || appSettings.reminder_recipients;
        appSettings.reminder_subject = payload.reminder_subject || appSettings.reminder_subject;
        appSettings.reminder_body = payload.reminder_body || appSettings.reminder_body;
        appSettings.smtp_host = payload.smtp_host || appSettings.smtp_host;
        appSettings.smtp_port = payload.smtp_port || appSettings.smtp_port;
        appSettings.smtp_username = payload.smtp_username || appSettings.smtp_username;
        appSettings.smtp_sender_email = payload.smtp_sender_email || appSettings.smtp_sender_email;
        appSettings.theme_mode = payload.theme_mode || appSettings.theme_mode;
        appSettings.accent_color = payload.accent_color || appSettings.accent_color;
        appSettings.date_format = payload.date_format || appSettings.date_format;
        appSettings.time_format = payload.time_format || appSettings.time_format;
        appSettings.reduced_motion = payload.reduced_motion || appSettings.reduced_motion;
        appSettings.density = payload.density || appSettings.density;
        applyUserPreferences();
        if (status) {
            status.className = 'small mt-3 mb-0 text-success';
            status.innerText = 'Settings saved successfully.';
        }

        await Promise.all([loadDashboard(), loadInventory()]);
        if (byId('chat-box')) {
            renderChatThread(activeConversationMessages);
            renderRecentConversationsSidebar(lastConversationList);
        }
    } catch (e) {
        if (status) {
            status.className = 'small mt-3 mb-0 text-danger';
            status.innerText = 'Failed to save settings.';
        }
    }
}

async function sendReminderEmailsNow() {
    const status = byId('settingsStatus');
    if (status) {
        status.className = 'small mt-3 mb-0 text-muted';
        status.innerText = 'Sending reminder emails...';
    }

    try {
        const res = await fetch('/api/reminders/send', { method: 'POST' });
        const data = await res.json();

        if (!res.ok) {
            if (status) {
                status.className = 'small mt-3 mb-0 text-danger';
                status.innerText = data.error || 'Failed to send reminder emails.';
            }
            return;
        }

        if (status) {
            status.className = 'small mt-3 mb-0 text-success';
            status.innerText = data.sent
                ? `Reminder email sent to ${data.sent} recipient(s).`
                : (data.message || 'Nothing to send.');
        }
    } catch (e) {
        if (status) {
            status.className = 'small mt-3 mb-0 text-danger';
            status.innerText = 'Failed to send reminder emails.';
        }
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
        case 'open-conversation':
            openConversation(target.dataset.conversationId);
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
    await loadSettings();
    await Promise.all([loadDashboard(), loadInventory()]);
    await loadSettingsIntoForm();
    if (byId('chat-box')) {
        await startAssistantSession();
    }
};
