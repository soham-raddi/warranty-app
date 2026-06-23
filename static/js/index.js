function byId(id) {
    return document.getElementById(id);
}

const appSettings = {
    timezone: 'UTC',
    currency_symbol: 'Rs',
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

const CURRENCY_RATES = {
    INR: { symbol: 'Rs', rate: 1 },
    USD: { symbol: '$', rate: 0.012 },
    EUR: { symbol: '€', rate: 0.011 },
    GBP: { symbol: '£', rate: 0.0094 },
    AED: { symbol: 'د.إ', rate: 0.044 },
    CAD: { symbol: 'C$', rate: 0.016 },
    AUD: { symbol: 'A$', rate: 0.018 },
    SGD: { symbol: 'S$', rate: 0.016 },
    JPY: { symbol: '¥', rate: 1.8 },
    CNY: { symbol: '¥', rate: 0.087 }
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

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function money(value) {
    const amount = Number(String(value ?? 0).replace(/[^0-9.-]/g, '')) || 0;
    const currency = CURRENCY_RATES[appSettings.currency_code] || CURRENCY_RATES.INR;
    const converted = amount * currency.rate;
    const formatted = new Intl.NumberFormat(undefined, {
        maximumFractionDigits: 2,
        minimumFractionDigits: 0
    }).format(converted);
    return `${currency.symbol} ${formatted}`;
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

function applyUserPreferences() {
    const body = document.body;
    if (!body) return;

    const resolvedTheme = resolveThemeMode();
    const accent = ACCENT_COLORS[appSettings.accent_color] || ACCENT_COLORS.blue;

    body.dataset.theme = resolvedTheme;
    body.dataset.density = appSettings.density || 'comfortable';
    body.dataset.reducedMotion = isTruthySetting(appSettings.reduced_motion) ? '1' : '0';

    document.documentElement.style.setProperty('--brand', accent[0]);
    document.documentElement.style.setProperty('--brand-2', accent[1]);
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
    return `${value.slice(0, maxLength - 1).trim()}...`;
}

let activeServiceHistoryItemId = null;

function renderServiceTimeline(entries) {
    const history = Array.isArray(entries) ? entries : [];
    if (!history.length) {
        return '<p class="text-muted mb-0">No service history yet for this product.</p>';
    }

    return `<div class="service-timeline">${history.map(entry => `
        <div class="service-timeline-item">
            <div class="service-timeline-dot"></div>
            <div class="service-timeline-card">
                <div class="d-flex justify-content-between flex-wrap gap-2">
                    <strong>${escapeHtml(entry.service_type || 'Service')}</strong>
                    <span class="text-muted small">${escapeHtml(formatDateTimeValue(entry.service_date) || entry.service_date || '')}</span>
                </div>
                <div class="small text-muted mt-1">
                    ${entry.vendor ? `Vendor: ${escapeHtml(entry.vendor)}` : ''}
                    ${entry.cost ? `${entry.vendor ? ' · ' : ''}Cost: ${escapeHtml(entry.cost)}` : ''}
                </div>
                ${entry.notes ? `<p class="mb-0 mt-2">${escapeHtml(entry.notes)}</p>` : ''}
                ${entry.next_service_date ? `<div class="small mt-2">Next service: ${escapeHtml(entry.next_service_date)}</div>` : ''}
            </div>
        </div>
    `).join('')}</div>`;
}

async function openServiceHistory(itemId, itemName) {
    const modalEl = byId('serviceHistoryModal');
    if (!modalEl) return;

    activeServiceHistoryItemId = Number(itemId);
    setText('serviceHistoryTitle', itemName || 'Item');
    setText('serviceHistoryStatus', 'Loading timeline...');
    const listEl = byId('serviceHistoryList');
    if (listEl) {
        listEl.innerHTML = '<p class="text-muted mb-0">Loading service history...</p>';
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
            listEl.innerHTML = '<p class="text-danger mb-0">Could not load service history.</p>';
        }
        setText('serviceHistoryStatus', 'Could not load timeline.');
    }

    const form = byId('serviceHistoryForm');
    if (form) form.reset();
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
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
    if (!file) return alert('Select a file!');

    const btn = byId('upBtn');
    const loader = byId('upLoader');
    if (btn) btn.disabled = true;
    if (loader) loader.classList.remove('d-none');

    const formData = new FormData();
    formData.append('receipt_image', file);

    try {
        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();

        if (res.ok) {
            setText('modalItemName', data.item_name || 'N/A');
            setText('modalCategory', data.category || 'N/A');
            setText('modalStore', data.store_name || 'N/A');
            setText('modalDate', data.date_of_purchase || 'N/A');
            setText('modalInvoice', data.invoice_number || 'N/A');
            setText('modalWarranty', data.warranty_info || 'Not specified on receipt');
            setText('modalPrice', data.total_amount || '0');

            const modalEl = byId('summaryModal');
            if (modalEl) {
                const summaryModal = new bootstrap.Modal(modalEl);
                summaryModal.show();
            }

            fileInput.value = '';
        } else {
            alert(`Error: ${data.error || 'Upload failed'}`);
        }
    } catch (e) {
        alert('Upload failed. Make sure the server is running.');
    } finally {
        if (btn) btn.disabled = false;
        if (loader) loader.classList.add('d-none');
    }
}

async function loadInventory() {
    const tbody = byId('invTable');
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="6" class="text-center p-5">Loading digital twin data...</td></tr>';

    try {
        const res = await fetch('/api/inventory');
        const data = await res.json();

        setText('s-tot', money(data.total_spent));
        setText('s-act', data.active_warranties || 0);

        if (!data.items || data.items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center p-5 text-muted">No items found.</td></tr>';
            return;
        }

        tbody.innerHTML = data.items.map(i => `
            <tr>
                <td class="ps-4">
                    <a href="/${i.file_path}" target="_blank" class="item-link" title="Click to view receipt">
                        <strong>${escapeHtml(i.item_name || 'Unknown')}</strong><br>
                        <small class="text-muted">${escapeHtml(i.brand || 'N/A')}</small>
                    </a>
                </td>
                <td><span class="badge bg-light text-dark border">${escapeHtml(i.category || 'N/A')}</span></td>
                <td>${escapeHtml(i.date_of_purchase || 'N/A')}<br><small class="text-muted">${escapeHtml(i.warranty_expires_on || '')}</small></td>
                <td class="fw-bold">${money(i.total_amount || 0)}</td>
                <td>
                    <span class="badge ${i.warranty_status === 'Active' ? 'bg-success' : i.warranty_status === 'Expiring Soon' ? 'bg-warning text-dark' : 'bg-danger'} me-1">${escapeHtml(i.warranty_status || 'Unknown')}</span>
                    ${i.has_warranty_card
                        ? `<a href="/${i.warranty_card_path}" target="_blank" class="badge text-bg-info text-decoration-none" title="View attached warranty card">Card Attached</a>`
                        : `<button onclick="attachWarrantyCard(${Number(i.id)})" class="btn btn-sm btn-outline-warning py-0 px-2" title="Attach warranty card">Attach Card</button>`}
                </td>
                <td class="text-end pe-4">
                    <div class="btn-group">
                        <button type="button" class="btn btn-sm btn-outline-info" onclick="openServiceHistory(${Number(i.id)}, ${JSON.stringify(String(i.item_name || 'Item'))})" title="View service history"><i class="bi bi-clock-history"></i></button>
                        <a href="/${i.file_path}" download class="btn btn-sm btn-outline-secondary" title="Download Image"><i class="bi bi-download"></i></a>
                        <button onclick="delItem(${Number(i.id)})" class="btn btn-sm btn-outline-danger" title="Delete Record"><i class="bi bi-trash"></i></button>
                    </div>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-danger p-5">Failed to connect to database.</td></tr>';
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

        items.sort((a, b) => String(b.date_of_purchase || '').localeCompare(String(a.date_of_purchase || '')));

        const recentBody = byId('dash-recent-body');
        if (recentBody) {
            if (!items.length) {
                recentBody.innerHTML = '<tr><td colspan="4" class="text-center p-4 text-muted">No items available.</td></tr>';
            } else {
                recentBody.innerHTML = items.slice(0, 6).map(i => `
                    <tr>
                        <td class="ps-4"><strong>${escapeHtml(i.item_name || 'Unknown')}</strong><br><small class="text-muted">${escapeHtml(i.brand || 'N/A')}</small></td>
                        <td>${escapeHtml(i.date_of_purchase || 'N/A')}</td>
                        <td>${money(i.total_amount || 0)}</td>
                        <td class="pe-4">${i.warranty_status === 'Active' ? '<span class="badge bg-success">Active</span>' : '<span class="badge bg-danger">Expired</span>'}</td>
                    </tr>
                `).join('');
            }
        }

        const alerts = byId('dash-alerts');
        if (alerts) {
            const alertItems = Array.isArray(data.alerts) ? data.alerts.slice(0, 6) : [];
            if (alertItems.length) {
                alerts.innerHTML = `<div class="dashboard-attention-list">${alertItems.map(alert => `
                    <div class="attention-item ${alert.status === 'expired' ? 'border-danger' : 'border-warning'}">
                        <h6>${escapeHtml(alert.item_name || 'Item')}</h6>
                        <p>
                            ${alert.status === 'expired' ? 'Expired' : 'Expiring soon'}
                            ${alert.expiry_date ? `on ${escapeHtml(alert.expiry_date)}` : ''}
                            ${typeof alert.days_remaining === 'number' ? `(${alert.days_remaining} days left)` : ''}.
                        </p>
                    </div>
                `).join('')}</div>`;
            } else {
                const missingCards = items.filter(i => !Number(i.has_warranty_card || 0)).slice(0, 4);
                const unknownWarranty = items.filter(i => (i.warranty_status || 'Unknown') === 'Unknown').slice(0, 4);
                const blocks = [];

                if (missingCards.length) {
                    blocks.push(...missingCards.map(i => `
                        <div class="attention-item">
                            <h6>${escapeHtml(i.item_name || 'Item')}</h6>
                            <p>Warranty card missing. Add it from Inventory for faster claim support.</p>
                        </div>
                    `));
                }

                if (unknownWarranty.length) {
                    blocks.push(...unknownWarranty.map(i => `
                        <div class="attention-item">
                            <h6>${escapeHtml(i.item_name || 'Item')}</h6>
                            <p>Warranty status unknown. Update purchase date or warranty details.</p>
                        </div>
                    `));
                }

                alerts.innerHTML = blocks.length
                    ? `<div class="dashboard-attention-list">${blocks.join('')}</div>`
                    : '<p class="text-muted mb-0">All items look healthy. No immediate follow-ups.</p>';
            }
        }
    } catch (e) {
        setText('dash-total', money(0));
        setText('dash-active', '0');
        setText('dash-count', '0');

        const recentBody = byId('dash-recent-body');
        if (recentBody) {
            recentBody.innerHTML = '<tr><td colspan="4" class="text-center p-4 text-danger">Could not load dashboard data.</td></tr>';
        }

        const alerts = byId('dash-alerts');
        if (alerts) {
            alerts.innerHTML = '<p class="text-danger mb-0">Could not load alerts.</p>';
        }
    }
}

async function attachWarrantyCard(id) {
    const picker = document.createElement('input');
    picker.type = 'file';
    picker.accept = 'image/*,application/pdf';

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
                alert(data.error || 'Failed to attach warranty card.');
                return;
            }
            await Promise.all([loadInventory(), loadDashboard()]);
        } catch (e) {
            alert('Failed to upload warranty card.');
        }
    };

    picker.click();
}

async function delItem(id) {
    if (!confirm('Delete this record permanently?')) return;
    const res = await fetch(`/api/delete/${id}`, { method: 'DELETE' });
    if (res.ok) {
        await Promise.all([loadInventory(), loadDashboard()]);
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
    const safeContent = escapeHtml(message.content || '').replace(/\n/g, '<br>');
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
        container.innerHTML = '<div class="text-muted small">No conversations yet.</div>';
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
            const convId = String(item.conversation_id || '').replace(/\"/g, '&quot;');
            const title = truncateText(item.title || 'New conversation', 46);
            const preview = truncateText(item.preview || '', 70);
            const isActive = item.conversation_id === currentConversationId ? 'active' : '';
            html += `
                <button type="button" class="conv-item w-100 text-start ${isActive}" data-conversation-id="${convId}" onclick="openConversation('${convId}')">
                    <div class="conv-preview"><strong>${escapeHtml(title)}</strong>${preview ? `<br>${escapeHtml(preview)}` : ''}</div>
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
        container.innerHTML = '<div class="text-muted small">Loading conversations...</div>';
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
            alert('Failed to clear chat history.');
            return;
        }

        lastConversationList = [];
        startNewConversation();
        await loadConversationList('');
    } catch (e) {
        alert('Failed to clear chat history.');
    }
}

async function sendMessage() {
    const input = byId('chatInput');
    if (!input) return;

    const msg = input.value.trim();
    if (!msg) return;

    const historyForRequest = activeConversationMessages.map(m => ({ role: m.role, content: m.content }));
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
                history: historyForRequest,
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
            status.innerText = `Reminder email sent to ${data.sent || 0} recipient(s).`;
        }
    } catch (e) {
        if (status) {
            status.className = 'small mt-3 mb-0 text-danger';
            status.innerText = 'Failed to send reminder emails.';
        }
    }
}

window.onload = async () => {
    await loadSettings();
    await Promise.all([loadDashboard(), loadInventory()]);
    await loadSettingsIntoForm();
    if (byId('chat-box')) {
        await startAssistantSession();
    }
};
