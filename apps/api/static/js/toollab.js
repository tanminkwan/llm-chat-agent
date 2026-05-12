/**
 * toollab.js — Tool Lab (편집) + Tool Run (실행) SPA views (Phase 07).
 *
 * Two distinct views:
 *  - /toollab     → list + editor (initToollab)
 *  - /toollab/run → tool picker + prompt + trace (initToollabRun)
 *
 * Why split: tool catalogue can grow large. Auto-binding all active tools
 * blows up the LLM context with their JSON schemas. The Run view requires
 * an *explicit* tool selection, so the user controls exactly which schemas
 * land in `tools[]` for each run.
 */

const TOOLLAB_BASE = '/api/toollab';

// State shared between the two views (cached after either view loads it).
let toollabTools = [];          // most recent list response
let toollabSelected = null;     // currently being edited (editor view)

// Run-view picker state
const toollabPickerSelectedIds = new Set();
let toollabPickerSearchTerm = '';

// Multi-turn state — frontend memory only (cleared on page reload or "새 대화").
// Each entry: {role: "user"|"assistant"|"tool", content?, tool_calls?, tool_call_id?, name?}
let toollabHistory = [];
let toollabTurnCount = 0;

// Boilerplate for the "new tool" template.
// 기본 code 에 타입 힌트를 모두 명시 — Generate from code 가 즉시 동작하도록.
const TOOLLAB_NEW_TEMPLATE = {
    name: '',
    description: '',
    parameters: {
        type: 'object',
        properties: { x: { type: 'integer' } },
        required: ['x'],
        additionalProperties: false,
    },
    returns: {
        type: 'object',
        properties: { out: { type: 'integer' } },
        required: ['out'],
    },
    code: 'def handler(x: int) -> dict:\n    return {"out": x}\n',
    tags: [],
    is_active: true,
    is_public: false,
};


// ===========================================================================
// View 1 — Tool Lab (editor)
// ===========================================================================


function initToollab() {
    document.getElementById('toollab-new-btn').addEventListener('click', toollabNew);
    document.getElementById('toollab-generate-btn').addEventListener('click', toollabGenerateSchemas);
    document.getElementById('toollab-validate-btn').addEventListener('click', toollabValidate);
    document.getElementById('toollab-save-btn').addEventListener('click', toollabSave);
    document.getElementById('toollab-active-btn').addEventListener('click', toollabToggleActive);
    document.getElementById('toollab-delete-btn').addEventListener('click', toollabDelete);
    document.getElementById('toollab-clear-btn').addEventListener('click', toollabNew);

    document.getElementById('toollab-show-inactive')
        .addEventListener('change', toollabRefresh);

    toollabRefresh().then(() => {
        // Pre-load the new-tool template so the editor is never empty.
        toollabNew();
    });
}


async function toollabRefresh() {
    const showInactive = document.getElementById('toollab-show-inactive')?.checked || false;
    try {
        const r = await fetch(`${TOOLLAB_BASE}/tools?include_inactive=${showInactive}`);
        if (!r.ok) {
            toollabStatus('error', `목록 조회 실패: ${r.status}`);
            return;
        }
        toollabTools = await r.json();
        toollabRenderList();
    } catch (err) {
        toollabStatus('error', `네트워크 오류: ${err}`);
    }
}


function toollabRenderList() {
    const container = document.getElementById('toollab-tool-list');
    if (!container) return;
    container.innerHTML = '';
    if (!toollabTools.length) {
        container.innerHTML = '<div class="toollab-empty">등록된 도구가 없습니다.</div>';
        return;
    }
    const system = toollabTools.filter(t => t.owner_user_id === 'system');
    const mine = toollabTools.filter(t => t.owner_user_id !== 'system');
    const groups = [
        { label: '내 도구', items: mine },
        { label: '시드 (system)', items: system },
    ];
    for (const grp of groups) {
        if (!grp.items.length) continue;
        const h = document.createElement('div');
        h.className = 'toollab-list-group-label';
        h.textContent = grp.label;
        container.appendChild(h);
        for (const t of grp.items) {
            const item = document.createElement('div');
            item.className = 'toollab-list-item';
            if (!t.is_active) item.classList.add('inactive');
            if (toollabSelected && t.id === toollabSelected.id) {
                item.classList.add('selected');
            }
            const macro = (t.tags || []).includes('macro');
            const publicBadge = t.is_public && t.owner_user_id !== 'system'
                ? '<span class="toollab-tag-public">공개</span>'
                : '';
            item.innerHTML = `
                <div class="toollab-list-item-name">
                    ${escapeHtml(t.name)}
                    ${macro ? '<span class="toollab-tag-macro">macro</span>' : ''}
                    ${publicBadge}
                    ${t.is_active ? '' : '<span class="toollab-tag-inactive">비활성</span>'}
                </div>
                <div class="toollab-list-item-desc">${escapeHtml(t.description || '')}</div>
                <div class="toollab-list-item-meta">v${t.version} · ${t.is_active ? '활성' : '비활성'}</div>
            `;
            item.addEventListener('click', () => toollabSelect(t));
            container.appendChild(item);
        }
    }
}


