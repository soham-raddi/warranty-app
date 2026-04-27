async function uploadReceipt() {
    const file = document.getElementById('receiptFile').files[0];
    if(!file) return alert("Select a file!");

    const btn = document.getElementById('upBtn');
    const loader = document.getElementById('upLoader');
    btn.disabled = true; loader.classList.remove('d-none');

    const formData = new FormData();
    formData.append('receipt_image', file);

    try {
        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();

        if(res.ok) {
            loadInventory();

            // Populate the rich modal data after successful extraction.
            document.getElementById('modalItemName').innerText = data.item_name || 'N/A';
            document.getElementById('modalCategory').innerText = data.category || 'N/A';
            document.getElementById('modalStore').innerText = data.store_name || 'N/A';
            document.getElementById('modalDate').innerText = data.date_of_purchase || 'N/A';
            document.getElementById('modalInvoice').innerText = data.invoice_number || 'N/A';
            document.getElementById('modalWarranty').innerText = data.warranty_info || 'Not specified on receipt';
            document.getElementById('modalPrice').innerText = data.total_amount || '0';

            const summaryModal = new bootstrap.Modal(document.getElementById('summaryModal'));
            summaryModal.show();

            document.getElementById('receiptFile').value = "";
        }
        else {
            alert("Error: " + data.error);
        }
    } catch(e) {
        alert("Upload failed. Make sure the server is running.");
    }
    finally {
        btn.disabled = false; loader.classList.add('d-none');
    }
}

async function loadInventory() {
    const tbody = document.getElementById('invTable');
    tbody.innerHTML = '<tr><td colspan="6" class="text-center p-5">Loading digital twin data...</td></tr>';

    try {
        const res = await fetch('/api/inventory');
        const data = await res.json();

        document.getElementById('s-tot').innerText = `₹${data.total_spent || 0}`;
        document.getElementById('s-act').innerText = data.active_warranties || 0;

        if(!data.items || data.items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center p-5 text-muted">No items found.</td></tr>';
            return;
        }

        tbody.innerHTML = data.items.map(i => `
            <tr>
                <td class="ps-4">
                    <a href="/${i.file_path}" target="_blank" class="item-link" title="Click to view receipt">
                        <strong>${i.item_name}</strong><br>
                        <small class="text-muted">${i.brand}</small>
                    </a>
                </td>
                <td><span class="badge bg-light text-dark border">${i.category || 'N/A'}</span></td>
                <td>${i.date_of_purchase}</td>
                <td class="fw-bold">₹${i.total_amount}</td>
                <td>
                    <span class="badge ${i.warranty_status==='Active'?'bg-success':'bg-danger'} me-1">${i.warranty_status}</span>
                    ${i.has_warranty_card ?
                        `<a href="/${i.warranty_card_path}" target="_blank" class="badge text-bg-info text-decoration-none" title="View attached warranty card">Card Attached</a>` :
                        `<button onclick="attachWarrantyCard(${i.id})" class="btn btn-sm btn-outline-warning py-0 px-2" title="Attach warranty card">Attach Card</button>`
                    }
                </td>
                <td class="text-end pe-4">
                    <div class="btn-group">
                        <a href="/${i.file_path}" download class="btn btn-sm btn-outline-secondary" title="Download Image"><i class="bi bi-download"></i></a>
                        <button onclick="delItem(${i.id})" class="btn btn-sm btn-outline-danger" title="Delete Record"><i class="bi bi-trash"></i></button>
                    </div>
                </td>
            </tr>
        `).join('');
    } catch(e) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-danger p-5 text-muted">Failed to connect to database.</td></tr>';
    }
}

