/**
 * bulk.js - 엑셀 일괄 업로드/삭제 로직
 */
let currentTaskId = null;

async function loadBulkDropdowns() {
    try {
        const colRes = await fetch('/api/collections');
        const domRes = await fetch('/api/domains');

        if (colRes.ok) {
            const cols = await colRes.json();
            const colSelect = document.getElementById('bulk-collection');
            const delColSelect = document.getElementById('delete-collection');
            cols.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c.collection_name;
                opt.textContent = c.name;
                colSelect.appendChild(opt);

                const opt2 = opt.cloneNode(true);
                delColSelect.appendChild(opt2);
            });
        }
        if (domRes.ok) {
            const doms = await domRes.json();
            const domSelect = document.getElementById('bulk-domain');
            const delDomSelect = document.getElementById('delete-domain');

            doms.forEach(d => {
                const opt = document.createElement('option');
                opt.value = d.id;
                opt.textContent = d.name;
                domSelect.appendChild(opt);

                const opt2 = opt.cloneNode(true);
                delDomSelect.appendChild(opt2);
            });
        }
    } catch (e) {
        console.error(e);
    }
}

async function confirmDelete() {
    const colName = document.getElementById('delete-collection').value;
    const domId = document.getElementById('delete-domain').value;
    const source = document.getElementById('delete-source').value.trim();

    if (!colName) {
        alert("콜렉션을 선택해주세요.");
        return;
    }
    if (!domId && !source) {
        alert("도메인 또는 소스값 중 하나는 반드시 입력해야 합니다.");
        return;
    }

    try {
        let url = `/api/rag/delete-count?collection=${colName}`;
        if (domId) url += `&domain_id=${domId}`;
        if (source) url += `&source=${encodeURIComponent(source)}`;

        const res = await fetch(url);
        if (!res.ok) throw new Error("Count failed");

        const data = await res.json();
        const count = data.count;

        if (count === 0) {
            alert("삭제할 대상이 없습니다.");
            return;
        }

        if (confirm(`총 ${count}건의 데이터를 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`)) {
            await startDelete(colName, domId, source);
        }
    } catch (e) {
        alert("에러가 발생했습니다: " + e.message);
    }
}

async function startDelete(colName, domId, source) {
    try {
        let url = `/api/rag/bulk-delete?collection=${colName}`;
        if (domId) url += `&domain_id=${domId}`;
        if (source) url += `&source=${encodeURIComponent(source)}`;

        const res = await fetch(url, { method: 'DELETE' });
        if (res.ok) {
            alert("삭제가 완료되었습니다.");
            document.getElementById('delete-source').value = '';
        } else {
            const err = await res.json();
            alert("삭제 실패: " + err.detail);
        }
    } catch (e) {
        alert("서버 통신 에러");
    }
}

async function startUpload() {
    const fileInput = document.getElementById('bulk-file');
    const colName = document.getElementById('bulk-collection').value;
    const domId = document.getElementById('bulk-domain').value;

    if (!fileInput.files || fileInput.files.length === 0) {
        alert("엑셀 파일을 선택해주세요.");
        return;
    }
    if (!colName || !domId) {
        alert("콜렉션과 도메인을 선택해주세요.");
        return;
    }

    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append("file", file);
    formData.append("collection", colName);
    formData.append("domain_id", domId);

    document.getElementById('progress-overlay').style.display = 'flex';
    document.getElementById('prog-total').innerText = '0';
    document.getElementById('prog-success').innerText = '0';
    document.getElementById('prog-error').innerText = '0';
    document.getElementById('prog-status').innerText = '작업이 진행 중입니다. 화면을 조작하거나 닫지 마세요.';
    document.getElementById('btn-close-prog').style.display = 'none';
    document.getElementById('btn-download-error').style.display = 'none';

    try {
        const res = await fetch('/api/rag/bulk-upload', {
            method: 'POST',
            body: formData
        });

        if (!res.ok) {
            const err = await res.json();
            alert("Upload failed: " + err.detail);
            document.getElementById('progress-overlay').style.display = 'none';
            return;
        }

        const data = await res.json();
        currentTaskId = data.task_id;

        pollProgress(currentTaskId);
    } catch (e) {
        alert("Server error");
        document.getElementById('progress-overlay').style.display = 'none';
    }
}

function pollProgress(taskId) {
    const interval = setInterval(async () => {
        try {
            const res = await fetch(`/api/rag/bulk-progress/${taskId}`);
            if (res.ok) {
                const status = await res.json();
                document.getElementById('prog-total').innerText = status.total;
                document.getElementById('prog-success').innerText = status.success;
                document.getElementById('prog-error').innerText = status.error;

                if (status.done) {
                    clearInterval(interval);
                    document.getElementById('prog-status').innerText = '작업이 완료되었습니다!';
                    document.getElementById('btn-close-prog').style.display = 'inline-block';

                    if (status.error > 0) {
                        document.getElementById('btn-download-error').style.display = 'inline-block';
                    }
                }
            }
        } catch (e) {
            console.error("Polling error", e);
        }
    }, 500);
}

function closeProgress() {
    document.getElementById('progress-overlay').style.display = 'none';
    document.getElementById('bulk-file').value = '';
}

function downloadErrors() {
    if (currentTaskId) {
        window.location.href = `/api/rag/bulk-error-download/${currentTaskId}`;
    }
}

/**
 * Bulk 뷰 초기화 - SPA 라우터가 첫 진입 시 호출
 */
async function initBulk() {
    await loadBulkDropdowns();
}