function toollabSelect(tool) {
    toollabSelected = tool;
    document.getElementById('toollab-edit-id').value = tool.id;
    document.getElementById('toollab-edit-owner').value = tool.owner_user_id;
    document.getElementById('toollab-name').value = tool.name;
    document.getElementById('toollab-name').readOnly = true;
    document.getElementById('toollab-description').value = tool.description || '';
    document.getElementById('toollab-tags').value = (tool.tags || []).join(', ');
    document.getElementById('toollab-parameters').value = JSON.stringify(tool.parameters, null, 2);
    document.getElementById('toollab-returns').value = JSON.stringify(tool.returns, null, 2);
    document.getElementById('toollab-code').value = tool.code;
    document.getElementById('toollab-is-public').checked = !!tool.is_public;
    document.getElementById('toollab-editor-title').textContent = `편집: ${tool.name}`;
    document.getElementById('toollab-editor-meta').textContent =
        `v${tool.version} · owner=${tool.owner_user_id}`;
    document.getElementById('toollab-active-btn').textContent =
        tool.is_active ? '비활성화' : '활성화';
    toollabRenderList();
    toollabClearStatus();
}


function toollabNew() {
    toollabSelected = null;
    document.getElementById('toollab-edit-id').value = '';
    document.getElementById('toollab-edit-owner').value = '';
    document.getElementById('toollab-name').value = TOOLLAB_NEW_TEMPLATE.name;
    document.getElementById('toollab-name').readOnly = false;
    document.getElementById('toollab-description').value = TOOLLAB_NEW_TEMPLATE.description;
    document.getElementById('toollab-tags').value = '';
    document.getElementById('toollab-parameters').value = JSON.stringify(TOOLLAB_NEW_TEMPLATE.parameters, null, 2);
    document.getElementById('toollab-returns').value = JSON.stringify(TOOLLAB_NEW_TEMPLATE.returns, null, 2);
    document.getElementById('toollab-code').value = TOOLLAB_NEW_TEMPLATE.code;
    document.getElementById('toollab-is-public').checked = TOOLLAB_NEW_TEMPLATE.is_public;
    document.getElementById('toollab-editor-title').textContent = '새 도구';
    document.getElementById('toollab-editor-meta').textContent = '';
    document.getElementById('toollab-active-btn').textContent = '비활성화';
    toollabRenderList();
    toollabClearStatus();
}


function toollabCollectPayload() {
    const tagsRaw = document.getElementById('toollab-tags').value || '';
    const tags = tagsRaw.split(',').map(s => s.trim()).filter(Boolean);
    let parameters, returns;
    try { parameters = JSON.parse(document.getElementById('toollab-parameters').value); }
    catch (e) { throw new Error(`parameters JSON 파싱 실패: ${e.message}`); }
    try { returns = JSON.parse(document.getElementById('toollab-returns').value); }
    catch (e) { throw new Error(`returns JSON 파싱 실패: ${e.message}`); }
    return {
        name: document.getElementById('toollab-name').value.trim(),
        description: document.getElementById('toollab-description').value,
        parameters,
        returns,
        code: document.getElementById('toollab-code').value,
        tags,
        is_active: true,
        is_public: document.getElementById('toollab-is-public').checked,
    };
}