async function attachWarrantyCard(id) {
    const picker = document.createElement('input');
    picker.type = 'file';
    picker.accept = 'image/*,application/pdf';
    picker.onchange = async () => {
        const file = picker.files && picker.files[0];
        if(!file) return;

        const formData = new FormData();
        formData.append('warranty_card', file);

        try {
            const res = await fetch(`/api/inventory/${id}/attach-warranty-card`, {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            if(!res.ok) {
                alert(data.error || 'Failed to attach warranty card.');
                return;
            }
            loadInventory();
        } catch(e) {
            alert('Failed to upload warranty card.');
        }
    };
    picker.click();
}

async function delItem(id) {
    if(!confirm("Delete this record permanently?")) return;
    const res = await fetch(`/api/delete/${id}`, { method: 'DELETE' });
    if(res.ok) loadInventory();
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function formatTime(value) {
    const dt = new Date(String(value).replace(' ', 'T'));
    if (isNaN(dt.getTime())) return '';
    return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function dateKey(value) {
    const dt = new Date(String(value).replace(' ', 'T'));
    if (isNaN(dt.getTime())) return '';
    return dt.toISOString().slice(0, 10);
}

function separatorLabel(value) {
    const dt = new Date(String(value).replace(' ', 'T'));
    if (isNaN(dt.getTime())) return '';

    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const d = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
    const diffDays = Math.round((today - d) / 86400000);

    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    return dt.toLocaleDateString([], { day: '2-digit', month: 'short', year: 'numeric' });
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

function generateLocalMessageId(prefix) {
    return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
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

function renderRecentConversationsSidebar(messages) {
    const container = document.getElementById('recentConversations');
    const userMessages = (messages || []).filter(m => m.role === 'user' && (m.content || '').trim());

    if (userMessages.length === 0) {
        container.innerHTML = '<div class="text-muted small">No conversations yet.</div>';
        return;
    }

    const groups = {};
    for (const m of userMessages) {
        const key = dateKey(m.created_at) || 'unknown';
        if (!groups[key]) {
            groups[key] = {
                label: separatorLabel(m.created_at) || 'Unknown date',
                items: []
            };
        }
        groups[key].items.push(m);
    }

    const sortedKeys = Object.keys(groups).sort((a, b) => (a < b ? 1 : -1));
    let html = '';

    for (const key of sortedKeys) {
        html += `<div class="conv-date-group"><div class="conv-date-label">${escapeHtml(groups[key].label)}</div>`;
        const items = groups[key].items.slice().reverse();
        for (const item of items) {
            const preview = escapeHtml(item.content || '');
            const itemId = String(item.id || '').replace(/\"/g, '&quot;');
            html += `
                <button type="button" class="conv-item w-100 text-start" data-target-msg-id="${itemId}" onclick="jumpToChatMessage('${itemId}')">
                    <div class="conv-preview">${preview}</div>
                    <div class="conv-time">${formatTime(item.created_at) || ''}</div>
                </button>
            `;
        }
        html += '</div>';
    }

    container.innerHTML = html;
}

function jumpToChatMessage(rawId) {
    if (!rawId) return;
    const safeId = String(rawId).replace(/[^a-zA-Z0-9_-]/g, '_');
    const target = document.getElementById(`msg_${safeId}`);
    if (!target) return;

    target.scrollIntoView({ behavior: 'smooth', block: 'center' });

    document.querySelectorAll('.conv-item').forEach(el => el.classList.remove('active'));
    const activeBtn = document.querySelector(`.conv-item[data-target-msg-id="${String(rawId).replace(/\"/g, '&quot;')}"]`);
    if (activeBtn) activeBtn.classList.add('active');
}

function renderChatThread(messages) {
    const box = document.getElementById('chat-box');
    if (!messages || messages.length === 0) {
        box.innerHTML = '<div class="msg-wrap assistant"><div class="msg-ai">Welcome back! How can I help you with your inventory today?</div></div>';
        box.scrollTop = box.scrollHeight;
        return;
    }

    let html = '';
    let lastDate = '';

    for (const m of messages) {
        const key = dateKey(m.created_at);
        if (key && key !== lastDate) {
            html += `<div class="date-sep"><span>${separatorLabel(m.created_at)}</span></div>`;
            lastDate = key;
        }
        html += renderChatMessage(m);
    }

    box.innerHTML = html;
    box.scrollTop = box.scrollHeight;
}

let allChatMessages = [];
let displayedChatMessages = [];
let activeSearchQuery = '';

async function loadChatHistory(query = '') {
    const box = document.getElementById('chat-box');
    box.innerHTML = '<div class="msg-wrap assistant"><div class="msg-ai">Loading your previous chats...</div></div>';

    try {
        const url = query
            ? `/api/chat/history?limit=200&query=${encodeURIComponent(query)}`
            : '/api/chat/history?limit=200';

        const res = await fetch(url);
        const data = await res.json();
        const messages = (data.messages || []).filter(m => m.role === 'user' || m.role === 'assistant');

        const normalized = messages.map(m => ({
            id: m.id || generateLocalMessageId('db'),
            role: m.role,
            content: m.content || '',
            created_at: m.created_at || new Date().toISOString()
        }));

        if (query) {
            activeSearchQuery = query;
            displayedChatMessages = normalized;
        } else {
            activeSearchQuery = '';
            allChatMessages = normalized;
            displayedChatMessages = normalized.slice();
        }

        renderChatThread(displayedChatMessages);
        renderRecentConversationsSidebar(allChatMessages);
    } catch (e) {
        box.innerHTML = '<div class="msg-wrap assistant"><div class="msg-ai text-danger">Could not load chat history.</div></div>';
        allChatMessages = [];
        displayedChatMessages = [];
        activeSearchQuery = '';
        renderRecentConversationsSidebar([]);
    }
}

async function searchChatHistory() {
    const input = document.getElementById('chatSearchInput');
    const query = (input.value || '').trim();
    if (!query) {
        resetChatSearch();
        return;
    }
    await loadChatHistory(query);
}

async function resetChatSearch() {
    document.getElementById('chatSearchInput').value = '';
    await loadChatHistory('');
}

async function clearChatHistory() {
    const ok = confirm('Clear all saved chat history? This cannot be undone.');
    if (!ok) return;

    try {
        const res = await fetch('/api/chat/history', { method: 'DELETE' });
        if (!res.ok) {
            alert('Failed to clear chat history.');
            return;
        }
        allChatMessages = [];
        displayedChatMessages = [];
        activeSearchQuery = '';
        document.getElementById('chatSearchInput').value = '';
        renderChatThread([]);
        renderRecentConversationsSidebar([]);
    } catch (e) {
        alert('Failed to clear chat history.');
    }
}

async function sendMessage() {
    const input = document.getElementById('chatInput');
    const msg = input.value.trim();
    if(!msg) return;

    if (activeSearchQuery) {
        activeSearchQuery = '';
        document.getElementById('chatSearchInput').value = '';
        displayedChatMessages = allChatMessages.slice();
    }

    const historyForRequest = allChatMessages.map(m => ({ role: m.role, content: m.content }));
    const nowIso = new Date().toISOString();
    const userMessageObj = { id: generateLocalMessageId('user'), role: 'user', content: msg, created_at: nowIso };

    allChatMessages.push(userMessageObj);
    displayedChatMessages = allChatMessages.slice();
    renderChatThread(displayedChatMessages);
    renderRecentConversationsSidebar(allChatMessages);

    input.value = "";

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg, history: historyForRequest })
        });
        const data = await res.json();
        const assistantMessageObj = {
            id: generateLocalMessageId('assistant'),
            role: 'assistant',
            content: data.reply || '',
            created_at: new Date().toISOString(),
            action: data.action || null
        };

        allChatMessages.push(assistantMessageObj);
        displayedChatMessages = allChatMessages.slice();
        renderChatThread(displayedChatMessages);
        renderRecentConversationsSidebar(allChatMessages);
    } catch(e) {
        allChatMessages.push({
            id: generateLocalMessageId('assistant'),
            role: 'assistant',
            content: 'Connection error.',
            created_at: new Date().toISOString()
        });
        displayedChatMessages = allChatMessages.slice();
        renderChatThread(displayedChatMessages);
        renderRecentConversationsSidebar(allChatMessages);
    }
}

window.onload = async () => {
    await loadInventory();
    await loadChatHistory();
};
