import * as vscode from 'vscode';
import * as cp from 'child_process';

const SERVER_URL = 'http://127.0.0.1:5000';
const WS_URL = 'ws://127.0.0.1:8765';

export function activate(context: vscode.ExtensionContext) {
    console.log('Activating JARVIS extension');

    // Command to start the engine
    const startCmd = vscode.commands.registerCommand('jarvis.startEngine', async () => {
        try {
            const resp = await fetch(SERVER_URL + '/api/state', { method: 'GET' });
            if (resp.ok) {
                vscode.window.showInformationMessage('JARVIS backend already running.');
                return;
            }
        } catch (e) {
            // not running — spawn
        }

        // Spawn jarvis_desktop.py in a detached background process
        try {
            const python = process.platform === 'win32' ? 'python' : 'python3';
            const script = context.asAbsolutePath('../../jarvis_desktop.py');
            const child = cp.spawn(python, [script], {
                detached: true,
                stdio: 'ignore',
                shell: false
            });
            child.unref();
            vscode.window.showInformationMessage('JARVIS engine started.');
        } catch (err) {
            vscode.window.showErrorMessage('Failed to spawn JARVIS engine: ' + String(err));
        }
    });
    context.subscriptions.push(startCmd);

    // Register webview sidebar (JARVIS HUD)
    const provider = new JarvisHudProvider(context.extensionUri);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider('jarvisHud', provider)
    );

    // Register a chat participant if the Chat API is available
    const chatApi: any = (vscode as any).chat;
    if (chatApi && typeof chatApi.createChatParticipant === 'function') {
        try {
            const participant = chatApi.createChatParticipant({ id: '@jarvis', label: 'JARVIS' });
            context.subscriptions.push(participant);
        } catch (err) {
            console.warn('Chat participant registration failed:', err);
        }
    }

    // Register a simple ChatProvider adapter if possible
    if (chatApi && typeof chatApi.registerChatProvider === 'function') {
        const providerId = 'jarvis-local-provider';
        chatApi.registerChatProvider(providerId, {
            async provideReply(conversation, message, cancellation, progressOrStream) {
                // message contains text in message.text
                const text = message?.text || '';
                if (!text) return { items: [] };

                // Determine if progressOrStream is a stream-like object (newer Chat API)
                const stream = (progressOrStream && typeof progressOrStream.markdown === 'function') ? progressOrStream : null;
                const progress = (!stream && progressOrStream && typeof progressOrStream.report === 'function') ? progressOrStream : null;

                // Determine preferred provider from extension configuration
                const cfg = vscode.workspace.getConfiguration('jarvis');
                const provider = cfg.get('provider', 'ollama');

                try {
                    const resp = await fetch(SERVER_URL + '/api/chat/stream', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: text, provider })
                    });

                    if (!resp.ok || !resp.body) {
                        const data = await resp.json().catch(() => ({}));
                        const reply = data.reply || `Error: ${resp.status}`;
                        return { items: [{ kind: 1, detail: 'JARVIS', text: reply, mime: 'text/markdown' }] };
                    }

                    // Read SSE stream from resp.body
                    const reader = resp.body.getReader();
                    const decoder = new TextDecoder('utf-8');
                    let buffer = '';

                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        buffer += decoder.decode(value, { stream: true });
                        // SSE events split by double newline
                        let idx;
                        while ((idx = buffer.indexOf('\n\n')) !== -1) {
                            const raw = buffer.slice(0, idx);
                            buffer = buffer.slice(idx + 2);
                            // parse lines starting with data:
                            const lines = raw.split('\n').map(l => l.trim());
                            for (const line of lines) {
                                if (!line) continue;
                                if (line.startsWith('data:')) {
                                    const payloadText = line.slice(5).trim();
                                    try {
                                        const payload = JSON.parse(payloadText);
                                        if (payload.token) {
                                            const token = payload.token;
                                            if (stream) {
                                                try { stream.markdown(token); } catch (e) { if (progress) progress.report({ message: token }); }
                                            } else if (progress) {
                                                progress.report({ message: token });
                                            }
                                        } else if (payload.done) {
                                            const final = payload.response || '';
                                            return { items: [{ kind: 1, detail: 'JARVIS', text: final, mime: 'text/markdown' }] };
                                        } else if (payload.error) {
                                            return { items: [{ kind: 1, detail: 'JARVIS', text: 'Error: ' + payload.error, mime: 'text/markdown' }] };
                                        }
                                    } catch (err) {
                                        // ignore JSON parse errors
                                    }
                                }
                            }
                        }
                    }

                    // If stream ended without explicit final chunk, attempt to flush remaining buffer
                    if (buffer.trim()) {
                        const lines = buffer.split('\n');
                        for (const line of lines) {
                            if (line.startsWith('data:')) {
                                try {
                                    const payload = JSON.parse(line.slice(5).trim());
                                    if (payload.response) {
                                        return { items: [{ kind: 1, detail: 'JARVIS', text: payload.response, mime: 'text/markdown' }] };
                                    }
                                } catch (e) {}
                            }
                        }
                    }

                    return { items: [{ kind: 1, detail: 'JARVIS', text: 'No response from JARVIS.', mime: 'text/markdown' }] };
                } catch (err) {
                    return { items: [{ kind: 1, detail: 'JARVIS', text: 'Error contacting local JARVIS server: ' + String(err), mime: 'text/markdown' }] };
                }
            }
        });
    }
}