/**
 * code 의 타입 힌트로 parameters / returns JSON Schema 초안을 채운다.
 *
 * 저장은 하지 않고 텍스트박스만 갱신 — 사용자가 결과를 검토한 뒤 저장 버튼을
 * 직접 눌러야 한다. 기존 schema 가 비어있지 않으면 덮어쓰기 확인을 받는다.
 */
async function toollabGenerateSchemas() {
    const code = document.getElementById('toollab-code').value;
    if (!code.trim()) {
        toollabStatus('error', 'code 가 비어있습니다. handler 함수를 먼저 작성하세요.');
        return;
    }
    const paramsCurrent = document.getElementById('toollab-parameters').value.trim();
    const returnsCurrent = document.getElementById('toollab-returns').value.trim();
    const hasExisting = paramsCurrent || returnsCurrent;
    if (hasExisting && !confirm(
        '기존 parameters / returns 가 덮어쓰여집니다. 진행할까요?\n\n'
        + '(저장은 자동으로 일어나지 않습니다 — 결과를 검토한 뒤 저장 버튼을 누르세요.)'
    )) {
        toollabStatus('info', '자동 생성이 취소되었습니다.');
        return;
    }
    toollabStatus('info', '코드 분석 중...');
    try {
        const r = await fetch(`${TOOLLAB_BASE}/tools/generate-schemas`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code }),
        });
        if (!r.ok) {
            const body = await r.text();
            toollabStatus('error', `요청 실패 (${r.status}): ${body}`);
            return;
        }
        const body = await r.json();
        if (!body.ok) {
            const err = body.error || {};
            const loc = err.line ? ` (line ${err.line}${err.col ? `, col ${err.col}` : ''})` : '';
            toollabStatus('error',
                `자동 생성 실패 — [${err.kind}]${loc}\n${err.detail}\n\n`
                + '모든 파라미터와 반환값에 타입 힌트를 추가한 뒤 다시 시도하세요.');
            return;
        }
        document.getElementById('toollab-parameters').value =
            JSON.stringify(body.parameters, null, 2);
        document.getElementById('toollab-returns').value =
            JSON.stringify(body.returns, null, 2);
        const warnLines = (body.warnings || []).map(w => `  • ${w}`).join('\n');
        const warnMsg = warnLines ? `\n\n⚠ 경고:\n${warnLines}` : '';
        toollabStatus('success',
            '✅ parameters / returns 가 자동 생성되었습니다. '
            + '내용을 검토한 뒤 [검증] → [저장] 순으로 진행하세요.'
            + warnMsg);
    } catch (err) {
        toollabStatus('error', `요청 실패: ${err}`);
    }
}


