/**
 * chat.js - 채팅 관련 로직 (SRP: 채팅 기능만 담당)
 */
let currentAiMessage = null;

// 대화 쓰레드 ID 관리 (페이지 로드 시마다 항상 새로 생성하여 화면 상태와 서버 컨텍스트 동기화)
const threadId = crypto.randomUUID();

function toggleSystemPrompt() {
    const input = document.getElementById('system-prompt-input');
    if (input.style.display === 'none' || input.style.display === '') {
        input.style.display = 'block';
    } else {
        input.style.display = 'none';
    }
}

async function sendMessage() {
    const input = document.getElementById('user-input');
    const modelSelect = document.getElementById('model-select');
    const systemPromptInput = document.getElementById('system-prompt-input');
    const tempInput = document.getElementById('temperature');

    const message = input.value.trim();
    const modelType = modelSelect.value;
    const systemPrompt = systemPromptInput.value.trim();
    const temperature = tempInput.value;

    if (!message) return;

    appendMessage('user', message);
    input.value = '';
    input.style.height = 'auto';

    document.getElementById('loading').style.display = 'block';

    try {
        const response = await fetch('/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                message: message,
                thread_id: threadId,
                model_type: modelType,
                system_prompt: systemPrompt,
                temperature: parseFloat(temperature)
            })
        });

        if (response.status === 401) {
            alert('로그인이 필요합니다.');
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        currentAiMessage = appendMessage('ai', '');

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value);
            const lines = chunk.split('\n');

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const dataStr = line.replace('data: ', '');
                    if (dataStr === '[DONE]') continue;
                    try {
                        const data = JSON.parse(dataStr);
                        currentAiMessage.innerText += data.content;
                        const messagesDiv = document.getElementById('messages');
                        messagesDiv.scrollTop = messagesDiv.scrollHeight;
                    } catch (e) { }
                }
            }
        }
    } catch (err) {
        console.error(err);
        appendMessage('ai', '에러가 발생했습니다.');
    } finally {
        document.getElementById('loading').style.display = 'none';
    }
}

function appendMessage(type, text) {
    const messagesDiv = document.getElementById('messages');
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${type}`;
    msgDiv.innerText = text;
    messagesDiv.appendChild(msgDiv);
    msgDiv.style.whiteSpace = 'pre-wrap';
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    return msgDiv;
}

// 입력창 자동 높이 조절 및 엔터키 이벤트 처리
document.addEventListener('DOMContentLoaded', () => {
    const userInput = document.getElementById('user-input');
    userInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    userInput.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';
    });
});