export function deactivate() {
    // nothing special
}

class JarvisHudProvider implements vscode.WebviewViewProvider {
    private _view?: vscode.WebviewView;
    constructor(private readonly _extensionUri: vscode.Uri) {}

    resolveWebviewView(webviewView: vscode.WebviewView) {
        this._view = webviewView;
        webviewView.webview.options = {
            enableScripts: true
        };
        webviewView.webview.html = this._getHtmlForWebview(webviewView.webview);
    }

    private _getHtmlForWebview(webview: vscode.Webview): string {
        const nonce = getNonce();
        return `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>JARVIS HUD</title>
<style>
body { background:#050b16; color:#eee; font-family: monospace; padding: 12px }
#status { font-weight:700; margin-bottom:8px }
canvas { width:100%; height:80px; background: rgba(255,255,255,0.03); border-radius:8px }
#transcript { margin-top:8px; font-size:14px }
#proposals { margin-top:12px }
.proposal { background: rgba(255,255,255,0.02); padding:8px; border-radius:6px; margin-bottom:8px }
.proposal pre { max-height:120px; overflow:auto; background: rgba(0,0,0,0.4); padding:8px; }
.button { padding:6px 10px; margin-right:6px; border-radius:6px; cursor:pointer; background:#2b7cff; color:#fff }
.button.reject { background:#ff6b6b }
</style>
</head>
<body>
<div id="status">Connecting to JARVIS...</div>
<canvas id="waveform"></canvas>
<div id="transcript"></div>
<h3>Pending Tool Proposals</h3>
<div id="proposals"></div>
<script nonce="${nonce}">
(function(){
        const status = document.getElementById('status');
        const transcript = document.getElementById('transcript');
        const canvas = document.getElementById('waveform');
        const proposalsEl = document.getElementById('proposals');
        const ctx = canvas.getContext('2d');
        function resize(){ canvas.width = canvas.clientWidth; canvas.height = canvas.clientHeight; }
        window.addEventListener('resize', resize); resize();

        function drawLevel(level){
            ctx.clearRect(0,0,canvas.width,canvas.height);
            const w = canvas.width; const h = canvas.height; const val = Math.max(0, Math.min(1, level));
            ctx.fillStyle='rgba(103,217,255,0.18)';
            ctx.fillRect(0, h*(1-val), w, h*val);
        }

        let ws;
        try{
            ws = new WebSocket('${WS_URL}');
            ws.onopen = () => { status.textContent = 'Connected to JARVIS (WS)'; };
            ws.onmessage = (ev) => {
                try{
                    const d = JSON.parse(ev.data);
                    if(d.type === 'audio_level' && d.payload){ drawLevel(d.payload.rms || 0); }
                    if(d.type === 'transcript' && d.payload){ transcript.textContent = d.payload.text || ''; }
                    if(d.type === 'state' && d.payload){ status.textContent = 'State: ' + (d.payload.state || 'unknown'); }
                    if(d.type === 'pending_tool_proposal' && d.payload){ addProposal(d.payload); }
                }catch(e){ console.warn(e); }
            };
            ws.onclose = () => { status.textContent = 'Disconnected from JARVIS (WS)'; };
        }catch(e){ status.textContent = 'WS connection failed: ' + e.message; }

        function addProposal(p){
            const container = document.createElement('div');
            container.className = 'proposal';
            const title = document.createElement('div'); title.textContent = `#${p.index}: ${p.spec}`;
            const pre = document.createElement('pre'); pre.textContent = p.preview || '';
            const reasons = document.createElement('div'); reasons.textContent = 'Reasons: ' + (p.reasons || []).join(', ');
            const approve = document.createElement('button'); approve.className='button'; approve.textContent='Approve & Inject';
            const reject = document.createElement('button'); reject.className='button reject'; reject.textContent='Reject';
            approve.onclick = () => { actionProposal(p.index, true, container); };
            reject.onclick = () => { actionProposal(p.index, false, container); };
            container.appendChild(title); container.appendChild(pre); container.appendChild(reasons); container.appendChild(approve); container.appendChild(reject);
            proposalsEl.appendChild(container);
        }

        async function actionProposal(index, confirm, container){
            try{
                const resp = await fetch('${SERVER_URL}/api/confirm_tool', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({index, confirm}) });
                const data = await resp.json().catch(()=>({}));
                if(resp.ok){ container.remove(); }
                else { alert('Failed: ' + (data.error || JSON.stringify(data))); }
            }catch(e){ alert('Request failed: ' + e.message); }
        }
})();
</script>
</body>
</html>`;
    }
}

function getNonce() {
    let text = '';
    const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    for (let i = 0; i < 32; i++) {
        text += possible.charAt(Math.floor(Math.random() * possible.length));
    }
    return text;
}