async function toollabValidate() {
    let payload;
    try { payload = toollabCollectPayload(); }
    catch (e) { toollabStatus('error', e.message); return; }
    toollabStatus('info', '검증 중...');
    try {
        const r = await fetch(`${TOOLLAB_BASE}/tools/validate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const body = await r.json();
        if (body.ok) {
            toollabStatus('success', '✅ 검증 통과 — 저장 가능');
        } else {
            const lines = (body.errors || []).map(e =>
                `[${e.kind}${e.line ? ` line ${e.line}` : ''}] ${e.detail}`);
            toollabStatus('error', '검증 실패:\n' + lines.join('\n'));
        }
    } catch (err) {
        toollabStatus('error', `요청 실패: ${err}`);
    }
}


async function toollabSave() {
    let payload;
    try { payload = toollabCollectPayload(); }
    catch (e) { toollabStatus('error', e.message); return; }
    toollabStatus('info', '저장 중...');
    const editId = document.getElementById('toollab-edit-id').value;
    const owner = document.getElementById('toollab-edit-owner').value;
    const isSystem = owner === 'system';
    if (editId && isSystem) {
        if (!confirm('시드 도구를 수정하면 사본이 본인 소유로 새로 생성됩니다. 진행할까요?')) {
            toollabStatus('info', '취소되었습니다.');
            return;
        }
        return toollabSaveAsNew(payload);
    }
    const url = editId
        ? `${TOOLLAB_BASE}/tools/${editId}`
        : `${TOOLLAB_BASE}/tools`;
    const method = editId ? 'PUT' : 'POST';
    try {
        const r = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!r.ok) {
            const body = await r.json().catch(() => ({}));
            const errLines = body.detail && body.detail.errors
                ? body.detail.errors.map(e => `[${e.kind}] ${e.detail}`).join('\n')
                : JSON.stringify(body.detail || `HTTP ${r.status}`);
            toollabStatus('error', '저장 실패:\n' + errLines);
            return;
        }
        const saved = await r.json();
        toollabStatus('success', `저장 완료 (v${saved.version})`);
        await toollabRefresh();
        toollabSelect(toollabTools.find(t => t.id === saved.id) || saved);
    } catch (err) {
        toollabStatus('error', `요청 실패: ${err}`);
    }
}


async function toollabSaveAsNew(payload) {
    try {
        const r = await fetch(`${TOOLLAB_BASE}/tools`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!r.ok) {
            const body = await r.json().catch(() => ({}));
            toollabStatus('error', '저장 실패: ' + JSON.stringify(body));
            return;
        }
        const saved = await r.json();
        toollabStatus('success', `시드 사본 저장 완료 (v${saved.version})`);
        await toollabRefresh();
        toollabSelect(toollabTools.find(t => t.id === saved.id) || saved);
    } catch (err) {
        toollabStatus('error', `요청 실패: ${err}`);
    }
}


async function toollabDelete() {
    const editId = document.getElementById('toollab-edit-id').value;
    if (!editId) {
        toollabStatus('error', '삭제할 도구가 선택되지 않았습니다.');
        return;
    }
    const owner = document.getElementById('toollab-edit-owner').value;
    if (owner === 'system' && !(window.__APP_USER__ && window.__APP_USER__.groups || []).includes('Admin')) {
        toollabStatus('error', '시드 도구는 Admin 만 삭제할 수 있습니다.');
        return;
    }
    if (!confirm('이 도구를 삭제(비활성화)할까요?')) return;
    try {
        const r = await fetch(`${TOOLLAB_BASE}/tools/${editId}`, { method: 'DELETE' });
        if (!r.ok) {
            const body = await r.text();
            toollabStatus('error', `삭제 실패: ${r.status} ${body}`);
            return;
        }
        toollabStatus('success', '삭제 완료');
        toollabSelected = null;
        await toollabRefresh();
        toollabNew();
    } catch (err) {
        toollabStatus('error', `요청 실패: ${err}`);
    }
}


async function toollabToggleActive() {
    const editId = document.getElementById('toollab-edit-id').value;
    if (!editId || !toollabSelected) {
        toollabStatus('error', '대상 도구가 선택되지 않았습니다.');
        return;
    }
    const next = !toollabSelected.is_active;
    try {
        const r = await fetch(`${TOOLLAB_BASE}/tools/${editId}/active`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: next }),
        });
        if (!r.ok) {
            const body = await r.text();
            toollabStatus('error', `토글 실패: ${r.status} ${body}`);
            return;
        }
        const saved = await r.json();
        toollabStatus('success', `${saved.is_active ? '활성' : '비활성'} 상태로 변경됨`);
        await toollabRefresh();
        toollabSelect(toollabTools.find(t => t.id === saved.id) || saved);
    } catch (err) {
        toollabStatus('error', `요청 실패: ${err}`);
    }
}


// ===========================================================================
// View 2 — Tool Run (picker + run + trace)
// ===========================================================================


function initToollabRun() {
    // Populate model select from /api/config (already loaded by app.js).
    const cfg = window.__APP_CONFIG__ || {};
    const modelSelect = document.getElementById('toollab-model');
    if (modelSelect && !modelSelect.options.length) {
        modelSelect.innerHTML = `
            <option value="chat">${cfg.chat_label || 'chat'} (${cfg.chat_model || ''})</option>
            <option value="reasoning">${cfg.reasoning_label || 'reasoning'} (${cfg.reasoning_model || ''})</option>
        `;
    }

    document.getElementById('toollab-run-btn').addEventListener('click', toollabRun);
    document.getElementById('toollab-clear-trace-btn').addEventListener('click', toollabNewConversation);

    document.getElementById('toollab-picker-search').addEventListener('input', (e) => {
        toollabPickerSearchTerm = (e.target.value || '').trim().toLowerCase();
        toollabPickerRender();
    });
    document.getElementById('toollab-picker-select-all').addEventListener('click', () => {
        for (const t of toollabPickerVisible()) toollabPickerSelectedIds.add(t.id);
        toollabPickerRender();
    });
    document.getElementById('toollab-picker-clear-all').addEventListener('click', () => {
        toollabPickerSelectedIds.clear();
        toollabPickerRender();
    });
    document.getElementById('toollab-picker-refresh').addEventListener('click', toollabPickerRefresh);

    toollabPickerRefresh();
}


async function toollabPickerRefresh() {
    try {
        const r = await fetch(`${TOOLLAB_BASE}/tools?include_inactive=false&include_shared=true`);
        if (!r.ok) {
            const list = document.getElementById('toollab-picker-list');
            if (list) list.innerHTML = `<div class="toollab-picker-empty">조회 실패: ${r.status}</div>`;
            return;
        }
        toollabTools = await r.json();
        // Drop any selected ids that no longer exist (e.g. deleted between visits).
        const validIds = new Set(toollabTools.map(t => t.id));
        for (const id of Array.from(toollabPickerSelectedIds)) {
            if (!validIds.has(id)) toollabPickerSelectedIds.delete(id);
        }
        toollabPickerRender();
    } catch (err) {
        const list = document.getElementById('toollab-picker-list');
        if (list) list.innerHTML = `<div class="toollab-picker-empty">네트워크 오류: ${err}</div>`;
    }
}


/** Tools currently visible after applying the search filter. */
function toollabPickerVisible() {
    const term = toollabPickerSearchTerm;
    if (!term) return toollabTools;
    return toollabTools.filter(t => {
        const name = (t.name || '').toLowerCase();
        const desc = (t.description || '').toLowerCase();
        const tags = (t.tags || []).map(x => x.toLowerCase()).join(' ');
        return name.includes(term) || desc.includes(term) || tags.includes(term);
    });
}


function toollabPickerRender() {
    const container = document.getElementById('toollab-picker-list');
    if (!container) return;
    const visible = toollabPickerVisible();
    container.innerHTML = '';
    if (!visible.length) {
        container.innerHTML = '<div class="toollab-picker-empty">조건에 맞는 도구가 없습니다.</div>';
    } else {
        const mine = visible.filter(t => t.owner_user_id !== 'system' && t.is_owner);
        const shared = visible.filter(t => t.owner_user_id !== 'system' && !t.is_owner);
        const system = visible.filter(t => t.owner_user_id === 'system');
        const groups = [
            { label: '내 도구', items: mine },
            { label: '공유 도구', items: shared },
            { label: '시드 (system)', items: system },
        ];
        for (const grp of groups) {
            if (!grp.items.length) continue;
            const h = document.createElement('div');
            h.className = 'toollab-picker-group-label';
            h.textContent = grp.label;
            container.appendChild(h);
            for (const t of grp.items) {
                const item = document.createElement('label');
                item.className = 'toollab-picker-item';
                if (toollabPickerSelectedIds.has(t.id)) item.classList.add('selected');
                const macro = (t.tags || []).includes('macro');
                const publicBadge = t.is_public && t.owner_user_id !== 'system'
                    ? '<span class="toollab-tag-public">공개</span>'
                    : '';
                const checked = toollabPickerSelectedIds.has(t.id) ? 'checked' : '';
                item.innerHTML = `
                    <input type="checkbox" data-tool-id="${t.id}" ${checked} />
                    <div class="toollab-picker-item-body">
                        <div class="toollab-picker-item-name">
                            ${escapeHtml(t.name)}
                            ${macro ? '<span class="toollab-tag-macro">macro</span>' : ''}
                            ${publicBadge}
                        </div>
                        <div class="toollab-picker-item-desc">${escapeHtml(t.description || '')}</div>
                        <div class="toollab-picker-item-meta">v${t.version} · owner=${escapeHtml(t.owner_user_id)}</div>
                    </div>
                `;
                const cb = item.querySelector('input');
                cb.addEventListener('change', () => {
                    if (cb.checked) toollabPickerSelectedIds.add(t.id);
                    else toollabPickerSelectedIds.delete(t.id);
                    item.classList.toggle('selected', cb.checked);
                    toollabPickerUpdateCount();
                });
                container.appendChild(item);
            }
        }
    }
    toollabPickerUpdateCount();
}


function toollabPickerUpdateCount() {
    const count = document.getElementById('toollab-picker-count');
    if (!count) return;
    const total = toollabTools.length;
    count.textContent = `${toollabPickerSelectedIds.size} / ${total} 선택`;
}


async function toollabRun() {
    const prompt = document.getElementById('toollab-prompt').value.trim();
    if (!prompt) {
        toollabAppendError('프롬프트를 입력하세요.');
        return;
    }
    const sysPrompt = document.getElementById('toollab-system-prompt').value;
    const modelType = document.getElementById('toollab-model').value;
    // Always send tool_ids — empty array means "no tools" (clean baseline run).
    // We never send `null` here so the backend doesn't fall back to "all visible".
    const toolIds = Array.from(toollabPickerSelectedIds);
    // Sequential 체크박스: 체크됨 = parallel_tool_calls=false (조건부 흐름 안정).
    const sequential = document.getElementById('toollab-sequential').checked;
    const maxIterRaw = document.getElementById('toollab-max-iter').value.trim();
    const maxIter = maxIterRaw ? Number(maxIterRaw) : null;
    const payload = {
        prompt,
        model_type: modelType,
        system_prompt: sysPrompt || null,
        tool_ids: toolIds,
        parallel_tool_calls: !sequential,
        history: toollabHistory.length ? toollabHistory : null,
    };
    if (maxIter && Number.isFinite(maxIter)) {
        payload.max_tool_iterations = maxIter;
    }
    toollabTurnCount += 1;
    toollabAppendTurnHeader(prompt, toollabTurnCount);
    document.getElementById('toollab-run-stats').innerHTML = '';
    try {
        const r = await fetch(`${TOOLLAB_BASE}/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!r.ok) {
            const body = await r.text();
            toollabAppendError(`실행 실패 (${r.status}): ${body}`);
            return;
        }
        const result = await r.json();
        toollabAppendTurnResult(result);
        // Persist this turn into history so the *next* call carries it.
        toollabPushTurnToHistory(prompt, result);
        // Clear the prompt textarea so the next turn starts fresh.
        document.getElementById('toollab-prompt').value = '';
    } catch (err) {
        toollabAppendError(`요청 실패: ${err}`);
    }
}


/** Reset trace area, run stats, and history — start a fresh conversation. */
function toollabNewConversation() {
    document.getElementById('toollab-trace').innerHTML = '';
    document.getElementById('toollab-run-stats').innerHTML = '';
    toollabHistory = [];
    toollabTurnCount = 0;
}


function toollabPushTurnToHistory(userPrompt, result) {
    // 1) user message that triggered this turn
    toollabHistory.push({ role: 'user', content: userPrompt });
    // 2) the AI/tool steps from the trace, in order
    for (const step of (result.steps || [])) {
        if (step.kind === 'ai') {
            const tcs = (step.tool_calls || []).map(tc => ({
                id: tc.id || '',
                name: tc.name || '',
                args: tc.args || {},
            }));
            toollabHistory.push({
                role: 'assistant',
                content: step.content || '',
                tool_calls: tcs.length ? tcs : null,
            });
        } else if (step.kind === 'tool') {
            // Mirror libs/toollab/serializer.to_tool_message_content so the
            // replay matches what the LLM saw at original execution time.
            const body = step.ok
                ? { ok: true, result: step.result }
                : { ok: false, error: step.error || '' };
            toollabHistory.push({
                role: 'tool',
                content: JSON.stringify(body),
                tool_call_id: step.tool_call_id || '',
                name: step.name || '',
            });
        }
    }
}


function toollabAppendTurnHeader(prompt, turn) {
    const traceEl = document.getElementById('toollab-trace');
    const header = document.createElement('div');
    header.className = 'toollab-turn-header';
    header.innerHTML = `
        <div class="toollab-turn-label">▶ Turn ${turn} · USER</div>
        <div class="toollab-turn-prompt">${escapeHtml(prompt)}</div>
        <div class="toollab-running">실행 중... LLM 호출 + 도구 실행 중입니다.</div>
    `;
    traceEl.appendChild(header);
    traceEl.scrollTop = traceEl.scrollHeight;
}


function toollabAppendError(msg) {
    const traceEl = document.getElementById('toollab-trace');
    // Drop the most-recent "실행 중..." placeholder if present.
    traceEl.querySelectorAll('.toollab-running').forEach(el => el.remove());
    const err = document.createElement('div');
    err.className = 'toollab-status error';
    err.textContent = msg;
    traceEl.appendChild(err);
    traceEl.scrollTop = traceEl.scrollHeight;
}


function toollabAppendTurnResult(result) {
    const traceEl = document.getElementById('toollab-trace');
    const statsEl = document.getElementById('toollab-run-stats');
    // Drop the "실행 중..." spinner from the just-added turn header.
    traceEl.querySelectorAll('.toollab-running').forEach(el => el.remove());

    const warnHtml = (result.warnings || [])
        .map(w => `<span class="toollab-warn">⚠ ${escapeHtml(w)}</span>`).join(' ');
    statsEl.innerHTML = `
        <div class="toollab-stats-row">
            <span>turn: <b>${toollabTurnCount}</b></span>
            <span>iterations: <b>${result.iterations}</b></span>
            <span>latency: <b>${result.latency_ms} ms</b></span>
            <span>tokens: <b>${result.input_tokens ?? '-'} / ${result.output_tokens ?? '-'}</b></span>
            <span>${result.truncated ? '<b class="toollab-truncated">truncated</b>' : ''}</span>
            <span>model: <b>${escapeHtml(result.model)}</b></span>
            <span>served_by: <b>${escapeHtml(result.served_by)}</b></span>
            ${result.tool_call_parser ? `<span>parser: <b>${escapeHtml(result.tool_call_parser)}</b></span>` : ''}
            ${warnHtml}
        </div>
    `;
    for (const step of (result.steps || [])) {
        traceEl.appendChild(renderStep(step));
    }
    if (result.final_response) {
        const finalEl = document.createElement('div');
        finalEl.className = 'toollab-final';
        finalEl.innerHTML = `<div class="toollab-final-label">최종 응답</div><div>${escapeHtml(result.final_response)}</div>`;
        traceEl.appendChild(finalEl);
    }
    traceEl.scrollTop = traceEl.scrollHeight;
}


function renderStep(step) {
    const card = document.createElement('div');
    card.className = `toollab-step toollab-step-${step.kind}`;
    if (step.kind === 'ai') {
        const calls = (step.tool_calls || []).map(tc =>
            `<div class="toollab-call">→ ${escapeHtml(tc.name)}(${escapeHtml(JSON.stringify(tc.args || {}))})</div>`
        ).join('');
        const reasoningSection = step.reasoning
            ? `<details class="toollab-reasoning">
                 <summary>🧠 추론 (${step.reasoning.length}자)</summary>
                 <pre>${escapeHtml(step.reasoning)}</pre>
               </details>`
            : '';
        card.innerHTML = `
            <div class="toollab-step-header">step ${step.step} · AI</div>
            ${step.content ? `<div class="toollab-content">${escapeHtml(step.content)}</div>` : ''}
            ${calls}
            ${reasoningSection}
        `;
    } else {
        const okBadge = step.ok
            ? '<span class="toollab-badge-ok">ok</span>'
            : '<span class="toollab-badge-err">error</span>';
        const body = step.ok
            ? `<pre>${escapeHtml(JSON.stringify(step.result, null, 2))}</pre>`
            : `<div class="toollab-error">${escapeHtml(step.error || '')}</div>`;
        card.innerHTML = `
            <div class="toollab-step-header">step ${step.step} · TOOL ${escapeHtml(step.name || '')} ${okBadge}</div>
            <div class="toollab-args">args: <code>${escapeHtml(JSON.stringify(step.args || {}))}</code></div>
            ${body}
            <div class="toollab-step-meta">latency ${step.latency_ms ?? '-'} ms · tool_call_id ${escapeHtml(step.tool_call_id || '')}</div>
        `;
    }
    return card;
}


// ===========================================================================
// Helpers
// ===========================================================================


function toollabStatus(level, message) {
    const el = document.getElementById('toollab-edit-status');
    if (!el) return;
    el.className = `toollab-status ${level}`;
    el.textContent = message;
}


function toollabClearStatus() {
    const el = document.getElementById('toollab-edit-status');
    if (!el) return;
    el.className = 'toollab-status';
    el.textContent = '';
}


function escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
